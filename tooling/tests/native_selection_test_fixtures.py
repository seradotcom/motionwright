"""Pure synthetic /2 graphs; never evidence of a real native media run.

C/ELF pins are deliberately patched inside a scoped test context. All payloads
and filesystem handles are in memory; fake Git returns synthetic committed
bytes. Native MPEG/PNG fixture bytes are NOT media acceptance fixtures.
"""
from contextlib import ExitStack,contextmanager
import copy
import io
import json
import math
from pathlib import Path
import re
import struct
import types
import unittest.mock as mock
import wave

import native_evidence_projection as core
import native_mlt_evidence_selection as mlt
import native_film180_evidence_selection as film
from native_pcm_source_fidelity import inspect_source_pcm16_stereo_content

CURRENT={"source_sha":"a"*40,"semwright_sha":mlt.SEM,"run_id":"34567","run_attempt":"1"}


def sample(rule):
    if isinstance(rule,core.Field): return sample(rule.rule)
    if isinstance(rule,core.Literal): return rule.value
    if isinstance(rule,core.Nullable): return None
    if isinstance(rule,core.Object): return {k:sample(v.rule) for k,v in rule.fields.items() if v.required}
    if isinstance(rule,core.Array): return [sample(rule.item) for _ in range(rule.minimum)]
    if isinstance(rule,core.Number):
        value=rule.minimum if rule.minimum is not None else 0
        return int(value) if rule.integer else float(value)
    if rule is bool: return False
    if isinstance(rule,core.String):
        if rule.values: return rule.values[0]
        pattern=rule.pattern
        candidates=("a"*64,"b"*40,"01234567-89ab-cdef-0123-456789abcdef",
            "project:01234567-89ab-cdef-0123-456789abcdef","0","1","0.000000","label","sample.bin",
            "frames/000000.png","render-"+"1"*32,"1/48000","1/30","mnt:[1]","pid:[2]","user:[3]",
            "r--p","K_","mw-"+"1"*32+"-r1-"+"2"*16+"-master.mp4")
        for value in candidates:
            if len(value)<=rule.maximum and (pattern is None or re.fullmatch(pattern,value)): return value
        raise AssertionError("No synthetic literal for "+str(pattern))
    raise AssertionError("Unknown policy rule")


def stereo(frames):
    raw=b"".join(struct.pack("<hh",int(12000*math.sin(2*math.pi*400*n/48000)),
                             int(10000*math.sin(2*math.pi*600*n/48000))) for n in range(frames))
    stream=io.BytesIO()
    with wave.open(stream,"wb") as writer:
        writer.setnchannels(2);writer.setsampwidth(2);writer.setframerate(48000);writer.writeframes(raw)
    return stream.getvalue(),raw


WAV33,PCM33=stereo(52800)
WAV180,PCM180=stereo(288000)
CONTENT33=inspect_source_pcm16_stereo_content(WAV33,PCM33,original_wav_sha256=core.sha(WAV33),decoded_pcm_sha256=core.sha(PCM33))
CONTENT180=inspect_source_pcm16_stereo_content(WAV180,PCM180,original_wav_sha256=core.sha(WAV180),decoded_pcm_sha256=core.sha(PCM180))


from functools import lru_cache

@lru_cache(maxsize=8, typed=True)
def _synthetic_clock_report_bytes(source_raw, packet_raw, wav, master, pcm, phase, scenario):
    """Cache immutable synthetic construction only, keyed by every original byte.

    Production capture and selected-original verification never use this cache.
    Exceptions are not cached; phase/scenario changes cannot borrow a result.
    """
    import native_aac_clock_custody as clocks
    import native_aac_clock_inspection as inspector
    core.require(all(type(value) is bytes for value in (source_raw, packet_raw, wav, master, pcm))
                 and type(phase) is str and type(scenario) is str, "synthetic-clock-cache-inputs")
    anchor = clocks.derive_anchor(core.strict(source_raw), clocks.desc(source_raw),
                                  scenario=scenario, phase=phase)
    packets = core.strict(packet_raw)
    frames = [dict(media_type="audio", stream_index=1, pts=p["pts"],
        best_effort_timestamp=p["pts"], nb_samples=p["duration"], channels=2,
        pkt_pos=p["pos"], pkt_size=p["size"], sample_fmt="fltp", duration=p["duration"])
        for p in packets["packets"]]
    frame_raw = core.canonical(dict(frames=frames, streams=packets["streams"]))
    inputs = dict(original_wav=wav, master=master, packets=packet_raw, decoded_pcm=pcm,
                  default_frames=frame_raw, manual_frames=frame_raw)
    binding = {name:clocks.desc(raw) for name,raw in inputs.items()}
    binding.update(schema=inspector.BINDING_SCHEMA, presentation_start=anchor["presentation_start"])
    return clocks.packed(inspector.inspect_originals(binding=binding, **inputs))


