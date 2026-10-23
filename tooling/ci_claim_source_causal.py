#!/usr/bin/env python3
"""Observe only two causal Claim failures on the frozen tests-only Linux source.

The existing Foundation observer owns process, stream, enumeration and causal
assertion custody. This module supplies only the closed Claim selection and its
domain/service binary binding. The separate Studio branch-claim gate owns repair
verification. This observer never executes the repair, renders media or promotes
acceptance.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

try:
    from tooling import ci_foundation_causal as observer
    from tooling import ci_storage_apply_recovery as custody
except ModuleNotFoundError as error:
    if error.name != "tooling":
        raise
    import ci_foundation_causal as observer
    import ci_storage_apply_recovery as custody

WORKFLOW = ".github/workflows/native-production.yml"
HELPER = "tooling/ci_claim_source_causal.py"
TEST = "tooling/tests/test_ci_claim_source_causal.py"
BASE = "7420119931c3611a950f9ab23a0de1665d179dc5"
RED = "8da7b461711efde37449941dd57aa1b0c64ba1d6"
GREEN = "7fada8c415f544f2c42b53405b4bac2013a34d6e"
TREES = {RED: "aba9a5df10cc31348274a24926bb6e393dad43c1",
         GREEN: "43065376c7ddf1341cb39bb2b03126dbfda11f7f"}
FIX = "crates/domain/src/claim_source_edit.rs"
DOMAIN = "crates/domain/tests/claim_source_edit.rs"
SERVICE = "crates/service/tests/claim_source_edit.rs"
PINS = {DOMAIN: "7de7e9ebcb670e05290f7021708eb6c95b03ce1de33d3dec711ab6b16f77a623",
        SERVICE: "8941b1b28e2bed5cb566c4745c2a04ceb58e56623583966a90abfaa19daec785",
        "Cargo.lock": "3d93b2ed700a67cbda781601b11d6bc3053e5697558775a68e699526a941e685",
        "SOURCE_LOCK.json": "2d830383f3df0731effb531cf713020be1ce4ced69ec66465fae86f94c2d740d"}
DOMAIN_CASES = (
    "source_only_edit_preserves_claim_id_order_references_and_unrelated_content",
    "invalid_preimage_id_noop_source_and_utf8_limits_are_atomic",
    "locks_protect_project_or_target_without_blocking_an_unrelated_claim",
    "wire_requires_complete_preimage_and_replacement_but_accepts_explicit_null",
    "source_change_invalidates_resolved_review_even_when_text_does_not_change",
    "new_review_requires_an_existing_claim_target",
    "immutable_capture_source_and_context_reject_edits_without_rebinding_original_evidence",
)
SERVICE_CASES = (
    "exact_source_edit_reopens_with_same_identity_history_and_original_request_receipt",
    "another_client_revision_conflicts_even_when_the_claim_preimage_is_still_exact",
    "current_revision_does_not_authorize_a_stale_claim_preimage",
    "a_source_only_change_invalidates_resolved_active_branch_reviews_and_retains_their_history",
    "content_locks_reject_project_or_target_edits_without_changing_the_saved_project",
    "explicit_source_withdrawal_is_durable_and_a_rejected_noop_adds_no_content_event",
    "immutable_capture_bindings_survive_direct_service_rejections_and_durable_reopen",
    "invalid_fields_assets_future_sources_and_missing_review_targets_have_no_partial_commit",
)
SPECS = {
    "domain": {"package": "motionwright-domain", "crate": "domain", "kind": "test",
               "name": "claim_source_edit", "source": DOMAIN, "prefixes": None},
    "service": {"package": "motionwright-service", "crate": "service", "kind": "test",
                "name": "claim_source_edit", "source": SERVICE, "prefixes": None},
}
CASES = {"domain": DOMAIN_CASES, "service": SERVICE_CASES}
NEGATIVES = {"domain": DOMAIN_CASES[1], "service": SERVICE_CASES[-1]}
GATE_FILES = (WORKFLOW, HELPER, TEST, "tooling/ci_foundation_causal.py",
              "tooling/ci_storage_apply_recovery.py", "Cargo.toml", "Cargo.lock", "SOURCE_LOCK.json")
SHA = re.compile(r"[0-9a-f]{40}\Z")
require = custody.require


def execution_context(environment, runtime_platform=None):
    runtime_platform = sys.platform if runtime_platform is None else runtime_platform
    require(runtime_platform == "linux" and environment.get("RUNNER_OS") == "Linux"
            and environment.get("RUNNER_ARCH") == "X64", "requires Linux x64")
    require(environment.get("GITHUB_ACTIONS") == "true"
            and environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and environment.get("MW_CLAIM_SCOPE") == "claim_causal_red", "explicit Claim dispatch required")
    require(environment.get("GITHUB_REPOSITORY") == "seradotcom/motionwright"
            and environment.get("GITHUB_JOB") == "claim-causal-red", "wrong repository or job")
    for key in ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA"):
        require(SHA.fullmatch(environment.get(key, "")), "full workflow SHA required")
    require(environment["GITHUB_SHA"] == environment["GITHUB_WORKFLOW_SHA"]
            and environment["GITHUB_SHA"] not in (BASE, RED, GREEN), "gate/product source identity differs")
    require(environment.get("GITHUB_RUN_ATTEMPT") == "1"
            and re.fullmatch(r"[1-9][0-9]*", environment.get("GITHUB_RUN_ID", "")),
            "first observed run attempt only; no automatic replay")
    require(environment.get("GITHUB_WORKFLOW_REF", "").startswith(
        "seradotcom/motionwright/" + WORKFLOW + "@"), "wrong workflow identity")
    return {key: environment[key] for key in (
        "GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_REPOSITORY",
        "GITHUB_JOB", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "RUNNER_OS", "RUNNER_ARCH")}


def changed_paths(repo, before, after):
    return tuple(x.decode("utf-8") for x in custody.git(
        repo, "diff", "--name-only", "-z", before, after).split(b"\0") if x)


def verify_pair(repo):
    require(custody.git(repo, "rev-parse", RED + "^").decode().strip() == BASE
            and custody.git(repo, "rev-parse", GREEN + "^").decode().strip() == RED,
            "frozen parent chain changed")
    require(set(changed_paths(repo, BASE, RED)) == {DOMAIN, SERVICE},
            "tests-only predecessor changed product inputs")
    require(changed_paths(repo, RED, GREEN) == (FIX,), "repair is not the single reviewed product path")
    for source, expected_fix in (
        (RED, "354fd611269bda8f9b13c46bc33af261f3529bb1ee81a3b323d89fc473552a5c"),
        (GREEN, "75fb74e594a87002bb215079a7ef4f49cb69b7993dc0f9386c98faf14505891d"),
    ):
        require(custody.git(repo, "rev-parse", source + "^{tree}").decode().strip() == TREES[source],
                "frozen product tree changed")
        for path, expected in {**PINS, FIX: expected_fix}.items():
            raw = custody.git(repo, "show", source + ":" + path)
            require(hashlib.sha256(raw).hexdigest() == expected, "frozen source changed: " + path)
    return {"base": BASE, "tests_only": RED, "repair": GREEN, "trees": TREES,
            "tests_identical_across_pair": True, "repair_paths": [FIX]}


def assertion_line(raw, source):
    require(hashlib.sha256(raw).hexdigest() == PINS[source], "unreviewed assertion source")
    text = raw.decode("utf-8")
    pattern = (r"(?m)^    assert!\(\n        project\.apply_change\(change\)\.is_err\(\),"
               if source == DOMAIN else r"(?m)^    assert!\(\n        service\n")
    matches = list(re.finditer(pattern, text))
    require(len(matches) == 1, "causal assertion site missing or ambiguous")
    return text.count("\n", 0, matches[0].start()) + 1


def pair_contract(repo):
    failures = {}
    for kind, source in (("domain", DOMAIN), ("service", SERVICE)):
        raw = custody.git(repo, "show", RED + ":" + source)
        failures[NEGATIVES[kind]] = {
            "source": source, "line": assertion_line(raw, source),
            "markers": ["must be rejected: UpdateClaim", "source: Some(Asset { asset_id:"],
        }
    return {"id": "claim-source-membership", "failures": failures,
            "baseline": {"sha": RED, "tree": TREES[RED],
                         "targets": {k: [v] for k, v in NEGATIVES.items()}},
            "repair": {"sha": GREEN, "tree": TREES[GREEN],
                       "targets": {k: list(v) for k, v in CASES.items()}}}


def input_paths(repo, source):
    paths = [x.decode("utf-8") for x in custody.git(
        repo, "ls-tree", "-r", "--name-only", "-z", source).split(b"\0") if x]
    selected = sorted(p for p in paths if p in ("Cargo.toml", "Cargo.lock", "SOURCE_LOCK.json")
        or p.startswith((".cargo/", "crates/domain/", "crates/storage/", "crates/service/",
                         "crates/native/", "vendor/"))
        or p.endswith(("/Cargo.toml", "/build.rs")))
    require(len(paths) == len(set(paths)) and {FIX, DOMAIN, SERVICE, *PINS} <= set(selected),
            "compiled source inventory incomplete")
    return selected


def select_binary(raw, code, repo, target, spec):
    require(spec in SPECS.values(), "unreviewed binary target")
    require(type(code) is int and code == 0, "compiler failure is not causal RED")
    rows = [custody.strict_json(line.encode()) for line in raw.decode("utf-8").splitlines() if line.strip()]
    require(all(isinstance(row, dict) for row in rows), "invalid Cargo JSON")
    ends = [row for row in rows if row.get("reason") == "build-finished"]
    require(len(ends) == 1 and ends[0].get("success") is True, "successful compilation absent")
    binaries = [row for row in rows if row.get("reason") == "compiler-artifact"
                and row.get("executable") and row.get("target", {}).get("name") == spec["name"]
                and row["target"].get("kind") == ["test"] and row.get("profile", {}).get("test") is True]
    require(len(binaries) == 1, "compiled selected binary absent or ambiguous")
    row = binaries[0]
    require(Path(row["manifest_path"]).absolute() == repo / ("crates/" + spec["crate"] + "/Cargo.toml")
            and Path(row["target"]["src_path"]).absolute() == repo / spec["source"],
            "compiled binary belongs to another source")
    path = Path(row["executable"]).absolute()
    identity = custody.fingerprint(path, target)
    require(identity["bytes"] > 0, "empty compiled binary")
    return {"absolute_path": str(path), **identity}


def run(repo, output):
    repo, output = repo.absolute(), output.absolute()
    require(not output.exists() and not output.is_relative_to(repo), "fresh external output required")
    output.mkdir(parents=True)
    context = execution_context(os.environ)
    report = {"schema": "motionwright.claim-source-causal-red/1", "at_utc": custody.now(),
              "status": "INCOMPLETE", "context": context, "workflow_sha": context["GITHUB_SHA"],
              "product_test_sha": {"baseline": RED, "repair": GREEN}, "roles": {},
              "new_native_operations": 0, "human_acceptance": False,
              "installed_recovery": "NOT_RUN", "browser_acceptance": "NOT_RUN",
              "branch_claim_134_unit_31_service_gate": "NOT_RUN",
              "repair_verification": "SEPARATE_BRANCH_CLAIM_ORIGINALS_REQUIRED",
              "repair_tests_executed": 0, "native_consumer_compilation": "NOT_RUN"}
    observations = []
    def command(args, cwd, env, folder, label, timeout, ctx):
        return observer.command(args, cwd, env, folder, label, timeout, ctx, (output, observations))
    try:
        gate_before = custody.source_snapshot(repo, context["GITHUB_SHA"], GATE_FILES)
        report["gate_before"] = gate_before
        report["pair_identity"] = verify_pair(repo)
        pair = pair_contract(repo)
        report["closed_pair"] = pair
        tool_env = observer.build_environment(os.environ, output/"tool-target", output/"tool-tmp")
        (output/"tool-target").mkdir();(output/"tool-tmp").mkdir()
        tool_env["RUSTUP_TOOLCHAIN"] = "1.98.1"
        tools = observer.tools_identity(repo, output, tool_env, context, (output, observations))
        report["tools"] = tools
        cargo = tools["cargo"]["absolute_path"]
        for role in ("baseline",):
            source = pair[role]["sha"]
            work = output/(role+"-checkout");target=output/(role+"-target");tmp=output/(role+"-tmp")
            logs=output/(role+"-originals")
            for path in (target,tmp,logs):path.mkdir()
            ctx={**context,"product_source_sha":source,"role":role}
            env=observer.build_environment(os.environ,target,tmp)
            env.update(RUSTUP_TOOLCHAIN="1.98.1",CARGO_BUILD_JOBS="2")
            create=command(["git","-c","core.autocrlf=false","-c","core.eol=lf","worktree","add",
                            "--detach",str(work),source],repo,env,logs,"checkout",60,ctx)
            require(create["exit_code"]==0,"product checkout failed")
            paths=input_paths(repo,source)
            before=custody.source_snapshot(work,source,paths,TREES[source])
            result={"source_before":before,"binaries":{},"rosters":{},"results":[],
                    "clippy":"NOT_SELECTED","retained_sources":observer.retain_source(work,before,output)}
            report["roles"][role]=result
            for kind in ("domain","service"):
                spec=dict(SPECS[kind])
                expected=pair[role]["targets"][kind]
                if role=="baseline":spec["prefixes"]=(NEGATIVES[kind],)
                compiled=command(observer.compile_argv(cargo,spec),work,env,logs,kind+"-compile",600,ctx)
                binary=select_binary(observer.original_stream(logs,compiled,"stdout"),
                                     compiled["exit_code"],work,target,SPECS[kind])
                result["binaries"][kind]={"observed":binary,
                    "retained":observer.retain_file(Path(binary["absolute_path"]),target,output,"binaries")}
                enumerated=command([binary["absolute_path"],"--list","--format","terse"],
                                   work,env,logs,kind+"-list",30,ctx)
                roster=observer.selected_roster(observer.original_stream(logs,enumerated,"stdout"),
                    enumerated["exit_code"],spec,expected,role)
                result["rosters"][kind]=roster
                for number,name in enumerate(expected):
                    row=command(observer.case_argv(binary,pair,role,name),work,env,logs,
                                kind+"-case-"+str(number),120,ctx)
                    after={"absolute_path":binary["absolute_path"],
                           **custody.fingerprint(Path(binary["absolute_path"]),target)}
                    text=observer.original_stream(logs,row,"stdout")+b"\n"+observer.original_stream(logs,row,"stderr")
                    result["results"].append(observer.admit_case(pair,role,name,text,row,binary,after,roster,ctx))
            result["source_after"]=custody.source_snapshot(work,source,paths,TREES[source])
            result["admission"]=observer.role_complete(pair,role,result)
        report["gate_after"]=custody.source_snapshot(repo,context["GITHUB_SHA"],GATE_FILES)
        require(report["gate_before"]==report["gate_after"],"gate source changed")
        for tool in tools.values():
            path=Path(tool["absolute_path"])
            require(custody.fingerprint(path,path.parent)==
                    {k:tool[k] for k in ("path","bytes","sha256","identity")},"compiler/tool identity changed")
        for obs in observations:
            folder=output/obs["folder"]
            require(custody.fingerprint(folder/Path(obs["receipt"]["path"]).name,output)==obs["receipt"],
                    "original command receipt changed")
            for channel in ("stdout","stderr"):
                observer.original_stream(folder,obs["command"],channel)
        report.update(status="EXPECTED_CAUSAL_RED_OBSERVED",expected_causal_red=2,
                      repaired_domain_cases=0,repaired_service_cases=0,skips=0,omitted_selected_cases=0)
    except (observer.UnknownExecution,custody.UnknownObservation) as error:
        report.update(status="UNKNOWN_NO_RETRY",error=str(error),uncertain_command=error.command)
    except Exception as error:
        report.update(status="FAIL_OR_INCOMPLETE",error=type(error).__name__+": "+str(error))
    report["commands"]=observations
    report["ended_at_utc"]=custody.now()
    custody.write_new(output/"receipt.json",report)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args(argv)
    report=run(args.repo,args.output)
    print(json.dumps({"status":report["status"],"receipt":str(args.output/"receipt.json")}))
    return 0 if report["status"]=="EXPECTED_CAUSAL_RED_OBSERVED" else 2


if __name__=="__main__":
    raise SystemExit(main())
