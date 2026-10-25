#!/usr/bin/env python3
"""Produce an auditable, non-accepting conformance transition for an exact Git source.

The preserved source-bound evidence remains historical. The emitted ledgers do
not claim that any prior test or human review ran on the new source. This
utility never modifies tracked files, merges, or grants release authorization.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sys

from conformance_history_guard import (
    INDEX, LEDGERS, LIMIT, HistoryViolation, _same, _unredirected_git,
    encode_assessment_history, inspect, parse_unique, source_limit,
)
from conformance_policy import (
    public_file, source_digest, validate_assessment, validate_catalog_links,
)

HEX40 = re.compile(r"[0-9a-f]{40}\Z")
CLAUSES = 1652
ROWS = 396


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def unique_append(first: list, other: list) -> list:
    return list(dict.fromkeys([*first, *other]))


def git(root: Path, *argv: str) -> str:
    return _unredirected_git(root, *argv).decode("utf-8").strip()


def _git_object(root: Path, identity: str, kind: str, limit: int) -> bytes:
    """Authenticate the raw, typed object read through H02's Git transport."""
    if not HEX40.fullmatch(identity):
        raise ValueError("invalid Git object identity")
    if _unredirected_git(root, "cat-file", "-t", identity).strip() != kind.encode():
        raise ValueError("Git object has an unexpected type")
    size = int(_unredirected_git(root, "cat-file", "-s", identity).strip())
    if size <= 0 or size > limit:
        raise ValueError("Git source object exceeds its bound")
    data = _unredirected_git(root, "cat-file", kind, identity)
    header = kind.encode() + b" " + str(len(data)).encode() + b"\0"
    if len(data) != size or hashlib.sha1(header + data).hexdigest() != identity:
        raise ValueError("Git object bytes do not match their identity")
    return data