def source_contract_receipt(scope, current, runner_sha, *, tree="b" * 40):
    """Synthetic runner receipt; test counts are fixtures, never execution proof."""
    names = mlt.FILM_CONTRACT_TESTS if scope == "film" else mlt.SOURCE_CONTRACT_TESTS
    result = sample(mlt.source_contract_policy(names))
    result.update(executed_source_sha=current["source_sha"], tree_sha=tree,
                  runner_sha256=runner_sha, status="PASS", exit_code=0, require_complete=True)
    result["github"].update(GITHUB_SHA=current["source_sha"], GITHUB_RUN_ID=current["run_id"],
                            GITHUB_RUN_ATTEMPT=current["run_attempt"])
    result["test_definition_sha256"] = {"tooling/tests/" + name + ".py": "d" * 64 for name in names}
    result["steps"] = []
    for name in names:
        result["steps"].append(dict(entry_point="dotted:tooling.tests." + name,
            command=["python", "-B", "-E", "-s", "-m", "unittest", "tooling.tests." + name, "-v"],
            status="PASS", exit_code=0,
            counts=dict(terminal_status="OK", tests_run=1, passed=1, failures=0, errors=0,
                        skipped=0, expected_failures=0, unexpected_successes=0)))
    return result


class World:
    def __init__(self,scenario):
        self.scenario=scenario;self.files={};self.committed={};self.reads=[];self.stats=[];self.closed=0
        self.on_read=None;self.on_stat=None;self.inodes={};self.aliases={};self.next_inode=10
        self.roots={k:"/synthetic-private-"+k for k in ("source","control","observation","owner","contracts")}
        if scenario=="film180-g2":self.roots["film-contracts"]="/synthetic-private-film-contracts"
        self.paths=mlt.FILM_SOURCE_PATHS if scenario=="film180-g2" else mlt.SOURCE_PATHS
        for path in self.paths:
            raw=("source-owned fixture: "+path+"\n").encode();self.files[("source",path)]=raw;self.committed[path]=raw
        self.bindings=[{"path":p,"sha256":core.sha(self.committed[p])} for p in self.paths]
        self.original_c=b"/"*96690;self.repaired_c=b"*"*99266
        self.compiled=b"\x7fELF"+b"S"*(719856-4);self.original_module=b"\x7fELF synthetic original distro module"
        self.pins=dict(ORIGINAL_C=core.sha(self.original_c),REPAIRED_C=core.sha(self.repaired_c),
            REPAIRED_ELF=core.sha(self.compiled),REFERENCE_WAV=core.sha(WAV33))
        self.common()
        if scenario=="film180-g2": self.film()
        else:self.mlt()

    def put(self,root,path,value):
        self.files[(root,path)]=core.canonical(value) if type(value) is dict else value
        return core.sha(self.files[(root,path)])

    def document(self,root,path): return core.strict(self.files[(root,path)])

    def edit(self,root,path,fn):
        value=self.document(root,path);fn(value);self.put(root,path,value)

    def common(self):
        runner_sha = core.sha(self.committed["tooling/ci_test_runner.py"])
        self.put("contracts", "contracts.json", source_contract_receipt("mlt", CURRENT, runner_sha))
        if self.scenario == "film180-g2":
            self.put("film-contracts", "contracts.json", source_contract_receipt("film", CURRENT, runner_sha))
        self.put("control","intake/original_c_source",self.original_c)
        self.put("control","intake/repaired_c_source",self.repaired_c)
        self.put("control","intake/compiled_module",self.compiled)
        self.put("control","handoff/original_system_mlt_avformat.so",self.original_module)
        compiler=sample(mlt.COMPILED)
        compiler.update(original_source_sha256=self.pins["ORIGINAL_C"],repaired_source_sha256=self.pins["REPAIRED_C"],
            compiled_source_sha256=self.pins["REPAIRED_C"],compiled_module_sha256=self.pins["REPAIRED_ELF"],compiled_module_bytes=len(self.compiled))
        self.pins["COMPILER"]=self.put("control","intake/compiler_receipt_path",compiler)
        self.closure=dict(original_MLT_source_sha256=self.pins["ORIGINAL_C"],repaired_MLT_source_sha256=self.pins["REPAIRED_C"],
            compiled_module_sha256=self.pins["REPAIRED_ELF"],compiled_module_bytes=len(self.compiled),compiler_receipt_sha256=self.pins["COMPILER"])
        installation=sample(mlt.INSTALLATION);installation.update(self.closure)
        installation.update(original_module_sha256=core.sha(self.original_module),original_module_bytes=len(self.original_module),
            actual_installed_module_sha256=self.pins["REPAIRED_ELF"],original_backup_sha256=core.sha(self.original_module))
        self.installation_sha=self.put("control","handoff/installation.json",installation)
        official=sample(mlt.OFFICIAL)
        official["runtime_bindings"]["original_module"]["sha256"]=core.sha(self.original_module)
        self.melt_sha=official["runtime_bindings"]["melt"]["sha256"]
        self.official_sha=self.put("control","intake/official-runtime-receipt.json",official)
        source=dict(context=CURRENT,tree="b"*40,files={r["path"]:r["sha256"] for r in self.bindings})
        for path in ("source-before.json","source-before-producer.json","source-after.json"):
            self.put("control","records/"+path,source)
        self.put("control","records/producer-exit.json",dict(context=CURRENT,exit_code=0,native_operation_retried=False))
        self.put("control","records/producer-started.json",dict(context=CURRENT,automatic_retry=False,producer_uid=1001,
            command="tooling/native_film180_original_g2.py --g2-inputs" if self.scenario=="film180-g2" else "tooling/native_mlt_sequence_e2e.py --g2-inputs"))
        owner=sample(mlt.OWNER);owner.update(motionwright_sha=CURRENT["source_sha"],run_id=CURRENT["run_id"],device=1,inode=1,uid=1001,gid=1001)
        self.put("observation","owner-workspace.json",owner)
        owner_terminal=sample(mlt.OWNER_TERMINAL);owner_terminal["workspace"]=owner
        self.put("observation","owner-workspace-terminal.json",owner_terminal)
        self.database="original-film180.sqlite3" if self.scenario=="film180-g2" else "motionwright.sqlite3"
        self.put("owner","motionwright-data/"+self.database,b"SQLite format 3\0"+b"synthetic only"*32)

    def cas(self,wav):
        digest=core.sha(wav);self.put("owner","motionwright-data/blobs/sha256/"+digest[:2]+"/"+digest,wav)

    def live(self,phase,master,*,wrapped=False,master_path=None):
        proof=sample(mlt.LIVE);proof["pid"]=112 if phase=="edited" else 111
        proof["process_start_ticks"]=2002 if phase=="edited" else 2001
        proof["live_module"]["actual_live_melt_pid"]=proof["pid"]
        proof["live_module"]["original_melt_sha256"]=self.melt_sha
        proof["live_module"]["original_repaired_avformat_module_sha256"]=self.pins["REPAIRED_ELF"]
        proof["owner_contract"]["owner_identity"]=[1,1,16832,1001,1001]
        proof["published_output_sha256"]=core.sha(master);proof["published_output_bytes"]=len(master)
        proof["source_descriptor_readback"].update(sha256=core.sha(master),bytes=len(master))
        snapshot=sample(mlt.CONSUMER_SNAPSHOT)
        snapshot.update(proof["source_descriptor_readback"])
        snapshot["process_identity"]=[proof["pid"],proof["process_start_ticks"]]
        directory=phase+"-live-observer/" if wrapped else ""
        base=directory+"original-melt-consumer-"+str(proof["pid"])+"-"+str(proof["process_start_ticks"])
        self.put("observation",base+".mp4",master);self.put("observation",base+".json",snapshot)
        if wrapped:
            value=sample(film.WRAPPED_LIVE)
            value.update(original_live_pid_observer_receipt=proof,actual_mapped_original_MLT_module_sha256=self.pins["REPAIRED_ELF"],
                original_MLT_command_basename=master_path)
            self.put("observation",phase+"-original-live-MLT-loaded-module.json",value)
        else:self.put("observation","original-live-module.json",proof)

    def artifact(self,path,blob):
        self.put("owner","output/"+path,blob)
        return dict(root="output",path=path,sha256=core.sha(blob),bytes=len(blob))

    def packets(self):
        # Artificial arithmetic geometry, not encoded AAC or a codec frame size.
        total=288000 if self.scenario=="film180-g2" else 52800
        count=total//5
        stream=dict(index=1,codec_name="aac",codec_type="audio",sample_rate="48000",
            channels=2,time_base="1/48000",start_pts=0,start_time="0.000000",
            duration_ts=total,duration=f"{total/48000:.6f}",nb_frames="5",nb_read_packets="5",
            initial_padding=0)
        packets=[]
        for index in range(5):
            point=index*count
            packets.append(dict(codec_type="audio",stream_index=1,pts=point,dts=point,
                duration=count,pts_time=f"{point/48000:.6f}",dts_time=f"{point/48000:.6f}",
                duration_time=f"{count/48000:.6f}",size="1",pos=str(index),flags="K_",
                side_data_list=[dict(side_data_type="Skip Samples",skip_samples=0,
                    discard_padding=0,skip_reason=0,discard_reason=0)]))
        return dict(packets=packets,streams=[stream])

    def clock_carrier(self,phase,source_path,master,wav,pcm,packet_path):
        # Pure fake World only. No tool is executed and these synthetic terminal
        # fields are never evidence for a native invocation or runtime admission.
        import native_aac_clock_custody as clocks
        source_raw=self.files[("observation",source_path)]
        anchor=clocks.derive_anchor(core.strict(source_raw),clocks.desc(source_raw),
            scenario=self.scenario,phase=phase)
        packet_raw=self.files[("observation",packet_path)]
        packets=core.strict(packet_raw)
        frames=[dict(media_type="audio",stream_index=1,pts=p["pts"],
            best_effort_timestamp=p["pts"],nb_samples=p["duration"],channels=2,
            pkt_pos=p["pos"],pkt_size=p["size"],sample_fmt="fltp",duration=p["duration"])
            for p in packets["packets"]]
        frame_raw=core.canonical(dict(frames=frames,streams=packets["streams"]))
        report_raw=_synthetic_clock_report_bytes(source_raw,packet_raw,wav,master,pcm,phase,self.scenario)
        report=core.strict(report_raw)
        executable=b"SYNTHETIC_NON_EXECUTABLE_TOOL_IDENTITY"
        tool_path="/synthetic-private-tool/ffprobe"
        master_path="/synthetic-private-owner/output/"+phase+".mp4"
        captures=[]
        for mode in clocks.MODES:
            terminal=dict(argv=clocks.frame_argv(tool_path,master_path,mode),timeout_seconds=60,
                exit_code=0,deadline_exceeded=False,error_class=None,automatic_retry=False,
                stdout=clocks.desc(frame_raw),stderr=clocks.desc(b""),stdout_original_included=True,
                stderr_original_included=False,terminal_captured_before_json_parse=True)
            captures.append(dict(mode=mode,terminal=terminal,runner_failed=False,
                original_stdout=clocks.encoded(frame_raw,clocks.MAX_PROBE)))
        value=dict(schema=clocks.SCHEMA,phase=phase,scenario=self.scenario,state="CAPTURED",
            anchor=anchor,original_wav=clocks.desc(wav),master=clocks.desc(master),
            decoded_pcm=clocks.desc(pcm),ffprobe=clocks.desc(executable),ffprobe_path=tool_path,
            master_path=master_path,master_unchanged_after_observation=True,
            ffprobe_unchanged_after_observation=True,
            packets=clocks.encoded(packet_raw,clocks.MAX_PROBE),captures=captures,
            report=clocks.encoded(report_raw,clocks.MAX_REPORT),report_status=report["status"],
            original_capture_authentication="EXTERNAL_CUSTODY_REQUIRED",
            original_pcm_decoder_equivalence="NOT_ATTESTED_BY_THIS_CAPTURE",
            native_operation_started=False,native_operation_retried=False,packet_probe_reinvoked=False,
            pcm_decoder_reinvoked=False,media_reencoded_or_trimmed=False,
            physical_AV_master_acceptance=False,human_acceptance=False,release_authorized=False)
        core.project(value,clocks.carrier_policy(),exact=True)
        self.put("observation",phase+"-aac-clock-capture.json",value)

    def mlt(self):
        timeline=sample(mlt.TIMELINE)
        for i,segment in enumerate(timeline["source"]["verified_video_segments"]):
            blob=("synthetic segment "+str(i)).encode();path="source-segment-"+str(i)+".mkv"
            self.put("owner","output/"+path,blob)
            segment.update(segment_id="segment-"+str(i),output_start_frame=32 if i else 0,
                frame_count=1 if i else 32,artifact_path=path,artifact_sha256=core.sha(blob),artifact_bytes=len(blob))
        video=b"synthetic ffv1 timeline, not real native media"
        video_art=self.artifact("timeline-video.mkv",video)
        timeline.update(artifact_path=video_art["path"],artifact_sha256=video_art["sha256"],artifact_bytes=len(video))
        self.put("observation","producer/native-multisegment-timeline-evidence.json",timeline)
        self.put("observation","producer/native-two-segment-video-only.mkv",video)
        master_bytes=b"synthetic mp4, not physical media evidence"
        master=sample(mlt.MLT_MASTER);master["video"]=copy.deepcopy(timeline)
        master["measured_voice"]["audio_sha256"]=core.sha(WAV33);master["measured_voice"]["audio_size_bytes"]=len(WAV33)
        master["staged_audio"]["sha256"]=core.sha(WAV33)
        mux=master["master"];mux["artifact"]=self.artifact("actual-master.mp4",master_bytes)
        mux.update(video_sha256=core.sha(video),audio_sha256=core.sha(WAV33),frame_count=33,
            audio_sample_frames=52800,decoded_audio_sample_frames=52800)
        mux["decoded_audio"]=self.artifact("decoded-master.wav",WAV33)
        master["decoded_audio"]=copy.deepcopy(mux["decoded_audio"])
        self.put("observation","producer/native-multisegment-av-master-evidence.json",master)
        for path in ("producer/native-multisegment-master.mp4","original-native-master.mp4"):self.put("observation",path,master_bytes)
        for path in ("original-source.wav","producer/native-master-source.wav"):self.put("observation",path,WAV33)
        self.put("observation","original-master-decoded.s16le",PCM33);self.cas(WAV33)
        content=sample(mlt.MLT_CONTENT)
        content.update(master_sha256=core.sha(master_bytes),decoded_pcm_sha256=core.sha(PCM33),original_wav_sha256=core.sha(WAV33))
        envelope=sample(mlt.FIXED_ENVELOPE);envelope.update(decoded_frames=52800,unexplained_aac_extra_frames=0)
        envelope["existing_midstream_stereo_observation"]["decoded_master_pcm_frames"]=52800
        content["oracles"]={"full_source_content":{"state":"PASS","witness":copy.deepcopy(CONTENT33)},
            "fixed_fixture_envelope":{"state":"PASS","witness":envelope}}
        self.put("observation","source-content.json",content)
        self.put("observation","producer/decoded-stereo-content-proof.json",envelope["existing_midstream_stereo_observation"])
        packet_sha=self.put("observation","original-master-packets.json",self.packets())
        self.clock_carrier("original","producer/native-multisegment-timeline-evidence.json",
            master_bytes,WAV33,PCM33,"original-master-packets.json")
        self.live("original",master_bytes)
        inputs=sample(mlt.MLT_INPUT);inputs.update(current_motionwright_sha=CURRENT["source_sha"],run_id=CURRENT["run_id"],
            source_compiler=self.closure,official_runtime_receipt_sha256=self.official_sha,installation_receipt_sha256=self.installation_sha,
            known_original_module_sha256=core.sha(self.original_module),known_melt_sha256=self.melt_sha)
        inputs["installed_melt_alias"]["sha256"]=self.melt_sha
        self.put("observation","input-custody.json",inputs)
        ack=sample(mlt.ACK);ack.update(project_id=timeline["project_resource"][8:],artifact_path=timeline["artifact_path"],
            artifact_sha256=timeline["artifact_sha256"],artifact_bytes=timeline["artifact_bytes"],
            master_artifact_path=mux["artifact"]["path"],master_artifact_sha256=core.sha(master_bytes),master_artifact_bytes=len(master_bytes),
            master_audio_sha256=core.sha(WAV33),master_decoded_audio_sha256=core.sha(WAV33))
        terminal=sample(mlt.MLT_TERMINAL);terminal.update(native_terminal="SUCCESS_REPORTED",native_result=ack)
        self.put("observation","native-terminal.json",terminal)
        final=sample(mlt.MLT_FINAL);final.update(native_terminal="SUCCESS_REPORTED",native_result=ack,source_content_and_live_module_verified=True)
        self.put("observation","final-custody.json",final)
        result=sample(mlt.RESULT);result.update(motionwright_sha=CURRENT["source_sha"],rendered_source_frames=[32,1],
            video_bytes=len(video),video_sha256=core.sha(video),final_master_sha256=core.sha(master_bytes),final_master_bytes=len(master_bytes),
            decoded_audio_sha256=core.sha(WAV33),g2_original_wav_sha256=core.sha(WAV33),g2_repaired_module_sha256=self.pins["REPAIRED_ELF"],
            g2_whole_decoded_pcm_sha256=core.sha(PCM33),g2_packet_observation_sha256=packet_sha)
        self.put("observation","producer/result.json",result)

    def film(self):
        self.cas(WAV180);self.put("observation","firstparty-source.wav",WAV180)
        self.put("owner","output/firstparty-film180-source.wav",WAV180)
        source=sample(film.SOURCE_CUSTODY);source["sha256"]=core.sha(WAV180)
        self.put("observation","firstparty-source-custody.json",source)
        runtime=sample(film.RUNTIME);runtime.update(motionwright_sha=CURRENT["source_sha"],source_compiler=self.closure,
            known_melt_sha256=self.melt_sha,official_runtime_receipt_sha256=self.official_sha,installation_receipt_sha256=self.installation_sha)
        runtime["installed_melt_alias"]["sha256"]=self.melt_sha
        self.put("observation","runtime-input-custody.json",runtime)
        plan=sample(film.SOURCE_PLAN);plan.update(motionwright_SHA=CURRENT["source_sha"],
            MLT_original_installer_receipt_sha256=self.installation_sha,repaired_MLT_ELF_sha256=self.pins["REPAIRED_ELF"])
        self.put("observation","source-plan.json",plan)
        stages={}
        for phase in film.PHASES:
            project=copy.deepcopy(film.PROJECT_TEMPLATE)
            project["assets"][0]["content_sha256"]=core.sha(WAV180)
            project["audio"]["voice_tracks"][0]["source_sha256"]=core.sha(WAV180)
            if phase=="edited":
                project["revision"]+=1;project["branches"][0]["head_revision"]+=1
                project["scenes"][1]["nodes"][0]["text"]="Edited scene retained after reopening"
            project_sha=self.put("observation",phase+"-project.json",project)
            identity=dict(project_resource="project:"+project["id"],generation=project["generation"],revision=project["revision"],deliverable_id=project["deliverables"][0]["id"])
            directory="render-"+("e" if phase=="edited" else "d")*32
            manifest=sample(film.MANIFEST)
            for i,row in enumerate(manifest["frames"]):
                # A bounded synthetic PNG-shaped header. This intentionally
                # does not prove native rendering or PNG decoding.
                png=b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR"+struct.pack(">IIBBBBB",1920,1080,8,6,0,0,0)+b"0000"+phase.encode()+str(i).encode()
                self.put("owner",f"output/{directory}/frames/{i:06d}.png",png)
                row.update(bytes=len(png),file=f"frames/{i:06d}.png",index=i,sha256=core.sha(png))
            manifest_path="output/"+directory+"/artifact-manifest.json"
            manifest_sha=self.put("owner",manifest_path,manifest)
            motion=sample(film.FILM);motion.update(identity)
            segment=motion["segments"][0];segment["scene_ids"]=[s["id"] for s in project["scenes"]]
            segment["artifact"].update(directory=directory,first_png=directory+"/frames/000000.png",last_png=directory+"/frames/000179.png",
                manifest=directory+"/artifact-manifest.json",manifest_bytes=len(self.files[("owner",manifest_path)]),manifest_sha256=manifest_sha)
            self.put("observation",phase+"-native-Film180-evidence.json",motion)
            master_blob=("SYNTHETIC_"+phase+"_MP4_DOES_NOT_PROVE_NATIVE_RENDER").encode()
            master=sample(film.MASTER);master.update(identity);master["source_audio"]["sha256"]=core.sha(WAV180)
            master["mezzanine"]["artifact"]=self.artifact(phase+"-mezzanine.mkv",("synthetic FFV1 "+phase).encode())
            master["mezzanine"].update(frame_count=180,source_manifest_sha256=manifest_sha)
            mux=master["master"];path="mw-"+project["id"].replace("-","")+"-r"+str(project["revision"])+"-"+("e" if phase=="edited" else "d")*16+"-master.mp4"
            mux["artifact"]=self.artifact(path,master_blob)
            mux["decoded_audio"]=self.artifact(phase+"-decoded.wav",WAV180)
            mux.update(video_sha256=master["mezzanine"]["artifact"]["sha256"],audio_sha256=core.sha(WAV180),frame_count=180,
                audio_sample_frames=288000,decoded_audio_sample_frames=288000)
            master["decoded_audio"]=copy.deepcopy(mux["decoded_audio"])
            self.put("observation",phase+"-H264-AAC-native-master.json",master)
            reopened=sample(film.REOPEN);reopened.update(project_id=project["id"],generation=project["generation"],revision=project["revision"],saved_film_sha256=core.sha((phase+"-canonical-film").encode()))
            self.put("observation",phase+"-reopened-before-render.json",reopened)
            self.live(phase,master_blob,wrapped=True,master_path=path)
            self.put("observation",phase+"-original-master-decoded.s16le",PCM180)
            pcm_capture=sample(film.PCM_CAPTURE);pcm_capture.update(phase=phase,master_sha256=core.sha(master_blob),decoded_pcm_sha256=core.sha(PCM180),decoded_pcm_bytes=len(PCM180),decoded_stereo_frames=288000)
            self.put("observation",phase+"-original-pcm-capture.json",pcm_capture)
            packets=self.packets();packet_path=phase+"-original-master-packets.json";packet_sha=self.put("observation",packet_path,packets)
            packet_capture=sample(film.policies()["film_packet_capture"]);packet_capture.update(phase=phase,master_sha256=core.sha(master_blob),original_packet_document_sha256=packet_sha,
                original_packet_document_bytes=len(self.files[("observation",packet_path)]),packet_count=len(packets["packets"]))
            self.put("observation",phase+"-original-packet-capture.json",packet_capture)
            self.clock_carrier(phase,phase+"-project.json",master_blob,WAV180,PCM180,packet_path)
            all_rgb=sample(film.ALL_RGB);all_rgb.update(source_master_sha256=core.sha(master_blob),decoded_pixel_size=[1920,1080],
                all_original_frame_sha256=[core.sha((phase+"decoded"+str(i)).encode()) for i in range(180)],unique_decoded_RGB_frames=180)
            all_rgb_sha=self.put("observation",phase+"-all180-decoded-original-RGB24.json",all_rgb)
            decoded=sample(film.DECODED);decoded.update(source_bound_PCM=copy.deepcopy(CONTENT180),decoded_PCM_sha256=core.sha(PCM180),master_sha256=core.sha(master_blob),source_WAV_sha256=core.sha(WAV180))
            decoded["identity"].update(project_id=project["id"],generation=project["generation"],revision=project["revision"],deliverable_id=project["deliverables"][0]["id"],
                scene_ids=[s["id"] for s in project["scenes"]],node_ids=[n["id"] for s in project["scenes"] for n in s["nodes"]])
            decoded["physical_video"].update(pixel_size=[1920,1080],last_pts=179/30)
            decoded["native_pixel_boundaries"]["selected_native_frame_rgbs_sha256"]={str(i):all_rgb["all_original_frame_sha256"][i] for i in film.SELECTED_FRAMES}
            decoded_sha=self.put("observation",phase+"-independent-AV-content.json",decoded)
            stats=b"".join((f"n:{i+1} mse_avg:1.0 psnr_avg:48.0\n").encode() for i in range(180))
            stats_sha=self.put("observation",phase+"-film180-allframe-RGB-PSNR.stats",stats)
            psnr=sample(film.PSNR);psnr.update(native_source_PNG_color_type_counts={"6":180},native_RGBA_or_greyscale_alpha_PNG_frames=180,
                original_native_source_Film_manifest_sha256=manifest_sha,H264_master_sha256=core.sha(master_blob),full_RGB_PSN_metrics_sha256=stats_sha)
            psnr_sha=self.put("observation",phase+"-native-PNG-versus-H264-RGB-observation.json",psnr)
            stage=sample(film.STAGE);stage.update(stage=phase,source_project_SHA=project_sha,original_audio_SHA=core.sha(WAV180),master_sha=core.sha(master_blob),
                independent_content_sha=decoded_sha,decoded_all180_RGB_manifest_sha256=all_rgb_sha,
                all180_native_PNG_vs_H264_RGB_observation_sha256=psnr_sha,actual_loaded_repaired_MLT_module=self.pins["REPAIRED_ELF"])
            stage["Film_manifest"].update(original_manifest_sha256=manifest_sha,first_original_png_sha256=manifest["frames"][0]["sha256"],last_original_png_sha256=manifest["frames"][-1]["sha256"])
            stages[phase]=stage
        status=sample(film.STATUS);status.update(product_source_sha=CURRENT["source_sha"],original=stages["original"],edited=stages["edited"],MLT_module_installed_SHA=self.pins["REPAIRED_ELF"])
        status["original_edited_pixel_change"]["changed_decoded_pixels"]=250
        self.put("observation","original-stage-status.json",status)
        final=sample(film.FINAL);final.update(native_terminal="SUCCESS_REPORTED",native_command_terminals={k:"SUCCESS_REPORTED" for k in ("original.render","original.master","edited.render","edited.master")},
            source_content_and_live_module_verified=True,completed_stages=stages,own_six_second_WAV_sha256=core.sha(WAV180))
        self.put("observation","final-custody.json",final)

    def mark(self,key):
        if key not in self.files: raise FileNotFoundError()
        alias=self.aliases.get(key,key)
        if alias not in self.inodes:self.inodes[alias]=self.next_inode;self.next_inode+=1
        return (1,self.inodes[alias],len(self.files[key]),1,1,1,1001,1001,33152)

    @contextmanager
    def patched(self):
        world=self
        class Handles:
            def __init__(self,roots): self.roots=roots
            def close(self): world.closed+=1
            def stable_roots(self): return True
            def stat_member(self,root,parts):
                key=(root,"/".join(parts));world.stats.append(key)
                if world.on_stat: world.on_stat(key)
                return world.mark(key)
            def read(self,root,parts,maximum):
                require_not_secret=not core.excluded(parts)
                if not require_not_secret: raise AssertionError("Forbidden source reached fake open")
                key=(root,"/".join(parts));world.reads.append(key)
                if world.on_read: world.on_read(key)
                mark=world.mark(key);raw=world.files[key]
                core.require(len(raw)<=maximum,"member-byte-budget")
                return raw,mark
        def git(argv,**kwargs):
            assert argv[0]=="git" and argv[-2]=="show"
            ref,path=argv[-1].split(":",1);assert ref==CURRENT["source_sha"]
            return types.SimpleNamespace(returncode=0,stdout=world.committed[path],stderr=b"")
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(core,"RootHandles",Handles))
            stack.enter_context(mock.patch.object(core.subprocess,"run",side_effect=git))
            for key,value in self.pins.items():stack.enter_context(mock.patch.object(mlt,key,value))
            yield

    def collector(self):
        if self.scenario=="film180-g2":return film.FilmSelection(roots=self.roots,current=CURRENT,source_bindings=self.bindings)
        return mlt.Selection(roots=self.roots,current=CURRENT,scenario=self.scenario,source_bindings=self.bindings,
            role_contract=mlt.mlt_contract(),json_policies=mlt.policies(self.scenario))

    def capture(self):return self.collector().capture()
    def verify(self,raw):return mlt.verify_decrypted_selection(raw,current=CURRENT,scenario=self.scenario)


