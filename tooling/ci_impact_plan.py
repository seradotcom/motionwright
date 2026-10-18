#!/usr/bin/env python3
"""Conservative, read-only CI planning and receipt consistency checks.

This tool is deliberately a shadow planner. It does not schedule or skip jobs,
retry native operations, authorize releases, or replace evidence admission.
Python 3.11+ and a complete local Git object database are required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any


SCHEMA = 1
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
ZERO = "0" * 40
CI = ".github/workflows/ci.yml"
PLANNER = ".github/workflows/verification-plan.yml"

# These are closed, reviewed risk mappings, not a capability inventory. Unknown
# paths expand to every workflow visible in the checked-out commit.
STUDIO_WORKFLOWS = {
    CI,
    ".github/workflows/creative-alternatives.yml",
    ".github/workflows/creative-conformance.yml",
    ".github/workflows/creative-runtime-doctor.yml",
    ".github/workflows/music-clock.yml",
    ".github/workflows/native-motion-outcubic.yml",
    ".github/workflows/native-property-fidelity.yml",
    ".github/workflows/package-candidate.yml",
    ".github/workflows/performance-acceptance.yml",
    ".github/workflows/progressive-native-preview.yml",
    ".github/workflows/v05-brand-campaign.yml",
    ".github/workflows/v05-generation-budget.yml",
    ".github/workflows/v05-narration-source.yml",
    ".github/workflows/v05-original-native-mix.yml",
    ".github/workflows/v05-otio-owner-cut.yml",
    ".github/workflows/variant-continuity.yml",
    ".github/workflows/variants-localization.yml",
}
KNOWN_PACKAGE_ROOTS = {
    "crates/domain",
    "crates/storage",
    "crates/service",
    "crates/native",
    "crates/manim-profile",
    "crates/driver-manim-community",
    "apps/desktop/src-tauri",
}
NEUTRAL_PATHS = {"README.md", "LICENSE", "CONTRIBUTING.md"}
NATIVE_HELPER_WORKFLOWS = {
    "tooling/native_motion_canvas_roundtrip.py": {
        ".github/workflows/native-render-e2e.yml",
        ".github/workflows/native-cross-app-recovery.yml",
    },
    "tooling/native_manim_e2e.py": {".github/workflows/native-manim-e2e.yml"},
    "tooling/native_mlt_sequence_e2e.py": {
        ".github/workflows/native-mlt-sequence-e2e.yml"
    },
    "tooling/native_mlt_ci_pcm_envelope.py": {
        ".github/workflows/native-mlt-sequence-e2e.yml"
    },
    "tooling/runtime_first_use_audit.py": {
        CI, ".github/workflows/runtime-first-use.yml",
        ".github/workflows/package-candidate.yml",
    },
    "tooling/package_receipt.py": {".github/workflows/package-candidate.yml"},
}


class PlanError(ValueError):
    """An input cannot be safely interpreted or bound to its source."""


def require(condition: Any, message: str) -> None:
    if not condition:
        raise PlanError(message)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        .encode("utf-8")
    ).hexdigest()


def safe_path(value: Any) -> str:
    require(isinstance(value, str) and bool(value), "path must be a nonempty string")
    require(
        not value.startswith("/")
        and "\\" not in value
        and ":" not in value
        and all(ord(c) >= 32 and ord(c) != 127 for c in value)
        and all(part not in ("", ".", "..") for part in value.split("/")),
        "unsafe repository path",
    )
    return value


def commit_sha(value: Any, *, zero: bool = False) -> str:
    require(isinstance(value, str) and HEX40.fullmatch(value), "full lowercase SHA required")
    require(zero or value != ZERO, "zero SHA is not a commit")
    return value


def git(repo: Path, *args: str) -> bytes:
    # No shells, revision expressions from users, replace refs, external diff,
    # text conversion, hooks, checkout, or dependency resolution are involved.
    env = dict(os.environ, GIT_NO_LAZY_FETCH="1")
    result = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(repo), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        timeout=30, env=env,
    )
    require(result.returncode == 0, f"Git operation failed: {args[0]}")
    return result.stdout


def git_text(repo: Path, *args: str) -> str:
    return git(repo, *args).decode("utf-8", "strict").strip()


class Tree:
    def __init__(self, repo: Path, sha: str):
        self.repo, self.sha = repo, commit_sha(sha)
        require(git_text(repo, "cat-file", "-t", sha) == "commit", "SHA is not a commit")
        self.tree_sha = git_text(repo, "rev-parse", f"{sha}^{{tree}}")
        self.entries: dict[str, tuple[str, str]] = {}
        for row in git(repo, "ls-tree", "-r", "-z", sha).split(b"\0"):
            if not row:
                continue
            metadata, path = row.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split()
            name = safe_path(path.decode("utf-8", "strict"))
            require(kind in ("blob", "commit"), "unsupported Git entry")
            self.entries[name] = (mode, oid)

    def read(self, path: str) -> bytes:
        mode, oid = self.entries[safe_path(path)]
        require(mode in ("100644", "100755"), "source input is not a regular Git blob")
        size = int(git_text(self.repo, "cat-file", "-s", oid))
        require(size <= 2_000_000, "source input exceeds bounded parser size")
        return git(self.repo, "cat-file", "blob", oid)

    def workflows(self) -> set[str]:
        return {
            p for p in self.entries
            if p.startswith(".github/workflows/")
            and p.count("/") == 2 and p.endswith((".yml", ".yaml"))
        }


def changed_paths(repo: Path, before: str, after: str) -> list[dict[str, Any]]:
    tokens = git(
        repo, "diff", "--no-ext-diff", "--no-textconv", "--name-status",
        "-z", "--find-renames", before, after, "--",
    ).split(b"\0")
    require(tokens.pop() == b"", "unterminated Git change stream")
    result = []
    index = 0
    while index < len(tokens):
        status = tokens[index].decode("ascii", "strict")
        index += 1
        require(re.fullmatch(r"[ADMT]|[RC][0-9]{1,3}", status), "unsupported diff status")
        count = 2 if status.startswith(("R", "C")) else 1
        require(index + count <= len(tokens), "truncated Git change stream")
        paths = [safe_path(t.decode("utf-8", "strict")) for t in tokens[index:index + count]]
        index += count
        result.append({"status": status, "paths": paths})
    return result


def dependency_tables(doc: dict[str, Any]):
    for key in ("dependencies", "dev-dependencies", "build-dependencies"):
        table = doc.get(key, {})
        require(isinstance(table, dict), "unsupported Cargo dependency table")
        yield table
    targets = doc.get("target", {})
    require(isinstance(targets, dict), "unsupported Cargo target table")
    for target in targets.values():
        require(isinstance(target, dict), "unsupported Cargo target")
        yield from dependency_tables(target)


def resolve_dependency(root: str, relative: Any) -> str:
    require(isinstance(relative, str) and relative, "invalid Cargo path dependency")
    require(not relative.startswith("/") and "\\" not in relative and ":" not in relative,
            "Cargo dependency escapes repository")
    resolved = posixpath.normpath(posixpath.join(root, relative))
    if resolved == ".":
        return ""
    return safe_path(resolved)


def cargo_graph(tree: Tree) -> dict[str, Any]:
    """Union of all local normal/dev/build/optional/target path dependencies.

    No cargo metadata is run: planning must not fetch crates or compile code.
    Unsupported/missing relationships broaden the plan instead of disappearing.
    """
    docs: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    hashes: dict[str, str] = {}
    for path in sorted(tree.entries):
        if path != "Cargo.toml" and not path.endswith("/Cargo.toml"):
            continue
        try:
            raw = tree.read(path)
            hashes[path] = hashlib.sha256(raw).hexdigest()
            docs[path.removesuffix("/Cargo.toml") if path != "Cargo.toml" else ""] = (
                tomllib.loads(raw.decode("utf-8"))
            )
        except (PlanError, UnicodeError, tomllib.TOMLDecodeError):
            issues.append(f"unreadable-cargo-manifest:{path}")
    if "" not in docs:
        issues.append("missing-root-cargo-manifest")
    names: dict[str, str] = {}
    for root, doc in docs.items():
        package = doc.get("package", {})
        if isinstance(package, dict) and isinstance(package.get("name"), str):
            names[root] = package["name"]
    edges: dict[str, set[str]] = {root: set() for root in names}
    for root, doc in docs.items():
        if root not in names:
            continue
        try:
            ancestors = [p for p in docs if "workspace" in docs[p]
                         and (not p or root == p or root.startswith(p + "/"))]
            require(bool(ancestors), "workspace root unavailable")
            workspace_root = max(ancestors, key=len)
            workspace = docs[workspace_root].get("workspace", {})
            require(isinstance(workspace, dict), "unsupported workspace")
            inherited = workspace.get("dependencies", {})
            require(isinstance(inherited, dict), "unsupported workspace dependencies")
            # Explicit relocated workspaces and replacement tables need review.
            require("workspace" not in doc.get("package", {}), "relocated workspace unsupported")
            for table in dependency_tables(doc):
                for alias, spec in table.items():
                    dep_root = root
                    if isinstance(spec, dict) and spec.get("workspace") is True:
                        require(alias in inherited, "unresolved workspace dependency")
                        spec, dep_root = inherited[alias], workspace_root
                    if isinstance(spec, dict) and "path" in spec:
                        target = resolve_dependency(dep_root, spec["path"])
                        require(target in names, "missing path dependency manifest")
                        edges[root].add(target)
        except PlanError as error:
            issues.append(f"incomplete-cargo-graph:{root}:{error}")
    # Patches/replace and workspace declarations can introduce edges that are
    # not normal package dependencies. Changes to their manifests expand all.
    rootdoc = docs.get("", {})
    members = rootdoc.get("workspace", {}).get("members", [])
    if not isinstance(members, list):
        issues.append("unsupported-workspace-members")
    else:
        for member in members:
            if not isinstance(member, str) or any(c in member for c in "*?["):
                issues.append("workspace-pattern-needs-review")
            elif member not in names:
                issues.append(f"workspace-member-missing:{member}")
    return {
        "source_sha": tree.sha, "manifests_sha256": hashes,
        "packages": names, "dependencies": {p: sorted(v) for p, v in edges.items()},
        "issues": sorted(set(issues)),
    }


def affected_packages(paths: set[str], graphs: list[dict[str, Any]]) -> list[str]:
    roots = set().union(*(set(g["packages"]) for g in graphs))
    affected = {root for root in roots for p in paths
                if root and (p == root or p.startswith(root + "/"))}
    while True:
        expanded = affected | {
            consumer for graph in graphs for consumer, dependencies in graph["dependencies"].items()
            if affected.intersection(dependencies)
        }
        if expanded == affected:
            return sorted(affected)
        affected = expanded


def build_plan(repo: Path, *, base: str, head: str, checkout: str,
               event: str, repository: str, candidate: bool = False) -> dict[str, Any]:
    commit_sha(base, zero=True)
    commit_sha(head)
    commit_sha(checkout)
    require(event in ("pull_request", "push", "workflow_dispatch"), "unsupported event")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository), "invalid repository")
    require(git_text(repo, "rev-parse", "--is-shallow-repository") == "false",
            "complete history is required; fetch depth must be zero")
    require(git_text(repo, "rev-parse", "HEAD") == checkout, "checkout SHA mismatch")
    require(not git_text(repo, "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching"),
            "tracked working tree changes or untracked/ignored files prevent exact-source planning")
    trees = {s: Tree(repo, s) for s in {head, checkout} | ({base} if base != ZERO else set())}
    current = trees[checkout]
    parents = git_text(repo, "show", "-s", "--format=%P", checkout).split()
    merge_base = None
    force_full: list[str] = []
    changes = []
    if event == "pull_request":
        require(base != ZERO, "PR base commit is required")
        bases = git_text(repo, "merge-base", "--all", base, head).split()
        require(len(bases) == 1, "PR requires one complete, unambiguous merge base")
        merge_base = bases[0]
        trees.setdefault(merge_base, Tree(repo, merge_base))
        require(checkout == head or (len(parents) == 2 and parents == [base, head]),
                "PR checkout must be exact head or exact ordered base/head merge")
        changes = changed_paths(repo, merge_base, head)
        if checkout != head:
            changes += changed_paths(repo, base, checkout)
    else:
        require(checkout == head, "non-PR checkout must equal head")
        if base == ZERO:
            force_full.append("no-previous-commit")
        else:
            changes = changed_paths(repo, base, head)
            ancestor = subprocess.run(
                ["git", "--no-replace-objects", "-C", str(repo), "merge-base", "--is-ancestor", base, head],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30,
                env=dict(os.environ, GIT_NO_LAZY_FETCH="1"),
            ).returncode
            require(ancestor in (0, 1), "cannot establish base ancestry")
            if ancestor == 1:
                force_full.append("base-is-not-head-ancestor")
    paths = {p for change in changes for p in change["paths"]}
    graphs = [cargo_graph(trees[s]) for s in sorted(trees)]
    affected = affected_packages(paths, graphs)
    graph_issues = sorted({issue for graph in graphs for issue in graph["issues"]})
    if graph_issues:
        force_full.append("incomplete-dependency-graph")
    inventory = current.workflows()
    require(CI in inventory, "mandatory CI workflow is missing")
    workflow_hashes = {p: hashlib.sha256(current.read(p)).hexdigest() for p in sorted(inventory)}
    old_workflows = set().union(*(tree.workflows() for tree in trees.values()))
    deleted_workflows = sorted(old_workflows - inventory)
    selected: dict[str, dict[str, Any]] = {}

    def select(workflows: set[str], stage: str, reason: str) -> None:
        for path in sorted(workflows & inventory):
            item = selected.setdefault(path, {"workflow": path, "stages": set(), "reasons": set()})
            item["stages"].add(stage)
            item["reasons"].add(reason)

    select({CI, PLANNER}, "G0", "always-fast-contract-format-lint-and-security-checks")
    unknown = []
    for path in sorted(paths):
        if path in NEUTRAL_PATHS:
            continue
        if (path.endswith(("Cargo.toml", "Cargo.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"))
                or path in ("SOURCE_LOCK.json", "rust-toolchain.toml", "rust-toolchain")
                or path.startswith((".cargo/", ".github/", "vendor/"))):
            force_full.append(f"dependency-or-workflow-boundary:{path}")
            continue
        # All known Rust product consumers can alter persistence, transport or
        # generated native sources. Keep G2 broad until smaller contracts exist.
        owners = {root for root in affected if path.startswith(root + "/")}
        if owners:
            if not owners <= KNOWN_PACKAGE_ROOTS:
                unknown.append(path)
            select({CI, ".github/workflows/native-production.yml"}, "G1", f"cargo-path-closure:{path}")
            select(inventory, "G2", f"shared-product-or-native-dependency:{path}")
            continue
        if path.startswith("apps/studio/"):
            select(STUDIO_WORKFLOWS, "G1", f"studio-source-or-test:{path}")
            # A Studio edit can change the authored source, owner review or the
            # final rendering. Filename resemblance is insufficient to narrow.
            select(inventory, "G2", f"studio-to-native-continuity:{path}")
            continue
        helper = path
        if path.startswith("tooling/tests/test_") and path.endswith(".py"):
            helper = "tooling/" + path.removeprefix("tooling/tests/test_")
        if helper in NATIVE_HELPER_WORKFLOWS:
            select(NATIVE_HELPER_WORKFLOWS[helper], "G2", f"closed-helper-risk:{path}")
            # Include every direct workflow reference as well as reviewed
            # transitive consumers, over both old and current source trees.
            for tree in trees.values():
                for workflow in tree.workflows() & inventory:
                    text = tree.read(workflow).decode("utf-8", "strict")
                    if helper in text or path in text or posixpath.basename(path) in text:
                        select({workflow}, "G1", f"workflow-source-consumer:{path}")
            continue
        unknown.append(path)
    if unknown:
        force_full.append("unknown-paths")
    if deleted_workflows:
        force_full.append("workflow-removal-requires-explicit-disposition")
    if force_full:
        select(inventory, "G2", ";".join(sorted(set(force_full))))
    if candidate:
        select(inventory, "G3", "candidate-full-observed-workflow-inventory")
    plan = {
        "schema_version": SCHEMA, "kind": "motionwright-ci-impact-plan", "mode": "shadow",
        "source": {
            "repository": repository, "event": event, "base_sha": base,
            "base_tree_sha": trees[base].tree_sha if base != ZERO else None,
            "head_sha": head, "head_tree_sha": trees[head].tree_sha,
            "checkout_sha": checkout, "tree_sha": current.tree_sha,
            "checkout_parents": parents, "merge_base_sha": merge_base,
            "scope": "pr-merge" if event == "pull_request" and checkout != head else "source-head",
        },
        "candidate": candidate,
        "selector": {
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "risk_map_sha256": digest({
                "studio": sorted(STUDIO_WORKFLOWS),
                "package_roots": sorted(KNOWN_PACKAGE_ROOTS),
                "neutral_paths": sorted(NEUTRAL_PATHS),
                "helpers": {p: sorted(v) for p, v in NATIVE_HELPER_WORKFLOWS.items()},
            }),
        },
        "changes": sorted({json.dumps(x, sort_keys=True) for x in changes}),
        "changed_paths": sorted(paths), "unknown_paths": sorted(unknown),
        "dependency_graphs": graphs, "affected_package_roots": affected,
        "workflow_inventory_sha256": workflow_hashes,
        "selected_workflows": [
            {"workflow": p, "stages": sorted(selected[p]["stages"]), "reasons": sorted(selected[p]["reasons"])}
            for p in sorted(selected)
        ],
        "blockers": [f"removed-workflow:{p}" for p in deleted_workflows],
        "expansion_reasons": sorted(set(force_full)),
        "accounting": {"executions_avoided": 0, "minutes_saved": 0,
                       "reason": "shadow-planning-does-not-change-existing-execution"},
        "evidence_claim": "selection-only-no-product-test-or-release-verdict",
    }
    plan["changes"] = [json.loads(x) for x in plan["changes"]]
    plan["plan_id"] = digest(plan)
    return plan


def unique_objects(items: Any, key: str, label: str) -> dict[str, Any]:
    require(isinstance(items, list), f"{label} must be a list")
    result = {}
    for item in items:
        require(isinstance(item, dict), f"invalid {label} item")
        value = item.get(key)
        require(isinstance(value, str) and bool(value), f"missing {label} identity")
        require(value not in result, f"duplicate {label} identity")
        result[value] = item
    return result


def aggregate(plan: dict[str, Any], expected: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    """Validate a reviewed expectation manifest against retained observations.

    The caller must independently authenticate original GitHub and source
    receipts, and establish the complete applicable job/check inventory. This
    function checks their consistency; it cannot authenticate self-written JSON.
    A successful return is not a technical or contractual acceptance verdict.
    """
    require(type(plan.get("schema_version")) is int
            and plan.get("schema_version") == SCHEMA and plan.get("mode") == "shadow",
            "unsupported plan schema or enforcement mode")
    require(plan.get("kind") == "motionwright-ci-impact-plan", "invalid plan kind")
    body = {k: v for k, v in plan.items() if k != "plan_id"}
    require(plan.get("plan_id") == digest(body), "plan digest mismatch")
    require(not plan.get("blockers"), "plan has unresolved workflow dispositions")
    plan_id = plan["plan_id"]
    source = plan["source"]
    for sha_key in ("checkout_sha", "tree_sha", "head_sha"):
        commit_sha(source[sha_key])
    for label, doc in (("expectations", expected), ("observations", observed)):
        require(type(doc.get("schema_version")) is int
                and doc.get("schema_version") == SCHEMA and doc.get("plan_id") == plan_id,
                f"{label} are not bound to this plan")
        require(doc.get("repository") == source["repository"], f"{label} repository mismatch")
    selected = unique_objects(plan.get("selected_workflows"), "workflow", "selection")
    require(bool(selected), "empty selection cannot pass")
    wanted = unique_objects(expected.get("runs"), "workflow", "expected workflow")
    actual = unique_objects(observed.get("runs"), "workflow", "observed workflow")
    require(set(wanted) == set(selected), "selected workflows missing from expected run manifest")
    require(set(actual) == set(wanted), "selected workflow observations are missing or unexpected")
    jobs_checked = 0
    checks_checked = 0
    run_ids: set[str] = set()
    job_ids: set[str] = set()
    for workflow in sorted(wanted):
        safe_path(workflow)
        want, got = wanted[workflow], actual[workflow]
        require(isinstance(want.get("workflow_sha256"), str) and HEX64.fullmatch(want["workflow_sha256"])
                and want["workflow_sha256"] == plan["workflow_inventory_sha256"].get(workflow),
                "expected workflow definition does not match plan inventory")
        require(isinstance(want.get("run_id"), str) and re.fullmatch(r"[1-9][0-9]*", want["run_id"]),
                "exact expected run ID required")
        require(want["run_id"] not in run_ids, "run ID reused across workflows")
        run_ids.add(want["run_id"])
        require(type(want.get("run_attempt")) is int and want["run_attempt"] >= 1,
                "exact expected run attempt required")
        commit_sha(want.get("event_sha"))
        commit_sha(want.get("workflow_sha"))
        for key in ("run_id", "run_attempt", "event_sha", "workflow_sha", "workflow_sha256"):
            require(got.get(key) == want[key], f"wrong workflow execution identity: {key}")
        require(type(got.get("run_attempt")) is int, "invalid observed run attempt type")
        require(got.get("status") == "completed" and got.get("conclusion") == "success",
                "selected workflow is incomplete, skipped, cancelled or failed")
        expected_jobs = unique_objects(want.get("jobs"), "name", "expected job")
        observed_jobs = unique_objects(got.get("jobs"), "name", "observed job")
        require(bool(expected_jobs) and set(expected_jobs) == set(observed_jobs),
                "required jobs are missing, empty or unexpected")
        for name, job in observed_jobs.items():
            require(isinstance(job.get("job_id"), str) and re.fullmatch(r"[1-9][0-9]*", job["job_id"]),
                    "original job ID is required")
            require(job["job_id"] not in job_ids, "original job ID reused")
            job_ids.add(job["job_id"])
            require(job.get("run_id") == want["run_id"] and job.get("run_attempt") == want["run_attempt"],
                    "job was taken from another run or attempt")
            require(type(job.get("run_attempt")) is int, "invalid observed job attempt type")
            require(job.get("status") == "completed" and job.get("conclusion") == "success",
                    "selected job is missing a completed success")
            binding = job.get("source", {})
            require(isinstance(binding, dict), "missing job source receipt")
            for key in ("checkout_sha", "tree_sha"):
                require(binding.get(key) == source[key], f"job source mismatch: {key}")
            require(binding.get("plan_id") == plan_id, "job source receipt belongs to another plan")
            require(isinstance(job.get("original_log_sha256"), str)
                    and HEX64.fullmatch(job["original_log_sha256"]), "original log hash missing")
            required_checks = expected_jobs[name].get("checks")
            require(isinstance(required_checks, list) and bool(required_checks)
                    and all(isinstance(x, str) and x for x in required_checks)
                    and len(required_checks) == len(set(required_checks)), "explicit nonempty check inventory required")
            checks = unique_objects(job.get("checks"), "id", "check")
            require(set(checks) == set(required_checks), "required checks omitted or unexpected")
            require(all(check.get("outcome") == "passed" for check in checks.values()),
                    "required check was skipped, failed, unknown or not run")
            jobs_checked += 1
            checks_checked += len(checks)
    return {
        "schema_version": SCHEMA, "plan_id": plan_id,
        "result": "RECEIPT_CONSISTENCY_ACCEPTED",
        "workflow_count": len(wanted), "job_count": jobs_checked, "check_count": checks_checked,
        "authenticity_verified": False, "contractual_acceptance": False,
        "release_ready": False,
        "limits": "Requires independent original-receipt admission and complete applicable check inventory.",
    }


def read_json(path: Path) -> dict[str, Any]:
    require(path.stat().st_size <= 10_000_000, "JSON input exceeds bound")

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(PlanError("nonfinite JSON number")))
    require(isinstance(result, dict), "JSON object required")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--repo", type=Path, default=Path("."))
    for name in ("base", "head", "checkout", "repository"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--event", choices=("pull_request", "push", "workflow_dispatch"), required=True)
    p.add_argument("--candidate", action="store_true")
    p.add_argument("--output", type=Path)
    a = sub.add_parser("aggregate")
    a.add_argument("--plan", type=Path, required=True)
    a.add_argument("--expected", type=Path, required=True)
    a.add_argument("--observed", type=Path, required=True)
    a.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "plan":
            result = build_plan(args.repo, base=args.base, head=args.head, checkout=args.checkout,
                                event=args.event, repository=args.repository, candidate=args.candidate)
        else:
            result = aggregate(read_json(args.plan), read_json(args.expected), read_json(args.observed))
        rendered = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0
    except (PlanError, OSError, UnicodeError, ValueError, KeyError, TypeError,
            subprocess.TimeoutExpired) as error:
        print(f"verification planning rejected: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