def _assert_git_identity(root: Path, target_sha: str) -> str:
    root = root.resolve(strict=True)
    actual = Path(git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if not root.is_dir() or actual != root:
        raise ValueError("root is not the actual Git worktree top-level")
    commit = _git_object(root, target_sha, "commit", LIMIT)
    first = commit.split(b"\n", 1)[0]
    if not first.startswith(b"tree "):
        raise ValueError("Git commit has no root tree")
    tree = first[5:].decode("ascii")
    if not HEX40.fullmatch(tree):
        raise ValueError("Git commit root tree is invalid")
    return tree


def _tree_entry(data: bytes, wanted: bytes) -> tuple[bytes, str]:
    cursor = 0
    names = set()
    selected = None
    while cursor < len(data):
        space = data.index(b" ", cursor)
        end = data.index(b"\0", space + 1)
        name = data[space + 1:end]
        identity = data[end + 1:end + 21]
        if not name or name in names or len(identity) != 20:
            raise ValueError("Git tree entry is ambiguous or truncated")
        names.add(name)
        if name == wanted:
            selected = (data[cursor:space], identity.hex())
        cursor = end + 21
    if selected is None:
        raise ValueError("required source input is absent from the Git tree")
    return selected


def _git_inputs(root: Path, target_sha: str) -> dict[str, bytes]:
    tree = _assert_git_identity(root, target_sha)
    trees = {}
    inputs = {}
    for name in ("SOURCE_LOCK.json", *LEDGERS, INDEX):
        identity = tree
        parts = name.split("/")
        for offset, part in enumerate(parts):
            if identity not in trees:
                trees[identity] = _git_object(root, identity, "tree", LIMIT)
            mode, identity = _tree_entry(trees[identity], part.encode("utf-8"))
            if offset < len(parts) - 1:
                if mode not in {b"40000", b"040000"}:
                    raise ValueError("source input ancestor is not a Git tree")
            elif mode not in {b"100644", b"100755"}:
                raise ValueError("source input is not a regular Git blob")
        inputs[name] = _git_object(root, identity, "blob", source_limit(name))
    return inputs


def _read_checkout_input(root: Path, name: str) -> bytes:
    path = public_file(root, name)
    with path.open("rb") as stream:
        data = stream.read(source_limit(name) + 1)
    if not data or len(data) > source_limit(name):
        raise ValueError("checkout source input exceeds its bound")
    data.decode("utf-8")
    return data


def capture_source_inputs(root: Path, target_sha: str) -> dict[str, bytes]:
    pinned = _git_inputs(root, target_sha)
    captured = {}
    for name, original in pinned.items():
        original.decode("utf-8")
        raw = _read_checkout_input(root, name)
        # The existing source_digest contract permits only CRLF-to-LF text
        # normalization. Keep physical bytes for custody and no-write checks.
        if raw.replace(b"\r\n", b"\n") != original.replace(b"\r\n", b"\n"):
            raise ValueError("checkout input differs from the exact Git blob: " + name)
        captured[name] = raw
    return captured


def _bound_originals(previous: dict, original_inputs: dict[str, bytes]) -> dict[str, bytes]:
    expected = set((*LEDGERS, INDEX))
    originals = dict(original_inputs)
    if set(previous) != expected or set(originals) != expected:
        raise ValueError("original source input roster changed")
    for name, raw in originals.items():
        if type(raw) is not bytes or not _same(parse_unique(raw, name), previous[name]):
            raise ValueError("original bytes do not describe the supplied source: " + name)
    return originals


def assert_source(root: Path, target_sha: str, *, integrated: bool) -> None:
    if not HEX40.fullmatch(target_sha):
        raise ValueError("target must be a full Git commit SHA")
    _assert_git_identity(root, target_sha)
    if git(root, "rev-parse", "HEAD") != target_sha:
        raise ValueError("checked-out HEAD is not the requested target source")
    if git(root, "status", "--porcelain"):
        raise ValueError("source checkout is not frozen; an active composition cannot be adjudicated")
    if integrated:
        remote = git(root, "ls-remote", "origin", "refs/heads/main").split()
        if len(remote) != 2 or remote[1] != "refs/heads/main" or remote[0] != target_sha:
            raise ValueError("main integration claim requires live matching remote main")


def rollforward(previous: dict, target_sha: str, lock_digest: str, *,
                original_inputs: dict[str, bytes],
                integrated: bool, at_utc: str, predecessor: str) -> tuple[dict, dict]:
    if not HEX40.fullmatch(target_sha) or not re.fullmatch(r"[0-9a-f]{64}", lock_digest):
        raise ValueError("invalid target identity")
    before_bytes = _bound_originals(previous, original_inputs)
    before = previous[INDEX]
    source = before["source"]
    if source["motionwright_sha"] == target_sha:
        raise ValueError("source is unchanged; do not manufacture another execution")
    newer = copy.deepcopy(previous)
    idx = newer[INDEX]
    idx["source"] = dict(source, motionwright_sha=target_sha,
                         source_lock_sha256=lock_digest)
    idx["assessment_history"].append({
        "source": source,
        "reviewed_at_utc": at_utc,
        "transition": "Historical-only currency change. No execution or acceptance is transferred.",
        "predecessor": predecessor,
        "original_index_sha256": digest(before_bytes[INDEX]),
        "original_record_count": len(before["records"]),
        "original_review_count": len(before["sufficiency_reviews"]),
        "new_product_executions": 0,
        "new_product_approvals": 0,
    })
    total = 0
    field_counts = {}
    for name in LEDGERS:
        doc = newer[name]
        doc["current_assessment_source"] = idx["source"]
        doc["current_assessment_source_semantics"] = (
            ("Exact integrated snapshot assessed here. " if integrated else
             "Exact branch snapshot assessed here; main integration is not claimed. ")
            + "Later integration requires its own currency review; "
              "no result is transferred to another source."
        )
        if name == "docs/creative-production/requirements-status.json":
            doc["upstream_integrated_sha"] = target_sha
            doc["upstream_integrated_sha_semantics"] = (
                "Candidate snapshot requiring separate integration and execution review. "
                "Semwright SDK revision is separately pinned in SOURCE_LOCK.json."
            )
        for collection in ("requirements", "cases", "expansion_cases"):
            for row in doc.get(collection, []):
                before_row = next(x for x in previous[name][collection]
                                  if x["id"] == row["id"])
                prior = before_row["technical_assessment"]
                if prior["source"] != source:
                    raise ValueError("a row differs from the original index source")
                assessment = row["technical_assessment"]
                prior_current = list(prior["evidence_currency"]["current_record_ids"])
                original_visible = {
                    k: copy.deepcopy(before_row[k])
                    for k in (
                        "status", "independent", "implementation",
                        "implementation_assessment", "verification_status",
                        "complete_requirement_acceptance", "current_visible_verdict_source",
                        "current_technical_verdict", "current_contract_verdict"
                    ) if k in before_row
                }
                row["assessment_history"].append({
                    "source": source,
                    "prior_technical_status": prior["technical_verification"]["status"],
                    "prior_contract_status": prior["contract_acceptance"]["status"],
                    "prior_current_record_ids": prior_current,
                    "prior_criteria": copy.deepcopy(prior["criteria"]),
                    "prior_technical_assessment": copy.deepcopy(prior),
                    "prior_visible_fields": original_visible,
                    "reviewed_at_utc": at_utc,
                    "reason": "New source must be independently re-evaluated; original remains historical.",
                })
                # A lossless compressed prefix also prevents subsequent source
                # transitions from breaching the bounded ledger size limit.
                row["assessment_history"] = [
                    encode_assessment_history(row["assessment_history"])
                ]
                assessment["source"] = idx["source"]
                assessment["integration"].update({
                    "status": "main" if integrated else "branch",
                    "source_sha": target_sha,
                    "observed_remote_main": target_sha if integrated else None,
                    "observed_at_utc": at_utc,
                    "meaning": ("Observed integrated source but no transferred execution."
                                if integrated else "Unmerged candidate, no transferred execution."),
                })
                assessment["implementation"].update({
                    "status": "not_assessed", "implemented_criterion_ids": [],
                    "pending_criterion_ids": [c["id"] for c in assessment["criteria"]],
                    "implemented_scope": "Earlier source only, retained verbatim in history.",
                })
                currency = assessment["evidence_currency"]
                currency.update({
                    "current_source": target_sha,
                    "validation_product_root_sha": target_sha,
                    "current_record_ids": [],
                    "historical_record_ids": unique_append(
                        currency.get("historical_record_ids", []), prior_current),
                    "historical_results_retagged": False,
                })
                artifacts = assessment.get("artifact_verification")
                if isinstance(artifacts, dict):
                    artifacts.update({
                        "current_record_ids": [],
                        "historical_record_ids": unique_append(
                            artifacts.get("historical_record_ids", []), prior_current),
                        "status": "original_artifacts_retained_only",
                    })
                technical = assessment["technical_verification"]
                technical.update({
                    "status": ("not_run" if all(
                        c["status"] == "not_run" for c in assessment["criteria"]
                    ) else "stale"),
                    "evidence_ids": [], "execution_kinds": [],
                })
                closure = assessment["contract_acceptance"]
                closure.update({
                    "status": "open",
                    "pending_criteria": [c["id"] for c in assessment["criteria"]],
                    "human_review_status": (
                        "not_run" if closure["human_review_required"] else "not_required"
                    ),
                })
                closure["independent_technical_review"]["source_currency"] = (
                    "HISTORICAL_ONLY_NOT_CURRENT"
                )
                assessment["review"]["current_source_status"] = "NO_NEW_EXECUTION"
                for field in assessment["criteria"]:
                    old_status = field["status"]
                    old_ids = list(field.get("evidence_ids", []))
                    field["status"] = ("not_run" if old_status == "not_run" else "stale")
                    field["evidence_ids"] = []
                    field["sufficiency_review_ids"] = []
                    missing = field.get("missing_assertions", [])
                    field["missing_assertions"] = unique_append(
                        missing, ["Exact new-source execution and independent sufficiency required; "
                                  "previous " + old_status + " preserved in history."])
                    field["scope_notes"] = unique_append(
                        field.get("scope_notes", []), [
                            "This field has no product evidence on " + target_sha + "."
                        ])
                    ec = field["evidence_currency"]
                    ec.update({
                        "current_record_ids": [],
                        "historical_record_ids": unique_append(
                            ec.get("historical_record_ids", []),
                            unique_append(old_ids, ec.get("current_record_ids", []))),
                        "classification": "new_source_unverified",
                        "target_source_sha": target_sha,
                        "validation_product_root_sha": target_sha,
                        "historical_results_retagged": False,
                    })
                    next_evidence = field.get("next_evidence")
                    if isinstance(next_evidence, dict):
                        next_evidence["assessment_source_sha"] = target_sha
                        next_evidence["disposition"] = "new_source_execution_pending"
                    field_counts[field["status"]] = field_counts.get(field["status"], 0) + 1
                row["current_visible_verdict_source"] = target_sha
                row["current_technical_verdict"] = technical["status"]
                row["current_contract_verdict"] = "open"
                if row.get("implementation_assessment") == "VERIFIED":
                    row["implementation_assessment"] = "EVIDENCE_PRESENT"
                if row.get("verification_status") == "VERIFIED":
                    row["verification_status"] = "NOT_RUN"
                if "complete_requirement_acceptance" in row:
                    row["complete_requirement_acceptance"] = "NOT_COMPLETED"
                    if row["implementation"] == "implemented":
                        row["implementation"] = "inherited_not_reassessed"
                    row["implemented_scope"] = "Only historical source is verified."
                    row["remaining_scope"] = "New-source execution and review pending."
                if "status" in row:
                    previous_visible_status = row["status"]
                    if previous_visible_status not in {"PASS", "FAIL", "BLOCKED", "NOT_RUN"}:
                        raise ValueError("unsupported visible acceptance status")
                    row["status"] = "NOT_RUN"
                    row["independent"] = False
                    row["current_acceptance_note"] = (
                        "Historical " + previous_visible_status
                        + " only; not reassessed on " + target_sha
                    )
                total += 1
    if total != ROWS or sum(field_counts.values()) != CLAUSES:
        raise ValueError("the canonical inventory was altered")
    raw_index = json_bytes(idx)
    for name in LEDGERS:
        newer[name]["technical_evidence_index"]["sha256"] = digest(raw_index)
    after_bytes = {p: json_bytes(v) for p, v in newer.items()}
    if any(len(raw) > source_limit(name) for name, raw in after_bytes.items()):
        raise ValueError("postimage exceeds bounded policy input")
    if field_counts.get("pass", 0) or field_counts.get("fail", 0):
        raise ValueError("a previous decision crossed source identity")
    verdict = inspect(previous, newer)
    if verdict["ledger_rows_verified"] != ROWS or verdict["changed_assessments_with_history"] != ROWS:
        raise ValueError("history or clauses were dropped")
    validate_catalog_links(idx, [
        row for name in LEDGERS for key in ("requirements", "cases", "expansion_cases")
        for row in newer[name].get(key, [])
    ])
    by_id = {item["id"]: item for item in idx["records"]}
    for name in LEDGERS:
        for key in ("requirements", "cases", "expansion_cases"):
            for row in newer[name].get(key, []):
                validate_assessment(row["technical_assessment"], row["id"], idx, by_id)
    report = {
        "schema": "motionwright.conformance-source-rollforward/1",
        "target_source": target_sha, "previous_source": source["motionwright_sha"],
        "previous_source_index_sha256": digest(before_bytes[INDEX]),
        "postimage_digests": {
            p: {"bytes": len(data), "sha256": digest(data)}
            for p, data in after_bytes.items()
        },
        "history": verdict, "criterion_counts": field_counts,
        "new_product_executions": 0, "new_acceptances": 0,
        "human_reviews_invented": 0, "release_authorized": False,
    }
    return after_bytes, report


def verify_frozen_inputs(root: Path, target_sha: str, integrated: bool,
                         original_tree: str, lock_bytes: bytes,
                         original_ledgers: dict[str, bytes]) -> None:
    """Reject a checkout or input changed during evidence creation."""
    assert_source(root, target_sha, integrated=integrated)
    if git(root, "rev-parse", "HEAD^{tree}") != original_tree:
        raise ValueError("source tree changed during evidence preparation")
    if set(original_ledgers) != set((*LEDGERS, INDEX)):
        raise ValueError("original source input roster changed")
    captured = capture_source_inputs(root, target_sha)
    if captured["SOURCE_LOCK.json"] != lock_bytes:
        raise ValueError("source lock changed during evidence preparation")
    for name, content in original_ledgers.items():
        if captured[name] != content:
            raise ValueError("ledger source changed during evidence preparation: " + name)
    assert_source(root, target_sha, integrated=integrated)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--target-sha", required=True)
    parser.add_argument("--expected-old-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--integrated", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    output = args.output.resolve(strict=False)
    if not HEX40.fullmatch(args.expected_old_sha):
        raise SystemExit("invalid predecessor SHA")
    outer = next((part for part in (root, *root.parents)
                  if part.name.lower() == "motionwright"), None)
    if outer is None:
        raise SystemExit("repository must be under a Motionwright project root")
    private = outer / "knowledge" / "runtime"
    if private not in output.parents:
        raise SystemExit("output must be under the project-private runtime directory")
    if output.exists():
        raise SystemExit("refuse to overwrite existing evidence output")
    assert_source(root, args.target_sha, integrated=args.integrated)
    initial_tree = git(root, "rev-parse", "HEAD^{tree}")
    captured = capture_source_inputs(root, args.target_sha)
    lock_bytes = captured["SOURCE_LOCK.json"]
    lock_bytes.decode("utf-8")
    locked_digest = digest(lock_bytes.replace(b"\r\n", b"\n"))
    raw_inputs = {name: captured[name] for name in (*LEDGERS, INDEX)}
    current = {name: parse_unique(raw_inputs[name], name)
               for name in (*LEDGERS, INDEX)}
    previous_sha = current[INDEX]["source"]["motionwright_sha"]
    if previous_sha != args.expected_old_sha:
        raise SystemExit("the current ledger source differs from the stated predecessor")
    stamped = dt.datetime.now(dt.timezone.utc).isoformat()
    posts, report = rollforward(current, args.target_sha, locked_digest,
                                original_inputs=raw_inputs,
                                integrated=args.integrated, at_utc=stamped,
                                predecessor=args.expected_old_sha)
    report.update({"created_at_utc": stamped,
                   "target_tree": initial_tree,
                   "source_lock_sha256": locked_digest,
                   "original_raw_inputs": {
                       name: {"bytes": len(raw), "sha256": digest(raw)}
                       for name, raw in raw_inputs.items()
                   }})
    def recheck_frozen_inputs() -> None:
        verify_frozen_inputs(root, args.target_sha, args.integrated,
                             initial_tree, lock_bytes, raw_inputs)

    recheck_frozen_inputs()
    staging = output.with_name(output.name + ".pending")
    if staging.exists():
        raise SystemExit("old incomplete evidence staging exists; no new execution")
    staging.mkdir(parents=True, exist_ok=False)
    for relative, data in posts.items():
        filepath = staging / "postimages" / relative
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with filepath.open("xb") as f:
            f.write(data)
    with (staging / "manifest.private.json").open("xb") as f:
        f.write(json_bytes(report))
    for relative, data in posts.items():
        if (staging / "postimages" / relative).read_bytes() != data:
            raise ValueError("staged evidence file bytes mismatch")
    recheck_frozen_inputs()
    staging.rename(output)
    try:
        recheck_frozen_inputs()
    except BaseException:
        output.rename(output.with_name(output.name + ".invalid-source"))
        raise
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
