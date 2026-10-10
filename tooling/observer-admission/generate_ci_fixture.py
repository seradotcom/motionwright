#!/usr/bin/env python3
"""Generate actual PNG, owner/observer Ed25519 test evidence on disposable CI.

Signing keys are temporary and destroyed when the run ends. The resulting
technical receipt is explicitly NOT a production owner-verifier admission.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys

from test_observer import SignedVerifierTests

def main()->None:
    if os.environ.get("GITHUB_ACTIONS")!="true":
        raise SystemExit("Signed CI synthetic evidence must run on an ephemeral GitHub runner")
    if len(sys.argv)!=2:raise SystemExit("usage: generate_ci_fixture.py NEW_OUTPUT_DIRECTORY")
    target=Path(sys.argv[1])
    if target.exists():raise SystemExit("Refusing to overwrite previously recorded evidence")
    fixture=SignedVerifierTests(methodName="test_separate_owner_and_observer_signatures_plus_actual_png_decode")
    fixture.setUp()
    try:
        passed=fixture.check()
        assert passed["gate_eligible"] and passed["status"]=="TECHNICAL_OBSERVATION_ADMITTED"
        target.mkdir(parents=True)
        original=fixture.image.read_bytes()
        (target/"native-source-frame-000030.png").write_bytes(original)
        # Keep only source pixels and the independently checked result; never
        # include ephemeral keys, credentials, owner private policy or signer.
        passed["ci_test_classification"]="EPHEMERAL_SYNTHETIC_KEYS_NO_REAL_OWNER_AUTHENTICATION"
        passed["gate_eligible"]=False
        passed["status"]="CI_TEST_ONLY_NOT_AN_OWNER_PRODUCTION_ADMISSION"
        passed["creative_quality_approved"]=False
        passed["publication_approved"]=False
        (target/"source-observation.json").write_text(json.dumps(passed,indent=2)+"\n")
        fixture.policy["owner_generation"]=8
        fixture.policy["revoked_ids"]=[fixture.admission["admission_id"]]
        fixture.write_all()
        revoked=fixture.check()
        assert not revoked["gate_eligible"]
        assert revoked["status"]=="REVOKED_RETAIN_HISTORICAL_EVIDENCE"
        (target/"owner-revocation-experiment.json").write_text(json.dumps({
            "schema":"motionwright.observation-ci-revocation/1",
            "original_png_sha256":hashlib.sha256(original).hexdigest(),
            "signed_owner_policy_revoked_current_admission":"VERIFIED",
            "current_gate_eligible":False,
            "historical_observation_bytes_preserved":True,
            "artifact_contains_private_keys":False,
            "trusted_key_provisioning":"EPHEMERAL_CI_ONLY",
            "real_production_host_admission":"NOT_RUN",
        },indent=2)+"\n")
        assert len(list(target.iterdir()))==3
        print(json.dumps({
            "synthetic_independent_observer":"PASS",
            "actual_png_readback":"PASS",
            "future_admission_after_owner_revocation":"DENIED",
            "real_owner_approval":"NOT_RUN"
        }))
    finally:
        fixture.doCleanups()

if __name__=="__main__":main()
