#!/usr/bin/env python3
"""Source-owned /2 field policies and fixed scenario roles; no capture or upload.

Original DB/WAL/SHM and local diagnostics remain untouched. The central
collector/consumer must use these same policies and role contract; generic
JSON pass-through, role inference and whole-owner traversal are forbidden.
"""
from __future__ import annotations
from pathlib import Path
from native_evidence_projection import (Object,Field,Array,String,Number,Nullable,Literal,member_path,excluded,_checked_spec)
from ci_private_evidence import regular
from native_hyperframes_evidence import sha,require

F=Field
H=String(64,r"[0-9a-f]{64}")
U=String(36,r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SLUG=String(96,r"[a-z0-9-]{1,96}")
I=Number(0,9_007_199_254_740_991,True)
PHASE=String(values=("original","edited"))
CONTEXT=Object({"source_sha":F(String(40,r"[0-9a-f]{40}")),"semwright_sha":F(String(40,r"[0-9a-f]{40}")),
    "run_id":F(String(20,r"[1-9][0-9]{0,19}")),"run_attempt":F(Literal("1")),"scenario":F(Literal("hyperframes-first-party-direct-mediated-edit"))})
BINDING=Object({"project_resource":F(String(44,r"project:[0-9a-f-]{36}")),"generation":F(U),"revision":F(I),"deliverable_id":F(U)})
CLIP=Object({"id":F(String(37,r"node-[0-9a-f]{32}")),"start_frame":F(Number(0,59,True)),"end_frame_exclusive":F(Number(1,60,True)),
    "x":F(Number(0,640,True)),"y":F(Number(0,360,True)),"width":F(Number(1,640,True)),"height":F(Number(1,360,True)),
    "fill":F(String(7,r"#[0-9A-Fa-f]{6}")),"opacity_milli":F(Number(0,1000,True)),"z_index":F(Number(-1024,1024,True))})
COMPOSITION=Object({"schema":F(Literal("semwright.hyperframes.composition/1")),"binding":F(BINDING),"width":F(Literal(640)),"height":F(Literal(360)),
    "fps":F(Literal(30)),"frame_count":F(Literal(60)),"background":F(Literal("#101820")),"clips":F(Array(CLIP,4,4))})
SOURCE=Object({"fingerprint":F(H),"source_sha256":F(H),"source_bytes":F(Number(1,262144,True)),"html_sha256":F(H),"html_bytes":F(Number(1,262144,True)),
    "compiler":F(Literal("semwright-hyperframes-rect-clips-v3")),"hyperframes_version":F(Literal("0.8.181")),
    "hyperframes_commit":F(Literal("ae02e5aceff4742e5d72c20f0859eb3e6b449035")),"composition":F(COMPOSITION)})
VERSIONS=Object({k:F(String(512)) for k in ("node","hyperframes","browser","ffmpeg","ffprobe")})
COMMAND=Object({"argv_sha256":F(H),"exit_code":F(Nullable(Number(-255,255,True))),"signal":F(Nullable(String(32,r"SIG[A-Z0-9]+"))),
    "stdout_sha256":F(H),"stdout_bytes":F(Number(0,4194304,True)),"stderr_sha256":F(H),"stderr_bytes":F(Number(0,4194304,True)),
    "capture_complete":F(bool),"automatic_retry":F(Literal(False))})
FRAME=Object({"index":F(Number(0,59,True)),"file":F(String(64,r"frames/frame_0000(?:0[1-9]|[1-5][0-9]|60)\.png")),"bytes":F(Number(1,16777216,True)),
    "sha256":F(H),"width":F(Literal(640)),"height":F(Literal(360))})
RECEIPT=Object({"schema":F(Literal("semwright.hyperframes.native-render/1")),"status":F(Literal("completed")),"attempt_id":F(SLUG),
    "fingerprint":F(H),"source_sha256":F(H),"html_sha256":F(H),"runtime_lock_sha256":F(H),"hyperframes_version":F(Literal("0.8.181")),
    "hyperframes_commit":F(Literal("ae02e5aceff4742e5d72c20f0859eb3e6b449035")),"frame_count":F(Literal(60)),"fps":F(Literal(30)),
    "frames":F(Array(FRAME,60,60)),"native_command":F(COMMAND),"versions":F(VERSIONS),"source_unchanged":F(Literal(True)),
    "pixel_decoding_performed":F(Literal(False)),"automatic_retry":F(Literal(False))})
JOB=Object({"job_ref":F(SLUG),"state":F(Literal("succeeded")),"fingerprint":F(H),"runtime_lock_sha256":F(H),
    "directory":F(String(110,r"render-[a-z0-9-]{1,96}")),"receipt":F(RECEIPT),"automatic_retry":F(Literal(False))})
EVIDENCE=Object({"project_resource":F(String(44,r"project:[0-9a-f-]{36}")),"generation":F(U),"revision":F(I),"deliverable_id":F(U),
    "source":F(SOURCE),"runtime_lock_sha256":F(H),"job_ref":F(SLUG),"result":F(JOB),"output_kind":F(Literal("native_png_sequence_intermediate")),"human_acceptance":F(Literal(False))})
POLICIES={
 "hf-source-document":Object({"schema":F(Literal("motionwright.native.hyperframes-source/1")),"phase":F(PHASE),"canonical_project_sha256":F(H),
    "source":F(SOURCE),"native_dispatch_performed":F(Literal(False)),"fixture_origin":F(Literal("generated-first-party-fixture")),"human_acceptance":F(Literal(False))}),
 "hf-native-source":COMPOSITION,"hf-source-inventory":SOURCE,"hf-native-receipt":RECEIPT,"hf-command":COMMAND,
 "hf-render":Object({"schema":F(Literal("motionwright.native.hyperframes-render/1")),"phase":F(PHASE),"canonical_project_sha256":F(H),
    "source_document_sha256":F(H),"hyperframes":F(EVIDENCE),"native_attempt":F(Literal(1)),"automatic_retry":F(Literal(False))}),
 "hf-reopen":Object({"schema":F(Literal("motionwright.native.hyperframes-reopen/1")),"phase":F(PHASE),"canonical_project_sha256":F(H),
    "source_document_sha256":F(H),"render_document_sha256":F(H),"receipts_sha256":F(H),"source":F(SOURCE),"fresh_service":F(Literal(True)),
    "production_receipts_unchanged":F(Literal(True)),"new_native_dispatch":F(Literal(False)),"editable_source_retained":F(Literal(True))}),
 "hf-inspection":Object({"ok":F(Literal(True)),"command":F(Literal("driver.hyperframes.project.inspect")),"data":F(Object({"source":F(SOURCE),"native_execution_performed":F(Literal(False))}))}),
 "hf-native-intent":Object({"schema":F(Literal("semwright.hyperframes.native-command/1")),"source_fingerprint":F(H),"runtime_lock_sha256":F(H),"automatic_retry":F(Literal(False))}),
 "hf-job-intent":Object({"schema":F(Literal("semwright.hyperframes.job-intent/1")),"source":F(SOURCE),"runtime_lock_sha256":F(H),"attempt_id":F(SLUG),"session_sha256":F(H),"automatic_retry":F(Literal(False))}),
 "hf-job-binding":Object({"schema":F(Literal("semwright.hyperframes.job-binding/1")),"attempt_id":F(SLUG),"session_sha256":F(H),"source":F(SOURCE),"runtime_lock_sha256":F(H)}),
 "hf-export":Object({"schema":F(Literal("motionwright.native.hyperframes-export/1")),"source":F(SOURCE),"source_document_sha256":F(H),"new_native_dispatch":F(Literal(False)),"editable_source_retained":F(Literal(True))}),
 "hf-direct-request":Object({"schema":F(Literal("semwright.hyperframes.render-request/1")),"source":F(SOURCE),"fingerprint":F(H),"runtime_lock_sha256":F(H),"attempt_id":F(SLUG)}),
 "hf-app-intent":Object({"schema":F(Literal("motionwright.native.hyperframes-intent/1")),"project_resource":F(String(44,r"project:[0-9a-f-]{36}")),"generation":F(U),"revision":F(I),
    "request_id":F(String(32,r"hyperframes:(original|edited)")),"source":F(SOURCE),"runtime_lock_sha256":F(H),"native_attempt":F(Literal(1)),"automatic_retry":F(Literal(False))}),
 "hf-app-command-intent":Object({"schema":F(Literal("motionwright.native.hyperframes-command-intent/1")),"label":F(String(90,r"[a-z][a-z0-9.-]{0,89}")),"argument_sha256":F(H),"automatic_retry":F(Literal(False))}),
 "hf-app-command-exit":Object({"schema":F(Literal("motionwright.native.hyperframes-command-exit/1")),"label":F(String(90,r"[a-z][a-z0-9.-]{0,89}")),
    "exit_code":F(Nullable(Number(-255,255,True))),"deadline_exceeded":F(bool),"automatic_retry":F(Literal(False)),"native_terminal_inferred_from_exit":F(Literal(False))}),
}
COMPARISON_FRAME=Object({"index":F(Number(0,59,True)),"direct_png_sha256":F(H),"mediated_png_sha256":F(H),"rgba_sha256":F(H),"expected_rgba_sha256":F(H),
    "maximum_channel_error":F(Number(0,1,True)),"all_pixels_within_tolerance":F(Literal(True))})
comparison_fields={"schema":F(Literal("motionwright.native.hyperframes-comparison/1")),"phase":F(PHASE),"status":F(Literal("SOURCE_BOUND_RGBA_PARITY_VERIFIED")),
    "actual_versions":F(VERSIONS),"frame_count":F(Literal(60)),"width":F(Literal(640)),"height":F(Literal(360)),"fps":F(Literal(30)),
    "frames":F(Array(COMPARISON_FRAME,60,60)),"maximum_allowed_channel_error":F(Literal(1)),
    "decoder":F(Object({"sha256":F(H),"bytes":F(Number(1,268435456,True)),"ambient_path_lookup":F(Literal(False)),
        "verified_before_each_decode":F(Literal(True)),"verified_after_each_decode":F(Literal(True)),"immutable_exec_attested":F(Literal(False))}))}
for key in ("source_fingerprint","source_sha256","html_sha256","runtime_lock_sha256","direct_receipt_sha256","mediated_receipt_sha256","decoded_rgba_sha256",
            "source_document_sha256","render_document_sha256","reopen_document_sha256","inspection_document_sha256"):comparison_fields[key]=F(H)
for key in ("direct_mediated_source_bytes_equal","native_sources_reopened","fresh_project_readback","decoded_all_frames","authored_rectangle_oracle"):comparison_fields[key]=F(Literal(True))
for key in ("formal_acceptance","human_acceptance","automatic_retry"):comparison_fields[key]=F(Literal(False))
POLICIES["hf-comparison"]=Object(comparison_fields)
POLICIES["hf-runtime-lock"]=Object({"schema":F(Literal("semwright.hyperframes.runtime-lock/1")),"hyperframes_version":F(Literal("0.8.181")),
    "hyperframes_commit":F(Literal("ae02e5aceff4742e5d72c20f0859eb3e6b449035")),"node_version":F(String(32,r"v[0-9]+\.[0-9]+\.[0-9]+")),
    "node_sha256":F(H),"browser_version":F(String(512)),"ffmpeg_version":F(String(512)),"ffprobe_version":F(String(512)),
    "files":F(Array(Object({"path":F(String(1024,r"[A-Za-z0-9_.@+/-]+")),"sha256":F(H),"bytes":F(Number(1,536870912,True)),
        "role":F(String(values=("runner","hyperframes-cli","package-lock","browser","ffmpeg","ffprobe","dependency")))}),30000,8))})

ROLE_CONTRACT={}
for phase in ("original","edited"):
    for layer,role,count in (("persistence","source-document",1),("attempt-custody","render-record",1),("readback","fresh-reopen",1),
        ("readback","native-inspection",1),("readback","comparison",1),("readback","native-receipt",2),("readback","native-frame",120),("attempt-custody","native-command",2),
        ("attempt-custody","native-intent",2),("attempt-custody","native-preflight",6),("attempt-custody","host-job-binding",1),
        ("attempt-custody","host-job-intent",1),("persistence","source-export",1),("attempt-custody","direct-request",1),
        ("attempt-custody","app-intent",1),("attempt-custody","app-command-intent",5),("attempt-custody","app-command-exit",5)):
        ROLE_CONTRACT[(phase,layer,role)]=(count,count)
    for route in ("direct","mediated"):
        for role in ("native-html","native-composition","native-inventory"):
            ROLE_CONTRACT[(phase,"persistence",route+"-"+role)]=(1,1)
ROLE_CONTRACT[("single","persistence","database")]=(1,1)
ROLE_CONTRACT[("single","persistence","database-wal")]=(0,1)
ROLE_CONTRACT[("single","persistence","database-shm")]=(0,1)
ROLE_CONTRACT[("single","origin","runtime-lock")]=(1,1)


def build_spec(args,context,bindings,*,incomplete=False):
    from native_hyperframes_e2e import document,direct_attempt,mediated_attempt
    require(type(incomplete) is bool,"selection_incomplete_flag")
    roots={"source":str(args.source_root),"records":str(args.records),"database":str(args.data_root),
        "direct-source":str(args.direct_project),"mediated-source":str(args.mediated_project),
        "direct-output":str(args.direct_output),"mediated-output":str(args.mediated_output),"dependencies":str(args.dependencies)}
    members=[];exclusions=[];absence=[]
    def select(root,path,phase,layer,role,kind,policy=None,optional=False):
        # Classification precedes every stat/open/hash. No root discovery occurs.
        require(not excluded(member_path(path)),"selection_before_open")
        digest=None;size=None
        try:raw=regular(Path(roots[root])/path,256*1024*1024)[0];digest=sha(raw);size=len(raw)
        except FileNotFoundError:
            if optional:
                absence.append({"root":root,"relative_path":path,"phase":phase,"layer":layer,"role":role});return
        except (OSError,ValueError):
            if not incomplete:raise
            # Preserve an explicit CAPTURE_FAILED candidate; never widen a read.
            digest=None;size=None
        # An unobserved required entry is explicit REQUIRED_MISSING/CAPTURE_FAILED
        # in the central capture; it is never relabeled an intentional exclusion.
        members.append({"root":root,"relative_path":path,"phase":phase,"layer":layer,"role":role,"kind":kind,
            "required":not optional,"expected_sha256":digest,"bytes":size,"policy":policy})
    def exclude(root,path):
        require(excluded(member_path(path)),"exclusion_policy")
        exclusions.append({"root":root,"relative_path":path,"reason":"outside-reviewed-scope"})
    for phase in ("original","edited"):
        source=None
        try:
            observed=document(args.records/(phase+"-source.json"))
            candidate=observed["source"]
            require(isinstance(candidate,dict) and __import__("re").fullmatch(r"[0-9a-f]{64}",candidate.get("fingerprint","")),"selected_source_fingerprint")
            source=candidate
        except (OSError,ValueError,KeyError,TypeError):
            if not incomplete:raise
        fingerprint=source["fingerprint"] if source is not None else "unprepared-"+phase
        if not incomplete:
            comparison=document(args.records/(phase+"-comparison.json"))
            require(comparison["status"]=="SOURCE_BOUND_RGBA_PARITY_VERIFIED" and comparison["source_fingerprint"]==fingerprint,"pair_not_observed_for_selection")
        for suffix,layer,role,policy in (("source","persistence","source-document","hf-source-document"),("render","attempt-custody","render-record","hf-render"),
            ("reopen","readback","fresh-reopen","hf-reopen"),("inspect","readback","native-inspection","hf-inspection"),("comparison","readback","comparison","hf-comparison"),
            ("export","persistence","source-export","hf-export"),("direct-request","attempt-custody","direct-request","hf-direct-request")):
            select("records",phase+"-"+suffix+".json",phase,layer,role,"json-projection",policy)
        select("records",phase+"-render.intent.json",phase,"attempt-custody","app-intent","json-projection","hf-app-intent")
        for verb in (("seed" if phase=="original" else "edit"),"export","render","readback","inspect"):
            stem=phase+"."+verb
            select("records",stem+".command-intent.json",phase,"attempt-custody","app-command-intent","json-projection","hf-app-command-intent")
            select("records",stem+".command-exit.json",phase,"attempt-custody","app-command-exit","json-projection","hf-app-command-exit")
            for stream in ("stdout","stderr"):exclude("records",stem+"."+stream)
        mediated=mediated_attempt(phase,fingerprint,args.runtime_lock_sha256) if source is not None else "unprepared-mediated-"+phase
        for suffix,role,policy in (("intent","host-job-intent","hf-job-intent"),("binding","host-job-binding","hf-job-binding")):
            select("mediated-source","native-job-"+mediated+"."+suffix+".json",phase,"attempt-custody",role,"json-projection",policy)
        direct=direct_attempt(phase,fingerprint) if source is not None else "unprepared-direct-"+phase
        for route,attempt in (("direct",direct),("mediated",mediated)):
            bundle="source-"+fingerprint+"/"
            select(route+"-source",bundle+"index.html",phase,"persistence",route+"-native-html","original")
            select(route+"-source",bundle+"semwright-hyperframes.json",phase,"persistence",route+"-native-composition","json-original","hf-native-source")
            select(route+"-source",bundle+"source-inventory.json",phase,"persistence",route+"-native-inventory","json-original","hf-source-inventory")
            output="render-"+attempt+"/"
            select(route+"-output",output+"native-render-receipt.json",phase,"readback","native-receipt","json-original","hf-native-receipt")
            select(route+"-output",output+"native-command.json",phase,"attempt-custody","native-command","json-original","hf-command")
            select(route+"-output",output+"native-command.intent.json",phase,"attempt-custody","native-intent","json-projection","hf-native-intent")
            if incomplete:
                # Fixed scenario names are a selection roster, never invented output.
                # Required missing/changed members remain incomplete in the /2 reader.
                frames=[{"index":i,"file":f"frames/frame_{i+1:06d}.png"} for i in range(60)]
            else:
                receipt=document(Path(roots[route+"-output"])/(output+"native-render-receipt.json"))
                require(len(receipt["frames"])==60,"selected_frame_roster")
                frames=receipt["frames"]
            for index,frame in enumerate(frames):
                require(frame["index"]==index and frame["file"]==f"frames/frame_{index+1:06d}.png","selected_frame_order")
                path=frame["file"];require(__import__("re").fullmatch(r"frames/frame_0000(?:0[1-9]|[1-5][0-9]|60)\.png",path or ""),"selected_frame_path")
                select(route+"-output",output+path,phase,"readback","native-frame","original")
            for role in ("browser","ffmpeg","ffprobe"):
                select(route+"-output",output+"preflight-"+role+".json",phase,"attempt-custody","native-preflight","json-original","hf-command")
                for stream in ("stdout","stderr"):exclude(route+"-output",output+"preflight-"+role+"."+stream)
            for stream in ("stdout","stderr"):exclude(route+"-output",output+"native-command."+stream)
    select("database","source.sqlite3","single","persistence","database","original")
    for suffix in ("wal","shm"):select("database","source.sqlite3-"+suffix,"single","persistence","database-"+suffix,"original",optional=True)
    select("dependencies","runtime-lock.json","single","origin","runtime-lock","json-original","hf-runtime-lock")
    spec={"schema":"motionwright.native.evidence-selection/2","context":context,"roots":roots,"members":members,"exclusions":exclusions,"absence_guards":absence,
        "source_bindings":bindings,"origin":{"kind":"generated-first-party-fixture","fixture_id":"hyperframes-rectangle-cuts-and-original-edit",
        "producer_paths":["crates/native/examples/native-hyperframes-e2e.rs","tooling/native_hyperframes_e2e.py","tooling/native_hyperframes_evidence.py","tooling/native_hyperframes_projection.py"],
        "private_data":True,"upload_authorization":"NOT_GRANTED_BY_COLLECTOR"}}
    # Structural validation only, not capture, encryption or publication.
    return _checked_spec(spec,POLICIES,ROLE_CONTRACT)
