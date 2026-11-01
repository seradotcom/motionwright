#!/usr/bin/env python3
"""Bounded historical-source impact planner for development, never acceptance.

Source-only usage:
  python -B tooling/conformance_development_impact.py --repo . --target HEAD

The strict conformance_currency_impact and technical_readiness gates are
unchanged. Do not infer a new PASS, independent review, G2 or G3 from this plan.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from conformance_currency_impact import commit_sha, git, source_path
from conformance_policy import (
    DIGEST, MAX_EVIDENCE_INDEX_BYTES, SHA, _pairs, load_json, public_file,
    validate_catalog_links,
)

SCHEMA = "motionwright.conformance-development-source-impact/1"
LEDGERS = (
    ("docs/acceptance/requirements-status.json", ("requirements",)),
    ("docs/creative-production/requirements-status.json", ("requirements",)),
    ("docs/acceptance/acceptance-status.json", ("cases", "expansion_cases")),
)
EXPECTED_ROWS = 396
EXPECTED_FIELDS = 1652
EXPECTED_EDGES = 506
GLOBAL_SOURCE_PINS = frozenset({
    "SOURCE_LOCK.json", "Cargo.lock", "Cargo.toml",
    "rust-toolchain.toml", "apps/studio/package-lock.json",
})


def need(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def text_digest(raw: bytes) -> str:
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def changed_git_paths(raw: bytes) -> set[str]:
    """Parse Git diff --name-only -z without quotePath, shell or newline loss.

    Git permits filenames containing newlines and whitespace; the unbounded
    line-oriented/quoted form can silently miss a changed source/oracle.
    Any ambiguous or invalid record fails closed before G1 scope selection.
    """
    if not raw:
        return set()
    need(raw.endswith(b"\x00"), "changed Git path list is not NUL-delimited")
    names = raw[:-1].split(b"\x00")
    need(all(names), "changed Git path list contains an empty record")
    parsed = [source_path(name.decode("utf-8", "strict")) for name in names]
    need(len(parsed) == len(set(parsed)),
         "duplicate Git diff path: impacted source scope is ambiguous")
    return set(parsed)


def original_git_tree(root: Path, sha: str) -> dict[str, tuple[str, str]]:
    """Authenticated Git source entries: (mode, object-id), never checkout bytes."""
    tree = git(root, "ls-tree", "-rz", "--full-tree", sha)
    entries: dict[str, tuple[str, str]] = {}
    for entry in tree.split(b"\x00"):
        if not entry:
            continue
        header, encoded = entry.split(b"\t", 1)
        mode, kind, oid = header.decode("ascii").split()
        name = encoded.decode("utf-8", "strict")
        need(name not in entries, "duplicate Git tree path")
        if kind == "blob":
            need(SHA.fullmatch(oid) is not None, "invalid Git blob identity")
            entries[name] = (mode, oid)
    return entries


def summarize_references(records: list[dict],
                         prior: dict[str, tuple[str, str]],
                         current: dict[str, tuple[str, str]],
                         changed: set[str]) -> dict:
    """Conservative source-reference impact only; no execution or PASS inference."""
    mapped: dict[str, set[str]] = collections.defaultdict(set)
    affected: set[str] = set()
    affected_fields: set[str] = set()
    missing: set[str] = set()
    seen_records: set[str] = set()
    counts = collections.Counter()
    for record in records:
        identity = record["id"]
        need(isinstance(identity, str) and identity and identity not in seen_records,
             "duplicate or invalid original record identity")
        seen_records.add(identity)
        criteria = record["criterion_ids"]
        need(isinstance(criteria, list), "invalid original criterion list")
        refs = record["source_inputs"] + record["definitions"]
        need(bool(refs), "original evidence lacks explicit source or oracle references")
        changed_references: set[str] = set()
        missing_reference = False
        for ref in refs:
            name = source_path(ref["path"])
            need(DIGEST.fullmatch(ref["sha256"]) is not None,
                 "original reference has invalid source digest")
            mapped[name].add(identity)
            before, after = prior.get(name), current.get(name)
            if after is None or after[0] not in {"100644", "100755"}:
                missing.add(name)
                missing_reference = True
            if name in changed or before != after:
                changed_references.add(name)
        counts["referenced_input_and_oracle_bindings"] += len(refs)
        if changed_references or missing_reference:
            affected.add(identity)
            affected_fields.update(criteria)
            if missing_reference:
                counts["records_with_missing_or_linked_references"] += 1
            if changed_references:
                if changed_references.issubset(GLOBAL_SOURCE_PINS):
                    counts["global_dependency_pin_only_record_candidates"] += 1
                elif changed_references.intersection(GLOBAL_SOURCE_PINS):
                    counts["dependency_pins_plus_direct_code_or_oracle_records"] += 1
                else:
                    counts["direct_nonpin_code_or_oracle_record_candidates"] += 1
        else:
            counts["unchanged_references_historical_only_records"] += 1
    counts["source_path_affected_records"] = len(affected)
    counts["source_path_affected_criteria"] = len(affected_fields)
    counts["original_evidence_records"] = len(records)
    counts["distinct_referenced_paths"] = len(mapped)
    return {
        "counts": dict(sorted(counts.items())),
        "impact_counts_are_record_associations_not_test_rerun_instructions": True,
        "global_dependency_pin_only_cannot_prove_behavioral_change": True,
        "source_changed_hotspots": [
            {"path": name, "historical_record_references": len(ids)}
            for name, ids in sorted(mapped.items(),
                                    key=lambda entry: (-len(entry[1]), entry[0]))
            if name in changed or prior.get(name) != current.get(name)
        ][:30],
        "affected_canonical_rows": sorted(
            {identity.split("#/", 1)[0] for identity in affected_fields}),
        "unmapped_changed_paths": sorted(changed.difference(mapped))[:60],
        "unmapped_changed_path_count": len(changed.difference(mapped)),
        "missing_original_referenced_git_paths": sorted(missing)[:30],
        "missing_original_referenced_git_path_count": len(missing),
        "automatically_current_evidence_records": 0,
        "automatically_admitted_criteria": 0,
    }


def checkout_source_boundary(root: Path, target: str) -> dict:
    """Distinguish immutable committed impact from live uncommitted source.

    A dirty checkout must never be mistaken for a complete impact analysis of
    the in-flight product; no tracked/untracked paths are exposed publicly.
    """
    checkout_head = commit_sha(root, "HEAD")
    # -z is robust for filenames containing whitespace/newlines or escape
    # characters. Nonignored new source files must also invalidate coverage.
    status = git(root, "status", "--porcelain=v1", "-z",
                 "--untracked-files=normal")
    dirty = bool(status)
    same = checkout_head == target
    return {
        "checkout_head": checkout_head,
        "target_is_checkout_head": same,
        "worktree_clean_including_untracked": not dirty,
        "committed_source_tree_includes_worktree_edits": False,
        "can_use_as_complete_current_checkout_impact": same and not dirty,
        "uncommitted_or_untracked_changes_require_owner_review": dirty,
        "historical_target_differs_from_checked_out_HEAD": not same,
        "decision": ("COMMITTED_SOURCE_ONLY_CHECKOUT_CLEAN" if same and not dirty
                     else "COMMITTED_SOURCE_ONLY_NOT_COMPLETE_CURRENT_WORKTREE"),
    }


def dependency_or_environment_paths(changed: set[str]) -> set[str]:
    """Expose build and execution changes even when historical records map them.

    These paths need a bounded consumer/environment review, not an automatic
    whole-product rerun. Direct source/oracle matches cannot prove that a
    dependency, feature, toolchain or CI environment change is local.
    """
    names = {
        "SOURCE_LOCK.json", "Cargo.toml", "Cargo.lock",
        "package.json", "package-lock.json", "rust-toolchain",
        "rust-toolchain.toml",
    }
    result = set()
    for name in changed:
        parts = source_path(name).split("/")
        if (parts[-1] in names
                or parts[-1] in {"requirements.txt", "constraints.txt"}
                or (len(parts) > 1 and parts[-2] == "requirements"
                    and parts[-1].endswith((".txt", ".in")))
                or (len(parts) > 1 and parts[-2] == ".cargo"
                    and parts[-1] in {"config", "config.toml"})
                or name.startswith((".github/workflows/", ".github/actions/"))):
            result.add(name)
    return result


def requires_transitive_owner_review(changed: set[str], risk_paths: frozenset[str],
                                     unmapped_count: int, checkout: dict) -> bool:
    """Hold G1 if immutable source impact cannot cover new or dirty inputs."""
    need(type(unmapped_count) is int and unmapped_count >= 0,
         "invalid untracked source-reference impact count")
    return (bool(changed & risk_paths)
            or bool(dependency_or_environment_paths(changed))
            or unmapped_count > 0
            or not checkout["can_use_as_complete_current_checkout_impact"])


def inspect_development(root: Path, spec: str = "HEAD", *, baseline: str | None = None) -> dict:
    """Authenticate historic catalog structure + compare two immutable Git trees.

    No historical evidence originals are opened or re-attested here. The full
    strict validator must run ONCE on the nominated frozen F before acceptance.
    """
    root = Path(root).resolve(strict=True)
    target = commit_sha(root, spec)
    ledgers = [load_json(public_file(root, filename)) for filename, _ in LEDGERS]
    descriptors = [doc.get("technical_evidence_index") for doc in ledgers]
    need(bool(descriptors[0]) and all(d == descriptors[0] for d in descriptors),
         "ledger evidence descriptor mismatch")
    desc = descriptors[0]
    need(desc.get("digest_kind") == "git-text-lf"
         and DIGEST.fullmatch(desc.get("sha256", "")) is not None,
         "untrusted canonical index declaration")
    path = source_path(desc.get("path"))
    need(path == "docs/acceptance/technical-evidence.json",
         "unexpected canonical evidence index path")
    original = public_file(root, path)
    with original.open("rb") as stream:
        raw = stream.read(MAX_EVIDENCE_INDEX_BYTES + 1)
    need(0 < len(raw) <= MAX_EVIDENCE_INDEX_BYTES and
         text_digest(raw) == desc["sha256"],
         "historical index SHA-256 or size differs from declaration")
    index = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_pairs)
    need(index.get("schema") == "motionwright.technical-evidence/1",
         "unrecognized historical index schema")
    original_source = index["source"]
    base = original_source["motionwright_sha"]
    need(original_source.get("digest_kind") == "git-text-lf"
         and SHA.fullmatch(base) is not None
         and DIGEST.fullmatch(original_source.get("source_lock_sha256", "")) is not None,
         "historical source identity invalid")
    need(all(doc["current_assessment_source"] == original_source
             for doc in ledgers), "ledger/index source claims disagree")
    assessed = commit_sha(root, base)
    selected_baseline = (commit_sha(root, baseline) if baseline is not None
                         else assessed)
    try:
        git(root, "merge-base", "--is-ancestor", selected_baseline, target)
    except ValueError as error:
        raise ValueError("comparison baseline must be an ancestor of target") from error
    prior = original_git_tree(root, selected_baseline)
    current = original_git_tree(root, target)
    lock_entry = prior.get("SOURCE_LOCK.json")
    need(lock_entry is not None and lock_entry[0] in ("100644", "100755"),
         "historical SOURCE_LOCK is missing or linked")
    historical_lock = git(root, "show", base + ":SOURCE_LOCK.json")
    need(text_digest(historical_lock) == original_source["source_lock_sha256"],
         "historical index source lock is not the authenticated Git source")

    rows: dict[str, dict] = {}
    for doc, (_, groups) in zip(ledgers, LEDGERS):
        for group in groups:
            for row in doc[group]:
                identity = row["id"]
                need(identity not in rows, "duplicate original canonical row")
                rows[identity] = row
    catalog = index["criteria"]
    contracts = index["contracts"]
    links = index["normative_edges"]
    need(len(rows) == EXPECTED_ROWS and len(contracts) == EXPECTED_ROWS
         and len(catalog) == EXPECTED_FIELDS and len(links) == EXPECTED_EDGES
         and set(rows) == set(contracts),
         "canonical 396/1652/506 catalog was modified")
    # Reuse the existing normative link/source-record integrity checker over
    # the already-loaded catalog. Counts alone cannot preserve 506 edge identities.
    # This is structural, not renewed evidence, new acceptance, or a G3 audit.
    validate_catalog_links(index, list(rows.values()))
    for rid, row in rows.items():
        fields = row["technical_assessment"]["criteria"]
        actual = {f["id"]: f["source_clause_sha256"] for f in fields}
        expected = {fid: digest for fid, digest in catalog.items()
                    if fid.startswith(rid + "#/")}
        need(len(fields) == len(actual) and actual == expected,
             "historical source clause identity mismatch: " + rid)

    p = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(root), "diff", "--no-renames",
         "--name-only", "-z", selected_baseline, target, "--"],
        capture_output=True, env={
            **{key: val for key, val in __import__("os").environ.items()
               if not key.upper().startswith("GIT_")},
            "GIT_NO_REPLACE_OBJECTS": "1", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": __import__("os").devnull,
            "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1"},
        timeout=30, check=False,
    )
    need(p.returncode == 0, "committed source diff unavailable")
    changed = changed_git_paths(p.stdout)
    for record in index["records"]:
        need(set(record["criterion_ids"]).issubset(catalog)
             and set(record["complete_criterion_ids"]).issubset(record["criterion_ids"]),
             "historic record cites an absent or incomplete original criterion")
    refs = summarize_references(index["records"], prior, current, changed)
    checkout = checkout_source_boundary(root, target)
    risk_paths = GLOBAL_SOURCE_PINS
    return {
        "schema": SCHEMA,
        "mode": "development-source-impact-only",
        "source": target,
        "historical_ledger_source": base,
        "comparison_baseline_source_sha": selected_baseline,
        "comparison_baseline_is_historical_assessment": selected_baseline == assessed,
        "comparison_is_development_delta": selected_baseline != assessed,
        "baseline_must_be_authenticated_ancestor": True,
        "historical_ledger_lock_sha256": original_source["source_lock_sha256"],
        "historical_ledger_lock_git_verified": True,
        "historical_index_digest_verified": True,
        "source_matches_current_ledger": (base == target),
        "checkout_source_boundary": checkout,
        "analysis_cannot_clear_dirty_current_worktree_for_G1": True,
        "source_changed_path_count": len(changed),
        "changed_source_paths": sorted(changed)[:100],
        "global_lock_or_dependency_paths_changed": sorted(changed & risk_paths),
        "dependency_or_environment_paths_changed":
            sorted(dependency_or_environment_paths(changed)),
        "transitive_impact_unknown_requires_owner_review":
            requires_transitive_owner_review(
                changed, risk_paths,
                refs["unmapped_changed_path_count"], checkout),
        "unmapped_changed_files_cannot_clear_G1":
            bool(refs["unmapped_changed_path_count"]),
        "catalog_preserved": {
            "rows": EXPECTED_ROWS, "criteria": EXPECTED_FIELDS,
            "normative_edges": EXPECTED_EDGES,
        },
        "impact": refs,
        "review_summary": ("G1 transitive dependency and physical-risk scope must "
                           "be selected by impact owner; unchanged blobs are "
                           "historical candidates, never new executions. Dirty "
                           "working trees are outside this committed-source plan."),
        "strict_full_F_index_and_artifact_review_performed": False,
        "recorded_or_new_current_PASS": 0,
        "Graph6_Native_G2_accepted": 0,
        "G3": "NOT_GRANTED",
        "release_authorized": False,
        "human_acceptance": "NOT_ASSESSED",
        "automatic_reruns": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path,
                        default=Path(__file__).resolve().parents[1])
    parser.add_argument("--target", default="HEAD",
                        help="Exact full source SHA or HEAD")
    parser.add_argument("--baseline",
                        help="Optional exact full ancestor SHA for incremental impact; never acceptance")
    args = parser.parse_args(argv)
    try:
        report = inspect_development(args.repo, args.target, baseline=args.baseline)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, KeyError, TypeError, UnicodeDecodeError,
            OSError, subprocess.TimeoutExpired) as error:
        print("source-only impact cannot be trusted: " + str(error),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
