"""Negative and positive controls for the closed Claim observer; no Rust execution."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tooling import ci_claim_source_causal as gate

class ClaimSelectionTests(unittest.TestCase):
    def context(self):
        return {"GITHUB_ACTIONS":"true","GITHUB_EVENT_NAME":"workflow_dispatch",
                "MW_CLAIM_SCOPE":"claim_causal_red","GITHUB_REPOSITORY":"seradotcom/motionwright",
                "GITHUB_JOB":"claim-causal-red","GITHUB_SHA":"a"*40,"GITHUB_WORKFLOW_SHA":"a"*40,
                "GITHUB_RUN_ID":"123","GITHUB_RUN_ATTEMPT":"1","RUNNER_OS":"Linux",
                "RUNNER_ARCH":"X64","GITHUB_WORKFLOW_REF":
                "seradotcom/motionwright/.github/workflows/native-production.yml@refs/heads/verify/claim"}
    def test_explicit_linux_original_attempt_binds_actual_workflow(self):
        result=gate.execution_context(self.context(),"linux")
        self.assertEqual(result["GITHUB_SHA"],"a"*40)
        self.assertEqual(result["GITHUB_RUN_ATTEMPT"],"1")
    def test_other_platform_event_repository_job_or_scope_cannot_execute(self):
        for key,value in (("RUNNER_OS","Windows"),("RUNNER_ARCH","ARM64"),
                          ("GITHUB_EVENT_NAME","push"),("GITHUB_ACTIONS","false"),
                          ("MW_CLAIM_SCOPE","full"),("GITHUB_REPOSITORY","other/repo"),
                          ("GITHUB_JOB","production-contracts")):
            with self.subTest(key=key),self.assertRaises(ValueError):
                env=self.context();env[key]=value;gate.execution_context(env,"linux")
        with self.assertRaises(ValueError):gate.execution_context(self.context(),"win32")
    def test_retry_is_refused_without_attempt_relabel(self):
        for value in ("2","01","0","",1,True):
            env=self.context();env["GITHUB_RUN_ATTEMPT"]=value
            with self.subTest(value=value),self.assertRaises(ValueError):
                gate.execution_context(env,"linux")
    def test_product_sources_cannot_impersonate_workflow_source(self):
        for value in (gate.BASE,gate.RED,gate.GREEN):
            env=self.context();env["GITHUB_SHA"]=env["GITHUB_WORKFLOW_SHA"]=value
            with self.subTest(value=value),self.assertRaises(ValueError):
                gate.execution_context(env,"linux")
    def test_mismatched_workflow_source_or_foreign_file_is_rejected(self):
        for key,value in (("GITHUB_SHA","b"*40),("GITHUB_WORKFLOW_SHA","short"),
                          ("GITHUB_WORKFLOW_REF","seradotcom/motionwright/.github/workflows/ci.yml@main")):
            env=self.context();env[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                gate.execution_context(env,"linux")
    def test_frozen_counts_and_negative_members_are_exact(self):
        self.assertEqual(len(set(gate.DOMAIN_CASES)),7)
        self.assertEqual(len(set(gate.SERVICE_CASES)),8)
        self.assertEqual(set(gate.NEGATIVES),{"domain","service"})
        for kind,name in gate.NEGATIVES.items():self.assertIn(name,gate.CASES[kind])
    def test_assertion_line_requires_original_test_bytes(self):
        with self.assertRaises(ValueError):gate.assertion_line(b"assert!(false);",gate.DOMAIN)
    def test_assertion_site_is_counted_and_ambiguous_site_refused(self):
        raw=b"line\n    assert!(\n        project.apply_change(change).is_err(),\n        msg\n    );\n"
        with patch.dict(gate.PINS,{gate.DOMAIN:hashlib.sha256(raw).hexdigest()}):
            self.assertEqual(gate.assertion_line(raw,gate.DOMAIN),2)
        duplicate=raw+raw
        with patch.dict(gate.PINS,{gate.DOMAIN:hashlib.sha256(duplicate).hexdigest()}):
            with self.assertRaises(ValueError):gate.assertion_line(duplicate,gate.DOMAIN)

class ClaimBinaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).absolute()
        self.repo=self.root/"repo";self.repo.mkdir()
        self.target=self.root/"target";self.target.mkdir()
        self.binary=self.target/"selected";self.binary.write_bytes(b"synthetic identity fixture")
    def messages(self,kind="domain"):
        spec=gate.SPECS[kind]
        return [{"reason":"compiler-artifact","executable":str(self.binary),
                 "manifest_path":str(self.repo/("crates/"+kind+"/Cargo.toml")),
                 "target":{"name":"claim_source_edit","kind":["test"],"src_path":str(self.repo/spec["source"])},
                 "profile":{"test":True}},
                {"reason":"build-finished","success":True}]
    def raw(self,rows):return ("\n".join(json.dumps(x) for x in rows)+"\n").encode()
    def select(self,rows,code=0,kind="domain"):
        return gate.select_binary(self.raw(rows),code,self.repo,self.target,gate.SPECS[kind])
    def test_domain_and_service_binary_bind_their_actual_manifest_and_source(self):
        for kind in ("domain","service"):
            actual=self.select(self.messages(kind),kind=kind)
            self.assertEqual(actual["sha256"],hashlib.sha256(self.binary.read_bytes()).hexdigest())
    def test_compiler_failure_bool_zero_or_missing_completion_are_not_red(self):
        for code in (101,1,False,None):
            with self.subTest(code=code),self.assertRaises(ValueError):self.select(self.messages(),code)
        with self.assertRaises(ValueError):self.select(self.messages()[:1])
    def test_wrong_source_or_package_rejected_even_with_matching_binary_hash(self):
        for field,value in (("manifest_path",str(self.repo/"crates/storage/Cargo.toml")),
                            ("src_path",str(self.repo/"crates/service/tests/claim_source_edit.rs"))):
            rows=self.messages()
            if field=="src_path":rows[0]["target"][field]=value
            else:rows[0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):self.select(rows)
    def test_missing_duplicate_or_non_test_artifact_is_rejected(self):
        rows=self.messages()
        for candidate in (rows[1:],rows+[rows[0]]):
            with self.assertRaises(ValueError):self.select(candidate)
        rows[0]["profile"]["test"]=False
        with self.assertRaises(ValueError):self.select(rows)
    def test_binary_outside_owned_target_or_empty_binary_is_rejected(self):
        other=self.root/"outside";other.write_bytes(b"x")
        rows=self.messages();rows[0]["executable"]=str(other)
        with self.assertRaises(ValueError):self.select(rows)
        self.binary.write_bytes(b"")
        with self.assertRaises(ValueError):self.select(self.messages())
    def test_duplicate_cargo_json_keys_are_rejected(self):
        raw=b'{"reason":"build-finished","success":true,"success":false}\n'
        with self.assertRaises(ValueError):
            gate.select_binary(raw,0,self.repo,self.target,gate.SPECS["domain"])
    def test_unreviewed_target_cannot_be_injected(self):
        spec=dict(gate.SPECS["domain"]);spec["package"]="other"
        with self.assertRaises(ValueError):gate.select_binary(self.raw(self.messages()),0,self.repo,self.target,spec)

class ClaimCausalCustodyTests(unittest.TestCase):
    def fixture(self,red=True):
        name=gate.NEGATIVES["domain"] if red else gate.DOMAIN_CASES[0]
        role="baseline" if red else "repair"
        pair={"id":"claim-source-membership","failures":{gate.NEGATIVES["domain"]:{
              "source":gate.DOMAIN,"line":58,"markers":["must be rejected: UpdateClaim","source: Some(Asset { asset_id:"]}},
              "baseline":{"targets":{"domain":[gate.NEGATIVES["domain"]]}},
              "repair":{"targets":{"domain":list(gate.DOMAIN_CASES)}}}
        binary={"absolute_path":"/owned/test","sha256":"c"*64}
        context={"product_source_sha":gate.RED if red else gate.GREEN,"role":role}
        row={"argv":gate.observer.case_argv(binary,pair,role,name),"context":context,
             "exit_code":101 if red else 0,"observation":"DIRECT_PROCESS_EXIT_OBSERVED",
             "log_custody":"VERIFIED_SNAPSHOT"}
        state="FAILED" if red else "ok"
        text="running 1 test\ntest "+name+" ... "+state+"\n"
        if red:text+="\nthread '"+name+"' panicked at "+gate.DOMAIN+":58:5:\nProposal must be rejected: UpdateClaim { source: Some(Asset { asset_id: synthetic }) }\n"
        text+="test result: "+state+". "+("0 passed; 1 failed" if red else "1 passed; 0 failed")+"; 0 ignored; 0 measured; 6 filtered out; finished in 0.01s\n"
        roster={"all":gate.DOMAIN_CASES,"selected":pair[role]["targets"]["domain"]}
        return pair,role,name,text.encode(),row,binary,copy.deepcopy(binary),roster,context
    def test_named_causal_assertion_and_exact_passing_control_use_existing_observer(self):
        for red in (True,False):
            result=gate.observer.admit_case(*self.fixture(red))
            self.assertEqual(result["status"],"EXPECTED_CAUSAL_RED" if red else "PASS")
    def test_wrong_assertion_site_or_missing_asset_marker_cannot_count_as_red(self):
        for old,new in ((b":58:5:",b":59:5:"),(b"source: Some(Asset { asset_id:",b"other failure")):
            args=list(self.fixture());args[3]=args[3].replace(old,new)
            with self.assertRaises(ValueError):gate.observer.admit_case(*args)
    def test_signal_setup_failure_skips_duplicates_or_missing_cases_are_not_red(self):
        args=list(self.fixture());args[4]["exit_code"]=-9
        with self.assertRaises(ValueError):gate.observer.admit_case(*args)
        for transform in (lambda b:b"linker error",lambda b:b.replace(b"0 ignored",b"1 ignored"),
                          lambda b:b+b,lambda b:b.replace(b" ... FAILED",b" ... ignored")):
            args=list(self.fixture());args[3]=transform(args[3])
            with self.assertRaises(ValueError):gate.observer.admit_case(*args)
    def test_unknown_log_loss_changed_binary_or_context_blocks_admission(self):
        for kind in ("UNKNOWN_TIMEOUT_NO_RETRY","UNKNOWN_CUSTODY_NO_RETRY"):
            args=list(self.fixture());args[4]["observation"]=kind
            with self.assertRaises(ValueError):gate.observer.admit_case(*args)
        args=list(self.fixture());args[6]["sha256"]="d"*64
        with self.assertRaises(ValueError):gate.observer.admit_case(*args)
        args=list(self.fixture());args[8]={"product_source_sha":gate.GREEN}
        with self.assertRaises(ValueError):gate.observer.admit_case(*args)
    def test_source_roster_rejects_missing_extra_or_duplicate_selected_tests(self):
        spec=gate.SPECS["domain"]
        raw=("\n".join(n+": test" for n in gate.DOMAIN_CASES)+"\n7 tests, 0 benchmarks\n").encode()
        roster=gate.observer.selected_roster(raw,0,spec,gate.DOMAIN_CASES,"repair")
        self.assertEqual(set(roster["selected"]),set(gate.DOMAIN_CASES))
        for data in (raw.replace((gate.DOMAIN_CASES[0]+": test\n").encode(),b""),raw+b"extra: test\n",
                     raw+(gate.DOMAIN_CASES[0]+": test\n").encode()):
            with self.assertRaises(ValueError):gate.observer.selected_roster(data,0,spec,gate.DOMAIN_CASES,"repair")

if __name__=="__main__":
    unittest.main()
