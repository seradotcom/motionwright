#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REQ=ROOT/"docs"/"acceptance"/"requirements-status.json"
ACC=ROOT/"docs"/"acceptance"/"acceptance-status.json"
IMPLEMENTATION={"EVIDENCE_PRESENT","PARTIAL_OR_GAP","BLOCKED_UPSTREAM","OPTIONAL_UNCLAIMED","VERIFIED"}
VERIFICATION={"CI_PARTIAL","NOT_RUN","BLOCKED_UPSTREAM","VERIFIED"}
ACCEPTANCE={"NOT_RUN","PASS","FAIL","BLOCKED"}

def load(path):
    return json.loads(path.read_text(encoding="utf-8"))

def fail(message):
    raise SystemExit("acceptance-policy: "+message)

requirements=load(REQ)
acceptance=load(ACC)
items=requirements.get("requirements",[])
expected_req={f"CR-{area}-{n:02d}" for area in ("FOUND","PROJECT","NARR","AUDIO","UX","TIME","CANVAS","CONCEPT","STYLE","CHANGE","SDK","MOTION","MLT","BLENDER","MANIM","GRAPH","EFFECT","JOBS","MODELS","WORKFLOW","VARIANT","SEC","DELIVER","PERF","PLATFORM","EXT") for n in range(1,9)}
ids=[item.get("id") for item in items]
if len(items)!=208 or set(ids)!=expected_req or len(ids)!=len(set(ids)):
    fail("requirement ledger must contain exactly the 208 canonical IDs once each")
for item in items:
    impl=item.get("implementation_assessment")
    ver=item.get("verification_status")
    evidence=item.get("evidence",[])
    if impl not in IMPLEMENTATION:
        fail(f"{item['id']} has invalid implementation status {impl!r}")
    if ver not in VERIFICATION:
        fail(f"{item['id']} has invalid verification status {ver!r}")
    if not isinstance(evidence,list):
        fail(f"{item['id']} evidence must be a list")
    for pointer in evidence:
        if not isinstance(pointer,str) or not pointer:
            fail(f"{item['id']} has an invalid evidence pointer")
        if not (ROOT/pointer).exists():
            fail(f"{item['id']} points to missing in-repo evidence: {pointer}")
    if (impl=="VERIFIED" or ver=="VERIFIED") and not evidence:
        fail(f"{item['id']} cannot be VERIFIED without evidence")
cases=acceptance.get("cases",[])
expected_acc={f"ACC-{n:03d}" for n in range(1,61)}
case_ids=[case.get("id") for case in cases]
if len(cases)!=60 or set(case_ids)!=expected_acc or len(case_ids)!=len(set(case_ids)):
    fail("acceptance ledger must contain exactly the 60 canonical IDs once each")
for case in cases:
    status=case.get("status")
    evidence=case.get("evidence",[])
    if status not in ACCEPTANCE:
        fail(f"{case['id']} has invalid acceptance status {status!r}")
    if not isinstance(evidence,list):
        fail(f"{case['id']} evidence must be a list")
    if status=="PASS" and (not case.get("independent") or not evidence):
        fail(f"{case['id']} PASS requires independent review and evidence")
print(f"acceptance-policy=ok requirements={len(items)} acceptance_cases={len(cases)} product_pass={sum(case['status']=='PASS' for case in cases)}")
