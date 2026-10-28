"""FFV1-to-H.264 caption pixel tests: no synthetic bytes imply real media approval."""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_mlt_caption_pixels import (
    _caption_mask, CaptionedMasterPixelsNotVerified, prove_native_captioned_master,
)

WIDTH = 50
HEIGHT = 20
ROI = (10, 5, 40, 18)


def samples():
    previous = bytearray(b"\xff" * (WIDTH * HEIGHT * 3))
    active = previous[:]
    master = bytearray(b"\xfd" * (WIDTH * HEIGHT * 3))
    for y in range(8, 16):
        for x in range(16, 28):
            start = (y * WIDTH + x) * 3
            active[start:start + 3] = b"\x00" * 3
            master[start:start + 3] = b"\x04" * 3
    return bytes(previous), bytes(active), bytes(master)


class NativeMltCaptionMaskTests(unittest.TestCase):
    def test_real_source_difference_and_lossy_master_preservation(self):
        previous, active, master = samples()
        observed = _caption_mask(
            previous, active, master,
            width=WIDTH, height=HEIGHT, roi=ROI, minimum=60,
        )
        self.assertEqual(observed["native_caption_mask_pixels"], 96)
        self.assertLess(observed["masked_lossy_master_error_mean"], 5)
        self.assertGreater(observed["masked_inactive_reference_error_mean"], 200)

    def test_final_master_without_actual_glyphs_fails(self):
        previous, active, _ = samples()
        with self.assertRaises(CaptionedMasterPixelsNotVerified):
            _caption_mask(
                previous, active, previous,
                width=WIDTH, height=HEIGHT, roi=ROI, minimum=60,
            )

    def test_native_source_that_never_drew_caption_fails(self):
        previous, _, master = samples()
        with self.assertRaises(CaptionedMasterPixelsNotVerified):
            _caption_mask(
                previous, previous, master,
                width=WIDTH, height=HEIGHT, roi=ROI, minimum=60,
            )

    def test_degenerate_incomplete_and_wrong_region_fail(self):
        previous, active, master = samples()
        for a, b, c, roi in [
            (previous[:-1], active, master, ROI),
            (previous, active, master[:-1], ROI),
            (previous, active, master, (0, 0, WIDTH + 1, HEIGHT)),
            (previous, active, master, (0, 0, 0, HEIGHT)),
        ]:
            with self.subTest(roi=roi, count=len(a)):
                with self.assertRaises(CaptionedMasterPixelsNotVerified):
                    _caption_mask(a, b, c, width=WIDTH, height=HEIGHT, roi=roi, minimum=60)

    def test_missing_original_and_untrusted_source_identity_cannot_pass(self):
        with self.assertRaises(CaptionedMasterPixelsNotVerified):
            prove_native_captioned_master(
                Path("not-real.mkv"), Path("not-real.mp4"), Path("ffmpeg"),
                source_sha="unverified", semwright_sha="b" * 40,
                selected_cues=2,
            )
        with self.assertRaises(CaptionedMasterPixelsNotVerified):
            prove_native_captioned_master(
                Path("not-real.mkv"), Path("not-real.mp4"), Path("ffmpeg"),
                source_sha="a" * 40, semwright_sha="b" * 40,
                selected_cues=0,
            )





