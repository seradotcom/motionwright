#!/usr/bin/env python3
"""Independent byte, timeline and RGBA observation of one direct/mediated pair.

Every native input comes from an explicit fixture path or source-bound receipt.
FFmpeg decodes original PNGs with executable checks before and after each process. This does
not execute HyperFrames, retry jobs, copy owner trees, seal or upload evidence.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import subprocess
from ci_private_evidence import regular
from native_evidence_projection import strict

VERSION="0.8.181"
COMMIT="ae02e5aceff4742e5d72c20f0859eb3e6b449035"
WIDTH,HEIGHT,FRAMES=640,360,60
SHA=re.compile(r"[0-9a-f]{64}\Z")
class HyperframesEvidenceRejected(ValueError):pass

def require(value,code):
    if not value:raise HyperframesEvidenceRejected(code)

def sha(data):return hashlib.sha256(data).hexdigest()
def js_json(value):return (json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False,separators=(",",": "))+"\n").encode()
def plain(value,keys,code):require(type(value) is dict and set(value)==set(keys),code)
def digest(value):return type(value) is str and SHA.fullmatch(value) is not None

def composition(value):
    plain(value,("schema","binding","width","height","fps","frame_count","background","clips"),"composition_shape")
    require(value["schema"]=="semwright.hyperframes.composition/1" and (value["width"],value["height"],value["fps"],value["frame_count"])==(WIDTH,HEIGHT,30,FRAMES),"scenario_clock")
    require(value["background"]=="#101820" and type(value["clips"]) is list and len(value["clips"])==4,"scenario_roster")
    plain(value["binding"],("project_resource","generation","revision","deliverable_id"),"source_binding_shape")
    ids=[]
    for clip in value["clips"]:
        plain(clip,("id","start_frame","end_frame_exclusive","x","y","width","height","fill","opacity_milli","z_index"),"clip_shape")
        require(re.fullmatch(r"node-[0-9a-f]{32}",clip["id"] or "") is not None,"clip_source_identity")
        require(all(type(clip[k]) is int for k in ("start_frame","end_frame_exclusive","x","y","width","height","opacity_milli","z_index")),"clip_number")
        require(re.fullmatch(r"#[0-9A-Fa-f]{6}",clip["fill"] or "") is not None and 0<=clip["opacity_milli"]<=1000,"clip_color")
        require(0<=clip["x"]<WIDTH and 0<=clip["y"]<HEIGHT and 0<clip["width"]<=WIDTH-clip["x"] and 0<clip["height"]<=HEIGHT-clip["y"],"clip_bounds")
        require(0<=clip["start_frame"]<clip["end_frame_exclusive"]<=FRAMES,"clip_clock")
        ids.append(clip["id"])
    require(len(set(ids))==4,"duplicate_clip_identity")
    # The first-party fixture is authored in source, not inferred from output.
    expected=[(0,30,40,40,240,120,1000,0),(0,30,100,70,120,60,500,1),
              (30,60,320,180,240,120,1000,0),(30,60,380,210,120,60,500,1)]
    for clip,values in zip(value["clips"],expected):
        require(tuple(clip[k] for k in ("start_frame","end_frame_exclusive","x","y","width","height","opacity_milli","z_index"))==values,"fixture_authored_properties")
    require(value["clips"][0]["fill"] in ("#E53935","#00AA66") and value["clips"][2]["fill"]=="#1E88E5"
            and all(value["clips"][i]["fill"]=="#FFFFFF" for i in (1,3)),"fixture_authored_fills")
    return value

def expected_rgba(c,index):
    """Independent integer CSS source-over oracle; no native image is a golden."""
    def rgb(s):return tuple(int(s[i:i+2],16) for i in (1,3,5))
    frame=bytearray(bytes((*rgb(c["background"]),255))*(c["width"]*c["height"]))
    for clip in sorted(c["clips"],key=lambda x:x["z_index"]):
        if not clip["start_frame"]<=index<clip["end_frame_exclusive"]:continue
        color=rgb(clip["fill"]);a=clip["opacity_milli"]
        for y in range(clip["y"],clip["y"]+clip["height"]):
            for x in range(clip["x"],clip["x"]+clip["width"]):
                offset=(y*c["width"]+x)*4
                for channel in range(3):frame[offset+channel]=(color[channel]*a+frame[offset+channel]*(1000-a)+500)//1000
    return bytes(frame)

def source_inventory(value):
    plain(value,("fingerprint","source_sha256","source_bytes","html_sha256","html_bytes","compiler","hyperframes_version","hyperframes_commit","composition"),"source_inventory_shape")
    require(all(digest(value[k]) for k in ("fingerprint","source_sha256","html_sha256")),"source_inventory_digest")
    require(value["compiler"]=="semwright-hyperframes-rect-clips-v3" and value["hyperframes_version"]==VERSION and value["hyperframes_commit"]==COMMIT,"source_version")
    require(all(type(value[k]) is int and 0<value[k]<=262144 for k in ("source_bytes","html_bytes")),"source_size")
    composition(value["composition"])
    return value

def source_bundle(root,source):
    bundle=Path(root)/("source-"+source["fingerprint"])
    result={}
    for name,hash_key,size_key in (("index.html","html_sha256","html_bytes"),("semwright-hyperframes.json","source_sha256","source_bytes")):
        raw=regular(bundle/name,262144)[0]
        require(len(raw)==source[size_key] and sha(raw)==source[hash_key],"native_original_source_drift")
        result[name]=raw
    require(strict(result["semwright-hyperframes.json"])==source["composition"],"native_composition_readback")
    require(strict(regular(bundle/"source-inventory.json",524288)[0])==source,"native_bundle_inventory")
    return result

def command_record(value):
    plain(value,("argv_sha256","exit_code","signal","stdout_sha256","stdout_bytes","stderr_sha256","stderr_bytes","capture_complete","automatic_retry"),"native_command_shape")
    require(value["exit_code"]==0 and value["signal"] is None and value["capture_complete"] is True and value["automatic_retry"] is False,"native_cli_not_completed")
    require(all(digest(value[k]) for k in ("argv_sha256","stdout_sha256","stderr_sha256")),"native_command_digest")
    require(all(type(value[k]) is int and 0<=value[k]<=4*1024*1024 for k in ("stdout_bytes","stderr_bytes")),"native_command_size")

def native_receipt(root,source,pin,lock):
    root=Path(root);raw=regular(root/"native-render-receipt.json",524288)[0];r=strict(raw)
    plain(r,("schema","status","attempt_id","fingerprint","source_sha256","html_sha256","runtime_lock_sha256","hyperframes_version","hyperframes_commit","frame_count","fps","frames","native_command","versions","source_unchanged","pixel_decoding_performed","automatic_retry"),"native_receipt_shape")
    require(r["schema"]=="semwright.hyperframes.native-render/1" and r["status"]=="completed" and r["hyperframes_version"]==VERSION and r["hyperframes_commit"]==COMMIT,"native_receipt_version")
    require(all(r[k]==source[k] for k in ("fingerprint","source_sha256","html_sha256")) and r["runtime_lock_sha256"]==pin,"native_receipt_source")
    require(r["source_unchanged"] is True and r["automatic_retry"] is False and r["pixel_decoding_performed"] is False and (r["frame_count"],r["fps"])==(FRAMES,30),"native_receipt_claims")
    require(type(r["attempt_id"]) is str and re.fullmatch(r"[a-z0-9-]{1,96}",r["attempt_id"]),"native_attempt_identity")
    command_record(r["native_command"])
    intent=strict(regular(root/"native-command.intent.json",524288)[0])
    plain(intent,("schema","argv","source_fingerprint","runtime_lock_sha256","automatic_retry"),"native_intent_shape")
    require(intent["schema"]=="semwright.hyperframes.native-command/1" and intent["source_fingerprint"]==source["fingerprint"]
            and intent["runtime_lock_sha256"]==pin and intent["automatic_retry"] is False,"native_intent_source")
    argv=intent["argv"]
    require(type(argv) is list and all(type(x) is str and len(x)<=4096 for x in argv) and len(argv)==19,"native_argv_shape")
    require(argv[0]=="/proc/self/exe" and argv[1].endswith("/node_modules/hyperframes/bin/hyperframes.mjs")
            and argv[2]=="render" and argv[3].endswith("/source-"+source["fingerprint"])
            and argv[4:-1]==["--composition",".","--format","png-sequence","--fps","30","--workers","1","--no-browser-gpu","--no-best-effort","--sdr","--strict","--quiet","--output"]
            and argv[-1]==str(root/"frames"),"native_cli_argv")
    require(sha(js_json(argv))==r["native_command"]["argv_sha256"],"native_argv_digest")
    for stream in ("stdout","stderr"):
        data=regular(root/("native-command."+stream),4*1024*1024)[0]
        require(sha(data)==r["native_command"][stream+"_sha256"] and len(data)==r["native_command"][stream+"_bytes"],"native_stream_binding")
    require(strict(regular(root/"native-command.json",524288)[0])==r["native_command"],"native_exit_record_binding")
    require(r["versions"]=={"node":lock["node_version"],"hyperframes":VERSION,"browser":lock["browser_version"],"ffmpeg":lock["ffmpeg_version"],"ffprobe":lock["ffprobe_version"]},"actual_versions_differ")
    for role in ("browser","ffmpeg","ffprobe"):
        preflight=strict(regular(root/("preflight-"+role+".json"),524288)[0]);command_record(preflight)
        text=regular(root/("preflight-"+role+".stdout"),4*1024*1024)[0]
        error=regular(root/("preflight-"+role+".stderr"),4*1024*1024)[0]
        require(sha(text)==preflight["stdout_sha256"] and len(text)==preflight["stdout_bytes"]
                and sha(error)==preflight["stderr_sha256"] and len(error)==preflight["stderr_bytes"],"native_preflight_stream")
        require(text.decode("utf-8").strip().splitlines()[0]==lock[role+"_version"],"actual_preflight_version")
    require(type(r["frames"]) is list and len(r["frames"])==FRAMES,"frame_roster")
    names=[];encoded=[]
    for i,frame in enumerate(r["frames"]):
        plain(frame,("index","file","bytes","sha256","width","height"),"frame_shape")
        # Exact ae02e5 formatExportFrameName: exported names are one-based; logical indices stay zero-based.
        require(frame["index"]==i and frame["file"]==f"frames/frame_{i+1:06d}.png","frame_name")
        require((frame["width"],frame["height"])==(WIDTH,HEIGHT) and digest(frame["sha256"]) and type(frame["bytes"]) is int and 0<frame["bytes"]<=16*1024*1024,"frame_identity")
        names.append(frame["file"]);data=regular(root/frame["file"],16*1024*1024)[0]
        require(len(data)==frame["bytes"] and sha(data)==frame["sha256"],"frame_original_changed")
        encoded.append(data)
    require(len(set(names))==FRAMES and sum(map(len,encoded))<=256*1024*1024,"frame_namespace_or_budget")
    return r,raw,encoded

def decode_pngs(encoded,ffmpeg,expected_sha256,expected_bytes):
    # The owner grants the executable; these checks detect file drift before
    # and after this decode. They do not attest to an immutable exec handle.
    require(digest(expected_sha256) and type(expected_bytes) is int
            and 0<expected_bytes<=256*1024*1024,"observer_ffmpeg_pin_shape")
    def verify_decoder():
        raw=regular(ffmpeg,256*1024*1024)[0]
        require(len(raw)==expected_bytes and sha(raw)==expected_sha256,"observer_ffmpeg_digest")
    verify_decoder()
    try:
        run=subprocess.run([str(ffmpeg),"-v","error","-nostdin","-f","image2pipe","-c:v","png","-i","pipe:0",
                            "-frames:v",str(FRAMES),"-f","rawvideo","-pix_fmt","rgba","pipe:1"],
                           input=b"".join(encoded),capture_output=True,timeout=120)
    finally:
        verify_decoder()
    require(run.returncode==0 and not run.stderr and len(run.stdout)==WIDTH*HEIGHT*4*FRAMES,"native_png_decode")
    return run.stdout

def verify_pair(source_bytes,render_bytes,reopen_bytes,inspect_bytes,direct_project,mediated_project,direct_output,mediated_output,runtime_root,pin):
    doc=strict(source_bytes);source=source_inventory(doc["source"]);c=source["composition"]
    require(doc["schema"]=="motionwright.native.hyperframes-source/1" and doc["phase"] in ("original","edited") and doc["fixture_origin"]=="generated-first-party-fixture","scenario_origin")
    expected_color="#E53935" if doc["phase"]=="original" else "#00AA66";require(c["clips"][0]["fill"]==expected_color,"scenario_phase_fill")
    p=doc["project"];require(c["binding"]["project_resource"]=="project:"+p["id"] and c["binding"]["generation"]==p["generation"] and c["binding"]["revision"]==p["revision"],"saved_project_binding")
    rendered=strict(render_bytes);reopen=strict(reopen_bytes);inspection=strict(inspect_bytes)
    require(rendered["schema"]=="motionwright.native.hyperframes-render/1" and rendered["phase"]==doc["phase"] and rendered["source_document_sha256"]==sha(source_bytes)
            and rendered["canonical_project_sha256"]==doc["canonical_project_sha256"] and rendered["automatic_retry"] is False and rendered["native_attempt"]==1,"application_render_binding")
    native=rendered["hyperframes"]
    require(native["source"]==source and native["runtime_lock_sha256"]==pin and native["human_acceptance"] is False
            and native["output_kind"]=="native_png_sequence_intermediate","application_native_binding")
    require(reopen["schema"]=="motionwright.native.hyperframes-reopen/1" and reopen["source"]==source and reopen["source_document_sha256"]==sha(source_bytes)
            and reopen["render_document_sha256"]==sha(render_bytes) and reopen["fresh_service"] is True and reopen["new_native_dispatch"] is False
            and reopen["production_receipts_unchanged"] is True and reopen["editable_source_retained"] is True,"fresh_process_readback")
    require(inspection.get("ok") is True and inspection.get("command")=="driver.hyperframes.project.inspect"
            and inspection.get("data",{}).get("source")==source and inspection["data"].get("native_execution_performed") is False,"fresh_broker_native_inspection")
    direct_sources=source_bundle(direct_project,source);mediated_sources=source_bundle(mediated_project,source)
    require(direct_sources==mediated_sources,"direct_mediated_source_byte_parity")
    lock_bytes=regular(Path(runtime_root)/"runtime-lock.json",8*1024*1024)[0];require(sha(lock_bytes)==pin,"observer_runtime_pin")
    lock=strict(lock_bytes);require(lock["hyperframes_version"]==VERSION and lock["hyperframes_commit"]==COMMIT,"observer_runtime_version")
    ff=[f for f in lock["files"] if f["role"]=="ffmpeg"];require(len(ff)==1,"observer_ffmpeg_role")
    require(type(ff[0]["path"]) is str and re.fullmatch(r"[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*",ff[0]["path"]) and all(v not in (".","..") for v in ff[0]["path"].split("/")),"observer_ffmpeg_path")
    ffmpeg=Path(runtime_root)/ff[0]["path"]
    direct,direct_raw,direct_pngs=native_receipt(direct_output,source,pin,lock)
    mediated,mediated_raw,mediated_pngs=native_receipt(mediated_output,source,pin,lock)
    require(direct["attempt_id"]!=mediated["attempt_id"] and native["job_ref"]==mediated["attempt_id"]
            and native["result"]["receipt"]==mediated and native["result"]["state"]=="succeeded","independent_original_attempts")
    left=decode_pngs(direct_pngs,ffmpeg,ff[0]["sha256"],ff[0]["bytes"])
    right=decode_pngs(mediated_pngs,ffmpeg,ff[0]["sha256"],ff[0]["bytes"])
    require(left==right,"direct_mediated_rgba_parity")
    size=WIDTH*HEIGHT*4;oracle={};rows=[]
    for i in range(FRAMES):
        frame=right[i*size:(i+1)*size];key=tuple(v["id"] for v in c["clips"] if v["start_frame"]<=i<v["end_frame_exclusive"])
        if key not in oracle:oracle[key]=expected_rgba(c,i)
        expected=oracle[key];maximum=max(abs(a-b) for a,b in zip(frame,expected))
        require(maximum<=1,"authored_geometry_color_opacity_timing_or_matte_differs")
        rows.append({"index":i,"direct_png_sha256":direct["frames"][i]["sha256"],"mediated_png_sha256":mediated["frames"][i]["sha256"],
                     "rgba_sha256":sha(frame),"expected_rgba_sha256":sha(expected),"maximum_channel_error":maximum,"all_pixels_within_tolerance":True})
    require(rows[29]["rgba_sha256"]!=rows[30]["rgba_sha256"],"authored_cut_not_visible")
    return {"schema":"motionwright.native.hyperframes-comparison/1","phase":doc["phase"],"status":"SOURCE_BOUND_RGBA_PARITY_VERIFIED",
        "source_fingerprint":source["fingerprint"],"source_sha256":source["source_sha256"],"html_sha256":source["html_sha256"],"runtime_lock_sha256":pin,
        "direct_receipt_sha256":sha(direct_raw),"mediated_receipt_sha256":sha(mediated_raw),"decoded_rgba_sha256":sha(right),
        "source_document_sha256":sha(source_bytes),"render_document_sha256":sha(render_bytes),"reopen_document_sha256":sha(reopen_bytes),
        "inspection_document_sha256":sha(inspect_bytes),"actual_versions":mediated["versions"],"frame_count":FRAMES,"width":WIDTH,"height":HEIGHT,"fps":30,
        "frames":rows,"direct_mediated_source_bytes_equal":True,"native_sources_reopened":True,"fresh_project_readback":True,
        "decoded_all_frames":True,"authored_rectangle_oracle":True,"maximum_allowed_channel_error":1,
        "decoder":{"sha256":ff[0]["sha256"],"bytes":ff[0]["bytes"],"ambient_path_lookup":False,
                   "verified_before_each_decode":True,"verified_after_each_decode":True,"immutable_exec_attested":False},
        "formal_acceptance":False,"human_acceptance":False,"automatic_retry":False}

def verify_edit(original,edited):
    left=source_inventory(original["source"]);right=source_inventory(edited["source"])
    require(left["fingerprint"]!=right["fingerprint"] and right["composition"]["binding"]["revision"]==left["composition"]["binding"]["revision"]+1,"edited_source_identity")
    expected=json.loads(json.dumps(left["composition"]));expected["binding"]["revision"]+=1;expected["clips"][0]["fill"]="#00AA66"
    require(right["composition"]==expected,"native_edit_changed_unselected_property")
