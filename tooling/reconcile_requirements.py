#!/usr/bin/env python3
"""Inventory every public requirement at one immutable Git commit.

This is a structural source audit, not private-criterion fulfillment, execution
of tests, native evidence verification, aesthetic review or release approval.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LEDGERS = {"v01": "docs/acceptance/requirements-status.json",
           "v05": "docs/creative-production/requirements-status.json",
           "acceptance": "docs/acceptance/acceptance-status.json"}


def git(*args: str) -> str:
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                            encoding="utf-8", errors="strict", timeout=30, check=True)
    return result.stdout


def kind(path: str) -> str:
    if path.startswith(".github/workflows/"):
        return "workflow_definition_not_run_evidence"
    if "/tests/" in path or path.endswith((".test.ts", ".spec.ts")):
        return "test_definition_not_test_result"
    if path.endswith((".md", ".txt")):
        return "documentation"
    if path.endswith((".rs", ".ts", ".tsx", ".py", ".css")):
        return "implementation_source"
    return "metadata_or_fixture"


def strict_json_pairs(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate authority field in immutable ledger")
        result[key] = value
    return result


def reject_nonfinite_json(_value: str) -> None:
    raise ValueError("nonfinite value in immutable ledger")


def reconcile(source: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("source must be a full immutable Git SHA")
    if git("rev-parse", "--is-shallow-repository").strip() != "false":
        raise ValueError("full Git history is mandatory for meaningful ledger/source drift")
    if git("rev-parse", source + "^{commit}").strip() != source:
        raise ValueError("source must identify the exact commit")
    tree = {}
    for entry in git("ls-tree", "-r", "-z", "--full-tree", source).split("\0"):
        if not entry: continue
        header, path = entry.split("\t", 1)
        mode, object_type, oid = header.split()
        tree[path] = {"mode": mode, "object_type": object_type, "git_blob": oid,
                      "kind": kind(path)}
    docs = {name: json.loads(git("show", source + ":" + path),
                             object_pairs_hook=strict_json_pairs,
                             parse_constant=reject_nonfinite_json)
            for name, path in LEDGERS.items()}
    history = {}
    for name, path in LEDGERS.items():
        commit = git("log", "-1", "--format=%H", source, "--", path).strip()
        changed = set(git("diff", "--name-only", "-z", commit, source).split("\0"))
        history[name] = {"last_ledger_change": commit, "changed_paths": changed}
    rows = []
    evidence_files = {}
    for version in ("v01", "v05"):
        for original in docs[version]["requirements"]:
            paths = original.get("evidence", original.get("source_paths", []))
            if not isinstance(paths, list): raise ValueError("source evidence must be a list")
            for path in paths:
                if not isinstance(path, str) or path not in tree:
                    raise ValueError("missing source pointer in immutable tree")
                if tree[path]["mode"] not in {"100644", "100755"} or tree[path]["object_type"] != "blob":
                    raise ValueError("source pointer is not a tracked regular file")
                evidence_files[path] = tree[path]
            state = original.get("implementation_assessment", original.get("implementation"))
            rows.append({
                "id": original["id"], "version": version,
                "recorded_implementation_status": state,
                "recorded_verification_status": original.get("verification_status", original.get("complete_requirement_acceptance")),
                "source_paths": paths,
                "source_pointer_check": "ALL_PRESENT_TRACKED_REGULAR" if paths else "NO_IMPLEMENTATION_CLAIM",
                "changed_source_paths_since_ledger": sorted(set(paths) & history[version]["changed_paths"]),
                "direct_test_definition_pointers": sorted({p for p in paths if kind(p) == "test_definition_not_test_result"}),
                "duplicate_pointers": sorted(p for p, count in Counter(paths).items() if count > 1),
                "private_criterion_review": "NOT_COMPLETED_ORIGINAL_CATALOG_BYTES_UNAVAILABLE",
                "recorded_remaining_scope": original.get("remaining_scope"),
                "test_execution_by_this_inventory": "NOT_RUN",
                "independent_acceptance_by_this_inventory": False,
            })
    expected_original = {f"CR-{area}-{n:02d}" for area in (
        "FOUND", "PROJECT", "NARR", "AUDIO", "UX", "TIME", "CANVAS", "CONCEPT", "STYLE",
        "CHANGE", "SDK", "MOTION", "MLT", "BLENDER", "MANIM", "GRAPH", "EFFECT", "JOBS",
        "MODELS", "WORKFLOW", "VARIANT", "SEC", "DELIVER", "PERF", "PLATFORM", "EXT") for n in range(1, 9)}
    expected_expansion = {f"MW05-E{e:02d}-{n:02d}" for e in range(16) for n in range(1, 5)}
    if len(rows) != 272 or {r["id"] for r in rows} != expected_original | expected_expansion:
        raise ValueError("canonical requirement IDs were changed or duplicated")
    cases = docs["acceptance"]["cases"]
    if len(cases) != 60 or {c["id"] for c in cases} != {f"ACC-{n:03d}" for n in range(1, 61)}:
        raise ValueError("canonical acceptance IDs were changed or duplicated")
    return {
        "schema": "motionwright.source-reconciliation/1", "source_sha": source,
        "method": "immutable Git tree, all IDs, tracked-file pointers, blob IDs and ledger/source drift",
        "limitations": ["Private criterion text was not recovered from the original archives.",
                        "A source pointer or workflow definition is not execution or fulfillment evidence.",
                        "No status is promoted and no acceptance case is executed by this inventory."],
        "ledger_last_changes": {k: v["last_ledger_change"] for k, v in history.items()},
        "counts": {v: dict(sorted(Counter(r["recorded_implementation_status"] for r in rows if r["version"] == v).items())) for v in ("v01", "v05")},
        "acceptance_counts": dict(sorted(Counter(c["status"] for c in cases).items())),
        "requirements_with_source_changes_since_ledger": {v: sum(bool(r["changed_source_paths_since_ledger"]) for r in rows if r["version"] == v) for v in ("v01", "v05")},
        "requirements_without_direct_test_definition_pointer": {v: sum(not r["direct_test_definition_pointers"] for r in rows if r["version"] == v) for v in ("v01", "v05")},
        "requirement_rows": rows, "evidence_files": dict(sorted(evidence_files.items())),
        "acceptance_rows": cases, "source_or_product_completion_percentage": None,
        "release_authorized": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = reconcile(args.source_sha)
    with args.output.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(report, output, indent=2, sort_keys=True, ensure_ascii=False)
        output.write("\n")
    print(json.dumps({key: report[key] for key in ("source_sha", "counts", "acceptance_counts", "requirements_with_source_changes_since_ledger", "requirements_without_direct_test_definition_pointer")}, sort_keys=True))


if __name__ == "__main__":
    main()
