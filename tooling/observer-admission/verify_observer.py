#!/usr/bin/env python3
"""Offline, data-only owner admission of independent visual evidence.

Two separate Ed25519 signing keys are mandatory:
 * A trusted owner key (supplied OUTSIDE the evidence bundle) admits the exact
   verifier source bytes, method, scope, limitations and active/revoked policy.
 * A different verifier key signs its own bounded frame observation.

The technical gate verifies signatures AND independently decodes the captured PNG bytes.
It NEVER executes submitted verifier source code, installs renderers, confers
Semwright Driver Host permissions, approves creative quality or publishes.
"""
from __future__ import annotations
import argparse
import base64
from contextlib import closing, contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mlt_official_runtime_intake import IntakeError, capture_regular

SCHEMA="motionwright.observation-verifier-admission/1"
POLICY="motionwright.observation-owner-policy/1"
EVIDENCE="motionwright.observation-evidence/1"
REPORT="motionwright.observation-admission-report/1"
HEAD="motionwright.observation-trusted-policy-head/1"
POLICY_STORE_APPLICATION_ID = 0x4D574F50
POLICY_STORE_VERSION = 1
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

def strict_json(raw: bytes, label: str) -> object:
    """Refuse duplicate or non-finite signed JSON values, including nested fields."""
    def unique_pairs(pairs: list[tuple[str, object]]) -> dict:
        parsed: dict = {}
        for key, value in pairs:
            require(key not in parsed, f"Duplicate JSON key in {label}")
            parsed[key] = value
        return parsed

    def invalid_constant(value: str) -> object:
        raise AdmissionRejected(f"Non-finite JSON value in {label}: {value}")

    try:
        return json.loads(raw, object_pairs_hook=unique_pairs,
                          parse_constant=invalid_constant)
    except (ValueError, UnicodeError, TypeError, RecursionError) as error:
        if isinstance(error, AdmissionRejected):
            raise
        raise AdmissionRejected(f"Invalid signed JSON for {label}") from error


def exact(value:object,names:set[str],name:str)->dict:
    require(isinstance(value,dict) and set(value)==names,
            f"{name} has missing or extra fields")
    return value

def canonical(value:dict)->bytes:
    return json.dumps(value,sort_keys=True,separators=(",",":"),
                      ensure_ascii=False,allow_nan=False).encode("utf8")

def link_or_reparse(path:Path)->bool:
    evidence=path.lstat()
    return stat.S_ISLNK(evidence.st_mode) or bool(
        getattr(evidence,"st_file_attributes",0)&stat.FILE_ATTRIBUTE_REPARSE_POINT)

def capture_bytes(path:Path,maximum:int,label:str)->bytes:
    try:
        require(not link_or_reparse(path),f"{label} cannot be linked")
        data,_=capture_regular(path,maximum)
    except (OSError,IntakeError)as error:
        raise AdmissionRejected(f"{label} is missing, changed, or exceeds its byte budget")from error
    require(bool(data),f"{label} cannot be empty")
    return data