class NativeHybridOriginalReadbackTests(unittest.TestCase):
    """Synthetic contracts only; these do not execute any native renderer."""

    @staticmethod
    def wave(frames=288000,channels=2,rate=48000):
        import io,wave
        output=io.BytesIO()
        with wave.open(output,"wb") as writer:
            writer.setnchannels(channels); writer.setsampwidth(2); writer.setframerate(rate)
            writer.writeframes(b"\0"*(frames*channels*2))
        return output.getvalue()

    @staticmethod
    def rgba(index):
        from native_mlt_caption_pixels import HYBRID_PIXEL_CONTRACT
        width,height=1920,1080
        value=bytearray(b"\0\0\0\xff"*(width*height))
        x,y,w,h=HYBRID_PIXEL_CONTRACT["source_geometry"][index]
        rgb=[0,0,0];rgb[index]=255
        line=bytes(rgb+[255])*w
        for row in range(y,y+h):
            offset=(row*width+x)*4;value[offset:offset+len(line)]=line
        return bytes(value)

    def test_hybrid_difference_records_loss_without_inventing_tolerance(self):
        from native_mlt_caption_pixels import hybrid_rgb_difference
        a=bytes([10,20,30,255,40,50,60,255])
        b=bytes([11,18,34,255,40,50,60,255])
        measured=hybrid_rgb_difference(a,b,width=2,height=1)
        self.assertEqual(measured["absolute_rgb_error_sum"],7)
        self.assertEqual(measured["maximum_absolute_rgb_error"],4)
        self.assertEqual(measured["changed_pixel_count"],1)
        self.assertFalse(measured["pixel_equal"])
        self.assertFalse(measured["quality_threshold_applied"])
        with self.assertRaises(AssertionError):hybrid_rgb_difference(a,b[:-1],width=2,height=1)

    def test_hybrid_three_source_cut_screen_preserves_boundaries_59_60_119_120(self):
        from native_mlt_caption_pixels import hybrid_frame_presence
        for index,frames in [(0,[0,59]),(1,[60,119]),(2,[120,179])]:
            data=self.rgba(index)
            for frame in frames:
                with self.subTest(index=index,frame=frame):
                    result=hybrid_frame_presence(data,frame)
                    self.assertEqual(result["selected_native_contribution"],index)
            with self.assertRaises(AssertionError):hybrid_frame_presence(data,((index+1)%3)*60)

    def test_hybrid_screen_rejects_lost_alpha_black_frame_and_wrong_matting(self):
        from native_mlt_caption_pixels import hybrid_frame_presence
        source=self.rgba(0)
        alpha=bytearray(source);alpha[3]=0
        wrong=bytearray(source);point=(20*1920+20)*4;wrong[point:point+3]=b"\xff\xff\xff"
        for raw in (bytes(alpha),bytes(wrong),b"\0\0\0\xff"*(1920*1080)):
            with self.assertRaises(AssertionError):hybrid_frame_presence(raw,0)

    def test_hybrid_original_wave_uses_actual_six_second_profile_and_keeps_legacy_pin(self):
        import hashlib
        from native_mlt_g2_observation import hybrid_source_wave,REFERENCE_WAV_SHA256
        from native_mlt_public_artifact import mode_contract,LEGACY_VOICE_WAV_SHA256
        raw=self.wave();proof=hybrid_source_wave(raw,hashlib.sha256(raw).hexdigest())
        self.assertEqual(proof["sample_frames"],288000)
        self.assertFalse(proof["generated_by_this_caller"])
        self.assertEqual(mode_contract("final-mp4").source_segment_frames,(32,1))
        self.assertEqual(mode_contract("final-mp4").pcm_frames,52800)
        self.assertEqual(REFERENCE_WAV_SHA256,LEGACY_VOICE_WAV_SHA256)
        self.assertEqual(REFERENCE_WAV_SHA256,"66aeaa3aa6fff53834ab4003671ce79068bcbd6f631609f0f988b3ac9744ebe9")
        self.assertEqual(mode_contract("hybrid-av").source_segment_frames,(60,60,60))

    def test_hybrid_wave_wrong_hash_channels_rate_or_duration_is_rejected(self):
        import hashlib
        from native_mlt_g2_observation import hybrid_source_wave
        for raw in (self.wave(frames=52800),self.wave(channels=1),self.wave(rate=44100),self.wave(frames=288001)):
            with self.assertRaises((AssertionError,ValueError)):
                hybrid_source_wave(raw,hashlib.sha256(raw).hexdigest())
        raw=self.wave()
        with self.assertRaises((AssertionError,ValueError)):hybrid_source_wave(raw,"0"*64)

    def test_hybrid_source_provenance_requires_original_canonical_synthetic_seed(self):
        import hashlib,json,copy
        from native_mlt_g2_observation import hybrid_film180_original_provenance
        raw=self.wave();digest=hashlib.sha256(raw).hexdigest()
        proof={"sha256":digest,"bytes":len(raw)}
        source_root=Path.cwd()/"synthetic-provenance-unit-fixture"
        binary=source_root/"native-film180-e2e"
        argv=[str(binary),"seed",str(source_root/"source.sqlite3"),
              str(source_root/"original.wav"),str(source_root/"original-project.json")]
        seed={"schema":"motionwright.native.film180.synthetic-source/1","project_id":"source-project",
            "generation":"source-generation","revision":7,"frame_count":180,"scene_boundary_frame":90,
            "saved_reopen":True,"source_only":True,"native_render_performed":False,
            "human_approval":False,"synthetic_WAV_sha256":digest}
        project={"id":"source-project","generation":"source-generation","revision":7,
                 "audio":{"voice_tracks":[{"source_sha256":digest}]}}
        custody={"origin":"CURRENT_CANONICAL_FILM180_SOURCE","sha256":digest,"bytes":len(raw),
            "pcm_frames":288000,"sample_rate":48000,"channels":2,"sample_width":2,
            "historical_reference_WAV_reused":False,"native_effect_started":False}
        def packed(value):return json.dumps(value).encode()
        def originals(changed):
            stdout=packed(changed);stderr=b""
            return {"source.seed.intent.json":packed({"argv":argv,"timeout_seconds":40,
                        "automatic_retry":False,"native_renderer_command":False}),
                "source.seed.stdout":stdout,"source.seed.stderr":stderr,
                "source.seed.terminal.json":packed({"argv":argv,"timeout_seconds":40,"exit_code":0,
                    "deadline_exceeded":False,"error_class":None,"error":None,"automatic_retry":False,
                    "native_terminal_inferred_from_exit":False,"stdout_sha256":hashlib.sha256(stdout).hexdigest(),
                    "stdout_bytes":len(stdout),"stderr_sha256":hashlib.sha256(stderr).hexdigest(),"stderr_bytes":0}),
                "original-project.json":packed(project),"firstparty-source-custody.json":packed(custody)}
        observed=hybrid_film180_original_provenance(originals(seed),proof,binary_path=binary,binary_sha256="a"*64)
        self.assertEqual(observed["kind"],"first_party_synthetic_Film180_source")
        self.assertFalse(observed["real_voice_or_VOiCES_fixture"])
        self.assertFalse(observed["source_transcript_or_alignment_imported_into_hybrid"])
        self.assertFalse(observed["producer_binary_at_execution_proved_by_these_JSON_files"])
        for key,value in [("native_render_performed",True),("source_only",False),
                          ("synthetic_WAV_sha256","0"*64),("revision",False)]:
            changed=copy.deepcopy(seed);changed[key]=value
            with self.assertRaises(AssertionError):
                hybrid_film180_original_provenance(originals(changed),proof,binary_path=binary,binary_sha256="a"*64)
        for key,value in [("exit_code",1),("exit_code",False),("deadline_exceeded",True),
                          ("error_class","TimeoutExpired"),("stdout_sha256","0"*64)]:
            changed=originals(seed)
            terminal=json.loads(changed["source.seed.terminal.json"]);terminal[key]=value
            changed["source.seed.terminal.json"]=packed(terminal)
            with self.assertRaises(AssertionError):
                hybrid_film180_original_provenance(changed,proof,binary_path=binary,binary_sha256="a"*64)

    def test_hybrid_capture_keeps_absent_pipes_absent_and_rejects_duplicate_label(self):
        import json,tempfile
        from native_mlt_g2_observation import G2Session
        with tempfile.TemporaryDirectory() as raw:
            session=G2Session({},Path(raw),b"",{})
            session.hybrid=True;session.hybrid_capture_labels=set()
            capture={"decoder":{"native_render_performed":False}}
            session.retain_hybrid_capture("hybrid-preflight-failure",capture)
            terminal=json.loads((Path(raw)/"hybrid-readonly-originals/hybrid-preflight-failure.terminal.json").read_bytes())
            self.assertNotIn("stdout",terminal);self.assertNotIn("stderr",terminal)
            with self.assertRaises((AssertionError,ValueError)):
                session.retain_hybrid_capture("hybrid-preflight-failure",capture)

    def test_hybrid_incomplete_capture_does_not_claim_observed_empty_pipes(self):
        import json,tempfile
        from native_mlt_g2_observation import G2Session
        with tempfile.TemporaryDirectory() as raw:
            session=G2Session({},Path(raw),b"",{});session.hybrid=True;session.hybrid_capture_labels=set()
            session.retain_hybrid_capture("hybrid-not-started",{"state":"NOT_STARTED","stdout":b"","stderr":b""})
            item=json.loads((Path(raw)/"hybrid-readonly-originals/hybrid-not-started.terminal.json").read_bytes())
            self.assertEqual(item["state"],"NOT_STARTED")
            self.assertFalse(item["stdout"]["pipe_bytes_observed"])
            self.assertFalse(item["stderr"]["pipe_bytes_observed"])

    def test_hybrid_audio_supervision_error_survives_failed_original_custody(self):
        from contextlib import nullcontext
        from unittest import mock
        import tempfile
        import native_mlt_sequence_e2e as wrapper
        original=OSError("original read-only supervision failure")
        secondary=OSError("original-capture disk failure")
        with tempfile.TemporaryDirectory() as raw:
            session,private,_,_=self.retained_master_fixture(raw)
            session.retain_hybrid_capture=mock.Mock(side_effect=secondary)
            with mock.patch.object(wrapper,"digest",return_value="a"*64),\
                 mock.patch("native_mlt_loaded_module._verified_binary",return_value=nullcontext()),\
                 mock.patch("manim_native_readback.run_bounded",side_effect=original) as run:
                with self.assertRaises(OSError) as raised:
                    wrapper._inspect_hybrid_original_audio(private,Path("/fixture/ffmpeg"),{},mode="hybrid-av",g2=session)
            self.assertIs(raised.exception,original)
            self.assertEqual(run.call_count,1)
            self.assertEqual(session.retain_hybrid_capture.call_count,1)

    def test_hybrid_audio_success_with_failed_custody_is_not_promoted(self):
        from contextlib import nullcontext
        from unittest import mock
        import tempfile
        import native_mlt_sequence_e2e as wrapper
        secondary=OSError("capture unavailable after successful decode")
        with tempfile.TemporaryDirectory() as raw:
            session,private,_,_=self.retained_master_fixture(raw)
            session.retain_hybrid_capture=mock.Mock(side_effect=secondary)
            def decoder(argv,max_stdout,timeout_seconds,*,input_bytes,capture):
                pcm=b"\0"*(288000*4)
                self.complete_probe_capture(capture,input_bytes,pcm)
                return pcm,dict(capture["terminal"])
            with mock.patch.object(wrapper,"digest",return_value="a"*64),\
                 mock.patch("native_mlt_loaded_module._verified_binary",return_value=nullcontext()),\
                 mock.patch("manim_native_readback.run_bounded",side_effect=decoder) as run:
                with self.assertRaises(OSError) as raised:
                    wrapper._inspect_hybrid_original_audio(private,Path("/fixture/ffmpeg"),{},mode="hybrid-av",g2=session)
            self.assertIs(raised.exception,secondary)
            self.assertEqual(run.call_count,1);self.assertIsNone(session.pcm_proof)

    def test_existing_owner_shutdown_preserves_primary_and_still_closes_log(self):
        from types import SimpleNamespace
        from unittest import mock
        import native_mlt_sequence_e2e as wrapper
        original=OSError("existing daemon stop failed")
        daemon=SimpleNamespace(pid=123,poll=lambda:None,terminate=mock.Mock(side_effect=original))
        log=SimpleNamespace(close=mock.Mock(side_effect=OSError("log close also failed")))
        with self.assertRaises(OSError) as raised:wrapper.close_native_owner(daemon,log)
        self.assertIs(raised.exception,original)
        self.assertEqual(daemon.terminate.call_count,1);self.assertEqual(log.close.call_count,1)

    @staticmethod
    def public_source():
        import native_mlt_public_artifact as public
        value={key:False for key in public.HYBRID_PUBLIC_FIELDS}
        value.update(schema="motionwright.native.hybrid-public-source/1",motionwright_sha="a"*40,semwright_sha="b"*40,
            source_audio_kind="first_party_synthetic_Film180_source",
            width=1920,height=1080,fps_num=30,fps_den=1,frame_count=180,source_segment_frames=[60,60,60],
            source_renderers=["motion_canvas","manim_static","motion_canvas"],original_PNG_frames_bound=540,
            original_PNG_decoder_invocations=9,exact_original_PNG_reuses=531,native_cut_and_content_screen="PASS",
            source_audio_content_screen="PASS",source_vs_output_differences_retained=True,
            original_source_reopened_unchanged=True,live_MLT_module_observed=True)
        for key in ("project_sha256","master_sha256","visual_sha256","source_wave_sha256","pixel_observation_sha256","audio_observation_sha256"):
            value[key]="c"*64
        return value

    def test_hybrid_public_summary_rejects_error_passthrough_claims_and_coverage_changes(self):
        import native_mlt_public_artifact as public
        original=self.public_source()
        public.validate_hybrid_public_source(original,source_sha="a"*40,semwright_sha="b"*40)
        for key,value in [("error",{"message":"PRIVATE"}),("physical_acceptance",True),
                          ("Host_receiver_closure_proved",True),("frame_count",True),
                          ("source_audio_kind","real_voice"),
                          ("original_PNG_frames_bound",539),("exact_original_PNG_reuses",530)]:
            altered=dict(original);altered[key]=value
            with self.assertRaises(public.PublicationError):
                public.validate_hybrid_public_source(altered,source_sha="a"*40,semwright_sha="b"*40)

    def test_hybrid_publisher_never_copies_original_audio_video_or_errors(self):
        import hashlib,json,tempfile
        import native_mlt_public_artifact as public
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)/"private";root.mkdir();output=Path(raw)/"public"
            record=self.public_source()
            for name,key in [("hybrid-pixel-observation.json","pixel_observation_sha256"),
                             ("hybrid-audio-content.json","audio_observation_sha256")]:
                original=b'{"unit_fixture_only":true,"PRIVATE":"must remain private"}\n'
                (root/name).write_bytes(original);record[key]=hashlib.sha256(original).hexdigest()
            (root/"hybrid-public-source.json").write_text(json.dumps(record))
            (root/"native-original.mp4").write_bytes(b"PRIVATE-MEDIA")
            originals={path.name:path.read_bytes() for path in root.iterdir()}
            with self.assertRaisesRegex(public.PublicationError,"^invalid_mlt_mode$"):
                public.publish_mlt(root,output,"hybrid-av","a"*40,"b"*40,"success")
            self.assertFalse(output.exists())
            self.assertEqual({path.name:path.read_bytes() for path in root.iterdir()},originals)


    @staticmethod
    def retained_master_fixture(root):
        """Synthetic bytes for IO/lineage only; no MP4 or native render claim."""
        import hashlib
        from native_mlt_g2_observation import G2Session
        root=Path(root).resolve()
        private=root/"private-originals";private.mkdir()
        output=root/"host-output";output.mkdir()
        body=b"synthetic unit container bytes; not rendered media"
        private_master=private/"original-native-master.mp4"
        host_master=output/"native-master.mp4"
        private_master.write_bytes(body);host_master.write_bytes(body)
        digest=hashlib.sha256(body).hexdigest()
        result={"schema":"motionwright.native.hybrid-av-summary/1","frame_count":180,
            "master_artifact_path":host_master.name,"master_artifact_sha256":digest,
            "master_artifact_bytes":len(body),"project_sha256":"b"*64,"visual_artifact_sha256":"c"*64}
        session=G2Session({},private,b"unit-wave",{"sha256":"d"*64})
        session.hybrid=True;session.hybrid_capture_labels=set()
        session.native_result=result;session.master_sha=digest
        return session,private_master,host_master,body

    @staticmethod
    def complete_probe_capture(capture,source,produced):
        """Exact shape of the existing executor, mocked; never native evidence."""
        import hashlib
        stderr=b"unit decoder stderr"
        capture.update(state="TERMINAL_OBSERVED",stdin_sha256=hashlib.sha256(source).hexdigest(),
            stdin_bytes=len(source),stdout=produced,stderr=stderr,terminal={
                "exit_code":0,"elapsed_seconds":0.01,"stdout_bytes":len(produced),
                "stderr_bytes":len(stderr),"stderr_sha256":hashlib.sha256(stderr).hexdigest(),
                "whole_process_completed":True,"timed_out":False,"output_budget_exceeded":False,
                "pipes_closed":True,"stdin_complete":True,"pipe_errors":[]})

    def test_hybrid_finish_probes_private_bytes_after_host_output_replacement(self):
        import json,tempfile
        from contextlib import nullcontext
        from types import SimpleNamespace
        from unittest import mock
        import native_mlt_sequence_e2e as wrapper
        with tempfile.TemporaryDirectory() as raw:
            session,private,host,original=self.retained_master_fixture(raw)
            session.observer=SimpleNamespace(require_one=lambda path:{
                "published_output_sha256":session.master_sha,"published_output_bytes":len(original)})
            session.inspect_hybrid_pcm=mock.Mock(return_value={"unit_fixture_only":True})
            session.complete_hybrid=mock.Mock(return_value={"unit_fixture_only":True})
            evidence=Path(raw).resolve()/"wrapper-evidence";evidence.mkdir()
            pixels={"original_PNG_frames_bound":540,"original_PNG_decoder_invocations":9,
                "equal_original_PNG_frames_reused":531,"fixed_cut_and_content_screen":"PASS",
                "visual_exact_clock_observed":True,"master_exact_clock_observed":True}
            def mocked_pixels(*args,**kwargs):
                # This is after the real retain_master, before either probe.
                host.write_bytes(b"replacement Host output that must never be decoded")
                return pixels
            calls=[]
            def mocked_decoder(argv,max_stdout,timeout_seconds,*,input_bytes,capture):
                self.assertEqual(capture,{})
                self.assertEqual(input_bytes,original)
                self.assertIn("pipe:0",argv)
                self.assertNotIn(str(host),argv);self.assertNotIn(str(private),argv)
                produced=(b'{"streams":[{}],"packets":[{}]}' if "-show_packets" in argv
                          else b"\0"*(288000*4))
                self.complete_probe_capture(capture,input_bytes,produced)
                calls.append(list(argv))
                return produced,dict(capture["terminal"])
            with mock.patch.object(wrapper,"EVIDENCE",evidence),\
                 mock.patch.object(wrapper,"digest",return_value="a"*64),\
                 mock.patch("native_mlt_caption_pixels.inspect_original_hybrid_pixels",side_effect=mocked_pixels),\
                 mock.patch("native_mlt_loaded_module._verified_binary",return_value=nullcontext()),\
                 mock.patch("manim_native_readback.run_bounded",side_effect=mocked_decoder):
                wrapper.finish_hybrid_original({"output":host.parent},session.native_result,
                    Path("/fixture/ffmpeg"),Path("/fixture/ffprobe"),{},session)
            self.assertEqual(len(calls),2)
            self.assertEqual(private.read_bytes(),original)
            self.assertNotEqual(host.read_bytes(),original)
            for label in ("hybrid-original-packet-probe","hybrid-original-audio-decode"):
                terminal=json.loads((session.evidence_dir/"hybrid-readonly-originals"/(label+".terminal.json")).read_bytes())
                self.assertEqual(terminal["original_private_master"]["path"],str(private))
                self.assertEqual(terminal["master_probe_binding"]["stdin_sha256"],session.master_sha)
                self.assertEqual(terminal["master_probe_binding"]["stdin_bytes"],len(original))
            self.assertEqual(session.packet_observation["decoder_input"]["original"]["path"],str(private))
            self.assertEqual(session.inspect_hybrid_pcm.call_args.kwargs["master_input"]["original"]["path"],str(private))

    def test_hybrid_changed_private_copy_is_rejected_before_either_probe(self):
        import tempfile
        from unittest import mock
        import native_mlt_sequence_e2e as wrapper
        for probe in ("audio","packets"):
            for change in ("same_size_changed_hash","changed_size","Host_path"):
                with self.subTest(probe=probe,change=change),tempfile.TemporaryDirectory() as raw:
                    session,private,host,original=self.retained_master_fixture(raw)
                    selected=private
                    if change=="same_size_changed_hash":private.write_bytes(b"x"*len(original))
                    elif change=="changed_size":private.write_bytes(original+b"x")
                    else:selected=host
                    with mock.patch("manim_native_readback.run_bounded") as run:
                        with self.assertRaises((AssertionError,ValueError)):
                            if probe=="audio":
                                wrapper._inspect_hybrid_original_audio(selected,Path("/fixture/ffmpeg"),{},g2=session)
                            else:wrapper.retain_native_master_packets(selected,Path("/fixture/ffprobe"),{},session)
                    run.assert_not_called()
                    self.assertIsNone(session.pcm_proof);self.assertIsNone(session.packet_observation)

    def test_hybrid_common_reader_error_survives_capture_and_final_custody_failure(self):
        import tempfile
        from unittest import mock
        import native_mlt_g2_observation as observed
        import native_mlt_sequence_e2e as wrapper
        for probe in ("audio","packets"):
            with self.subTest(probe=probe),tempfile.TemporaryDirectory() as raw:
                session,private,_,_=self.retained_master_fixture(raw)
                primary=OSError("same common held-file read error")
                session.retain_hybrid_capture=mock.Mock(side_effect=OSError("capture writer also failed"))
                with mock.patch("ci_private_evidence.regular",side_effect=primary),\
                     mock.patch.object(observed,"new_json",side_effect=OSError("final custody also failed")),\
                     mock.patch("manim_native_readback.run_bounded") as run:
                    with self.assertRaises(OSError) as raised:
                        with session:
                            if probe=="audio":
                                wrapper._inspect_hybrid_original_audio(private,Path("/fixture/ffmpeg"),{},g2=session)
                            else:wrapper.retain_native_master_packets(private,Path("/fixture/ffprobe"),{},session)
                self.assertIs(raised.exception,primary);run.assert_not_called()
                captured=session.retain_hybrid_capture.call_args.args[1]
                self.assertNotIn("stdout",captured);self.assertNotIn("stderr",captured)
                self.assertNotIn("stdin_sha256",captured);self.assertNotIn("master_probe_binding",captured)
                self.assertIn("final_custody",session.secondary_failures)

    def test_hybrid_binding_rejects_incomplete_or_foreign_stdin_and_output(self):
        import copy,tempfile
        import native_mlt_g2_observation as observed
        with tempfile.TemporaryDirectory() as raw:
            session,private,_,_=self.retained_master_fixture(raw)
            source,identity=observed.hybrid_master_probe_input(session,private)
            original={};produced=b"unit output"
            self.complete_probe_capture(original,source,produced)
            binding=observed.hybrid_master_probe_binding(session,identity,original,produced)
            observed.require_hybrid_master_probe_binding(session,binding)
            for where,key,value in (("top","state","RESERVED"),("top","stdin_sha256","0"*64),
                ("top","stdin_bytes",False),("top","stdin_bytes",len(source)+1),
                ("top","stdout",b"changed"),("terminal","stdin_complete",False),
                ("terminal","pipes_closed",False),("terminal","whole_process_completed",False),
                ("terminal","timed_out",True),("terminal","output_budget_exceeded",True),
                ("terminal","pipe_errors",["read:OSError"]),("terminal","exit_code",False),
                ("terminal","exit_code",1),("terminal","stdout_bytes",len(produced)+1),
                ("terminal","stderr_sha256","0"*64)):
                with self.subTest(where=where,key=key):
                    changed=copy.deepcopy(original)
                    (changed if where=="top" else changed["terminal"])[key]=value
                    with self.assertRaises((AssertionError,ValueError)):
                        observed.hybrid_master_probe_binding(session,identity,changed,produced)
            foreign=copy.deepcopy(binding);foreign["original"]["path"]=str(private.parent/"another.mp4")
            with self.assertRaises((AssertionError,ValueError)):
                observed.require_hybrid_master_probe_binding(session,foreign)

    def test_hybrid_probe_input_budget_rejects_before_reader_and_process(self):
        import tempfile
        from unittest import mock
        import native_mlt_g2_observation as observed
        import native_mlt_sequence_e2e as wrapper
        for size in (0,True,observed.HYBRID_MASTER_PROBE_INPUT_LIMIT+1):
            with self.subTest(size=size),tempfile.TemporaryDirectory() as raw:
                session,private,_,_=self.retained_master_fixture(raw)
                session.native_result["master_artifact_bytes"]=size
                with mock.patch("ci_private_evidence.regular") as read,\
                     mock.patch("manim_native_readback.run_bounded") as run:
                    with self.assertRaises((AssertionError,ValueError)):
                        wrapper._inspect_hybrid_original_audio(private,Path("/fixture/ffmpeg"),{},g2=session)
                read.assert_not_called();run.assert_not_called()

    def test_hybrid_pcm_proof_requires_original_decoder_input_before_publication(self):
        import copy,tempfile
        from unittest import mock
        import native_mlt_g2_observation as observed
        with tempfile.TemporaryDirectory() as raw:
            session,private,_,_=self.retained_master_fixture(raw)
            source,identity=observed.hybrid_master_probe_input(session,private)
            pcm=b"\0"*(288000*4);capture={}
            self.complete_probe_capture(capture,source,pcm)
            binding=observed.hybrid_master_probe_binding(session,identity,capture,pcm)
            changed=copy.deepcopy(binding);changed["stdout_sha256"]="0"*64
            with mock.patch("native_pcm_source_fidelity.inspect_source_pcm16_stereo_content",return_value={"unit_fixture_only":True}) as oracle:
                with self.assertRaises((AssertionError,ValueError)):
                    session.inspect_hybrid_pcm(pcm,master_input=changed)
                self.assertFalse((session.evidence_dir/"original-master-decoded.s16le").exists())
                oracle.assert_not_called()
                result=session.inspect_hybrid_pcm(pcm,master_input=binding)
            self.assertEqual(result["decoder_input"],binding)
            self.assertEqual((session.evidence_dir/"original-master-decoded.s16le").read_bytes(),pcm)
            self.assertEqual(oracle.call_count,1)

    def test_hybrid_decoder_error_identity_survives_real_final_custody_failure(self):
        import tempfile
        from contextlib import nullcontext
        from unittest import mock
        import native_mlt_g2_observation as observed
        import native_mlt_sequence_e2e as wrapper
        for probe in ("audio","packets"):
            for error_type in (OSError,KeyboardInterrupt):
                with self.subTest(probe=probe,error_type=error_type.__name__),tempfile.TemporaryDirectory() as raw:
                    session,private,_,_=self.retained_master_fixture(raw)
                    primary=error_type("original supervised process failed")
                    real_new_json=observed.new_json
                    def failing_final(path,value):
                        if path.name=="final-custody.json":raise OSError("final writer failed")
                        return real_new_json(path,value)
                    def failed_decoder(argv,max_stdout,timeout_seconds,*,input_bytes,capture):
                        self.complete_probe_capture(capture,input_bytes,b"partial original stdout")
                        capture["terminal"]["exit_code"]=-9
                        capture["terminal"]["timed_out"]=True
                        raise primary
                    with mock.patch.object(wrapper,"digest",return_value="a"*64),\
                         mock.patch("native_mlt_loaded_module._verified_binary",return_value=nullcontext()),\
                         mock.patch("manim_native_readback.run_bounded",side_effect=failed_decoder) as run,\
                         mock.patch.object(observed,"new_json",side_effect=failing_final):
                        with self.assertRaises(error_type) as raised:
                            with session:
                                if probe=="audio":
                                    wrapper._inspect_hybrid_original_audio(private,Path("/fixture/ffmpeg"),{},g2=session)
                                else:wrapper.retain_native_master_packets(private,Path("/fixture/ffprobe"),{},session)
                    self.assertIs(raised.exception,primary);self.assertEqual(run.call_count,1)
                    label="hybrid-original-audio-decode" if probe=="audio" else "hybrid-original-packet-probe"
                    directory=session.evidence_dir/"hybrid-readonly-originals"
                    self.assertEqual((directory/(label+".stdout.bin")).read_bytes(),b"partial original stdout")
                    self.assertEqual((directory/(label+".stderr.bin")).read_bytes(),b"unit decoder stderr")
                    self.assertIsNone(session.pcm_proof);self.assertIsNone(session.packet_observation)

    def test_hybrid_private_path_rebound_after_capture_cannot_change_delivered_bytes(self):
        import tempfile
        from contextlib import nullcontext
        from unittest import mock
        import native_mlt_sequence_e2e as wrapper
        with tempfile.TemporaryDirectory() as raw:
            session,private,_,original=self.retained_master_fixture(raw)
            def after_read(_binary):
                private.unlink();private.write_bytes(b"later namespace bytes")
                return "a"*64
            def decoder(argv,max_stdout,timeout_seconds,*,input_bytes,capture):
                self.assertEqual(input_bytes,original)
                produced=b'{"streams":[{}],"packets":[{}]}'
                self.complete_probe_capture(capture,input_bytes,produced)
                return produced,dict(capture["terminal"])
            with mock.patch.object(wrapper,"digest",side_effect=after_read),\
                 mock.patch("native_mlt_loaded_module._verified_binary",return_value=nullcontext()),\
                 mock.patch("manim_native_readback.run_bounded",side_effect=decoder) as run:
                wrapper.retain_native_master_packets(private,Path("/fixture/ffprobe"),{},session)
            self.assertEqual(run.call_count,1)
            self.assertNotEqual(private.read_bytes(),original)
            self.assertEqual(session.packet_observation["decoder_input"]["stdin_sha256"],session.master_sha)


    def test_hybrid_exit_preserves_primary_and_rejects_custody_only_failures(self):
        import json,tempfile
        from types import SimpleNamespace
        from unittest import mock
        import native_mlt_g2_observation as observed
        for stage in ("diagnostic","close","final_publication","secondary"):
            for secondary_type in (OSError,KeyboardInterrupt,SystemExit):
                for source in ("entered_IO","entered_interrupt","native_before_entered","native_without_entered","no_primary"):
                    with self.subTest(stage=stage,secondary=secondary_type.__name__,source=source),tempfile.TemporaryDirectory() as raw:
                        session,_,_,_=self.retained_master_fixture(raw)
                        session.completed=True
                        incoming=(OSError("original incoming IO") if source=="entered_IO" else
                                  KeyboardInterrupt("original incoming interrupt") if source in ("entered_interrupt","native_before_entered") else None)
                        native=OSError("original native UNKNOWN fixture") if source.startswith("native_") else None
                        session.native_error=native
                        primary=native if native is not None else incoming
                        secondary=secondary_type("custody secondary")
                        close_trigger=OSError("first custody failure before secondary reporting")
                        first_custody=close_trigger if stage=="secondary" else secondary
                        session.observer=SimpleNamespace(close=mock.Mock(
                            side_effect=first_custody if stage in ("close","secondary") else None))
                        session.retain_observer_diagnostic=mock.Mock(side_effect=secondary if stage=="diagnostic" else None)
                        if stage=="secondary":session.secondary=mock.Mock(side_effect=secondary)
                        real_write=observed.new_json
                        final_attempts=[]
                        def publication(path,value):
                            if path.name=="final-custody.json":
                                final_attempts.append(value)
                                if stage=="final_publication":raise secondary
                            return real_write(path,value)
                        expected_type=(type(primary) if primary is not None else
                                       observed.G2Unverified if isinstance(first_custody,Exception) else type(first_custody))
                        with mock.patch.object(observed,"new_json",side_effect=publication),\
                             mock.patch.object(observed,"warn_secondary"):
                            with self.assertRaises(expected_type) as caught:
                                with session:
                                    if incoming is not None:raise incoming
                        if primary is not None:self.assertIs(caught.exception,primary)
                        elif isinstance(first_custody,Exception):self.assertIs(caught.exception.__cause__,first_custody)
                        else:self.assertIs(caught.exception,first_custody)
                        session.retain_observer_diagnostic.assert_called_once_with()
                        session.observer.close.assert_called_once_with()
                        self.assertEqual(len(final_attempts),1)
                        if stage!="final_publication" or primary is not None:
                            self.assertFalse(final_attempts[0]["source_content_and_live_module_verified"])
                        if stage!="final_publication":
                            retained=json.loads((session.evidence_dir/"final-custody.json").read_bytes())
                            self.assertFalse(retained["source_content_and_live_module_verified"])
                        else:
                            self.assertFalse((session.evidence_dir/"final-custody.json").exists())

        # The existing diagnostic collector catches ordinary IO internally.
        # Its retained failure must also prevent a HYBRID success without a
        # preceding exception; use that actual method, not the boundary mock.
        with tempfile.TemporaryDirectory() as raw:
            session,_,_,_=self.retained_master_fixture(raw)
            session.completed=True
            session.observer=SimpleNamespace(
                diagnostic=mock.Mock(side_effect=OSError("original diagnostic IO")),close=mock.Mock())
            with mock.patch.object(observed,"warn_secondary"):
                with self.assertRaisesRegex(observed.G2Unverified,"g2_hybrid_exit_custody_incomplete"):
                    with session:pass
            session.observer.diagnostic.assert_called_once_with()
            session.observer.close.assert_called_once_with()
            retained=json.loads((session.evidence_dir/"final-custody.json").read_bytes())
            self.assertIn("observer_diagnostic_custody",retained["secondary_failures"])
            self.assertFalse(retained["source_content_and_live_module_verified"])


if __name__ == "__main__":
    unittest.main()
