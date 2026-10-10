#!/usr/bin/env python3
"""Immutable, owner-reviewed Motionwright -> Launchwright artifact handoff.

Only packs an already-exported native Project snapshot, an existing video and
a source-bound public Launchwright HandoffBinding. No rendering, backend
duplication, Launchwright private storage, network or publish authority.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile
from verify_handoff import verify_zip, HandoffVerificationError

SCHEMA="motionwright.launchwright-handoff-request/1"
OUT_SCHEMA="motionwright.launchwright-immutable-artifact/1"
HEX=re.compile(r"^[0-9a-f]{64}$")
UUID=re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MAX_MASTER=512*1024*1024
MAX_PROJECT=32*1024*1024
MAX_REQUEST=512*1024

class Refused(ValueError):
    pass

def require(condition:bool,message:str)->None:
    if not condition:raise Refused(message)

def exact_fields(value:object,fields:set[str],name:str)->dict:
    require(isinstance(value,dict) and set(value)==fields,
            f"{name} must contain exactly {sorted(fields)}")
    return value

def relative_file(root:Path,reference:object,budget:int,name:str)->tuple[Path,dict]:
    ref=exact_fields(reference,{"path","sha256"},name)
    require(isinstance(ref["path"],str) and 1<=len(ref["path"])<=240
            and isinstance(ref["sha256"],str) and HEX.fullmatch(ref["sha256"]) is not None,
            f"{name} must be a pinned relative file and digest")
    rel=Path(ref["path"])
    require(not rel.is_absolute() and ".." not in rel.parts and "." not in rel.parts
            and rel.suffix.lower() in (".json",".mp4",".mov",".mkv"),
            f"{name} path is unsupported or escaped")
    file=root
    for part in rel.parts:
        file=file/part
        require(not file.is_symlink(),f"{name} contains a symlink")
    require(file.is_file(),f"{name} file missing")
    size=file.stat().st_size
    require(0<size<=budget,f"{name} exceeds its byte budget")
    digest=hashlib.sha256()
    with file.open("rb")as handle:
        while block:=handle.read(1024*1024):
            digest.update(block)
    require(digest.hexdigest()==ref["sha256"],f"{name} content changed from its admitted SHA")
    return file,{"path":ref["path"],"sha256":digest.hexdigest(),"bytes":size}

def identity(value:object,name:str)->str:
    require(isinstance(value,str) and UUID.fullmatch(value.lower()) is not None,
            f"{name} must be an exact UUID")
    return value.lower()

def validate(request:dict,root:Path)->tuple[dict,Path,Path]:
    require(not root.is_symlink() and root.is_dir(),"Artifact root must be an owner-controlled directory")
    root=root.resolve(strict=True)
    r=exact_fields(request,{"schema","mode","project_snapshot","master_video",
             "expected_project","handoff_binding_id","asset_rights","claim_reviews","owner_review"}, "Handoff")
    require(r["schema"]==SCHEMA,"Unknown immutable handoff request schema")
    require(r["mode"] in ("real_work","synthetic_fixture"),"Unknown immutable handoff mode")
    pfile,pref=relative_file(root,r["project_snapshot"],MAX_PROJECT,"Project snapshot")
    vfile,vref=relative_file(root,r["master_video"],MAX_MASTER,"Approved master")
    require(vfile.suffix.lower() in (".mp4",".mov",".mkv"),"Master requires a video container")
    with vfile.open("rb")as handle:
        header=handle.read(24)
    require((len(header)>=12 and header[4:8]==b"ftyp")
            or (vfile.suffix.lower()==".mkv" and header[:4]==b"\x1aE\xdf\xa3"),
            "Master lacks a bounded MOV/MP4/Matroska container signature")
    require(pfile.suffix.lower()==".json","Project snapshot must be the original exported native JSON")
    project=json.loads(pfile.read_text(encoding="utf8"))
    require(isinstance(project,dict),"Project snapshot is not a native project object")
    identity_project=exact_fields(r["expected_project"],{"id","generation","revision"},"Project revision")
    for name in ("id","generation"):
        require(identity(project.get(name),f"Current project {name}")==identity(identity_project[name],f"Requested {name}"),
                "The supplied project snapshot belongs to another project/generation")
    revision=identity_project["revision"]
    require(type(revision) is int and 0<=revision<1_000_000_000
            and project.get("revision")==revision,"Project revision is stale or malformed")
    require(type(project.get("schema_version")) is int,"Project requires the native schema version")
    binding_id=identity(r["handoff_binding_id"],"Handoff ID")
    bindings=project.get("handoffs")
    require(isinstance(bindings,list),"No native external handoff registry exists")
    matches=[item for item in bindings if isinstance(item,dict)
             and str(item.get("id","")).lower()==binding_id]
    require(len(matches)==1,"Exactly one project-owned Launchwright handoff binding is required")
    binding=matches[0]
    require(binding.get("system")=="launchwright"
            and binding.get("direction")=="artifact_output"
            and binding.get("external_kind")=="artifact"
            and binding.get("artifact_sha256")==vref["sha256"],
            "Handoff binding is not an approved exact media artifact reference")
    require(isinstance(binding.get("external_id"),str) and binding["external_id"].strip()
            and len(binding["external_id"])<=256,
            "Handoff destination must be one explicit public artifact reference")
    require(isinstance(binding.get("local_resource"),str)
            and project["id"] in binding["local_resource"],
            "Handoff local resource is not bound to this specific native project")
    plan=project.get("production_design",{}).get("plan")
    require(isinstance(plan,dict),"A production plan must exist before handoff")
    approval=plan.get("approval")
    require(isinstance(approval,dict) and
            isinstance(approval.get("content_sha256"),str)
            and HEX.fullmatch(approval["content_sha256"]) is not None,
            "ProductionPlan is missing exact source-bound human approval")
    owner=exact_fields(r["owner_review"],{"reviewer","approval_content_sha256",
            "creative_approved","publication_requested"}, "Owner review")
    require(owner["creative_approved"] is True and owner["publication_requested"] is False,
            "Handoff requires human creative approval but cannot request publication")
    require(isinstance(owner["reviewer"],str) and 1<=len(owner["reviewer"].strip())<=200
            and owner["reviewer"]==approval.get("reviewer")
            and owner["approval_content_sha256"]==approval["content_sha256"],
            "Owner review is not bound to the exact approved ProductionPlan")
    scenes=project.get("scenes",[])
    require(isinstance(scenes,list) and scenes,"Native project has no authored scenes")
    shot_list=plan.get("shots")
    require(isinstance(shot_list,list) and 1<=len(shot_list)<=256,
            "Handoff requires at least one specifically authored shot")
    scene_ids={item.get("id") for item in scenes if isinstance(item,dict)}
    require(all(isinstance(shot,dict) and shot.get("scene_id") in scene_ids for shot in shot_list),
            "ProductionPlan contains an absent scene")
    actual_assets=project.get("assets",[])
    require(isinstance(actual_assets,list),"Project source asset ledger invalid")
    assets={item.get("id"):item for item in actual_assets if isinstance(item,dict)}
    covered={asset_id for shot in shot_list for asset_id in shot.get("asset_ids",[])}
    rights=r["asset_rights"]
    require(isinstance(rights,list) and len(rights)<=128,"Rights declarations exceed budget")
    rights_ids=set()
    for entry in rights:
        a=exact_fields(entry,{"asset_id","sha256","owner","usage_rights","authorized"}, "Asset rights")
        require(a["asset_id"] in covered and a["asset_id"] not in rights_ids,
                "Rights declaration is missing an actual independently referenced project asset")
        rights_ids.add(a["asset_id"])
        require(a["asset_id"] in assets and
                assets[a["asset_id"]].get("content_sha256")==a["sha256"]
                and isinstance(a["sha256"],str) and HEX.fullmatch(a["sha256"]) is not None,
                "Source media digest does not match the native asset ledger")
        require(a["authorized"] is True
                and all(isinstance(a[key],str) and 1<=len(a[key].strip())<=500 for key in ("owner","usage_rights")),
                "Source rights are not affirmatively documented")
    require(covered==rights_ids,"Each shot source must have an exact rights declaration")
    claim_list=project.get("brief",{}).get("claims",[])
    require(isinstance(claim_list,list) and len(claim_list)<=512,"Native claim ledger invalid")
    claims={item.get("id"):item for item in claim_list if isinstance(item,dict)}
    needed={claim for shot in shot_list for claim in shot.get("claim_ids",[])}
    reviews=r["claim_reviews"]
    require(isinstance(reviews,list) and len(reviews)<=128,"Claims review list exceeds budget")
    reviewed=set()
    for item in reviews:
        c=exact_fields(item,{"claim_id","verified","source_note"},"Claim review")
        require(c["claim_id"] in needed and c["claim_id"] not in reviewed,
                "Claim review references a duplicate or unplanned assertion")
        reviewed.add(c["claim_id"])
        require(c["verified"] is True and c["claim_id"] in claims and
                claims[c["claim_id"]].get("source") is not None,
                "No independently sourced claim verification is provided")
        require(isinstance(c["source_note"],str) and 1<=len(c["source_note"].strip())<=1200,
                "Claim verification requires an explicit original-source note")
    require(needed==reviewed,"Every claim displayed in the approved plan must be reviewed")
    manifest={
        "schema":OUT_SCHEMA,
        "scope":"exact_native_motionwright_revision_and_existing_public_launchwright_binding",
        "mode":r["mode"],"native_project":{
            "id":project["id"],"generation":project["generation"],"revision":revision,
            "schema_version":project["schema_version"],"file":pref},
        "master_video":vref,
        "handoff_binding":binding,
        "source_approval":{"reviewer":owner["reviewer"],
                           "production_plan_sha256":approval["content_sha256"],
                           "human_creative_approval_attested":True},
        "assets":[{"asset_id":item["asset_id"],"sha256":item["sha256"],
                   "owner":item["owner"],"usage_rights":item["usage_rights"]}
                  for item in rights],
        "claims":[{"claim_id":item["claim_id"],"source_note":item["source_note"]}
                  for item in reviews],
        "timeline_unchanged":True,
        "launchwright_import_status":"NOT_PERFORMED",
        "publication_approved":False,
        "backend_execution_authority":False,
        "creative_quality_independently_verified":False,
        "technical_media_decode_verified":False,
    }
    return manifest,pfile,vfile

def package(request:dict,root:Path,output:Path)->dict:
    require(not output.exists(),"Refusing to overwrite a handoff or prior approval evidence")
    manifest,pfile,vfile=validate(request,root)
    output.parent.mkdir(parents=True,exist_ok=True)
    members=(("source/motionwright-project.json",pfile),
             ("media/creative-master"+vfile.suffix.lower(),vfile))
    with zipfile.ZipFile(output,"x",compression=zipfile.ZIP_STORED,allowZip64=True)as archive:
        for name,original in members:
            item=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0))
            item.external_attr=0o644<<16
            item.compress_type=zipfile.ZIP_STORED
            with original.open("rb")as inp,archive.open(item,"w")as dest:
                shutil.copyfileobj(inp,dest,1024*1024)
        info=zipfile.ZipInfo("handoff.json",date_time=(2026,1,1,0,0,0))
        info.external_attr=0o644<<16
        archive.writestr(info,json.dumps(manifest,sort_keys=True,indent=2).encode()+b"\n")
    # Independently inspect the actual archived bytes before exposing the
    # candidate. This catches any input-file replacement between hash admission
    # and ZIP streaming, without trusting path names or a source-provided receipt.
    try:
        receiver=verify_zip(output)
        require(receiver["byte_identity"]=="PASS",
                "Source/receiver handoff SHA verification failed")
    except (HandoffVerificationError,OSError,KeyError,ValueError,zipfile.BadZipFile)as error:
        output.unlink(missing_ok=True)
        raise Refused("Receiver validation rejected the immutable handoff bytes") from error
    digest=hashlib.sha256()
    with output.open("rb")as data:
        while chunk:=data.read(1024*1024):digest.update(chunk)
    return {"status":"SYNTHETIC_HANDOFF_PROTOCOL_ONLY" if request["mode"]=="synthetic_fixture"
            else "SOURCE_BOUND_LAUNCHWRIGHT_IMPORT_PENDING",
            "archive_sha256":digest.hexdigest(),"archive_bytes":output.stat().st_size,
            "creative_approval":"OWNER_RECORDED_NOT_AUTHENTICATED",
            "publication_approved":False}

def main()->None:
    parser=argparse.ArgumentParser()
    parser.add_argument("request",type=Path)
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    try:
        require(args.request.is_file() and not args.request.is_symlink()
                and args.request.stat().st_size<=MAX_REQUEST,"Handoff request missing or oversized")
        request=json.loads(args.request.read_text(encoding="utf8"))
        print(json.dumps(package(request,args.root,args.output),sort_keys=True))
    except (OSError,Refused,json.JSONDecodeError)as error:
        print(f"handoff-refused: {error}",file=sys.stderr)
        raise SystemExit(2)from error
if __name__=="__main__":main()