def signed_data(path:Path,root:Path,label:str)->dict:
    require(path.parent==root,f"{label} must be an explicit in-bundle file")
    return exact(strict_json(capture_bytes(path,MAX_JSON,label),label),
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

def capture_png(path:Path)->bytes:
    require(path.suffix.lower()==".png","Verifier only admits original PNG source evidence")
    return capture_bytes(path,MAX_PNG,"Observed source PNG")

def png_dimensions(data:bytes)->tuple[int,int]:
    try:
        with Image.open(io.BytesIO(data), formats=["PNG"]) as frame:
            require(frame.format=="PNG" and frame.mode in ("RGB","RGBA"),
                    "Observed source is not a standard RGB(A) PNG")
            width,height=frame.size
            require(1<=width<=4096 and 1<=height<=4096 and width*height<=4096*4096,
                    "Observed source dimensions exceed verified bounds")
            frame.verify()
        with Image.open(io.BytesIO(data), formats=["PNG"]) as frame:
            frame.load()
    except (OSError,ValueError,Image.DecompressionBombError)as error:
        raise AdmissionRejected("Observed source PNG failed independent decoding")from error
    return width,height

def file_under(root:Path,relative:object,label:str)->Path:
    require(isinstance(relative,str) and 1<=len(relative)<=220,
            f"{label} requires bounded source path")
    p=Path(relative)
    require(not p.anchor and p.parts and all(x not in (".","..") for x in p.parts)
            and (os.name!="nt" or ":" not in relative),
            f"{label} path cannot traverse owner root")
    cursor=root
    for part in p.parts:
        cursor=cursor/part
        try:
            require(not link_or_reparse(cursor),f"{label} path cannot contain symlinks or reparse points")
        except OSError as error:
            raise AdmissionRejected(f"{label} path must exist beneath its evidence root")from error
    return cursor

def outside_untrusted_evidence(path: Path, root: Path) -> bool:
    """Trusted source paths cannot be the evidence root or ANY descendant."""
    try:
        lexical = path.absolute()
        resolved = path.resolve(strict=True)
    except OSError:
        return False
    return (lexical != root and root not in lexical.parents
            and resolved != root and root not in resolved.parents)


def verified_policy(envelope: object, owner: bytes) -> dict:
    envelope = exact(envelope, {"payload", "signature_b64"}, "Owner policy envelope")
    policy = exact(envelope["payload"], {
        "schema", "owner_generation", "admitted_ids", "revoked_ids",
    }, "Owner policy descriptor")
    verified_signature(policy, envelope["signature_b64"], owner, "Owner policy")
    require(policy["schema"] == POLICY and type(policy["owner_generation"]) is int
            and 1 <= policy["owner_generation"] <= 1_000_000,
            "Owner policy schema or generation is invalid")
    for field in ("admitted_ids", "revoked_ids"):
        ids = policy[field]
        require(isinstance(ids, list) and len(ids) <= 256
                and all(isinstance(value, str) and ID.fullmatch(value) for value in ids)
                and len(set(ids)) == len(ids),
                "Owner admission/revocation identifiers must be a bounded unique set")
    return policy


def policy_head(policy: dict, envelope: dict) -> dict:
    return {"schema": HEAD, "owner_generation": policy["owner_generation"],
            "canonical_policy_envelope_sha256": hashlib.sha256(canonical(envelope)).hexdigest()}


def read_policy_head(path: Path) -> tuple[dict, str]:
    data = capture_bytes(path, 1024, "Trusted current policy head")
    head = exact(strict_json(data, "Trusted policy head"),
                 {"schema", "owner_generation", "canonical_policy_envelope_sha256"},
                 "Trusted policy head")
    digest = head["canonical_policy_envelope_sha256"]
    require(head["schema"] == HEAD and type(head["owner_generation"]) is int
            and 1 <= head["owner_generation"] <= 1_000_000
            and isinstance(digest, str) and SHA.fullmatch(digest),
            "Trusted owner policy head is malformed")
    return head, hashlib.sha256(data).hexdigest()


@contextmanager
def policy_connection(path: Path, mode: str):
    path = path.absolute()
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        require(mode == "rwc", "A new policy registry requires an existing trusted head")
    else:
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1
                and not link_or_reparse(path),
                "Trusted policy registry must be an ordinary, unlinked SQLite file")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=" + mode, uri=True, timeout=5)) as conn:
            conn.execute("PRAGMA trusted_schema=OFF")
            yield conn
    except sqlite3.Error as error:
        raise AdmissionRejected("The trusted policy registry could not be read or updated") from error


def registry_format(conn: sqlite3.Connection) -> tuple[int, int]:
    return (conn.execute("PRAGMA application_id").fetchone()[0],
            conn.execute("PRAGMA user_version").fetchone()[0])


def current_registry_policy(conn: sqlite3.Connection, owner: bytes) -> tuple[dict, dict]:
    require(registry_format(conn) == (POLICY_STORE_APPLICATION_ID, POLICY_STORE_VERSION),
            "The SQLite file is not a supported trusted policy registry")
    key = conn.execute("SELECT key_sha256 FROM policy_owner WHERE singleton=1").fetchone()
    require(key == (hashlib.sha256(owner).hexdigest(),),
            "The trusted policy registry belongs to a different owner identity")
    row = conn.execute(
        """SELECT generation,
                  CASE WHEN length(envelope) BETWEEN 1 AND ? THEN envelope END
           FROM policies ORDER BY generation DESC LIMIT 1""",
        (MAX_JSON,),
    ).fetchone()
    require(row is not None and isinstance(row[1], bytes),
            "The trusted policy registry has no bounded current policy")
    envelope = strict_json(row[1], "Retained owner policy")
    policy = verified_policy(envelope, owner)
    require(policy["owner_generation"] == row[0],
            "The retained owner policy does not match its registry generation")
    return policy, policy_head(policy, envelope)


