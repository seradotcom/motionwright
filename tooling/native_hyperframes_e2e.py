#!/usr/bin/env python3
"""Stage producer on the existing public Broker/Driver Host.

Stages are prepare-original, render-original, compare-original, prepare-edited,
render-edited, compare-edited, and selection. Between prepare and render/compare,
The owner executes the fixed direct-reference request with its admitted native
executor. No arbitrary command callback, second Broker, sandbox, job runtime,
downloader, upload, encryption or UNKNOWN retry exists here.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
from ci_private_evidence import new_bytes,new_json,regular,json_bytes
from native_av_failure_guard import run_sanitized_json
from native_evidence_projection import strict,verify_source_bindings,member_path,excluded
from native_hyperframes_evidence import (sha,require,source_inventory,verify_pair,verify_edit)

SEMWRIGHT="4849b3c1dcee9b37438801b4264f59480bff9e40"
SOURCE_PATHS=("Cargo.toml","Cargo.lock","crates/native/Cargo.toml","crates/native/src/lib.rs","crates/native/src/production.rs",
 "crates/native/src/hyperframes.rs","crates/native/src/production/hyperframes.rs","crates/native/examples/native-hyperframes-e2e.rs",
 "crates/hyperframes-profile/Cargo.toml","crates/hyperframes-profile/src/lib.rs","crates/driver-hyperframes/Cargo.toml",
 "crates/driver-hyperframes/src/lib.rs","crates/driver-hyperframes/src/main.rs","crates/driver-hyperframes/src/store.rs","crates/driver-hyperframes/src/driver.rs",
 "native/hyperframes/semwright-hyperframes-runner.mjs","tooling/native_hyperframes_evidence.py","tooling/native_hyperframes_e2e.py",
 "tooling/native_hyperframes_projection.py","tooling/native_evidence_projection.py","tooling/ci_private_evidence.py","tooling/native_av_failure_guard.py",
 "tooling/mlt_official_runtime_intake.py","SOURCE_LOCK.json",
 "crates/driver-hyperframes/examples/native-hyperframes-direct-reference.rs",
 "tooling/native_hyperframes_actions.py","tooling/native_hyperframes_runtime_intake.py",
 "tooling/tests/test_native_hyperframes_actions.py","tooling/tests/test_native_hyperframes_runtime_intake.py",
 "tooling/tests/test_native_hyperframes.py","tooling/tests/test_native_evidence_projection.py",
 "native/hyperframes/tests/adapter-contract.test.mjs",
 "tooling/ci_test_runner.py",
 "tooling/hyperframes/package.json",
 "tooling/hyperframes/prepare-runtime.mjs","tooling/hyperframes/prepare-runtime.test.mjs",
 "tooling/hyperframes/owner_provision.py","tooling/hyperframes/test_owner_provision.py",
 "tooling/hyperframes/fixtures/hyperframes-0.8.181-original.json",
 "tooling/requirements/mlt-g2-custody.txt",".github/workflows/native-production.yml")
STAGES=("prepare-original","render-original","compare-original","prepare-edited","render-edited","compare-edited","selection")

def original(path,maximum=8*1024*1024):return regular(Path(path),maximum)[0]
def document(path):return strict(original(path))
def context(args):
    require(os.environ.get("GITHUB_ACTIONS")=="true" and os.environ.get("GITHUB_RUN_ATTEMPT")=="1","original_actions_attempt_required")
    require(args.source_sha==os.environ.get("GITHUB_SHA") and re.fullmatch(r"[0-9a-f]{40}",args.source_sha) and args.sem_sha==SEMWRIGHT,"exact_build_sources")
    roots=(args.records,args.data_root,args.direct_project,args.mediated_project,args.direct_output,args.mediated_output,args.dependencies)
    for root in (*roots,args.source_root,args.private_config):
        require(root.is_absolute() and root.resolve(strict=True)==root and root.is_dir(),"canonical_existing_root")
    for i,left in enumerate(roots):
        require((left.stat().st_mode&0o077)==0,"private_native_root_mode")
        for right in roots[i+1:]:require(left!=right and left not in right.parents and right not in left.parents,"overlapping_native_roots")
    require(all(args.private_config!=r and r not in args.private_config.parents and args.private_config not in r.parents for r in roots),"credential_root_not_selected")
    source_bytes=original(args.source_bindings,524288);require(sha(source_bytes)==args.source_bindings_sha256,"admitted_source_bindings_pin")
    binding_doc=strict(source_bytes);require(set(binding_doc)=={"schema","source_sha","source_bindings"} and binding_doc["schema"]=="motionwright.native.source-bindings/1"
        and binding_doc["source_sha"]==args.source_sha and sorted(x["path"] for x in binding_doc["source_bindings"])==sorted(SOURCE_PATHS),"source_roster")
    # Before every effect, compare actual code bytes AND exact committed blobs.
    verify_source_bindings(args.source_root,args.source_sha,binding_doc["source_bindings"])
    for file,pin in ((args.mw_binary,args.mw_binary_sha256),(args.sem_cli,args.sem_cli_sha256)):
        require(sha(original(file,256*1024*1024))==pin,"original_built_binary_pin")
    require(re.fullmatch(r"[0-9a-f]{64}",args.runtime_lock_sha256) and sha(original(args.dependencies/"runtime-lock.json"))==args.runtime_lock_sha256,"admitted_dependency_pin")
    return ({"source_sha":args.source_sha,"semwright_sha":args.sem_sha,"run_id":os.environ["GITHUB_RUN_ID"],"run_attempt":"1","scenario":"hyperframes-first-party-direct-mediated-edit"},binding_doc["source_bindings"])

class Attempt:
    def __init__(self,args):self.args=args
    def command(self,label,argv,timeout=420):
        require(re.fullmatch(r"[a-z][a-z0-9.-]{0,89}",label),"closed_stage_label")
        root=self.args.records
        new_json(root/(label+".command-intent.json"),{"schema":"motionwright.native.hyperframes-command-intent/1","label":label,
            "argument_sha256":sha(json.dumps(argv,separators=(",",":"),ensure_ascii=False).encode()),"automatic_retry":False})
        def capture(event):
            for stream in ("stdout","stderr"):
                raw=event.get(stream,b"");raw=raw.encode() if isinstance(raw,str) else raw
                new_bytes(root/(label+"."+stream),raw)
            new_json(root/(label+".command-exit.json"),{"schema":"motionwright.native.hyperframes-command-exit/1","label":label,
                "exit_code":event.get("exit_code"),"deadline_exceeded":event.get("deadline_exceeded",False),
                "automatic_retry":False,"native_terminal_inferred_from_exit":False})
        # Existing bounded helper retains originals before interpreting stdout.
        return run_sanitized_json(argv,env=dict(os.environ),timeout=timeout,capture=capture)


def connection(args,source):
    path=args.private_config/"hyperframes-connection.json"
    expected={"schema":"motionwright-semwright-connection/1","executable":str(args.sem_cli),"executable_sha256":args.sem_cli_sha256,
        "socket":str(args.socket),"session_file":str(args.session_file),"output_root":str(args.mediated_output),
        "resource":source["source"]["composition"]["binding"]["project_resource"]}
    if path.exists():require(document(path)==expected,"existing_original_connection_changed")
    else:new_json(path,expected)
    return path

def paths(args,phase):
    return (args.records/(phase+"-source.json"),args.records/(phase+"-render.json"),args.records/(phase+"-reopen.json"),
            args.records/(phase+"-inspect.json"),args.records/(phase+"-comparison.json"))

def direct_attempt(phase,fingerprint):return "direct-"+phase+"-"+fingerprint[:16]
def mediated_attempt(phase,fingerprint,pin):return "hf-"+sha(("hyperframes:"+phase+"\n"+fingerprint+"\n"+pin).encode())

def retained_comparison(args, phase):
    from native_hyperframes_projection import PhaseOriginals
    anchor = getattr(args, "phase_results", {}).get(phase)
    require(type(anchor) is PhaseOriginals and anchor.phase == phase,
            "original_oracle_result_not_retained")
    # Read once and consume only those verified bytes; do not reopen the path
    # between checking its hash and using the comparison for a new edit.
    raw = original(args.records / (phase + "-comparison.json"))
    require(raw == anchor.comparison_raw, "original_oracle_comparison_changed")
    return strict(raw)

def prepare(args,phase,attempt):
    source,_,_,_,_=paths(args,phase);db=args.data_root/"source.sqlite3"
    if phase=="original":attempt.command("original.seed",[str(args.mw_binary),"seed",str(db),str(source)])
    else:
        previous=retained_comparison(args,"original")
        require(previous["status"]=="SOURCE_BOUND_RGBA_PARITY_VERIFIED" and previous["automatic_retry"] is False,"original_pair_not_verified")
        attempt.command("edited.edit",[str(args.mw_binary),"edit",str(db),str(source)])
    doc=document(source);source_inventory(doc["source"])
    if phase=="edited":verify_edit(document(args.records/"original-source.json"),doc)
    attempt.command(phase+".export",[str(args.mw_binary),"export",str(db),str(source),str(args.direct_project),str(args.records/(phase+"-export.json"))])
    native=doc["source"]
    new_json(args.records/(phase+"-direct-request.json"),{"schema":"semwright.hyperframes.render-request/1","source":native,
        "fingerprint":native["fingerprint"],"runtime_lock_sha256":args.runtime_lock_sha256,"attempt_id":direct_attempt(phase,native["fingerprint"])})
    # The owner runs that exact request through its admitted native executor.
    # This stage does not install dependencies, execute the reference or infer
    # completion from a prepared request.


def render(args,phase,attempt):
    source,rendered,reopen,inspection,_=paths(args,phase);doc=document(source);conn=connection(args,doc)
    direct=args.direct_output/("render-"+direct_attempt(phase,doc["source"]["fingerprint"]))
    require((direct/"native-render-receipt.json").is_file(),"original_direct_reference_required")
    attempt.command(phase+".render",[str(args.mw_binary),"render",str(args.data_root/"source.sqlite3"),str(conn),str(source),args.runtime_lock_sha256,str(rendered)])
    attempt.command(phase+".readback",[str(args.mw_binary),"readback",str(args.data_root/"source.sqlite3"),str(source),str(rendered),str(reopen)])
    response=attempt.command(phase+".inspect",[str(args.sem_cli),"--socket",str(args.socket),"--session-file",str(args.session_file),
        "--json","execute","driver.hyperframes.project.inspect","--args-json","{}"],timeout=60)
    new_json(inspection,response)


def compare(args,phase):
    source,rendered,reopen,inspection,output=paths(args,phase);doc=document(source);fingerprint=doc["source"]["fingerprint"]
    result=verify_pair(original(source),original(rendered),original(reopen),original(inspection),args.direct_project,args.mediated_project,
        args.direct_output/("render-"+direct_attempt(phase,fingerprint)),
        args.mediated_output/("render-"+mediated_attempt(phase,fingerprint,args.runtime_lock_sha256)),args.dependencies,args.runtime_lock_sha256)
    if phase=="edited":
        first=retained_comparison(args,"original");require(first["decoded_rgba_sha256"]!=result["decoded_rgba_sha256"],"edited_native_pixels_unchanged")
    from native_hyperframes_projection import freeze_phase_originals
    raw = json_bytes(result)
    new_bytes(output, raw)
    return freeze_phase_originals(args, phase, raw)


def run(args):
    ctx,bindings=context(args)
    # Source-checked before this durable reservation; no stage is replayable.
    new_json(args.records/(args.stage+".stage-intent.json"),{"schema":"motionwright.native.hyperframes-stage-intent/1",
        "stage":args.stage,"context":ctx,"automatic_retry":False})
    try:
        attempt=Attempt(args); phase_result=None
        if args.stage.startswith("prepare-"):prepare(args,args.stage[8:],attempt)
        elif args.stage.startswith("render-"):render(args,args.stage[7:],attempt)
        elif args.stage.startswith("compare-"):phase_result=compare(args,args.stage[8:])
        else:
            from native_hyperframes_projection import build_spec
            spec=build_spec(args,ctx,bindings)
            new_json(args.records/"selected-evidence-spec.json",spec)
        new_json(args.records/(args.stage+".stage-result.json"),{"schema":"motionwright.native.hyperframes-stage-result/1",
            "stage":args.stage,"status":"STAGE_COMPLETED","automatic_retry":False,"formal_acceptance":False})
        return phase_result
    except Exception:
        # Already produced DB/WAL/SHM, source, Host jobs and outputs remain in the
        # original isolated owner; no cleanup/recovery mutation is attempted.
        new_json(args.records/(args.stage+".stage-failure.json"),{"schema":"motionwright.native.hyperframes-stage-failure/1",
            "stage":args.stage,"status":"NOT_VERIFIED","automatic_retry":False,"originals_retained":True})
        raise

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("stage",choices=STAGES)
    for name in ("source-root","source-bindings","mw-binary","sem-cli","socket","session-file","private-config","records","data-root",
                 "direct-project","mediated-project","direct-output","mediated-output","dependencies"):
        parser.add_argument("--"+name,type=Path,required=True)
    for name in ("source-sha","sem-sha","source-bindings-sha256","mw-binary-sha256","sem-cli-sha256","runtime-lock-sha256"):
        parser.add_argument("--"+name,required=True)
    args=parser.parse_args()
    try:run(args)
    except Exception:
        print("native-hyperframes=NOT_VERIFIED; automatic_retry=false");raise SystemExit(1)
    print("native-hyperframes=STAGE_COMPLETED; formal_acceptance=false")
if __name__=="__main__":main()
