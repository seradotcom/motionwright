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

# This narrowly reviewed profile is invalidated by any other tracked input.
# Root prose is not a runtime fixture. The history helper remains executable
# only through its existing source gates; changes to its Python AST are NOT
# eligible. G0, its history consumer and broad security test discovery remain.
# Planner files are excluded only to avoid a self-referential inventory hash;
# changing either planner is never an eligible change and always expands CI.
NONPRODUCT_PLANNERS = {"tooling/ci_impact_plan.py", "tooling/ci_job_plan.py"}
NONPRODUCT_DOCUMENTS = {"CONTRIBUTING.md", "README.md"}
NONPRODUCT_HISTORY = "tooling/conformance_history_guard.py"
NONPRODUCT_CONSUMERS = {
    ".github/workflows/history-archive-conformance.yml",
    ".github/workflows/security-delivery.yml",
}
REVIEWED_NONPRODUCT_INPUTS = {
    "status": "REVIEWED_CLOSED_INPUTS",
    "documents": ["CONTRIBUTING.md", "README.md"],
    "python_asts": {"tooling/conformance_history_guard.py": "db86256231cacb883a0a1fb908b81992a298c1706162def90de71cd5c1f6df35"},
    "consumer_inventory_sha256": "85a1948d4cd703582dcf939c14ac06566c2de7988f071499270121f923ad8b94",
    "unresolved_consumers": [],
}


def nonproduct_python_ast(raw: bytes) -> str:
    """Bound comments/format changes to the same reviewed UTF-8 Python AST."""
    import ast
    import io
    import tokenize

    require(tokenize.detect_encoding(io.BytesIO(raw).readline)[0] == "utf-8",
            "nonproduct Python encoding is not reviewed UTF-8")
    module = ast.parse(raw, type_comments=True, feature_version=(3, 11))

    def normalized(value):
        if isinstance(value, ast.AST):
            # Empty type_params is added to existing nodes by Python 3.12+.
            # Locations are not semantic fields; type-ignore location changes
            # likewise cannot introduce a new import, call, value or statement.
            return [type(value).__name__, {key: normalized(item)
                    for key, item in ast.iter_fields(value)
                    if key != "lineno" and not (key == "type_params" and item == [])}]
        if isinstance(value, list):
            return [normalized(item) for item in value]
        if isinstance(value, bytes):
            return ["bytes", value.hex()]
        if value is Ellipsis:
            return ["ellipsis"]
        return value

    return digest(normalized(module))


def reviewed_nonproduct_closure(*, trees, paths, changes):
    """Apply only an exact consumer inventory, never a filename-only exclusion."""
    review = REVIEWED_NONPRODUCT_INPUTS
    require(isinstance(review, dict) and set(review) == {
        "status", "documents", "python_asts", "consumer_inventory_sha256",
        "unresolved_consumers"}, "nonproduct input review is incomplete")
    require(review["status"] == "REVIEWED_CLOSED_INPUTS"
            and review["unresolved_consumers"] == [], "nonproduct consumers are unresolved")
    require(review["documents"] == sorted(NONPRODUCT_DOCUMENTS)
            and isinstance(review["python_asts"], dict)
            and set(review["python_asts"]) == {NONPRODUCT_HISTORY},
            "nonproduct input scope is not reviewed")
    expected = review["consumer_inventory_sha256"]
    require(isinstance(expected, str) and HEX64.fullmatch(expected),
            "nonproduct consumer inventory digest is missing")
    ast_hash = review["python_asts"][NONPRODUCT_HISTORY]
    require(isinstance(ast_hash, str) and HEX64.fullmatch(ast_hash),
            "reviewed Python AST digest is missing")
    subjects = NONPRODUCT_DOCUMENTS | {NONPRODUCT_HISTORY}
    require(bool(paths) and paths <= subjects and bool(changes)
            and all(change["status"] == "M" and len(change["paths"]) == 1
                    for change in changes), "nonproduct review needs existing modified inputs only")
    require(bool(trees), "nonproduct review has no source trees")
    sources = []
    for source, tree in sorted(trees.items()):
        inventory = [[path, list(metadata)] for path, metadata in sorted(tree.entries.items())
                     if path not in subjects | NONPRODUCT_PLANNERS]
        require(digest(inventory) == expected,
                "nonproduct consumer inventory changed; full verification is required")
        require({CI, PLANNER, *NONPRODUCT_CONSUMERS} <= tree.workflows(),
                "nonproduct G0 or source consumers are missing")
        for path in sorted(subjects):
            require(tree.entries.get(path, (None,))[0] == "100644",
                    "nonproduct input is absent, executable or linked")
            raw = tree.read(path)
            raw.decode("utf-8", "strict")
            if path == NONPRODUCT_HISTORY:
                require(nonproduct_python_ast(raw) == ast_hash,
                        "history helper AST changed; source and product scope need review")
        sources.append({"source_sha": source, "tree_sha": tree.tree_sha,
                        "consumer_inventory_sha256": expected})
    return {"status": "APPLIED", "review_sha256": digest(review),
            "reviewed_paths": sorted(paths), "source_views": sources,
            "product_jobs": ["rust-tests", "studio-tests", "browser-acceptance"],
            "source_consumers": sorted(NONPRODUCT_CONSUMERS) if NONPRODUCT_HISTORY in paths else [],
            "scope": "closed-prose-or-identical-reviewed-history-AST",
            "evidence_claim": "input-independence-only-no-test-or-release-verdict"}


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
    # Ancestry must come from commit objects, not local graft metadata.
    env = dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_GRAFT_FILE=os.devnull)
    result = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(repo), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        timeout=30, env=env,
    )
    require(result.returncode == 0, f"Git operation failed: {args[0]}")
    return result.stdout


