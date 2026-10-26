#!/usr/bin/env python3
"""Select five existing portability/recovery methods after explicit A1 admission.

The product and gate are separate commits. The empty product allowlist is
intentional: source controls may run now, but no product is admitted yet.
Shared Foundation observers retain process, command, binary and log custody.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import textwrap
from types import MappingProxyType

# Isolated Python omits both cwd and the script directory. Import only from the
# checkout containing this reviewed entrypoint; never an environment override.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from tooling import ci_foundation_causal as observer
    from tooling import ci_storage_apply_recovery as custody
    from tooling import ci_sdk_native_source as producer
    from tooling import ci_test_runner as fast_runner
except ModuleNotFoundError as error:
    if error.name != "tooling":
        raise
    import ci_foundation_causal as observer
    import ci_storage_apply_recovery as custody
    import ci_sdk_native_source as producer
    import ci_test_runner as fast_runner

WORKFLOW = ".github/workflows/project-portability.yml"
HELPER = "tooling/ci_project_portability_recovery.py"
TEST = "tooling/tests/test_project_portability_contracts.py"
SCOPE = "bundle_recovery_five"
JOB = "bundle-recovery-five"
REVIEW_BRANCH = "refs/heads/verify/project-portability-recovery-"
RUST = "1.98.1"
SCHEMA = "motionwright.project-portability-recovery-five/1"
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
require = custody.require

# A1 must review a separate immutable product F and add its exact SHA, tree,
# complete Git-tree listing digest and required input pins in a later change.
# Neither a dispatch input nor the source543 preparation baseline is authority.
REVIEWED_PRODUCTS = MappingProxyType({})

CASES = MappingProxyType({
    "storage": (
        "apply_crash_tests::same_base_competing_writer_wins_after_real_before_commit_exit",
        "apply_crash_tests::same_base_competing_writer_conflicts_after_real_after_commit_exit",
        "apply_crash_tests::real_bundle_exit_after_first_cas_publication_recovers_without_partial_sqlite",
        "apply_crash_tests::real_bundle_exit_after_commit_is_observed_without_reissuing_import",
    ),
    "service": (
        "service_inspected_bundle_transfers_real_cas_and_reopens_without_operational_receipts",
    ),
})
TARGETS = MappingProxyType({
    "storage": {"package": "motionwright-storage", "crate": "storage", "kind": "lib",
                "name": "motionwright_storage", "source": "crates/storage/src/lib.rs"},
    "service": {"package": "motionwright-service", "crate": "service", "kind": "test",
                "name": "project_portability_authority",
                "source": "crates/service/tests/project_portability_authority.rs"},
})
G0_POLICIES = ("source_policy", "acceptance_policy", "creative_status_policy", "public_surface_policy")
G0_PATTERNS = ("test_project_portability_contracts.py", "test_ci_job_plan.py",
               "test_source_policy_credentials.py", "test_conformance_policy.py")
G0_FILES = ("tooling/ci_test_runner.py", *("tooling/" + p + ".py" for p in G0_POLICIES),
            *("tooling/tests/" + p for p in G0_PATTERNS))
REQUIRED_INPUTS = (
    ".gitattributes", "Cargo.toml", "Cargo.lock", "SOURCE_LOCK.json", "rust-toolchain.toml",
    "crates/storage/Cargo.toml", "crates/storage/src/lib.rs",
    "crates/storage/src/apply_crash_tests.rs", "crates/storage/src/portability.rs",
    "crates/service/Cargo.toml", "crates/service/src/lib.rs",
    "crates/service/src/portability.rs", "crates/service/tests/project_portability_authority.rs",
    *G0_FILES,
)
GATE_PINS = {
    "tooling/ci_foundation_causal.py": "1d31c58aa7d63c927ceb2518282a433e0f728fc125da8978fb635b18cff354c2",
    "tooling/ci_storage_apply_recovery.py": "9aea9a41d365a36fd1abae86ec5a0974236d62d9781a372eace0578ef2da1520",
    "tooling/ci_sdk_native_source.py": "65bcda73f16423ebb644a0979bca3a7927f8e7337c124702874bce51adcf8127",
    "tooling/ci_test_runner.py": "b7d39ad4c94be70abcac75880f92d00cf3c8d49eb0198b404a962ce5ab1a712f",
}
GATE_FILES = (WORKFLOW, HELPER, TEST, *GATE_PINS)


def admitted_product(source):
    require(type(source) is str and SHA.fullmatch(source), "full product F SHA required")
    require(source in REVIEWED_PRODUCTS, "product F is not admitted by A1; source controls only")
    profile = REVIEWED_PRODUCTS[source]
    require(type(profile) is dict and set(profile) == {"tree", "tree_listing_sha256", "pins", "review"},
            "reviewed product profile shape differs")
    review, pins = profile["review"], profile["pins"]
    require(type(profile["tree"]) is str and SHA.fullmatch(profile["tree"])
            and type(profile["tree_listing_sha256"]) is str and DIGEST.fullmatch(profile["tree_listing_sha256"]),
            "reviewed product tree identity differs")
    require(type(review) is dict and set(review) == {"authority", "status", "reference_sha256"}
            and review["authority"] == "A1" and review["status"] == "ADMITTED_PRODUCT_F"
            and type(review["reference_sha256"]) is str and DIGEST.fullmatch(review["reference_sha256"]),
            "missing reviewed A1 product admission")
    require(type(pins) is dict and set(pins) == set(REQUIRED_INPUTS)
            and all(type(v) is str and DIGEST.fullmatch(v) for v in pins.values()),
            "reviewed target, lock or input pins are incomplete")
    return profile


def admit_request(env):
    require(env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and env.get("MW_PP5_SCOPE") == SCOPE, "five-method execution requires its explicit manual scope")
    require(env.get("MW_PP5_OS_SCOPE") == "linux", "five-method profile requires explicit os_scope=linux")
    require(env.get("MW_PP5_CANDIDATE_MANIFEST", "") == ""
            and env.get("MW_PP5_CANDIDATE_DIGEST", "") == "", "legacy candidate inputs cannot authorize this profile")
    require(env.get("GITHUB_REPOSITORY") == "seradotcom/motionwright", "foreign repository")
    require(env.get("GITHUB_RUN_ATTEMPT") == "1", "only an original first attempt is admitted")
    gate = env.get("GITHUB_SHA", "")
    require(SHA.fullmatch(gate) and env.get("MW_PP5_WORKFLOW_SHA") == gate,
            "workflow and gate source differ")
    ref = env.get("GITHUB_REF", "")
    require(ref.startswith("refs/heads/") and env.get("GITHUB_WORKFLOW_REF") ==
            "seradotcom/motionwright/" + WORKFLOW + "@" + ref, "workflow identity differs")
    require(re.fullmatch(r"[1-9][0-9]*", env.get("GITHUB_RUN_ID", "")), "original run identity missing")
    source = env.get("MW_PP5_PRODUCT_SOURCE", "")
    profile = admitted_product(source)
    require(source != gate, "gate and reviewed product must be independently bound commits")
    return {"gate_source_sha": gate, "product_source_sha": source, "product_tree": profile["tree"],
            "scope": SCOPE, "os_scope": "linux", "run_id": env["GITHUB_RUN_ID"],
            "run_attempt": "1", "review": profile["review"], "product_execution": "NOT_RUN"}


def execution_context(env, runtime_platform=None):
    context = admit_request(env)
    require((sys.platform if runtime_platform is None else runtime_platform) == "linux"
            and env.get("RUNNER_OS") == "Linux" and env.get("RUNNER_ARCH") == "X64"
            and env.get("GITHUB_JOB") == JOB, "five-method runner or job differs")
    context["admission_product_execution"] = context.pop("product_execution")
    return {**context, "job_id": JOB, "runner_os": "Linux", "runner_arch": "X64",
            "workflow_ref": env["GITHUB_WORKFLOW_REF"],
            "runner_name": env.get("RUNNER_NAME", ""), "image_os": env.get("ImageOS", ""),
            "image_version": env.get("ImageVersion", "")}


def source_paths(raw, profile):
    require(type(raw) is bytes and raw.endswith(b"\0")
            and hashlib.sha256(raw).hexdigest() == profile["tree_listing_sha256"],
            "reviewed complete product tree listing differs")
    paths, folded = [], set()
    for entry in raw[:-1].split(b"\0"):
        parts = entry.split(b"\t")
        require(len(parts) == 2, "malformed source tree entry")
        mode, kind, oid = parts[0].decode("ascii").split(" ")
        name = parts[1].decode("utf-8")
        require(mode in {"100644", "100755"} and kind == "blob" and SHA.fullmatch(oid)
                and name and not name.startswith("/") and not any(c in name for c in "\\:")
                and not any(ord(c) < 32 for c in name)
                and all(p not in {"", ".", "..", ".git"} for p in name.split("/"))
                and name.casefold() not in folded, "linked, unsafe or duplicate product input")
        folded.add(name.casefold()); paths.append(name)
    require(0 < len(paths) <= 100000 and set(REQUIRED_INPUTS) <= set(paths), "product source closure incomplete")
    return tuple(sorted(paths))


def product_snapshot(repo, source, profile, paths):
    snapshot = custody.source_snapshot(repo, source, paths, profile["tree"])
    require(all(snapshot["files"][p]["sha256"] == digest for p, digest in profile["pins"].items()),
            "admitted product input bytes differ")
    return snapshot


def producer_plan(cargo):
    return tuple((label, observer.compile_argv(cargo, spec), (spec,)) for label, spec in TARGETS.items())


def product_g0_plan(python, repo, context, receipt):
    plans = [("policy-" + policy, [python, "-B", "-E", "-s", "tooling/" + policy + ".py"], 60)
             for policy in G0_POLICIES]
    argv = [python, "-B", "-E", "-s", "tooling/ci_test_runner.py", "--repo", str(repo),
            "--expected-source", context["product_source_sha"], "--test-directory", "tooling/tests"]
    for pattern in G0_PATTERNS: argv.extend(("--pattern", pattern))
    argv.extend(("--mode", "both", "--require-complete", "--timeout", "180", "--report", str(receipt)))
    return (*plans, ("contracts", argv, 1500))


def g0_entrypoints(python):
    result = []
    for pattern in G0_PATTERNS:
        result.append(("discovery:" + pattern, [python, "-B", "-E", "-s", "-m", "unittest", "discover",
                       "-s", "tooling/tests", "-p", pattern, "-v"], pattern[:-3]))
    for pattern in sorted(G0_PATTERNS):
        module = "tooling.tests." + pattern[:-3]
        result.append(("dotted:" + module, [python, "-B", "-E", "-s", "-m", "unittest", module, "-v"], pattern[:-3]))
    return tuple(result)


def product_g0_receipt(path, evidence, source, context, python, row, raw_stdout):
    value, original = custody.original_json(path, evidence)
    if value.get("status") == "UNKNOWN" or any(s.get("status") == "UNKNOWN" for s in value.get("steps", [])
                                               if type(s) is dict):
        raise observer.UnknownExecution("product G0 is UNKNOWN; no Rust or replay", {
            **row, "original_command": row, "observation": "UNKNOWN_PRODUCT_G0_NO_RETRY", "g0_receipt": original})
    require(type(row.get("exit_code")) is int and row["exit_code"] == 0
            and row.get("context") == context and row.get("owned_group_after_wait") == "ABSENT"
            and row.get("observation") == "DIRECT_PROCESS_EXIT_OBSERVED" and row.get("log_custody") == "VERIFIED_SNAPSHOT"
            and row.get("argv") == product_g0_plan(python["absolute_path"], Path(source["checkout"]), context, path)[-1][1],
            "product G0 parent command did not close successfully")
    require(value.get("schema") == "motionwright.unittest-execution/1"
            and value.get("status") == "PASS" and type(value.get("exit_code")) is int and value["exit_code"] == 0
            and value.get("require_complete") is True and "source_postcheck_error" not in value,
            "product G0 receipt failed, omitted cases or lacks source postcheck")
    require(value.get("executed_source_sha") == context["product_source_sha"]
            and value.get("tree_sha") == source["tree"]
            and value.get("runner_sha256") == source["files"]["tooling/ci_test_runner.py"]["sha256"]
            and value.get("python_executable_sha256") == python["sha256"], "product G0 source or tools differ")
    github = {"GITHUB_SHA": context["gate_source_sha"], "GITHUB_WORKFLOW_REF": context["workflow_ref"],
              "GITHUB_RUN_ID": context["run_id"], "GITHUB_RUN_ATTEMPT": context["run_attempt"],
              "GITHUB_JOB": context["job_id"]}
    require(value.get("github") == github, "product G0 falsely relabelled its actual GitHub context")
    definitions = {"tooling/tests/" + p: source["files"]["tooling/tests/" + p]["sha256"] for p in G0_PATTERNS}
    locks = {p: source["files"][p]["sha256"] for p in ("Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json")
             if p in source["files"]}
    require(value.get("test_definition_sha256") == definitions and value.get("source_locks") == locks,
            "product G0 definitions or source lock bytes differ")
    steps = value.get("steps"); expected = g0_entrypoints(python["absolute_path"])
    require(type(steps) is list and len(steps) == len(expected), "product G0 entrypoint roster differs")
    require([custody.strict_json(line) for line in raw_stdout.splitlines() if line.strip()] == steps,
            "product G0 receipt differs from original runner stdout")
    observed, canonical = [], {}
    for index, (step, (label, argv, module)) in enumerate(zip(steps, expected)):
        require(type(step) is dict and step.get("entry_point") == label and step.get("command") == argv
                and step.get("status") == "PASS" and type(step.get("exit_code")) is int and step["exit_code"] == 0,
                "product G0 entrypoint is absent, repeated, foreign, failed or omitted")
        streams, originals = {}, {}
        for channel in ("stdout", "stderr"):
            bound = step.get(channel); filename = path.stem + f"-{index:02d}-{channel}.log"
            require(type(bound) is dict and set(bound) == {"file", "sha256"} and bound["file"] == filename,
                    "product G0 original path differs")
            log = path.with_name(filename); stamp = custody.fingerprint(log, evidence, 16 * 1024**2)
            require(bound["sha256"] == stamp["sha256"], "product G0 original log digest differs")
            raw = log.read_bytes()
            require(hashlib.sha256(raw).hexdigest() == stamp["sha256"]
                    and custody.fingerprint(log, evidence, 16 * 1024**2) == stamp, "product G0 original changed")
            streams[channel], originals[channel] = raw, stamp
        text = streams["stderr"].decode("utf-8", "replace")
        counts = fast_runner.parse_unittest_summary(text)
        declared = step.get("counts")
        require(type(declared) is dict and declared == counts
                and all(type(declared[k]) is int for k in counts if k != "terminal_status")
                and counts["terminal_status"] == "OK"
                and type(counts["tests_run"]) is int and counts["tests_run"] > 0
                and counts["passed"] == counts["tests_run"]
                and all(counts[k] == 0 for k in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes")),
                "product G0 counts disagree with originals or contain omissions")
        identifiers = re.findall(r"(?m)^test[A-Za-z_0-9]* \(([A-Za-z_][A-Za-z_0-9.]*)\)(?= \.\.\. |[ \t]*$)",
                                 text.replace("\r\n", "\n").replace("\r", "\n"))
        normalized = [name.removeprefix("tooling.tests.") for name in identifiers]
        require(len(identifiers) == counts["tests_run"] and len(set(normalized)) == len(normalized)
                and all(name.startswith(module + ".") for name in normalized),
                "product G0 actual case IDs are incomplete, duplicated or foreign")
        if module in canonical:
            require(canonical[module] == sorted(normalized), "product G0 discovery and dotted rosters differ")
        else:
            canonical[module] = sorted(normalized)
        observed.append({"entry_point": label, "counts": counts, "actual_case_ids": identifiers,
                         "originals": originals})
    require(custody.fingerprint(path, evidence, 16 * 1024**2) == original, "product G0 receipt changed")
    return {"status": "PASS_CONTRACTS_ONLY", "executed_source_sha": context["product_source_sha"],
            "github_source_sha": context["gate_source_sha"], "tree_sha": source["tree"], "receipt": original,
            "entry_points": observed, "unique_methods": sum(len(ids) for ids in canonical.values()),
            "method_observations": sum(s["counts"]["tests_run"] for s in observed), "format": "NOT_RUN"}


def observe_product_g0(report, repo, evidence, source, env, logs, context, python, command):
    stage = {"status": "STARTED", "executed_source_sha": context["product_source_sha"], "policy_commands": []}
    report["product_g0"] = stage
    folder = evidence / "product-g0"; folder.mkdir(mode=0o700)
    receipt = folder / "receipt.json"
    ctx = {**context, "verification_phase": "PRODUCT_G0", "executed_source_sha": context["product_source_sha"]}
    for label, argv, timeout in product_g0_plan(python["absolute_path"], repo, ctx, receipt):
        row = command(argv, repo, env, logs, "product-g0-" + label, timeout, ctx)
        require(row.get("argv") == argv and row.get("context") == ctx, "product G0 command or source context differs")
        raw = observer.original_stream(logs, row, "stdout")
        observer.original_stream(logs, row, "stderr")
        if label == "contracts":
            admitted = product_g0_receipt(receipt, evidence, source, ctx, python, row, raw)
            stage.update(admitted); stage["contract_command"] = row
        else:
            stage["policy_commands"].append({"policy": label, "command": row})
            require(type(row.get("exit_code")) is int and row["exit_code"] == 0
                    and row.get("owned_group_after_wait") == "ABSENT", "product G0 policy failed: " + label)
    require([r["policy"] for r in stage["policy_commands"]] == ["policy-" + p for p in G0_POLICIES]
            and stage["status"] == "PASS_CONTRACTS_ONLY", "product G0 incomplete")
    stage["status"] = "PASS_POLICIES_AND_CONTRACTS_FORMAT_PENDING"
    return stage


def finish_product_g0(stage, formatted, before, after):
    require(stage.get("status") == "PASS_POLICIES_AND_CONTRACTS_FORMAT_PENDING"
            and type(formatted.get("exit_code")) is int and formatted["exit_code"] == 0
            and formatted.get("owned_group_after_wait") == "ABSENT" and before == after,
            "product G0 format or exact source postcheck failed")
    stage.update(status="PASS_PRODUCT_G0", format=formatted, source_postcheck="EXACT_PRODUCT_SOURCE_UNCHANGED")


def verify_product_g0_originals(stage, evidence):
    require(stage.get("status") == "PASS_PRODUCT_G0", "product G0 was not completed")
    stamps = [stage["receipt"], *(stamp for entry in stage["entry_points"] for stamp in entry["originals"].values())]
    for stamp in stamps:
        require(custody.fingerprint(evidence / stamp["path"], evidence, 16 * 1024**2) == stamp,
                "product G0 original changed after admission")


def existing_formatter_policy(workflow, expected_digest):
    raw = workflow.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == expected_digest, "formatter policy workflow bytes changed")
    text = raw.decode("utf-8")
    start = text.index("          def gc_proxy_formatter_environment(environment, tools, formatters):\n")
    end = text.index("          def gc_proxy_finish_status(report):\n", start)
    parsed = ast.parse(textwrap.dedent(text[start:end]))
    require(len(parsed.body) == 1 and isinstance(parsed.body[0], ast.FunctionDef)
            and parsed.body[0].name == "gc_proxy_formatter_environment", "existing formatter policy shape differs")
    namespace = {"re": re}
    exec(compile(parsed, str(workflow) + ":gc_proxy_formatter_environment", "exec"), namespace)
    return namespace["gc_proxy_formatter_environment"]


def observed_formatters(gate, workflow_digest, tools, env, logs, context, command):
    require("RUSTFMT" not in env, "inherited formatter override before any formatter query")
    require(env.get("CARGO") in (None, tools["cargo"]["absolute_path"]), "foreign Cargo metadata producer for formatter")
    env["CARGO"] = tools["cargo"]["absolute_path"]
    formatters = {}
    for name in ("cargo-fmt", "rustfmt"):
        row = command(["rustup", "which", "--toolchain", RUST, name], gate, env, logs,
                      "formatter-" + name + "-path", 45, context)
        raw = observer.original_stream(logs, row, "stdout")
        require(type(row.get("exit_code")) is int and row["exit_code"] == 0, "selected formatter is unavailable")
        path = Path(raw.decode("utf-8").strip())
        require(path.is_absolute(), "selected formatter path is not absolute")
        path = path.resolve(strict=True)
        require(path.parent == Path(tools["cargo"]["absolute_path"]).parent,
                "selected formatter belongs to another toolchain")
        formatters[name] = {"absolute_path": str(path), **custody.fingerprint(path, path.parent)}
    env["RUSTFMT"] = formatters["rustfmt"]["absolute_path"]
    for name in ("cargo-fmt", "rustfmt"):
        row = command([formatters[name]["absolute_path"], "--version"], gate, env, logs,
                      "formatter-" + name + "-version", 45, context)
        require(type(row.get("exit_code")) is int and row["exit_code"] == 0, "selected formatter version failed")
        formatters[name]["version"] = observer.original_stream(logs, row, "stdout").decode("utf-8")
    policy = existing_formatter_policy(gate / WORKFLOW, workflow_digest)
    require(policy(env, tools, formatters) == env, "observed formatter environment differs")
    return formatters


def verify_tool_identities(tools):
    for tool in tools:
        path = Path(tool["absolute_path"])
        require(custody.fingerprint(path, path.parent) ==
                {key: tool[key] for key in ("path", "bytes", "sha256", "identity")}, "observed tool changed")


def compiled_roster(raw, code, label):
    require(label in TARGETS and type(code) is int and code == 0, "foreign target or failed enumeration")
    text = raw.decode("utf-8").replace("\r\n", "\n")
    names = tuple(re.findall(r"(?m)^([A-Za-z_][A-Za-z_0-9:]*): test$", text))
    declared = re.findall(r"(?m)^([0-9]+) tests?, 0 benchmarks?\s*$", text)
    require(len(declared) == 1 and int(declared[0]) == len(names), "compiled roster summary differs")
    observer.selected_roster(raw, code, {"prefixes": None}, names, "repair")
    selected = CASES[label]
    require(set(selected) <= set(names), "a required method is absent from the compiled binary")
    return {"all": names, "selected": selected,
            "not_selected": tuple(n for n in names if n not in selected), "not_selected_credit": 0}


def pair_contract():
    # This reuses the shared exact-result verifier API, not a causal baseline.
    return {"id": SCOPE, "failures": {}, "repair": {"targets": dict(CASES)}}


def case_argv(binary, name):
    return observer.case_argv(binary, pair_contract(), "repair", name)


def admit_case(name, raw, row, binary, after, listed, context):
    if custody.execution_unknown(row):
        raise observer.UnknownExecution("uncertain selected case; no replay", row)
    require(type(row.get("exit_code")) is int and row["exit_code"] == 0
            and row.get("owned_group_after_wait") == "ABSENT", "selected case did not close successfully")
    verified = observer.admit_case(pair_contract(), "repair", name, raw, row, binary, after, listed, context)
    return {"name": name, "status": verified["status"], "binary_sha256": binary["sha256"],
            "context": context, "command": row, "oracle": "EXISTING_SELECTED_RUST_METHOD",
            "child_invocations_are_additional_cases": False}


def case_environment(base, name, evidence, index):
    env = dict(base)
    hook = ("MOTIONWRIGHT_TEST_APPLY_EVIDENCE_ROOT" if name in CASES["storage"][:2]
            else "MOTIONWRIGHT_TEST_PORTABILITY_EVIDENCE_ROOT" if name in CASES["service"] else None)
    if hook is None:
        return env, None
    folder = evidence / ("case-" + str(index) + "-physical")
    folder.mkdir(mode=0o700)
    env[hook] = str(folder)
    return env, folder


def physical_originals(folder, name):
    if folder is None:
        require(name in CASES["storage"][2:], "missing supported evidence hook")
        return {"status": "INTERNAL_TEMPDIR_NOT_RETAINED", "db_cas_child_logs_retained": False,
                "scope": "Existing Rust assertions and original parent command only"}
    paths, files, total = [folder], {}, 0
    while paths:
        parent = paths.pop()
        require(stat.S_ISDIR(parent.lstat().st_mode) and len(parent.relative_to(folder).parts) <= 32,
                "physical custody contains linked or excessively deep directories")
        for child in sorted(parent.iterdir()):
            info = child.lstat()
            require(not stat.S_ISLNK(info.st_mode), "linked physical evidence")
            if stat.S_ISDIR(info.st_mode):
                paths.append(child)
            else:
                require(stat.S_ISREG(info.st_mode) and len(files) < 4096, "unbounded or nonregular physical evidence")
                observed = custody.fingerprint(child, folder)
                total += observed["bytes"]
                require(total <= 512 * 1024**2, "physical evidence exceeds custody budget")
                files[observed["path"]] = observed
    require(files, "supported evidence hook produced no originals")
    children = tuple(folder.iterdir())
    require(len(children) == 1 and children[0].is_dir(), "physical evidence has an unexpected root roster")
    owned = children[0]
    if name in CASES["storage"][:2]:
        manifest = custody.strict_json((owned / "custody.json").read_bytes())
        phase = "before_commit" if name == CASES["storage"][0] else "after_commit"
        require(manifest.get("schema") == "motionwright.apply-phase-custody/1"
                and manifest.get("test_name") == name.split("::")[-1]
                and manifest.get("checkpoint") == phase and manifest.get("status") == "complete"
                and manifest.get("parent_unwinding") is False and manifest.get("errors") == [],
                "concurrent phase custody incomplete or belongs to another method")
        require(manifest.get("phases") == [{"phase": p, "status": "captured", "reason": None}
                for p in ("before", "interrupted", "cleaned", "after_retry", "final")],
                "concurrent snapshots incomplete")
        listed = manifest.get("files")
        require(type(listed) is list and all(type(r) is dict for r in listed)
                and len(listed) == len({r.get("file") for r in listed})
                and set(r.get("file") for r in listed) == {p.name for p in owned.iterdir()} - {"custody.json"},
                "concurrent original file roster differs")
        required = {p + ".json" for p in ("before", "interrupted", "cleaned", "after_retry", "final",
                    "case", "contention", "writer-ready", "writer-release", "terminal-results")}
        require(required <= {r.get("file") for r in listed} and manifest.get("case_identity") == "case.json",
                "concurrent claimed snapshots or case originals are missing")
        for row in listed:
            require(type(row) is dict and set(row) == {"file", "bytes", "sha256"}
                    and type(row["file"]) is str and Path(row["file"]).name == row["file"],
                    "malformed concurrent original entry")
            current = files[owned.name + "/" + row["file"]]
            require(type(row["bytes"]) is int and row["bytes"] == current["bytes"]
                    and row["sha256"] == current["sha256"], "concurrent retained original changed")
        scope = "Five SQL/blob snapshots and original child/contender receipts; no raw database or full CAS archive"
    else:
        require(name in CASES["service"], "foreign method cannot supply service originals")
        checkpoint = custody.strict_json((owned / "checkpoint.json").read_bytes())
        require(checkpoint.get("schema") == "motionwright.test.portability-checkpoint/1"
                and type(checkpoint.get("nonce")) is str
                and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", checkpoint["nonce"])
                and checkpoint["nonce"] != "00000000-0000-0000-0000-000000000000"
                and owned.name == "acc003-" + checkpoint["nonce"], "service checkpoint identity differs")
        fixture = checkpoint.get("fixture")
        require(type(fixture) is dict and fixture.get("bundle") == str(owned / "portable"),
                "service bundle path is not the retained fixture")
        manifest_bytes = fixture.get("manifest_bytes")
        require(type(manifest_bytes) is list and manifest_bytes
                and all(type(b) is int and 0 <= b <= 255 for b in manifest_bytes),
                "service original manifest bytes are missing or mistyped")
        manifest_hash = hashlib.sha256(bytes(manifest_bytes)).hexdigest()
        manifest_path = owned.name + "/portable/manifest.json"
        require(manifest_path in files and files[manifest_path]["sha256"] == manifest_hash
                and files[manifest_path]["bytes"] == len(manifest_bytes),
                "service manifest bytes differ from the retained original")
        checkpoint_hash = files[owned.name + "/checkpoint.json"]["sha256"]
        for phase in ("prepare", "continue"):
            phase_paths = {"stdout_path": owned / (phase + ".stdout.log"),
                           "stderr_path": owned / (phase + ".stderr.log"),
                           "status_path": owned / (phase + ".status.json"),
                           "completion_receipt_path": owned / (phase + ".completed.json")}
            require(all(p.relative_to(folder).as_posix() in files for p in phase_paths.values()),
                    "service original logs or receipts are missing")
            complete = custody.strict_json((owned / (phase + ".completed.json")).read_bytes())
            status = custody.strict_json((owned / (phase + ".status.json")).read_bytes())
            require(complete.get("schema") == "motionwright.test.portability-phase/1"
                    and complete.get("selector") == name and complete.get("nonce") == checkpoint["nonce"]
                    and complete.get("phase") == phase and complete.get("checkpoint_sha256") == checkpoint_hash
                    and complete.get("manifest_sha256") == manifest_hash,
                    "service phase completion differs")
            require(status.get("schema") == "motionwright.test.portability-child-status/2"
                    and status.get("selector") == name and status.get("nonce") == checkpoint["nonce"]
                    and status.get("phase") == phase and status.get("process_state") == "EXITED"
                    and type(status.get("exit_code")) is int and status["exit_code"] == 0
                    and status.get("success") is True and status.get("timed_out") is False
                    and status.get("cleanup_timed_out") is False
                    and status.get("completion_receipt_verified") is True and status.get("errors") == [],
                    "service child status is incomplete or UNKNOWN")
            require(status.get("root") == str(owned)
                    and all(status.get(key) == str(path) for key, path in phase_paths.items()),
                    "service child receipt paths differ from the retained originals")
        scope = "Existing service fixture tree, SQLite/CAS, prepare/continue receipts and original child logs"
    for item in files.values():
        require(custody.fingerprint(folder / item["path"], folder) == item, "physical original changed during readback")
    return {"status": "VERIFIED_EXISTING_FIXTURE_ORIGINALS", "folder": str(folder), "files": files,
            "bytes": total, "scope": scope, "independent_oracle_reexecution": False}


def observe_five(report, binaries, rosters, repo, target, env, logs, context, command):
    require(set(binaries) == {s["name"] for s in TARGETS.values()}
            and set(rosters) == set(TARGETS), "both sealed targets and rosters are required before observation")
    index = 0
    for label, spec in TARGETS.items():
        binary = binaries[spec["name"]]
        for name in CASES[label]:
            report["currently_selected_case"] = name
            producer.verify_binaries(binaries, target)
            case_env, physical = case_environment(env, name, logs.parent, index)
            report["selected_methods_execution"] = "STARTED_TERMINAL_PENDING"
            row = command(case_argv(binary, name), repo, case_env, logs, "case-" + str(index), 180, context)
            raw = observer.original_stream(logs, row, "stdout")
            stderr = observer.original_stream(logs, row, "stderr")
            after = {"absolute_path": binary["absolute_path"],
                     **custody.fingerprint(Path(binary["absolute_path"]), target)}
            result = admit_case(name, raw + b"\n" + stderr, row, binary, after, rosters[label], context)
            result["physical_originals"] = physical_originals(physical, name)
            report["cases"].append(result)
            index += 1


def final_counts(cases):
    expected = tuple(n for rows in CASES.values() for n in rows)
    require(len(cases) == 5 and [r["name"] for r in cases] == list(expected)
            and all(r["status"] == "PASS" for r in cases), "selected methods incomplete, duplicated or unsuccessful")
    return {"selected_methods": 5, "passed": 5, "failed": 0, "skipped": 0,
            "internal_children_counted_as_extra_methods": 0}


def build_environment(environment, target, temporary, run_root):
    blocked = [k for k in environment if k.startswith("SEMWRIGHT_")
               or k in {"LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "DYLD_INSERT_LIBRARIES",
                         "CC", "AR", "CFLAGS", "CPPFLAGS", "LDFLAGS", "HOST_CC", "TARGET_CC", "HOST_AR", "TARGET_AR",
                         "RUSTFMT", "CARGO"}
               or k.startswith(("CC_", "AR_", "CFLAGS_", "CPPFLAGS_", "LDFLAGS_"))
               or (k.startswith("CARGO_TARGET_") and k.endswith(("_LINKER", "_RUNNER")))]
    require(not blocked, "inherited product or loader overrides: " + ",".join(sorted(blocked)))
    env = observer.build_environment(environment, target, temporary)
    runner = Path(environment["RUNNER_TEMP"]).resolve(strict=True)
    for key in ("CARGO_HOME", "RUSTUP_HOME"):
        value = Path(env.get(key, "")).resolve(strict=True)
        require(value.is_relative_to(runner) and value != runner, "cache outside owned runner temporary root")
    require(target.is_relative_to(run_root) and temporary.is_relative_to(run_root), "build paths escaped run root")
    env.update(CARGO_BUILD_JOBS="2", RUSTUP_TOOLCHAIN=RUST, PYTHONDONTWRITEBYTECODE="1")
    return env


def installed_c_packages(raw, code):
    require(type(code) is int and code == 0, "required Linux C build packages are unavailable")
    rows = {}
    for line in raw.decode("utf-8").splitlines():
        fields = line.split("\t")
        require(len(fields) == 3 and fields[0] in {"gcc", "libc6-dev", "binutils"}
                and fields[0] not in rows and fields[1] == "install ok installed"
                and fields[2] and not any(ord(c) < 32 for c in fields[2]),
                "Linux C build package status is incomplete or differs")
        rows[fields[0]] = fields[2]
    require(set(rows) == {"gcc", "libc6-dev", "binutils"}, "Linux C build package roster differs")
    return rows


def linux_build_preflight(repo, env, logs, context, command):
    # The reviewed selected closure reaches bundled SQLite, not desktop/SDK host
    # backends. Observe the required C compiler, headers and binutils before any
    # Cargo build; do not install unrelated GTK/PipeWire packages or change policy.
    tools = {}
    for name in ("dpkg-query", "cc", "ar", "ld"):
        path = (Path("/usr/bin") / name).resolve(strict=True)
        tools[name] = {"absolute_path": str(path), **custody.fingerprint(path, path.parent)}
    row = command([tools["dpkg-query"]["absolute_path"], "--show",
                   "--showformat=${Package}\t${Status}\t${Version}\n", "gcc", "libc6-dev", "binutils"],
                  repo, env, logs, "linux-c-package-versions", 30, context)
    packages = installed_c_packages(observer.original_stream(logs, row, "stdout"), row["exit_code"])
    for name in ("cc", "ar", "ld"):
        row = command([tools[name]["absolute_path"], "--version"], repo, env, logs,
                      "linux-c-tool-" + name, 30, context)
        version = observer.original_stream(logs, row, "stdout").decode("utf-8")
        require(type(row["exit_code"]) is int and row["exit_code"] == 0 and version.strip(),
                "Linux C build tool version is unavailable: " + name)
        tools[name]["version"] = version
    env.update(CC=tools["cc"]["absolute_path"], AR=tools["ar"]["absolute_path"],
               CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=tools["cc"]["absolute_path"])
    return {"status": "OBSERVED_PREREQUISITES_ONLY", "packages": packages, "tools": tools,
            "product_compilation": "NOT_RUN_BY_PREFLIGHT", "host_policy_changed": False,
            "native_dependency_basis": "rusqlite bundled SQLite; selected storage/service dependency closure"}


def check_budget(raw_meminfo, free_bytes):
    available = re.findall(r"(?m)^MemAvailable:\s+([0-9]+) kB$", raw_meminfo.decode("ascii"))
    require(len(available) == 1 and type(free_bytes) is int, "resource observation incomplete")
    memory = int(available[0]) * 1024
    require(memory >= 4 * 1024**3 and free_bytes >= 12 * 1024**3, "insufficient disposable runner resources")
    return {"available_memory_bytes": memory, "free_disk_bytes": free_bytes, "cargo_jobs": 2}


def source_postcheck(report, gate, repo, profile, paths):
    if report["status"] == "UNKNOWN_NO_RETRY":
        report["source_postcheck"] = "NOT_OBSERVED_AFTER_UNKNOWN"
        return
    errors = []
    for key, callback in (
        ("gate", lambda: custody.source_snapshot(gate, report["context"]["gate_source_sha"], GATE_FILES)),
        ("product", lambda: product_snapshot(repo, report["context"]["product_source_sha"], profile, paths)),
    ):
        if key + "_before" not in report:
            continue
        try:
            after = callback(); report[key + "_after"] = after
            require(after == report[key + "_before"], key + " source changed")
        except BaseException as error:
            errors.append(type(error).__name__ + ": " + str(error))
    if errors:
        report.update(status="FAIL_OR_INCOMPLETE", source_postcheck_errors=errors)


def run(gate, repo, output):
    context = execution_context(os.environ)
    profile = admitted_product(context["product_source_sha"])
    gate, repo, output = gate.resolve(strict=True), repo.resolve(strict=True), output.absolute()
    workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    runner = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    require(gate != repo and gate.is_relative_to(workspace) and repo.is_relative_to(workspace)
            and output.is_relative_to(runner) and output != runner and ".." not in output.parts
            and not output.exists() and not output.is_relative_to(gate) and not output.is_relative_to(repo),
            "fresh and separate gate, product and output paths required")
    output.mkdir(mode=0o700)
    evidence, target, temporary = (output / n for n in ("evidence", "target", "tmp"))
    for path in (evidence, target, temporary): path.mkdir(mode=0o700)
    logs = evidence / "commands"; logs.mkdir(mode=0o700)
    observations, paths = [], ()
    report = {"schema": SCHEMA, "context": context, "status": "NOT_RUN_PREPARATION", "cases": [],
              "selected_methods_execution": "NOT_RUN",
              "started_at_utc": custody.now(), "required_methods": dict(CASES), "automatic_retry": False,
              "installed_application": "NOT_RUN", "native_render": "NOT_RUN", "power_loss_atomicity": "NOT_RUN",
              "human_acceptance": False, "formal_acceptance": False, "release_authorized": False,
              "historical_results_relabelled": False,
              "physical_internal_fixtures": {
                  "concurrent_two": "EXISTING_EXTERNAL_SNAPSHOT_AND_CHILD_RECEIPT_HOOK",
                  "service_one": "EXISTING_EXTERNAL_SQLITE_CAS_AND_PHASE_RECEIPT_HOOK",
                  "bundle_two": "INTERNAL_TEMPDIR_REMOVED_BY_TEST_DROP_NOT_RETAINED"},
              "replay_scope": "No harness retry after UNKNOWN; only after_bundle_commit forbids reissuing import. "
                              "Other selected methods intentionally exercise known NotApplied or ProjectExists outcomes."}
    custody.write_new(evidence / "started.json", {**report, "status": "UNKNOWN_IF_NO_TERMINAL_RECEIPT"})

    def command(argv, cwd, env, folder, label, timeout, ctx):
        return observer.command(argv, cwd, env, folder, label, timeout, ctx, (evidence, observations))

    try:
        for module, name in ((observer, "ci_foundation_causal.py"), (custody, "ci_storage_apply_recovery.py"),
                             (producer, "ci_sdk_native_source.py"), (fast_runner, "ci_test_runner.py")):
            require(Path(module.__file__).resolve() == gate / "tooling" / name, "verifier import origin differs")
        before_gate = custody.source_snapshot(gate, context["gate_source_sha"], GATE_FILES)
        require(all(before_gate["files"][p]["sha256"] == h for p, h in GATE_PINS.items()), "shared verifier changed")
        report["gate_before"] = before_gate
        report["retained_gate"] = observer.retain_source(gate, before_gate, evidence)
        raw_tree = custody.git(repo, "ls-tree", "-r", "-z", context["product_source_sha"])
        paths = source_paths(raw_tree, profile)
        before = product_snapshot(repo, context["product_source_sha"], profile, paths)
        report["product_before"] = before
        report["retained_product"] = observer.retain_source(repo, before, evidence)
        env = build_environment(os.environ, target, temporary, output)
        report["resources"] = check_budget(Path("/proc/meminfo").read_bytes(), shutil.disk_usage(output).free)
        native_build = linux_build_preflight(repo, env, logs, context, command)
        report["linux_build_prerequisites"] = native_build
        tools = observer.tools_identity(gate, logs, env, context, (evidence, observations)); report["tools"] = tools
        product_g0 = observe_product_g0(report, repo, evidence, before, env, logs, context, tools["python"], command)
        formatters = observed_formatters(gate, before_gate["files"][WORKFLOW]["sha256"], tools, env, logs, context, command)
        report["formatters"] = formatters
        verify_tool_identities((*tools.values(), *formatters.values()))
        barrier = producer.ProducerBarrier(command, tools["cargo"]["absolute_path"])
        formatted = barrier([formatters["cargo-fmt"]["absolute_path"], "fmt", "--all", "--", "--check"],
                            repo, env, logs, "product-format", 180, context)
        require(type(formatted["exit_code"]) is int and formatted["exit_code"] == 0, "product format rejected")
        report["product_format"] = formatted
        verify_tool_identities((*tools.values(), *formatters.values()))
        finish_product_g0(product_g0, formatted, before,
                          product_snapshot(repo, context["product_source_sha"], profile, paths))
        prepared = {"collections": {}, "steps": []}
        for label, argv, specs in producer_plan(tools["cargo"]["absolute_path"]):
            row = barrier(argv, repo, env, logs, "compile-" + label, 1500, context)
            producer.select_binary_paths(observer.original_stream(logs, row, "stdout"), row["exit_code"],
                                         repo, target, specs, testing=True)
            prepared["collections"][label] = {"command": row, "specs": specs, "testing": True}
            prepared["steps"].append(label)
        barrier.begin_sealing()
        sealed = producer.seal_compiled_role(prepared, repo, target, logs, evidence, barrier)
        binaries = {name: b for group in sealed.values() for name, b in group.items()}
        require(set(binaries) == {s["name"] for s in TARGETS.values()}, "compiled target closure differs")
        report["binaries"] = binaries
        report["producer_barrier"] = {"status": "SEALED_BEFORE_OBSERVATION", "targets_compiled_once": prepared["steps"]}
        barrier.begin_observation()
        rosters = {}
        for label, spec in TARGETS.items():
            binary = binaries[spec["name"]]
            row = barrier([binary["absolute_path"], "--list", "--format", "terse"], repo, env, logs,
                          "roster-" + label, 45, context)
            rosters[label] = compiled_roster(observer.original_stream(logs, row, "stdout"), row["exit_code"], label)
        report["rosters"] = rosters
        custody.write_new(evidence / "compiled-rosters.json", rosters)
        observe_five(report, binaries, rosters, repo, target, env, logs, context, barrier)
        producer.verify_binaries(binaries, target)
        verify_tool_identities((*tools.values(), *native_build["tools"].values(), *formatters.values()))
        producer.verify_commands(evidence, observations)
        verify_product_g0_originals(product_g0, evidence)
        report["counts"] = final_counts(report["cases"])
        report.update(status="PASS_FIVE_EXACT_SOURCE_METHODS", selected_methods_execution="FIVE_EXACT_METHODS_OBSERVED")
        report.pop("currently_selected_case", None)
    except (observer.UnknownExecution, custody.UnknownObservation) as error:
        report.update(status="UNKNOWN_NO_RETRY", error=str(error), uncertain_command=error.command)
        if report.get("product_g0", {}).get("status") != "PASS_PRODUCT_G0" and "product_g0" in report:
            report["product_g0"]["status"] = "UNKNOWN_NO_RETRY"
        if report["selected_methods_execution"] == "STARTED_TERMINAL_PENDING":
            report["selected_methods_execution"] = "UNKNOWN_NO_RETRY"
    except BaseException as error:
        report.update(status="FAIL_OR_INCOMPLETE" if report["cases"] or report.get("currently_selected_case")
                      else "NOT_RUN_PREPARATION_REJECTED", error=type(error).__name__ + ": " + str(error))
        if report.get("product_g0", {}).get("status") != "PASS_PRODUCT_G0" and "product_g0" in report:
            report["product_g0"]["status"] = "FAIL_OR_INCOMPLETE"
        if report["selected_methods_execution"] == "STARTED_TERMINAL_PENDING":
            report["selected_methods_execution"] = "INCOMPLETE_OR_FAILED"
    source_postcheck(report, gate, repo, profile, paths)
    report["commands"] = observations; report["ended_at_utc"] = custody.now()
    report = custody.strict_json(json.dumps(report, allow_nan=False).encode("utf-8"))
    custody.write_new(evidence / "receipt.json", report)
    receipt = custody.fingerprint(evidence / "receipt.json", evidence)
    require(custody.strict_json((evidence / "receipt.json").read_bytes()) == report
            and custody.fingerprint(evidence / "receipt.json", evidence) == receipt, "final receipt changed")
    custody.write_new(evidence / "receipt-readback.json", receipt)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    admission = sub.add_parser("admit"); admission.add_argument("--report", type=Path, required=True)
    execute = sub.add_parser("execute")
    execute.add_argument("--gate", type=Path, required=True)
    execute.add_argument("--product", type=Path, required=True)
    execute.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.mode == "admit":
        try:
            result = {"status": "ADMITTED_INPUT_ONLY", **admit_request(os.environ)}
        except BaseException as error:
            result = {"status": "NOT_ADMITTED", "product_execution": "NOT_RUN",
                      "error": type(error).__name__ + ": " + str(error)}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        custody.write_new(args.report, result)
        print(json.dumps(result)); return 0 if result["status"] == "ADMITTED_INPUT_ONLY" else 1
    result = run(args.gate, args.product, args.output)
    print(json.dumps({"status": result["status"], "context": result["context"], "counts": result.get("counts")}))
    return 0 if result["status"] == "PASS_FIVE_EXACT_SOURCE_METHODS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