def adopt_policy(database: Path, key_file: Path, policy_file: Path,
                 bootstrap_head: Path | None = None) -> int:
    owner = capture_bytes(key_file, 32, "Trusted owner Ed25519 public key")
    require(len(owner) == 32, "Trusted owner Ed25519 raw public key is malformed")
    data = capture_bytes(policy_file, MAX_JSON, "Signed owner policy")
    envelope = strict_json(data, "Signed owner policy")
    policy = verified_policy(envelope, owner)
    head = policy_head(policy, envelope)
    generation = policy["owner_generation"]
    anchor = None
    if bootstrap_head is not None:
        anchor = read_policy_head(bootstrap_head)
        require(anchor[0] == head, "Initial signed policy must match the existing trusted head")

    with policy_connection(database, "rwc" if bootstrap_head is not None else "rw") as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        require(capture_bytes(key_file, 32, "Trusted owner Ed25519 public key") == owner,
                "The owner public key changed during policy adoption")
        if bootstrap_head is not None:
            require(read_policy_head(bootstrap_head) == anchor,
                    "The existing trusted head changed during policy adoption")
        if registry_format(conn) == (0, 0):
            require(bootstrap_head is not None,
                    "A new policy registry requires an existing trusted head")
            require(conn.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone() is None,
                    "The selected SQLite file already has another purpose")
            conn.execute("""CREATE TABLE policy_owner (
                singleton INTEGER PRIMARY KEY CHECK (singleton=1),
                key_sha256 TEXT NOT NULL CHECK (length(key_sha256)=64)
            ) STRICT""")
            conn.execute("""CREATE TABLE policies (
                generation INTEGER PRIMARY KEY CHECK (generation BETWEEN 1 AND 1000000),
                envelope BLOB NOT NULL
            ) STRICT""")
            conn.execute(f"PRAGMA application_id={POLICY_STORE_APPLICATION_ID}")
            conn.execute(f"PRAGMA user_version={POLICY_STORE_VERSION}")
            conn.execute("INSERT INTO policy_owner VALUES (1, ?)",
                         (hashlib.sha256(owner).hexdigest(),))
        else:
            previous, previous_head = current_registry_policy(conn, owner)
            require(generation >= previous["owner_generation"], "The signed policy is stale")
            if generation == previous["owner_generation"]:
                require(head == previous_head, "This policy generation already identifies different bytes")
                return generation
            require(set(previous["revoked_ids"]).issubset(policy["revoked_ids"]),
                    "A new policy cannot remove previous revocations")
        conn.execute("INSERT INTO policies (generation, envelope) VALUES (?, ?)",
                     (generation, data))
    return generation


def trusted_policy_snapshot(path: Path, root: Path, owner: bytes, from_store: bool) -> tuple[dict, str]:
    require(outside_untrusted_evidence(path, root),
            "Trusted current policy head must be provisioned outside the untrusted evidence bundle")
    if not from_store:
        return read_policy_head(path)
    with policy_connection(path, "ro") as conn:
        conn.execute("BEGIN")
        _, head = current_registry_policy(conn, owner)
        return head, head["canonical_policy_envelope_sha256"]