SOURCE_TEXTS = None

def source_text(path):
    if SOURCE_TEXTS is not None: return SOURCE_TEXTS[path]
    return (Path(__file__).resolve().parents[2] / path).read_text(encoding="utf-8")


def rewrite_selected(raw,change):
    import tarfile
    entries={}
    with tarfile.open(fileobj=io.BytesIO(raw),mode="r:") as archive:
        for info in archive.getmembers(): entries[info.name]=archive.extractfile(info).read()
    manifest=core.strict(entries["selected-evidence-manifest.json"])
    change(manifest,entries)
    entries["selected-evidence-manifest.json"]=core.canonical(manifest)
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode="w",format=tarfile.USTAR_FORMAT) as archive:
        for name,payload in sorted(entries.items()):
            info=tarfile.TarInfo(name);info.size=len(payload);info.mode=0o600
            archive.addfile(info,io.BytesIO(payload))
    return output.getvalue()


def selected_value_change(manifest,entries,role,phase,change):
    row=next(r for r in manifest["members"] if r["role"]==role and r["phase"]==phase)
    document=core.strict(entries[row["member"]]);change(document["value"])
    payload=core.canonical(document);entries[row["member"]]=payload
    row.update(retained_sha256=core.sha(payload),retained_bytes=len(payload))
