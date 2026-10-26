"""Exact-source technical evidence completeness, not a release authorization.

A technical claim is eligible for independent release review only after every
REQUIRED canonical criterion is 'pass' in the authenticated physical evidence
index and every REQUIRED row's technical verification is complete on main.
Human/rights/product acceptance and the release decision are SEPARATE external
acts, never inferred from current repository JSON or CI alone.

This tool does not grant release, install packages, create human reviews,
alter ledgers or reclassify a historical source. Future authentic independent
review may consume its source- and artifact-bound result.
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from conformance_policy import (
    MAX_EVIDENCE_INDEX_BYTES, _pairs, load_evidence_index, public_file,
    validate_assessment,
    validate_catalog_links,
)

LEDGERS = {
    "docs/acceptance/requirements-status.json": ("requirements",),
    "docs/creative-production/requirements-status.json": ("requirements",),
    "docs/acceptance/acceptance-status.json": ("cases", "expansion_cases"),
}
INDEX = "docs/acceptance/technical-evidence.json"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
REQUIRED_ROWS = 367
REQUIRED_FIELDS = 1532
CONTRACT_ROWS = 396
ATOMIC_FIELDS = 1652
NORMATIVE_EDGES = 506
EVIDENCE_PREFIX = "docs/acceptance/evidence/"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
# Reader resource ceilings, not contractual limits on evidence. An oversized
# addition needs a separately reviewed retention route; never hash a prefix.
MAX_ADDED_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_ADDED_ARTIFACT_TOTAL_BYTES = 256 * 1024 * 1024
CANONICAL_REPOSITORY = "seradotcom/motionwright"
CANONICAL_REMOTE_URLS = frozenset(
    prefix + CANONICAL_REPOSITORY + suffix
    for prefix in ("https://github.com/", "git@github.com:",
                   "ssh://git@github.com/")
    for suffix in ("", ".git")
)


class TechnicalReadinessError(ValueError):
    """Authenticated mechanical assessment could not be completed."""


def _need(valid: bool, why: str) -> None:
    if not valid:
        raise TechnicalReadinessError(why)


def _git(root: Path, *args: str, raw: bool = False) -> str | bytes:
    # Never honor caller-inherited Git redirects or replace-refs while binding.
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("GIT_")}
    env.update({
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1",
    })
    command = ["git", "--no-replace-objects", "-C", str(root), *args]
    try:
        p = subprocess.run(command, capture_output=True, env=env, timeout=50)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise TechnicalReadinessError("Git source authentication unavailable") from e
    _need(p.returncode == 0, "Git source authentication failed")
    return p.stdout if raw else p.stdout.decode("utf-8", "strict").strip()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_text(data: bytes) -> bytes:
    """The sole permitted tracked-text normalization; never strip content."""
    data.decode("utf-8", "strict")
    return data.replace(b"\r\n", b"\n")


def _read_tracked_bytes(root: Path, name: str, *,
                        max_bytes: int = MAX_EVIDENCE_INDEX_BYTES) -> bytes:
    # Bound the actual read, not only a previous stat or the Git blob. The
    # one extra byte detects growth without ever accepting a matching prefix.
    _need(type(max_bytes) is int and 0 <= max_bytes <= MAX_ADDED_ARTIFACT_BYTES,
          "physical tracked read has an invalid byte limit")
    path = public_file(root, name)

    def identity(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns)

    before = path.stat()
    _need(before.st_size <= max_bytes,
          "physical tracked evidence exceeds the reader byte limit")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        _need(stat.S_ISREG(opened.st_mode) and identity(opened) == identity(before),
              "physical tracked evidence changed before capture")
        raw = stream.read(max_bytes + 1)
        _need(len(raw) <= max_bytes,
              "physical tracked evidence exceeds the reader byte limit")
        after = os.fstat(stream.fileno())
        _need(identity(after) == identity(opened) and len(raw) == after.st_size,
              "physical tracked evidence changed during capture")
    # Repeat the canonical path/reparse guard after using the handle, then
    # bind that path to the same ordinary-file observation.
    _need(identity(public_file(root, name).stat()) == identity(after),
          "physical tracked evidence changed after capture")
    return raw


def _canonical_remote(root: Path) -> dict:
    # get-url uses Git's effective fetch URL, including local insteadOf rules.
    # The same isolated Git environment is used here and by ls-remote.
    effective = _git(root, "remote", "get-url", "--all", "origin").splitlines()
    _need(len(effective) == 1 and effective[0] in CANONICAL_REMOTE_URLS,
          "live main requires the canonical effective Motionwright remote")
    return {"name": "origin", "repository": CANONICAL_REPOSITORY,
            "effective_url": effective[0]}


def _canonical_evidence_addition(name: str) -> bool:
    return (name.startswith(EVIDENCE_PREFIX) and "\\" not in name
            and ":" not in name
            and all(part and part not in {".", ".."}
                    and part == part.rstrip(" .")
                    and not any(ord(char) < 32 for char in part)
                    for part in name.split("/")))


def _declared_retained_artifacts(index: dict) -> dict[str, str]:
    """Only roles whose physical bytes the canonical index loader validates."""
    _need(isinstance(index, dict)
          and isinstance(index.get("records"), list)
          and isinstance(index.get("human_reviews"), list),
          "evidence declarations must come from the canonical index")
    declared = {}

    def retain(item):
        _need(isinstance(item, dict) and isinstance(item.get("path"), str)
              and isinstance(item.get("sha256"), str)
              and SHA256.fullmatch(item["sha256"]) is not None,
              "retained artifact needs an explicit path and raw digest")
        name, digest = item["path"], item["sha256"]
        _need(name not in declared or declared[name] == digest,
              "retained artifact has conflicting raw digests")
        declared[name] = digest

    for record in index["records"]:
        _need(isinstance(record, dict) and isinstance(record.get("artifacts"), list),
              "evidence record artifact declarations are invalid")
        for artifact in record["artifacts"]:
            retain(artifact)
    for review in index["human_reviews"]:
        _need(isinstance(review, dict), "human record declaration is invalid")
        retain(review.get("record"))
    return declared


def _blob_size(root: Path, revision: str, name: str) -> int:
    size = _git(root, "cat-file", "-s", revision + ":" + name)
    _need(isinstance(size, str) and re.fullmatch(r"[0-9]+", size) is not None,
          "packaged artifact Git size is invalid")
    return int(size)


def _validated_package_additions(index: dict, expected: dict[str, str]) -> None:
    if not expected:
        return
    declared = _declared_retained_artifacts(index)
    _need(all(declared.get(name) == digest for name, digest in expected.items()),
          "packaged additions were not consumed by the validated evidence index")


def _frozen_checkout(root: Path, source: str) -> dict:
    _need(SHA40.fullmatch(source) is not None, "full exact SHA required")
    root = root.resolve(strict=True)
    _need(Path(_git(root, "rev-parse", "--show-toplevel")).resolve() == root,
          "Git checkout root not the expected directory")
    package_head = _git(root, "rev-parse", "HEAD")
    _need(SHA40.fullmatch(package_head) is not None, "ledger package SHA invalid")
    _need(not _git(root, "status", "--porcelain"),
          "working source checkout is not clean")
    # A ledger package is necessarily a CHILD commit of the actual product F:
    # storing the product SHA inside tracked JSON makes HEAD==assessed_sha
    # impossible without a self-referential Git object. Validate the parent
    # product and the separate ledger/evidence publication precisely.
    parents = _git(root, "rev-list", "--parents", "-n", "1", package_head).split()
    _need(len(parents) == 2 and parents[0] == package_head,
          "ledger package must be a single-parent commit")
    parent = parents[1]
    _need(parent == source, "ledger packaging must directly follow assessed product F")
    product_tree = _git(root, "rev-parse", source + "^{tree}")
    package_tree = _git(root, "rev-parse", package_head + "^{tree}")
    _need(SHA40.fullmatch(product_tree) is not None and
          SHA40.fullmatch(package_tree) is not None,
          "product or ledger package tree identity invalid")
    allowed = set(LEDGERS) | {INDEX}
    delta = _git(root, "diff", "--name-status", "--no-renames", source, package_head)
    modified = [line.split("\t") for line in delta.splitlines() if line]
    _need(all(len(item) == 2 for item in modified),
          "source-to-ledger package has an ambiguous path delta")
    changes = {name: status for status, name in modified}
    additions = set(changes) - allowed
    _need(len(changes) == len(modified)
          and all(changes.get(name) == "M" for name in allowed)
          and all(changes[name] == "A" and _canonical_evidence_addition(name)
                  for name in additions),
          "source-to-ledger package must modify exactly four permitted ledgers "
          "and only add declared public evidence")
    _need(len({name.casefold() for name in additions}) == len(additions),
          "new evidence paths must not alias by case")
    # Preserve regular-file identity. Neither a symlink nor an untracked file
    # with the same contents is a valid historical evidence package.
    packaged_digests = {}
    index_blob = None
    for name in sorted(allowed):
        entry = _git(root, "ls-tree", "--full-tree", package_head, "--", name)
        _need(entry.startswith("100644 blob ") or
              entry.startswith("100755 blob "),
              "packaged ledger must be a tracked ordinary file")
        _need(entry.endswith("\t" + name), "packaged ledger tree lookup differs")
        if name == INDEX and additions:
            size = _blob_size(root, package_head, name)
            _need(0 < size <= MAX_EVIDENCE_INDEX_BYTES,
                  "packaged index exceeds the existing reader byte limit")
        blob = _git(root, "show", package_head + ":" + name, raw=True)
        if name == INDEX and additions:
            _need(len(blob) == size, "packaged index Git size changed")
            index_blob = blob
        expected = _sha(_git_text(blob))
        _need(_sha(_git_text(_read_tracked_bytes(root, name))) == expected,
              "consumed ledger bytes differ from the frozen Git package")
        packaged_digests[name] = expected
    added_artifacts = {}
    sizes = {}
    if additions:
        declared = _declared_retained_artifacts(json.loads(
            index_blob.decode("utf-8", "strict"), object_pairs_hook=_pairs))
        _need(additions.issubset(declared),
              "new evidence file is not a retained artifact in the packaged index")
        historical_wire = _git(
            root, "ls-tree", "--full-tree", "-r", "--name-only", "-z", source,
            "--", ":(literal)" + EVIDENCE_PREFIX, raw=True)
        _need(not historical_wire or historical_wire.endswith(b"\0"),
              "historical evidence path inventory is incomplete")
        historical = ([name.decode("utf-8", "strict")
                       for name in historical_wire[:-1].split(b"\0")]
                      if historical_wire else [])
        _need(len(set(historical)) == len(historical)
              and all(name.startswith(EVIDENCE_PREFIX) for name in historical),
              "historical evidence path inventory differs from its exact scope")
        historical_casefold = {name.casefold() for name in historical}
        for name in sorted(additions):
            _need(name.casefold() not in historical_casefold,
                  "new evidence must not replace or alias a historical path")
            entry = _git(root, "ls-tree", "--full-tree", package_head,
                         "--", ":(literal)" + name)
            _need("\n" not in entry
                  and (entry.startswith("100644 blob ") or entry.startswith("100755 blob "))
                  and entry.endswith("\t" + name),
                  "new evidence must be a tracked ordinary file")
            size = _blob_size(root, package_head, name)
            _need(size <= MAX_ADDED_ARTIFACT_BYTES,
                  "new evidence exceeds the reader per-file byte limit")
            sizes[name] = size
        _need(sum(sizes.values()) <= MAX_ADDED_ARTIFACT_TOTAL_BYTES,
              "new evidence exceeds the reader total byte limit")
        for name in sorted(additions):
            blob = _git(root, "show", package_head + ":" + name, raw=True)
            _need(len(blob) == sizes[name] and _sha(blob) == declared[name],
                  "new evidence Git bytes differ from their declared raw digest")
            _need(_sha(_read_tracked_bytes(root, name, max_bytes=sizes[name])) == declared[name],
                  "consumed new evidence bytes differ from the frozen Git package")
            added_artifacts[name] = declared[name]
    original_lock = _git(root, "show", source + ":SOURCE_LOCK.json", raw=True)
    # Exact Git source bytes, including the final newline, must match the
    # source lock seen by the ledger package. Never strip Git blob content.
    lock_digest = _sha(_git_text(_read_tracked_bytes(root, "SOURCE_LOCK.json")))
    _need(_sha(original_lock.replace(b"\r\n", b"\n")) == lock_digest,
          "ledger-only publication changed product source lock")
    # Local origin/main may be stale. A foreign origin reporting the same
    # object is not evidence of integration in the canonical repository.
    remote_identity = _canonical_remote(root)
    remote = _git(root, "ls-remote", "origin", "refs/heads/main").split()
    _need(_canonical_remote(root) == remote_identity,
          "effective remote changed during main observation")
    _need(len(remote) == 2 and remote[1] == "refs/heads/main"
          and SHA40.fullmatch(remote[0]) is not None,
          "main integration requires one exact live remote ref")
    return {
        "sha": source,
        "product_tree": product_tree,
        "ledger_package_sha": package_head,
        "ledger_package_tree": package_tree,
        "packaged_paths": sorted(allowed | additions),
        "packaged_artifact_sha256": added_artifacts,
        "packaged_artifact_sizes": sizes,
        "packaged_git_text_sha256": packaged_digests,
        "remote_identity": remote_identity,
        "remote_main_sha": remote[0],
        "source_lock_sha256": lock_digest,
    }


def _rows_and_index(root: Path, packaged_digests: dict[str, str]):
    _need(set(packaged_digests) == set(LEDGERS) | {INDEX},
          "all four frozen ledger digests are required")
    docs = {}
    for name in LEDGERS:
        # Parse exactly the bytes authenticated here, not a second file read.
        raw = _read_tracked_bytes(root, name)
        _need(_sha(_git_text(raw)) == packaged_digests[name],
              "consumed ledger bytes differ from the frozen Git package")
        docs[name] = json.loads(raw.decode("utf-8", "strict"),
                               object_pairs_hook=_pairs)
    descriptor = docs["docs/acceptance/requirements-status.json"].get(
        "technical_evidence_index")
    _need(isinstance(descriptor, dict) and descriptor.get("path") == INDEX,
          "the evidence index must use its literal canonical path")
    _need(all(doc.get("technical_evidence_index") == descriptor for doc in docs.values()),
          "the three ledgers do not reference the same source-bound index")
    _need(descriptor.get("sha256") == packaged_digests[INDEX],
          "the evidence index descriptor differs from the frozen Git blob")
    # The canonical loader captures the index once and checks this digest
    # before parsing it; artifact and sufficiency verification stay intact.
    index, records = load_evidence_index(root, descriptor)
    required_rows = []
    for name, keys in LEDGERS.items():
        doc = docs[name]
        _need(doc.get("conformance_schema_version") == 1,
              "explicit conformance schema missing")
        _need(doc.get("current_assessment_source") == index["source"],
              "ledger header source differs from actual evidence index")
        for key in keys:
            rows = doc.get(key, [])
            _need(isinstance(rows, list), "canonical row bucket invalid")
            for row in rows:
                validate_assessment(row["technical_assessment"], row["id"], index, records)
                required_rows.append(row)
    validate_catalog_links(index, required_rows)
    _need(len(required_rows) == CONTRACT_ROWS
          and len(index["criteria"]) == ATOMIC_FIELDS
          and len(index["normative_edges"]) == NORMATIVE_EDGES,
          "original catalog cardinality changed")
    return index, required_rows


def derive_eligibility(index: dict, rows: list[dict], checkout: dict) -> dict:
    """Summarize *already independently validated* physical source statements.

    Callers MUST use this only after _rows_and_index() fully verifies every
    referenced artifact/review and _frozen_checkout() authenticates source.
    Synthetic unit inputs may exercise this pure arithmetic, never prove a
    product run or a human review.
    """
    _need(len(rows) == CONTRACT_ROWS and
          len(index["contracts"]) == CONTRACT_ROWS and
          len(index["criteria"]) == ATOMIC_FIELDS and
          len(index["normative_edges"]) == NORMATIVE_EDGES,
          "original complete catalog required")
    by_id = {}
    scope = Counter()
    statuses = {"required": Counter(), "conditional": Counter(), "optional": Counter()}
    row_not_ready = Counter()
    human_fields = set()
    fully_technical = {"required": 0, "conditional": 0, "optional": 0}
    recorded_contracts = {"required": 0, "conditional": 0, "optional": 0}
    for row in rows:
        rid = row["id"]
        _need(rid not in by_id and rid in index["contracts"],
              "missing/duplicate original canonical row")
        by_id[rid] = row
        contract = index["contracts"][rid]
        tier = contract["scope"]
        _need(tier in statuses, "invalid canonical applicability tier")
        if tier == "required":
            _need(row["technical_assessment"]["contract_acceptance"]["status"] != "not_applicable",
                  "required original criteria cannot be waived")
        scope[(tier, "rows")] += 1
        human_fields.update(contract["human_criterion_ids"])
        assessment = row["technical_assessment"]
        criteria = assessment["criteria"]
        _need(len(criteria) > 0, "canonical technical criteria missing")
        for field in criteria:
            _need(field["id"] in index["criteria"] and
                  index["criteria"][field["id"]] == field["source_clause_sha256"],
                  "unbound original atomic criterion")
            statuses[tier][field["status"]] += 1
            scope[(tier, "fields")] += 1
        complete = (
            all(field["status"] == "pass" for field in criteria)
            and assessment["implementation"]["status"] == "implemented"
            and assessment["integration"]["status"] == "main"
            and assessment["technical_verification"]["status"] == "pass"
            and assessment["source"] == index["source"]
            and not assessment["contract_acceptance"]["pending_criteria"]
        )
        if complete:
            fully_technical[tier] += 1
        elif tier == "required":
            if any(field["status"] != "pass" for field in criteria):
                row_not_ready["incomplete_atomic_criteria"] += 1
            if assessment["implementation"]["status"] != "implemented":
                row_not_ready["implementation_not_complete"] += 1
            if assessment["integration"]["status"] != "main":
                row_not_ready["not_main_integrated"] += 1
            if assessment["technical_verification"]["status"] != "pass":
                row_not_ready["technical_review_not_pass"] += 1
            if assessment["contract_acceptance"]["pending_criteria"]:
                row_not_ready["pending_obligations"] += 1
        if assessment["contract_acceptance"]["status"] == "accepted":
            recorded_contracts[tier] += 1
    _need(set(by_id) == set(index["contracts"]), "canonical owner row omitted")
    _need(len(human_fields) == 35, "canonical 35 human criterion obligations changed")
    _need(scope[("required", "rows")] == REQUIRED_ROWS and
          scope[("required", "fields")] == REQUIRED_FIELDS,
          "required original denominator differs")
    _need(sum(scope[(tier, "fields")] for tier in statuses) == ATOMIC_FIELDS,
          "atomic field total changed")
    _need(sum(scope[(tier, "rows")] for tier in statuses) == CONTRACT_ROWS,
          "contract row total changed")
    need_current = (
        checkout["sha"] == index["source"]["motionwright_sha"]
        and checkout["source_lock_sha256"] == index["source"]["source_lock_sha256"]
        and checkout.get("remote_main_sha") == checkout.get("ledger_package_sha")
    )
    # True technical evidence completion is a legitimate positive path.
    # It does not authorize any human/rights/product approval.
    all_required_technical = (need_current and
                              fully_technical["required"] == REQUIRED_ROWS and
                              statuses["required"].get("pass", 0) == REQUIRED_FIELDS)
    state = ("TECHNICAL_SOURCE_CLAIMS_COMPLETE_AWAITING_INDEPENDENT_ATTESTATION"
             if all_required_technical else
             "BLOCKED_INCOMPLETE_OR_STALE_REQUIRED_TECHNICAL_EVIDENCE")
    return {
        "schema": "motionwright.conformance-technical-readiness/1",
        "source": checkout,
        "assessed_source": index["source"],
        "source_identity_current": need_current,
        "status": state,
        "technical_source_claims_complete": all_required_technical,
        "reviewed_executions_authenticity_independently_attested": False,
        "technical_RC_READY": "NOT_ESTABLISHED_EXTERNALLY",
        "product_acceptance_READY": False,
        "release_authorized": False,
        "automatic_publish": False,
        "canonical_counts": {
            "requirements": 272, "scenarios": 124,
            "rows": CONTRACT_ROWS, "criteria": ATOMIC_FIELDS,
            "normative_edges": NORMATIVE_EDGES,
        },
        "scope": {
            tier: {
                "rows": scope[(tier, "rows")],
                "atomic_fields": scope[(tier, "fields")],
                "atomic_statuses": dict(sorted(statuses[tier].items())),
                "full_technical_rows": fully_technical[tier],
                "recorded_contract_acceptances_not_authenticated":
                    recorded_contracts[tier],
            } for tier in statuses
        },
        "required_incomplete_reasons": dict(sorted(row_not_ready.items())),
        "human_required_criterion_fields": len(human_fields),
        "human_review_identity_authentication": "NOT_ASSESSED_BY_THIS_REPORT",
        "human_observations_not_inferred_from_evidence_index": True,
        "external_release_authorization": "NOT_ISSUED",
        "source_bound_technical_claims_are_not_actual_release_approval": True,
    }


def inspect(root: Path, source_sha: str) -> dict:
    root = Path(root).resolve(strict=True)
    frozen = _frozen_checkout(root, source_sha)
    limits = frozen.get("packaged_artifact_sizes", {})
    original_bytes = {name: _read_tracked_bytes(
                          root, name, max_bytes=limits.get(name, MAX_EVIDENCE_INDEX_BYTES))
                      for name in (*LEDGERS, INDEX, "SOURCE_LOCK.json",
                                   *frozen["packaged_artifact_sha256"])}
    index, rows = _rows_and_index(root, frozen["packaged_git_text_sha256"])
    # This call follows the full canonical loader, including every retained
    # record artifact/member and human-record byte check, regardless of PASS.
    _validated_package_additions(index, frozen["packaged_artifact_sha256"])
    result = derive_eligibility(index, rows, frozen)
    _need(_frozen_checkout(root, source_sha) == frozen,
          "source changed during evidence assessment")
    _need(all(_read_tracked_bytes(
                  root, name, max_bytes=limits.get(name, MAX_EVIDENCE_INDEX_BYTES)) == value
              for name, value in original_bytes.items()),
          "exact tracked evidence changed during assessment")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True,
                        help="Exact 40-byte lowercase committed product source")
    parser.add_argument("--output", type=Path,
                        help="Emit one fresh immutable JSON receipt")
    parser.add_argument("--require-technical-completeness", action="store_true",
                        help="Exit 2 when evidence still incomplete or stale")
    opts = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        result = inspect(root, opts.source_sha)
        if opts.output is not None:
            with opts.output.open("x", encoding="utf8") as stream:
                json.dump(result, stream, indent=2, sort_keys=True, ensure_ascii=False)
                stream.write("\n")
        print("conformance-technical-status=" + result["status"] +
              " required_technical_rows=" +
              str(result["scope"]["required"]["full_technical_rows"]) +
              "/" + str(REQUIRED_ROWS))
        if opts.require_technical_completeness and not result["technical_source_claims_complete"]:
            return 2
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        # Errors are displayed as class only. Never print private source,
        # original human text, paths, or raw record JSON in CI logs.
        print("conformance-technical-gate=failed code=" +
              type(error).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