def inspect(
    evidence_root:Path,
    owner_public_key_file:Path,
    trusted_policy_head_file:Path|None,
    verifier_source_file:Path,
    expected_producer_sha256:str,
    *,
    policy_store_file:Path|None=None,
)->dict:
    require(evidence_root.is_dir() and not link_or_reparse(evidence_root),
            "Observation evidence root must be an owner-authorized directory")
    root=evidence_root.resolve(strict=True)
    require(owner_public_key_file.is_file() and
            not owner_public_key_file.is_symlink() and
            outside_untrusted_evidence(owner_public_key_file,root),
            "Trusted owner public key must be separately provisioned outside untrusted evidence root")
    owner_pub=capture_bytes(owner_public_key_file,32,"Trusted owner Ed25519 public key")
    require(len(owner_pub)==32,"Trusted owner Ed25519 raw public key is malformed")
    require((trusted_policy_head_file is None) != (policy_store_file is None),
            "Select exactly one trusted current policy source")
    from_store = policy_store_file is not None
    policy_path = policy_store_file if from_store else trusted_policy_head_file
    trusted = trusted_policy_snapshot(policy_path, root, owner_pub, from_store)
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
    policy=verified_policy(policy_envelope,owner_pub)
    report=exact(evidence_envelope["payload"],{
        "schema","admission_id","verifier_id","verifier_version",
        "method","units","coverage","limitations",
        "producer_id","producer_sha256","source_frame_path",
        "source_frame_sha256","frame_index","width","height"
    },"Observed technical evidence")
    verified_signature(admission,admission_envelope["signature_b64"],owner_pub,"Owner admission")
    require(trusted[0]==policy_head(policy,policy_envelope),
            "Replay of a stale previously signed policy is not authorized by current owner head")
    require(admission["schema"]==SCHEMA and report["schema"]==EVIDENCE,
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
    require(outside_untrusted_evidence(verifier_source_file,root) and
            verifier_source_file.suffix==".py" and
            hashlib.sha256(capture_bytes(verifier_source_file,2*1024*1024,"Verifier code bytes")).hexdigest()
               ==admission["verifier_source_sha256"],
            "Verifier code bytes differ from independently admitted source")
    require(isinstance(admission["owner_generation"],int) and
            not isinstance(admission["owner_generation"],bool)
            and 1<=admission["owner_generation"]<=1_000_000,
            "Owner admission generation invalid")
    require(policy["owner_generation"]>=admission["owner_generation"],
            "Owner policy is from a stale generation")
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
    frame_bytes=capture_png(image)
    observed_sha=hashlib.sha256(frame_bytes).hexdigest()
    require(observed_sha==report["source_frame_sha256"],
            "Source frame bytes differ from signed verifier observation")
    width,height=png_dimensions(frame_bytes)
    require(type(report["width"])is int and type(report["height"])is int and
            (width,height)==(report["width"],report["height"]),
            "Independent PNG decoder disagrees with signed verifier measurement")
    require(outside_untrusted_evidence(owner_public_key_file,root) and
            capture_bytes(owner_public_key_file,32,"Trusted owner Ed25519 public key")==owner_pub and
            trusted_policy_snapshot(policy_path,root,owner_pub,from_store)==trusted,
            "Trusted owner policy changed during independent observation")
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

def policy_admin(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="verify_observer.py adopt-policy")
    parser.add_argument("--trusted-policy-store", type=Path, required=True)
    parser.add_argument("--trusted-owner-key", type=Path, required=True)
    parser.add_argument("--signed-policy", type=Path, required=True)
    parser.add_argument("--bootstrap-head", type=Path)
    args = parser.parse_args(argv)
    generation = adopt_policy(args.trusted_policy_store, args.trusted_owner_key,
                              args.signed_policy, args.bootstrap_head)
    print(json.dumps({"owner_generation": generation}))


def main()->None:
    if sys.argv[1:2] == ["adopt-policy"]:
        try:
            policy_admin(sys.argv[2:])
        except (AdmissionRejected, OSError) as error:
            print("policy-adoption-refused: " + str(error), file=sys.stderr)
            raise SystemExit(2) from error
        return
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--trusted-owner-key",type=Path,required=True)
    policy_source=parser.add_mutually_exclusive_group(required=True)
    policy_source.add_argument("--trusted-policy-head",type=Path)
    policy_source.add_argument("--trusted-policy-store",type=Path)
    parser.add_argument("--admitted-verifier-source",type=Path,required=True)
    parser.add_argument("--expected-producer-sha256",required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    try:
        require(not args.output.exists(),"Observation report cannot overwrite owner evidence")
        answer=inspect(args.root,args.trusted_owner_key,
                       args.trusted_policy_head,args.admitted_verifier_source,
                       args.expected_producer_sha256, policy_store_file=args.trusted_policy_store)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open("x",encoding="utf-8") as report:
            report.write(json.dumps(answer,sort_keys=True,indent=2)+"\n")
        print(json.dumps({"observation":answer["status"],
                          "gate_eligible":answer["gate_eligible"],
                          "creative_approval":"SEPARATE"}))
    except (AdmissionRejected,OSError,ValueError,json.JSONDecodeError)as error:
        print("observation-refused: "+str(error),file=sys.stderr)
        raise SystemExit(2)from error

if __name__=="__main__":
    main()