def git_text(repo: Path, *args: str) -> str:
    return git(repo, *args).decode("utf-8", "strict").strip()


def commit_parents(repo: Path, sha: str) -> list[str]:
    headers = git(repo, "cat-file", "commit", commit_sha(sha)).split(b"\n\n", 1)[0]
    return [commit_sha(line.removeprefix(b"parent ").decode("ascii", "strict"))
            for line in headers.splitlines() if line.startswith(b"parent ")]


def is_ancestor(repo: Path, before: str, after: str) -> bool:
    result = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(repo), "merge-base", "--is-ancestor",
         commit_sha(before), commit_sha(after)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30,
        env=dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_GRAFT_FILE=os.devnull),
    )
    require(result.returncode in (0, 1), "cannot establish base ancestry")
    return result.returncode == 0


class Tree:
    def __init__(self, repo: Path, sha: str):
        self.repo, self.sha = repo, commit_sha(sha)
        require(git_text(repo, "cat-file", "-t", sha) == "commit", "SHA is not a commit")
        self.tree_sha = git_text(repo, "rev-parse", f"{sha}^{{tree}}")
        self.entries: dict[str, tuple[str, str]] = {}
        # Git object identity is immutable within this source snapshot. Repeated
        # consumer scans may read the same workflow many times; retain only the
        # already bounded bytes, with no reuse across planning invocations.
        self._blob_cache: dict[str, bytes] = {}
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
        if oid not in self._blob_cache:
            size = int(git_text(self.repo, "cat-file", "-s", oid))
            require(size <= 2_000_000, "source input exceeds bounded parser size")
            self._blob_cache[oid] = git(self.repo, "cat-file", "blob", oid)
        return self._blob_cache[oid]

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


