#!/usr/bin/env python3
"""Offline, data-only owner admission of independent visual evidence.

Two separate Ed25519 signing keys are mandatory:
 * A trusted owner key (supplied OUTSIDE the evidence bundle) admits the exact
   verifier source bytes, method, scope, limitations and active/revoked policy.
 * A different verifier key signs its own bounded frame observation.

The technical gate verifies signatures AND independently reopens PNG bytes.
It NEVER executes submitted verifier source code, installs renderers, confers
Semwright Driver Host permissions, approves creative quality or publishes.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from PIL import Image,UnidentifiedImageError

SCHEMA="motionwright.observation-verifier-admission/1"
POLICY="motionwright.observation-owner-policy/1"
EVIDENCE="motionwright.observation-evidence/1"
REPORT="motionwright.observation-admission-report/1"
HEAD="motionwright.observation-trusted-policy-head/1"
ID=re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
SHA=re.compile(r"^[a-f0-9]{64}$")
MAX_JSON=512*1024
MAX_PNG=32*1024*1024
METHOD="png_dimensions_v1"
UNITS="pixels"
COVERAGE="single_full_frame"
Image.MAX_IMAGE_PIXELS=4096*4096

class AdmissionRejected(ValueError):
    pass

def require(ok:bool,message:str)->None:
    if not ok:raise AdmissionRejected(message)

def exact(value:object,names:set[str],name:str)->dict:
    require(isinstance(value,dict) and set(value)==names,
            f"{name} has missing or extra fields")
    return value

def canonical(value:dict)->bytes:
    return json.dumps(value,sort_keys=True,separators=(",",":"),
                      ensure_ascii=False,allow_nan=False).encode("utf8")

def signed_data(path:Path,root:Path,label:str)->dict:
    require(path.parent==root and path.is_file() and not path.is_symlink() and
            0<path.stat().st_size<=MAX_JSON,
            f"{label} must be an explicit in-bundle bounded regular JSON file")
    return exact(json.loads(path.read_text(encoding="utf8")),
                 {"payload","signature_b64"},label)

def raw_public_key(value:object,label:str)->bytes:
    require(isinstance(value,str) and len(value)<=88,f"{label} public key is malformed")
    try:bits=base64.b64decode(value,validate=True)
    except (ValueError,base64.binascii.Error)as e:
        raise AdmissionRejected(f"{label} public key cannot be decoded")from e
    require(len(bits)==32,f"{label} public key must be Ed25519 raw 32 bytes")
    return bits

def verified_signature(message:dict,signature:object,pub:bytes,label:str)->None:
    require(isinstance(signature,str) and len(signature)<=100,
            f"{label} signature missing")
    try:
        raw=base64.b64decode(signature,validate=True)
        require(len(raw)==64,f"{label} signature byte length invalid")
        Ed25519PublicKey.from_public_bytes(pub).verify(raw,canonical(message))
    except (InvalidSignature,ValueError,base64.binascii.Error) as exc:
        raise AdmissionRejected(f"{label} signature is not authentic")from exc

def fingerprint(path:Path,maximum:int,label:str)->str:
    require(path.is_file() and not path.is_symlink() and
            0<path.stat().st_size<=maximum,
            f"{label} is absent, a link, or exceeds its byte budget")
    digest=hashlib.sha256()
    with path.open("rb")as source:
        while buffer:=source.read(1024*1024):digest.update(buffer)
    return digest.hexdigest()

def png_dimensions(path:Path)->tuple[int,int]:
    require(path.suffix.lower()==".png","Verifier only admits original PNG source evidence")
    fingerprint(path,MAX_PNG,"Observed source PNG")
    try:
        with Image.open(path) as frame:
            require(frame.format=="PNG" and frame.mode in ("RGB","RGBA"),
                    "Observed source is not a standard RGB(A) PNG")
            width,height=frame.size
            require(1<=width<=4096 and 1<=height<=4096 and width*height<=4096*4096,
                    "Observed source dimensions exceed verified bounds")
            frame.verify()
        with Image.open(path) as frame:
            frame.load()
    except (OSError,UnidentifiedImageError,ValueError)as error:
        raise AdmissionRejected("Observed source PNG failed independent decoding")from error
    return width,height

def file_under(root:Path,relative:object,label:str)->Path:
    require(isinstance(relative,str) and 1<=len(relative)<=220,
            f"{label} requires bounded source path")
    p=Path(relative)
    require(not p.is_absolute() and all(x not in (".","..") for x in p.parts),
            f"{label} path cannot traverse owner root")
    cursor=root
    for part in p.parts:
        cursor=cursor/part
        require(not cursor.is_symlink(),f"{label} path cannot contain symlinks")
    return cursor

def inspect(
    evidence_root:Path,
    owner_public_key_file:Path,
    trusted_policy_head_file:Path,
    verifier_source_file:Path,
    expected_producer_sha256:str,
)->dict:
    require(evidence_root.is_dir() and not evidence_root.is_symlink(),
            "Observation evidence root must be an owner-authorized directory")
    root=evidence_root.resolve(strict=True)
    require(owner_public_key_file.is_file() and
            not owner_public_key_file.is_symlink() and
            owner_public_key_file.resolve(strict=True).parent!=root,
            "Trusted owner public key must be separately provisioned outside untrusted evidence root")
    require(owner_public_key_file.stat().st_size==32,
            "Trusted owner Ed25519 raw public key has invalid bytes")
    owner_pub=owner_public_key_file.read_bytes()
    require(len(owner_pub)==32,"Trusted owner Ed25519 raw public key is malformed")
    require(trusted_policy_head_file.is_file() and
            not trusted_policy_head_file.is_symlink() and
            trusted_policy_head_file.parent.resolve(strict=True)!=root and
            0<trusted_policy_head_file.stat().st_size<=1024,
            "Trusted current policy head must be provisioned outside the untrusted evidence bundle")
    trusted=exact(json.loads(trusted_policy_head_file.read_text(encoding="utf8")),
                  {"schema","owner_generation","canonical_policy_envelope_sha256"},
                  "Trusted policy head")
    require(trusted["schema"]==HEAD and
            type(trusted["owner_generation"])is int and
            1<=trusted["owner_generation"]<=1_000_000 and
            isinstance(trusted["canonical_policy_envelope_sha256"],str) and
            SHA.fullmatch(trusted["canonical_policy_envelope_sha256"]),
            "Trusted owner policy head is malformed")
    require(isinstance(expected_producer_sha256,str)and SHA.fullmatch(expected_producer_sha256),
            "An explicit SHA-bound producer version must be supplied by the caller")
    admission_envelope=signed_data(root/"owner-admission.json",root,"Owner admission")
    policy_envelope=signed_data(root/"owner-policy.json",root,"Owner policy")
    evidence_envelope=signed_data(root/"observed-frame.json",root,"Verifier observation")
    admission=exact(admission_envelope["payload"],{
        "schema","admission_id","verifier_id","verifier_version",
        "verifier_source_sha256","verifier_public_key_b64",
        "method","units","coverage","limitations","owner_generation",
        "permission"
    },"Owner admission descriptor")
    policy=exact(policy_envelope["payload"],{
        "schema","owner_generation","admitted_ids","revoked_ids"
    },"Owner policy descriptor")
    report=exact(evidence_envelope["payload"],{
        "schema","admission_id","verifier_id","verifier_version",
        "method","units","coverage","limitations",
        "producer_id","producer_sha256","source_frame_path",
        "source_frame_sha256","frame_index","width","height"
    },"Observed technical evidence")
    verified_signature(admission,admission_envelope["signature_b64"],owner_pub,"Owner admission")
    verified_signature(policy,policy_envelope["signature_b64"],owner_pub,"Owner policy")
    # The separately trusted head is a compare-and-swap anchor controlled by
    # the owner. Replaying an earlier validly signed policy cannot remove a
    # revocation or resurrect an old verifier permission.
    require(trusted["owner_generation"]==policy["owner_generation"] and
            hashlib.sha256(canonical(policy_envelope)).hexdigest()==
                trusted["canonical_policy_envelope_sha256"],
            "Replay of a stale previously signed policy is not authorized by current owner head")
    require(admission["schema"]==SCHEMA and policy["schema"]==POLICY and
            report["schema"]==EVIDENCE,
            "Verifier, policy or evidence schema is not the admitted version")
    for label in ("admission_id","verifier_id"):
        require(isinstance(admission[label],str) and ID.fullmatch(admission[label]),
                f"{label} is not a canonical verifier identifier")
    require(admission["verifier_version"]=="1.0.0" and
            admission["method"]==METHOD and admission["units"]==UNITS and
            admission["coverage"]==COVERAGE and
            admission["permission"]=="observe_png_dimensions_only",
            "Verifier requested unknown measurements or executable powers")
    require(isinstance(admission["verifier_source_sha256"],str)
            and SHA.fullmatch(admission["verifier_source_sha256"]),
            "Verifier code was not pinned by SHA")
    require(verifier_source_file.parent!=root and
            verifier_source_file.suffix==".py" and
            fingerprint(verifier_source_file,2*1024*1024,"Owner-admitted verifier source")
               ==admission["verifier_source_sha256"],
            "Verifier code bytes differ from independently admitted source")
    require(isinstance(admission["owner_generation"],int) and
            not isinstance(admission["owner_generation"],bool)
            and 1<=admission["owner_generation"]<=1_000_000,
            "Owner admission generation invalid")
    require(isinstance(policy["owner_generation"],int) and
            not isinstance(policy["owner_generation"],bool)
            and policy["owner_generation"]>=admission["owner_generation"],
            "Owner policy is from a stale generation")
    for field in ("admitted_ids","revoked_ids"):
        value=policy[field]
        require(isinstance(value,list) and len(value)<=256 and
                all(isinstance(x,str) and ID.fullmatch(x)for x in value) and
                len(set(value))==len(value),
                "Owner admission/revocation identifiers must be a bounded unique set")
    require(admission["admission_id"] in policy["admitted_ids"],
            "Independent owner policy did not admit this verifier identity")
    limitations=admission["limitations"]
    require(isinstance(limitations,list) and 1<=len(limitations)<=10 and
            all(isinstance(s,str) and 1<=len(s)<=350 for s in limitations),
            "Verifier method limitations must be explicit and bounded")
    verifier_pub=raw_public_key(admission["verifier_public_key_b64"],"Independent verifier")
    require(verifier_pub!=owner_pub,"Owner and observer must use distinct signing identities")
    verified_signature(report,evidence_envelope["signature_b64"],verifier_pub,
                       "Independent verifier observation")
    require(report["admission_id"]==admission["admission_id"] and
            report["verifier_id"]==admission["verifier_id"] and
            report["verifier_version"]==admission["verifier_version"] and
            report["method"]==admission["method"] and
            report["units"]==admission["units"] and
            report["coverage"]==admission["coverage"] and
            report["limitations"]==limitations,
            "Verifier observation is outside its separately approved method/units/coverage/limits")
    require(isinstance(report["producer_id"],str) and ID.fullmatch(report["producer_id"])
            and report["producer_id"]!=report["verifier_id"] and
            report["producer_sha256"]==expected_producer_sha256,
            "A renderer cannot self-certify or substitute another producer revision")
    require(type(report["frame_index"])is int and 0<=report["frame_index"]<=3600,
            "Frame evidence has an invalid source timeline index")
    image=file_under(root,report["source_frame_path"],"Source PNG")
    observed_sha=fingerprint(image,MAX_PNG,"Source PNG")
    require(observed_sha==report["source_frame_sha256"],
            "Source frame bytes differ from signed verifier observation")
    width,height=png_dimensions(image)
    require(type(report["width"])is int and type(report["height"])is int and
            (width,height)==(report["width"],report["height"]),
            "Independent PNG decoder disagrees with signed verifier measurement")
    revoked=admission["admission_id"] in policy["revoked_ids"]
    return {
        "schema":REPORT,
        "admission_id":admission["admission_id"],
        "verifier_id":admission["verifier_id"],
        "source_frame_sha256":observed_sha,
        "producer_sha256":expected_producer_sha256,
        "method":METHOD,"version":admission["verifier_version"],
        "units":UNITS,"coverage":COVERAGE,"limitations":limitations,
        "measured_pixels":{"width":width,"height":height},
        "owner_signed_admission":"VERIFIED",
        "independent_verifier_signed_observation":"VERIFIED",
        "actual_source_png_decode":"VERIFIED",
        "gate_eligible":not revoked,
        "status":"REVOKED_RETAIN_HISTORICAL_EVIDENCE" if revoked else "TECHNICAL_OBSERVATION_ADMITTED",
        "historical_evidence_preserved":True,
        "producer_execution_granted":False,
        "verifier_source_code_executed":False,
        "creative_quality_approved":False,
        "publication_approved":False
    }

def main()->None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--trusted-owner-key",type=Path,required=True)
    parser.add_argument("--trusted-policy-head",type=Path,required=True)
    parser.add_argument("--admitted-verifier-source",type=Path,required=True)
    parser.add_argument("--expected-producer-sha256",required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    try:
        require(not args.output.exists(),"Observation report cannot overwrite owner evidence")
        answer=inspect(args.root,args.trusted_owner_key,
                       args.trusted_policy_head,args.admitted_verifier_source,
                       args.expected_producer_sha256)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(answer,sort_keys=True,indent=2)+"\n")
        print(json.dumps({"observation":answer["status"],
                          "gate_eligible":answer["gate_eligible"],
                          "creative_approval":"SEPARATE"}))
    except (AdmissionRejected,OSError,ValueError,json.JSONDecodeError)as error:
        print("observation-refused: "+str(error),file=sys.stderr)
        raise SystemExit(2)from error

if __name__=="__main__":
    main()
