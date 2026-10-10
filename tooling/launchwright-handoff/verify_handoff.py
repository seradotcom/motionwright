#!/usr/bin/env python3
"""Independent receiver-side byte check for immutable Motionwright handoff ZIPs.

No Launchwright Release/Target/Scenario admission, local app execution, IPC,
network transfer, media render, publication rights or source-code evaluation.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import stat
import sys
import zipfile

SCHEMA="motionwright.launchwright-immutable-artifact/1"

class HandoffVerificationError(ValueError):
    pass

def check(condition:bool,why:str)->None:
    if not condition:raise HandoffVerificationError(why)

def item_hash(archive:zipfile.ZipFile,name:str,bound:int)->str:
    descriptor=archive.getinfo(name)
    check(0<descriptor.file_size<=bound,f"Member {name} exceeds byte budget")
    check(not stat.S_ISLNK((descriptor.external_attr >>16)&0xffff),
          f"Member {name} cannot be a symbolic link")
    digest=hashlib.sha256()
    total=0
    with archive.open(name,"r") as content:
        while chunk:=content.read(1024*1024):
            total+=len(chunk)
            check(total<=bound,f"Member {name} streamed too much")
            digest.update(chunk)
    check(total==descriptor.file_size,f"Member {name} changed size during inspection")
    return digest.hexdigest()

def verify_zip(path:Path)->dict:
    check(path.is_file() and not path.is_symlink(),"Candidate handoff must be a regular ZIP")
    check(path.stat().st_size<600*1024*1024,"Handoff ZIP exceeds maximal distribution budget")
    with zipfile.ZipFile(path) as archive:
        all_names=archive.namelist()
        check(len(all_names)==3 and len(set(all_names))==3 and
              "handoff.json" in all_names and "source/motionwright-project.json" in all_names,
              "The ZIP contains unknown, duplicate or missing files")
        video=[item for item in all_names if item.startswith("media/creative-master")]
        check(len(video)==1 and video[0].rsplit(".",1)[-1] in ("mp4","mov","mkv"),
              "The ZIP must include precisely one video master")
        media=video[0]
        check(all(not name.startswith("/") and ".." not in Path(name).parts
                  for name in all_names),"ZIP member path traversal")
        check(archive.getinfo("handoff.json").file_size<=128*1024,"Handoff manifest too large")
        manifest=json.loads(archive.read("handoff.json"))
        check(isinstance(manifest,dict) and manifest.get("schema")==SCHEMA,
              "Unknown immutable handoff manifest")
        check(manifest.get("publication_approved") is False and
              manifest.get("backend_execution_authority") is False and
              manifest.get("launchwright_import_status")=="NOT_PERFORMED",
              "A handoff package must never claim it already published, imported or executed")
        project=manifest.get("native_project")
        master=manifest.get("master_video")
        check(isinstance(project,dict) and isinstance(master,dict),"Missing original source receipts")
        src=project.get("file")
        check(isinstance(src,dict) and
              item_hash(archive,"source/motionwright-project.json",32*1024*1024)==src.get("sha256"),
              "Original Project bytes differ from source receipt")
        check(item_hash(archive,media,512*1024*1024)==master.get("sha256"),
              "Media master bytes differ from original receipt")
        original=json.loads(archive.read("source/motionwright-project.json"))
        check(original.get("id")==project.get("id") and
              original.get("generation")==project.get("generation") and
              original.get("revision")==project.get("revision"),
              "Source Project revision differs from attested manifest")
        binding=manifest.get("handoff_binding")
        check(isinstance(binding,dict) and binding.get("system")=="launchwright" and
              binding.get("direction")=="artifact_output" and
              binding.get("external_kind")=="artifact" and
              binding.get("artifact_sha256")==master["sha256"] and
              binding in original.get("handoffs",[]),
              "Public Launchwright output binding differs from original Project")
        check(manifest.get("timeline_unchanged") is True,
              "Source timeline preservation was not declared")
        return {"schema":"motionwright.launchwright-receiver-inspection/1",
                "source_revision":project["revision"],
                "media_sha256":master["sha256"],
                "project_sha256":src["sha256"],
                "byte_identity":"PASS",
                "launchwright_import":"NOT_RUN",
                "creative_quality_approval":"SEPARATE",
                "publication_approved":False}

def main()->None:
    p=argparse.ArgumentParser()
    p.add_argument("zip_file",type=Path)
    a=p.parse_args()
    try:
        print(json.dumps(verify_zip(a.zip_file),sort_keys=True))
    except (OSError,KeyError,ValueError,json.JSONDecodeError,zipfile.BadZipFile) as error:
        print(f"handoff-receiver-rejected: {error}",file=sys.stderr)
        raise SystemExit(2)from error

if __name__=="__main__":main()
