#!/usr/bin/env python3
"""Apply a closed selection to CI jobs and reject missing selected results.

This covers .github/workflows/ci.yml only. It does not schedule other workflows,
admit test-case evidence, retry operations, or give a release verdict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import types


WORKFLOW = ".github/workflows/ci.yml"
SELF = "tooling/ci_job_plan.py"
PROVIDER = "tooling/ci_impact_plan.py"
REVIEWED_PROVIDER_SHA256 = "4033074d76d7e337c3869eb08d4355523b46151c928848b84f6e96b100f60afa"
SHA = re.compile(r"[0-9a-f]{40}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
ZERO = "0" * 40
ALWAYS = ("source-policy", "rust-core", "studio", "desktop-linux")
SELECTABLE = ("rust-tests", "studio-tests", "browser-acceptance")
JOBS = (*ALWAYS, *SELECTABLE)

# An empty review admits no exclusions. Before adding neutral paths, inspect
# static and dynamic readers in Rust, Studio, browser fixtures and generators.
# A Cargo dependency graph alone does not prove those files are independent.
# The reviewed inventory must bind every tracked input except this policy file
# and the explicitly reviewed neutral paths. Any later input change disables it.
NEUTRAL_INPUT_REVIEW = None


class PlanError(ValueError):
    pass


def require(value, message):
    if not value:
        raise PlanError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON field")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(PlanError("Nonfinite JSON")))
    require(isinstance(value, dict), "JSON object required")
    return value


def read(path):
    path = Path(path).absolute()
    for component in (path, *path.parents):
        info = component.lstat()
        require(not stat.S_ISLNK(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400, "Linked input path")
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_size <= 16_000_000, "Input is not a bounded regular file")
    raw = path.read_bytes()
    require(len(raw) == info.st_size, "Input changed during read")
    return raw


def git_env():
    value = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    value.update(GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull,
                 GIT_NO_LAZY_FETCH="1", GIT_CONFIG_NOSYSTEM="1",
                 GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0")
    return value


def git(repo, *args):
    process = subprocess.run(["git", "--no-replace-objects", "-c", "core.fsmonitor=false",
                              "-C", str(repo), *args], env=git_env(), capture_output=True, timeout=45)
    require(process.returncode == 0, "Git source read failed: " + args[0])
    return process.stdout


def context():
    keys = ("GITHUB_REPOSITORY", "GITHUB_EVENT_NAME", "GITHUB_SHA", "GITHUB_REF",
            "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF")
    result = {key: os.environ.get(key) for key in keys}
    require(all(isinstance(x, str) and x for x in result.values()), "Incomplete workflow context")
    require(SHA.fullmatch(result["GITHUB_SHA"]) and SHA.fullmatch(result["GITHUB_WORKFLOW_SHA"]),
            "Workflow source must use exact SHAs")
    require(result["GITHUB_RUN_ID"].isdecimal() and int(result["GITHUB_RUN_ID"]) > 0
            and result["GITHUB_RUN_ATTEMPT"].isdecimal() and int(result["GITHUB_RUN_ATTEMPT"]) > 0,
            "Invalid workflow execution identity")
    require(result["GITHUB_EVENT_NAME"] in ("pull_request", "push", "workflow_dispatch"), "Unsupported CI event")
    return result


def blob(repo, sha, path):
    listing = git(repo, "ls-tree", "-z", sha, "--", path).split(b"\0")
    require(len(listing) == 2 and listing[1] == b"", "Missing tracked source: " + path)
    metadata, name = listing[0].split(b"\t", 1)
    mode, kind, oid = metadata.decode("ascii").split()
    require(mode in ("100644", "100755") and kind == "blob" and name.decode() == path,
            "Source is not a regular Git blob: " + path)
    raw = git(repo, "cat-file", "blob", oid)
    require(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == oid,
            "Raw Git blob identity changed")
    return raw


def source(repo, execution):
    repo = Path(repo).resolve(strict=True)
    sha = git(repo, "rev-parse", "HEAD").decode().strip()
    require(sha == execution["GITHUB_SHA"], "Checkout does not match this workflow SHA")
    raw = git(repo, "cat-file", "commit", sha)
    require(hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == sha,
            "Raw commit identity changed")
    header = raw.split(b"\n\n", 1)[0].splitlines()
    trees = [line[5:].decode() for line in header if line.startswith(b"tree ")]
    require(len(trees) == 1 and SHA.fullmatch(trees[0]), "Missing raw source tree")
    require(not git(repo, "diff", "--no-ext-diff", "--no-textconv", "--name-only", "HEAD", "--"),
            "Tracked source changed during CI")
    own_git = blob(repo, sha, SELF)
    own_execution = read(Path(__file__))
    require(own_execution == own_git or
            (os.name == "nt" and b"\r\n" not in own_git and own_execution == own_git.replace(b"\n", b"\r\n")),
            "Executed job planner differs from its source commit")
    lock = blob(repo, sha, "Cargo.lock")
    observed_lock = read(repo / "Cargo.lock")
    require(observed_lock == lock or
            (os.name == "nt" and b"\r\n" not in lock and observed_lock == lock.replace(b"\n", b"\r\n")),
            "Tracked Cargo.lock differs; do not regenerate it")
    return {"checkout_sha": sha, "tree_sha": trees[0],
            "parents": [line[7:].decode() for line in header if line.startswith(b"parent ")],
            "workflow_sha256": digest(blob(repo, sha, WORKFLOW)),
            "planner_git_sha256": digest(own_git), "planner_execution_sha256": digest(own_execution),
            "cargo_lock_git_sha256": digest(lock), "cargo_lock_worktree_sha256": digest(observed_lock)}


def load_provider(repo, sha):
    raw = blob(repo, sha, PROVIDER)
    require(read(Path(repo) / PROVIDER) == raw, "Impact provider worktree differs from its Git source")
    if digest(raw) != REVIEWED_PROVIDER_SHA256:
        return None, digest(raw)
    module = types.ModuleType("_motionwright_ci_job_impact_provider")
    module.__file__ = str(Path(repo) / PROVIDER)
    exec(compile(raw, module.__file__, "exec"), module.__dict__)
    return module, digest(raw)


def event_inputs(execution, event):
    name = execution["GITHUB_EVENT_NAME"]
    if name == "pull_request":
        base, head = event["pull_request"]["base"]["sha"], event["pull_request"]["head"]["sha"]
    elif name == "push":
        base, head = event["before"], event["after"]
        require(head == execution["GITHUB_SHA"], "Push payload and executed source differ")
    else:
        base, head = ZERO, execution["GITHUB_SHA"]
    require(isinstance(base, str) and SHA.fullmatch(base)
            and isinstance(head, str) and SHA.fullmatch(head) and head != ZERO, "Invalid event source identity")
    return base, head


def reviewed_neutral_paths(repo, impact):
    review = NEUTRAL_INPUT_REVIEW
    if not isinstance(review, dict) or review.get("status") != "REVIEWED_CLOSED_INPUTS":
        return set(), "no-complete-neutral-input-review"
    paths = review.get("neutral_paths")
    require(isinstance(paths, list) and paths and len(paths) == len(set(paths))
            and set(paths) <= {"README.md", "LICENSE", "CONTRIBUTING.md"}, "Invalid neutral path catalog")
    require(review.get("unresolved_consumers") == [], "Unresolved neutral-input consumers")
    expected = review.get("consumer_inventory_sha256")
    require(isinstance(expected, str) and HASH.fullmatch(expected), "Missing reviewed consumer inventory")
    for graph in impact["dependency_graphs"]:
        inventory = []
        for row in git(repo, "ls-tree", "-r", "-z", graph["source_sha"]).split(b"\0"):
            if not row:
                continue
            metadata, path = row.split(b"\t", 1)
            if path.decode("utf-8") not in {*paths, SELF}:
                inventory.append([path.decode("utf-8"), metadata.decode("ascii")])
        if digest(canonical(sorted(inventory))) != expected:
            return set(), "reviewed-consumer-inventory-changed"
    return set(paths), "closed-neutral-input-review-applied"


def select_jobs(impact, execution, neutral_paths):
    reasons = []
    if execution["GITHUB_EVENT_NAME"] == "workflow_dispatch":
        reasons.append("manual-candidate-full-CI")
    if execution["GITHUB_REF"] == "refs/heads/main":
        reasons.append("main-full-CI")
    if impact.get("candidate"):
        reasons.append("G3-candidate-full-CI")
    reasons.extend(impact.get("expansion_reasons", []))
    if impact.get("blockers"):
        reasons.append("unresolved-source-or-workflow-disposition")
    if impact.get("unknown_paths"):
        reasons.append("unknown-inputs")
    graphs = impact.get("dependency_graphs", [])
    if not graphs or any(graph.get("issues") for graph in graphs):
        reasons.append("incomplete-consumer-graph")
    changed = impact.get("changed_paths", [])
    if not changed:
        reasons.append("no-complete-nonempty-change-set")
    if any(path not in neutral_paths for path in changed):
        reasons.append("test-input-independence-not-demonstrated")
    full = bool(reasons)
    return {job: {"selected": job in ALWAYS or full,
                  "reason": "always-source-format-lint-typecheck-build-and-desktop-contracts" if job in ALWAYS
                  else (";".join(sorted(set(reasons))) if full else "reviewed-neutral-inputs-only")}
            for job in JOBS}


def make_plan(repo, event, execution):
    observed = source(repo, execution)
    require(not git(repo, "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching"),
            "Planning requires an unchanged checkout before dependencies or outputs")
    base, head = event_inputs(execution, event)
    provider, provider_hash = load_provider(repo, observed["checkout_sha"])
    if provider is None:
        impact = {"changed_paths": [], "unknown_paths": [], "dependency_graphs": [],
                  "affected_package_roots": [], "candidate": True, "blockers": [],
                  "expansion_reasons": ["unreviewed-impact-provider-full-CI"]}
        neutral, neutral_reason = set(), "provider-review-required"
    else:
        original_environment = dict(os.environ)
        try:
            os.environ.clear()
            os.environ.update(git_env_from(original_environment))
            impact = provider.build_plan(Path(repo), base=base, head=head, checkout=observed["checkout_sha"],
                                         event=execution["GITHUB_EVENT_NAME"], repository=execution["GITHUB_REPOSITORY"],
                                         candidate=execution["GITHUB_EVENT_NAME"] == "workflow_dispatch",
                                         allow_advanced_pr_base=execution["GITHUB_EVENT_NAME"] == "pull_request")
        finally:
            os.environ.clear()
            os.environ.update(original_environment)
        neutral, neutral_reason = reviewed_neutral_paths(repo, impact)
    result = {"schema": "motionwright.ci-job-plan/1", "workflow": WORKFLOW,
              "execution_context": execution, "source": observed,
              "event_source": {"base_sha": base, "head_sha": head},
              "event_payload_sha256": digest(canonical(event)),
              "impact_provider_sha256": provider_hash,
              "impact_plan_id": impact.get("plan_id"),
              "impact_source": impact.get("source"),
              "impact_source_status": "SOURCE_RELATIONSHIP_VERIFIED" if provider is not None
              else "UNVERIFIED_PROVIDER_FULL_CI_REQUIRED",
              "change_ranges": impact.get("change_ranges", []),
              "changed_paths": impact["changed_paths"],
              "dependency_graphs": impact["dependency_graphs"],
              "affected_package_roots": impact["affected_package_roots"],
              "neutral_review_status": neutral_reason,
              "selection_policy_sha256": digest(canonical({"always": ALWAYS, "selectable": SELECTABLE,
                                                           "neutral_review": NEUTRAL_INPUT_REVIEW})),
              "jobs": select_jobs(impact, execution, neutral),
              "test_case_results_admitted": False, "other_workflows_controlled": False,
              "release_verdict": False}
    result["plan_id"] = digest(canonical(result))
    return result


def git_env_from(original):
    result = {k: v for k, v in original.items() if not k.upper().startswith("GIT_")}
    result.update(GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull, GIT_NO_LAZY_FETCH="1",
                  GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0")
    return result


def aggregate(plan, needs, execution, observed_source):
    require(plan.get("schema") == "motionwright.ci-job-plan/1" and plan.get("workflow") == WORKFLOW,
            "Unsupported CI job plan")
    require(plan.get("plan_id") == digest(canonical({k: v for k, v in plan.items() if k != "plan_id"})),
            "Plan digest differs")
    require(plan["execution_context"] == execution and plan["source"] == observed_source,
            "Plan belongs to another source, workflow, run or attempt")
    require(set(plan["jobs"]) == set(JOBS) and set(needs) == {"plan", *JOBS}, "Incomplete job inventory")
    require(needs["plan"].get("result") == "success"
            and needs["plan"].get("outputs", {}).get("plan_id") == plan["plan_id"], "Original planning job did not succeed")
    outcomes, failures = {}, []
    for job in JOBS:
        expected = plan["jobs"][job]
        require(type(expected.get("selected")) is bool, "Invalid selected-job boolean")
        require(job not in ALWAYS or expected["selected"], "An always check was deselected")
        actual = needs[job]
        status = actual.get("result")
        outputs = actual.get("outputs", {})
        if not expected["selected"]:
            valid = status == "skipped" and outputs == {}
            outcomes[job] = {"applicability": "NOT_APPLICABLE_TO_REVIEWED_CHANGESET", "job_result": status,
                             "execution_claim": "NOT_EXECUTED" if valid else "INCONSISTENT_NO_ADMISSION",
                             "reason": expected["reason"]}
        else:
            valid = status == "success" and all(outputs.get(key) == str(value)
                     for key, value in observed_source.items() if key != "parents") and all(
                     outputs.get(key) == value for key, value in {
                         "plan_id": plan["plan_id"], "run_id": execution["GITHUB_RUN_ID"],
                         "run_attempt": execution["GITHUB_RUN_ATTEMPT"], "emitter_job": job}.items())
            outcomes[job] = {"applicability": "REQUIRED", "job_result": status,
                             "source_binding_valid": valid, "reason": expected["reason"]}
        if not valid:
            failures.append(job)
    return {"schema": "motionwright.ci-job-outcomes/1", "plan_id": plan["plan_id"],
            "source": observed_source, "execution_context": execution, "jobs": outcomes,
            "status": "JOB_SELECTION_CONSISTENT" if not failures else "FAIL",
            "inconsistent_jobs": failures, "test_case_results_admitted": False,
            "formal_acceptance": False, "release_verdict": False}


def write_new(path, value, repo):
    path = Path(path).absolute()
    require(".." not in path.parts, "Output path must not traverse parents")
    require(Path(repo).resolve() not in (path, *path.parents), "Evidence must stay outside the source checkout")
    for parent in path.parents:
        if parent.exists() or parent.is_symlink():
            info = parent.lstat()
            require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                    and not getattr(info, "st_file_attributes", 0) & 0x400, "Linked output ancestor")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, indent=2, sort_keys=True, allow_nan=False).encode() + b"\n"
    with path.open("xb") as stream:
        require(stream.write(raw) == len(raw), "Short evidence write")
        stream.flush()
        os.fsync(stream.fileno())
    require(read(path) == raw, "Evidence readback differs")


def github_outputs(path, values):
    for key, value in values.items():
        require(re.fullmatch(r"[a-z][a-z0-9_]*", key) and isinstance(value, str)
                and not any(c in value for c in "\r\n"), "Unsafe GitHub output")
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write("".join(f"{key}={value}\n" for key, value in values.items()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "observe", "aggregate"))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--event-path", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-id")
    args = parser.parse_args(argv)
    require(sys.flags.isolated and sys.dont_write_bytecode, "Use python -I -B")
    execution = context()
    if args.operation == "plan":
        require(args.event_path and args.output and args.github_output, "Missing planner outputs or original event")
        result = make_plan(args.repo, strict_json(read(args.event_path)), execution)
        write_new(args.output, result, args.repo)
        outputs = {"plan_id": result["plan_id"]}
        outputs.update({job.replace("-", "_"): str(result["jobs"][job]["selected"]).lower() for job in SELECTABLE})
        github_outputs(args.github_output, outputs)
    elif args.operation == "observe":
        result = source(args.repo, execution)
        require(args.github_output, "Missing source observation output")
        require(args.plan_id and HASH.fullmatch(args.plan_id), "Missing original planning job identity")
        emitter = os.environ.get("GITHUB_JOB")
        require(emitter in JOBS, "Unknown observation job")
        outputs = {k: str(v) for k, v in result.items() if k != "parents"}
        outputs.update(plan_id=args.plan_id, run_id=execution["GITHUB_RUN_ID"],
                       run_attempt=execution["GITHUB_RUN_ATTEMPT"], emitter_job=emitter)
        github_outputs(args.github_output, outputs)
    else:
        require(args.plan and args.output, "Missing retained plan or aggregate output")
        result = aggregate(strict_json(read(args.plan)), strict_json(os.environ["CI_NEEDS_JSON"]),
                           execution, source(args.repo, execution))
        write_new(args.output, result, args.repo)
    print(json.dumps({"scope": WORKFLOW, "status": result.get("status", "SOURCE_OR_SELECTION_RECORDED"),
                      "test_case_results_admitted": False}))
    return 1 if result.get("status") == "FAIL" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PlanError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "NOT_ADMITTED", "error": str(error)}), file=sys.stderr)
        raise SystemExit(2)