def reviewed_workflow_closure(review: Any, *, trees: dict[str, Tree],
                              source: dict[str, str], paths: set[str],
                              changes: list[dict[str, Any]]) -> dict[str, Any]:
    """Check a caller-reviewed, exact-source workflow dependency inventory.

    This validates a trust input; a JSON review ID cannot authenticate its
    author or establish that its dependency audit was complete. Literal source
    references add a positive omission check. Absence of a literal reference
    never establishes independence. Unsupported relationships stay broad.
    """
    require(isinstance(review, dict), "workflow impact review must be an object")
    require(set(review) == {"schema_version", "kind", "mode", "status", "review_id",
                            "source", "changed_paths", "workflow_graphs"},
            "workflow impact review fields are missing or unsupported")
    require(type(review["schema_version"]) is int and review["schema_version"] == 1
            and review["kind"] == "motionwright-reviewed-workflow-impact"
            and review["mode"] == "shadow" and review["status"] == "REVIEWED",
            "workflow impact review is not an explicit reviewed shadow inventory")
    require(isinstance(review["review_id"], str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", review["review_id"]),
            "workflow impact review ID is invalid")
    require(review["source"] == source, "workflow impact review source is stale or unrelated")
    require(bool(paths) and isinstance(review["changed_paths"], list)
            and review["changed_paths"] == sorted(paths),
            "workflow impact review must cover every changed path exactly")
    inventories = {sha: tree.workflows() for sha, tree in trees.items()}
    current = inventories[source["checkout_sha"]]
    require(all(inventory == current for inventory in inventories.values()),
            "added, removed or renamed workflows require full disposition")
    require(paths <= current and all(change["status"] == "M"
                                    and len(change["paths"]) == 1 for change in changes),
            "reviewed narrowing only covers modified workflow definitions")
    views = unique_objects(review["workflow_graphs"], "source_sha", "workflow graph")
    require(set(views) == set(trees), "workflow review does not cover every compared source tree")
    edges: dict[str, set[str]] = {workflow: set() for workflow in current}
    for sha, tree in sorted(trees.items()):
        view = views[sha]
        require(set(view) == {"source_sha", "tree_sha", "workflows", "unresolved_dependencies"},
                "workflow graph fields are missing or unsupported")
        require(view["tree_sha"] == tree.tree_sha, "workflow review tree differs from Git")
        require(view["unresolved_dependencies"] == [], "workflow graph has unresolved dependencies")
        nodes = view["workflows"]
        require(isinstance(nodes, dict) and set(nodes) == inventories[sha],
                "workflow graph inventory is missing or unexpected")
        for workflow, node in sorted(nodes.items()):
            safe_path(workflow)
            require(isinstance(node, dict) and set(node) == {"sha256", "depends_on", "basis"},
                    "workflow dependency record is incomplete")
            raw = tree.read(workflow)
            require(node["sha256"] == hashlib.sha256(raw).hexdigest(),
                    "workflow review definition hash differs from Git")
            dependencies = node["depends_on"]
            require(isinstance(dependencies, list)
                    and all(isinstance(item, str) for item in dependencies)
                    and dependencies == sorted(set(dependencies))
                    and set(dependencies) <= inventories[sha] and workflow not in dependencies,
                    "workflow dependencies are duplicate, unsafe or outside the inventory")
            require(isinstance(node["basis"], str) and 1 <= len(node["basis"]) <= 2000,
                    "workflow dependency review basis is required")
            # This initial profile deliberately excludes implicit workflow
            # activation and reusable workflows. A reviewed graph alone does
            # not supply their event/ref/artifact applicability contracts.
            require(not any(token in raw for token in
                            (b"workflow_call", b"workflow_run", b"repository_dispatch")),
                    "workflow activation or reusable relationship needs a wider contract")
            references = {other for other in inventories[sha]
                          if other != workflow and other.encode("utf-8") in raw}
            require(references <= set(dependencies),
                    "workflow graph omits a literal definition reference")
            edges[workflow].update(dependencies)
    closure = set(paths)
    while True:
        expanded = closure | {consumer for consumer, dependencies in edges.items()
                              if closure.intersection(dependencies)}
        if expanded == closure:
            break
        closure = expanded
    return {
        "status": "APPLIED",
        "review_id_sha256": hashlib.sha256(review["review_id"].encode("utf-8")).hexdigest(),
        "review_sha256": digest(review),
        "authentication": "caller-must-authenticate-review-and-audit-completeness",
        # A plan may be retained as a public technical artifact. Keep the
        # original review ID and narrative basis in the private review only.
        "workflow_graphs": [
            {"source_sha": sha, "tree_sha": views[sha]["tree_sha"],
             "workflows": {path: {"sha256": node["sha256"], "depends_on": node["depends_on"]}
                           for path, node in sorted(views[sha]["workflows"].items())}}
            for sha in sorted(views)
        ],
        "selected_workflows": sorted(closure),
        "evidence_claim": "workflow-impact-review-not-execution-or-acceptance",
    }


def build_plan(repo: Path, *, base: str, head: str, checkout: str,
               event: str, repository: str, candidate: bool = False,
               allow_advanced_pr_base: bool = False,
               workflow_impact_review: dict[str, Any] | None = None) -> dict[str, Any]:
    commit_sha(base, zero=True)
    commit_sha(head)
    commit_sha(checkout)
    require(event in ("pull_request", "push", "workflow_dispatch"), "unsupported event")
    require(type(allow_advanced_pr_base) is bool, "advanced PR base policy must be boolean")
    require(not allow_advanced_pr_base or event == "pull_request",
            "advanced PR base reconciliation is only available for pull_request")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository), "invalid repository")
    require(git_text(repo, "rev-parse", "--is-shallow-repository") == "false",
            "complete history is required; fetch depth must be zero")
    require(git_text(repo, "rev-parse", "HEAD") == checkout, "checkout SHA mismatch")
    require(not git_text(repo, "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching"),
            "tracked working tree changes or untracked/ignored files prevent exact-source planning")
    trees = {s: Tree(repo, s) for s in {head, checkout} | ({base} if base != ZERO else set())}
    current = trees[checkout]
    parents = commit_parents(repo, checkout)
    merge_base = None
    pr_base_reconciliation = None
    force_full: list[str] = []
    changes = []
    change_ranges = []
    if event == "pull_request":
        require(base != ZERO, "PR base commit is required")
        bases = git_text(repo, "merge-base", "--all", base, head).split()
        require(len(bases) == 1, "PR requires one complete, unambiguous merge base")
        merge_base = bases[0]
        trees.setdefault(merge_base, Tree(repo, merge_base))
        exact_merge = len(parents) == 2 and parents == [base, head]
        advanced_merge = (allow_advanced_pr_base and len(parents) == 2
                          and parents[1] == head and parents[0] != head)
        require(checkout == head or exact_merge or advanced_merge,
                "PR checkout must be exact head or exact ordered base/head merge")
        actual_base = parents[0] if checkout != head else None
        advanced = actual_base is not None and actual_base != base
        actual_merge_base = merge_base if actual_base == base else None
        if advanced:
            require(allow_advanced_pr_base, "advanced PR base requires explicit opt-in")
            trees.setdefault(actual_base, Tree(repo, actual_base))
            require(is_ancestor(repo, base, actual_base),
                    "PR event base must be an ancestor of the actual first merge parent")
            actual_bases = git_text(repo, "merge-base", "--all", actual_base, head).split()
            require(len(actual_bases) == 1, "actual PR base requires one unambiguous merge base")
            actual_merge_base = actual_bases[0]
            trees.setdefault(actual_merge_base, Tree(repo, actual_merge_base))
            # Include the advanced base even if merge resolution later reverts
            # a path. This is additive source impact, never another execution.
            changes += changed_paths(repo, base, actual_base)
            change_ranges.append({"kind": "advanced-event-base", "before": base,
                                  "after": actual_base})
            force_full.append("advanced-pr-base-requires-full-shadow-inventory")
        pr_base_reconciliation = {
            "policy": "allow-advanced-merge-parent" if allow_advanced_pr_base else "strict",
            "event_base_sha": base, "event_base_tree_sha": trees[base].tree_sha,
            "actual_merge_parent_sha": actual_base,
            "actual_merge_parent_tree_sha": trees[actual_base].tree_sha if actual_base else None,
            "actual_merge_base_sha": actual_merge_base,
            "advanced": advanced,
            "ancestry": "verified-ancestor" if advanced else ("same-commit" if actual_base else "not-a-merge"),
        }
        changes += changed_paths(repo, merge_base, head)
        change_ranges.append({"kind": "complete-pr-history", "before": merge_base, "after": head})
        if checkout != head:
            changes += changed_paths(repo, actual_base, checkout)
            change_ranges.append({"kind": "actual-base-to-checkout", "before": actual_base,
                                  "after": checkout})
    else:
        require(checkout == head, "non-PR checkout must equal head")
        if base == ZERO:
            force_full.append("no-previous-commit")
        else:
            changes = changed_paths(repo, base, head)
            change_ranges.append({"kind": "base-to-head", "before": base, "after": head})
            if not is_ancestor(repo, base, head):
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
    workflow_review = {"status": "NOT_SUPPLIED"}
    if workflow_impact_review is not None:
        try:
            require(not candidate and not force_full and not deleted_workflows,
                    "candidate, prior expansion or removed workflow takes precedence")
            workflow_review = reviewed_workflow_closure(
                workflow_impact_review, trees=trees,
                source={"repository": repository, "event": event, "base_sha": base,
                        "head_sha": head, "checkout_sha": checkout},
                paths=paths, changes=changes,
            )
        except PlanError as error:
            workflow_review = {"status": "NOT_APPLIED", "reason": str(error),
                               "review_sha256": digest(workflow_impact_review)}
            force_full.append("workflow-impact-review-not-applicable")
    nonproduct_review = {"status": "NOT_APPLIED", "reason": "scope-not-reviewed"}
    if not candidate and not force_full and not deleted_workflows:
        try:
            nonproduct_review = reviewed_nonproduct_closure(trees=trees, paths=paths, changes=changes)
        except (PlanError, SyntaxError, UnicodeError, LookupError, ValueError, TypeError) as error:
            nonproduct_review = {"status": "NOT_APPLIED", "reason": str(error)}
    selected: dict[str, dict[str, Any]] = {}

    def select(workflows: set[str], stage: str, reason: str) -> None:
        for path in sorted(workflows & inventory):
            item = selected.setdefault(path, {"workflow": path, "stages": set(), "reasons": set()})
            item["stages"].add(stage)
            item["reasons"].add(reason)

    select({CI, PLANNER}, "G0", "always-fast-contract-format-lint-and-security-checks")
    if nonproduct_review["status"] == "APPLIED":
        select(set(nonproduct_review["source_consumers"]), "G1",
               "closed-history-source-and-broad-security-discovery-consumers")
    if workflow_review["status"] == "APPLIED":
        select(paths, "G1", "changed-workflow-execution-definition")
        select(set(workflow_review["selected_workflows"]) - paths, "G2",
               "reviewed-workflow-consumer-closure-over-compared-source-trees")
    unknown = []
    for path in sorted(paths):
        if (nonproduct_review["status"] == "APPLIED"
                and path in nonproduct_review["reviewed_paths"]):
            continue
        if path in NEUTRAL_PATHS:
            continue
        if workflow_review["status"] == "APPLIED":
            # Admission above requires that every changed path is a modified
            # workflow, so no product, lock or shared action input bypasses G2.
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
            "pr_base_reconciliation": pr_base_reconciliation,
            "scope": "pr-merge" if event == "pull_request" and checkout != head else "source-head",
        },
        "candidate": candidate,
        "change_ranges": change_ranges,
        "selector": {
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "risk_map_sha256": digest({
                "studio": sorted(STUDIO_WORKFLOWS),
                "package_roots": sorted(KNOWN_PACKAGE_ROOTS),
                "neutral_paths": sorted(NEUTRAL_PATHS),
                "helpers": {p: sorted(v) for p, v in NATIVE_HELPER_WORKFLOWS.items()},
                "nonproduct_review": REVIEWED_NONPRODUCT_INPUTS,
            }),
        },
        "changes": sorted({json.dumps(x, sort_keys=True) for x in changes}),
        "changed_paths": sorted(paths), "unknown_paths": sorted(unknown),
        "dependency_graphs": graphs, "affected_package_roots": affected,
        "workflow_inventory_sha256": workflow_hashes,
        "workflow_impact_review": workflow_review,
        "nonproduct_input_review": nonproduct_review,
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
    p.add_argument("--allow-advanced-pr-base", action="store_true",
                   help="PR only: accept an exact two-parent merge whose first parent descends from "
                        "the event base; record both bases and expand the full shadow inventory")
    p.add_argument("--reviewed-workflow-impact", type=Path,
                   help="optional private, independently reviewed workflow dependency inventory; "
                        "must be outside the source checkout and match every compared source")
    p.add_argument("--output", type=Path)
    a = sub.add_parser("aggregate")
    a.add_argument("--plan", type=Path, required=True)
    a.add_argument("--expected", type=Path, required=True)
    a.add_argument("--observed", type=Path, required=True)
    a.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "plan":
            workflow_review = None
            if args.reviewed_workflow_impact:
                require(not args.reviewed_workflow_impact.resolve().is_relative_to(args.repo.resolve()),
                        "private workflow impact review must be outside source checkout")
                workflow_review = read_json(args.reviewed_workflow_impact)
            result = build_plan(args.repo, base=args.base, head=args.head, checkout=args.checkout,
                                event=args.event, repository=args.repository, candidate=args.candidate,
                                allow_advanced_pr_base=args.allow_advanced_pr_base,
                                workflow_impact_review=workflow_review)
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
