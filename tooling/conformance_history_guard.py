"""Check append-only technical history across two exact Git source snapshots.

This does not certify a product release or any human acceptance. It compares
public structured metadata only. Private original requirements never leave the
ignored knowledge directory.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import zlib

SHA = re.compile(r"[0-9a-f]{40}\Z")
LEDGERS = (
    "docs/acceptance/requirements-status.json",
    "docs/acceptance/acceptance-status.json",
    "docs/creative-production/requirements-status.json",
)
INDEX = "docs/acceptance/technical-evidence.json"
LIMIT = 16 * 1024 * 1024
INDEX_LIMIT = 32 * 1024 * 1024


class HistoryViolation(ValueError):
    pass


def source_limit(label: str) -> int:
    return INDEX_LIMIT if label == INDEX else LIMIT


def _reject_nonfinite(value):
    raise HistoryViolation("nonfinite_json_value")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise HistoryViolation("nonfinite_json_value")
    return number


def _typed_canonical(value) -> bytes:
    # Python equality treats false as 0 and 1 as 1.0. Canonical JSON keeps
    # these distinct while retaining object-key independence and list order.
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise HistoryViolation("invalid_typed_json_value") from exc


def _same(before, after) -> bool:
    return _typed_canonical(before) == _typed_canonical(after)


def parse_unique(data: bytes, label: str) -> dict:
    def unique(pairs):
        value = {}
        for key, val in pairs:
            if key in value:
                raise HistoryViolation("duplicate_json_field")
            value[key] = val
        return value

    if not data or len(data) > source_limit(label):
        raise HistoryViolation("source_size_out_of_bounds")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=_reject_nonfinite, parse_float=_finite_float)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HistoryViolation("invalid_source_json") from exc
    if not isinstance(value, dict):
        raise HistoryViolation("expected_json_object")
    return value


def fixed_identity_map(rows: list, what: str) -> dict:
    if not isinstance(rows, list):
        raise HistoryViolation("missing_" + what)
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise HistoryViolation("invalid_" + what + "_record")
        if row["id"] in result:
            raise HistoryViolation("duplicate_" + what + "_id")
        result[row["id"]] = row
    return result


def prefix(previous: list, candidate: list, label: str) -> None:
    if not isinstance(previous, list) or not isinstance(candidate, list):
        raise HistoryViolation("invalid_" + label)
    if not _same(candidate[:len(previous)], previous):
        raise HistoryViolation(label + "_rewritten_or_truncated")


# H03: bounded, reversible lossless history archive for long source lineages.
HISTORY_ARCHIVE_SCHEMA = "motionwright.assessment-history-zlib-json/1"
MAX_HISTORY_PLAIN_BYTES = 1_048_576
MAX_HISTORY_ENCODED_BYTES = 1_048_576


def _history_canonical(value: list) -> bytes:
    return _typed_canonical(value)


def _history_pairs_unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise HistoryViolation("duplicate_history_archive_json_field")
        result[key] = value
    return result


def _history_archive_entry(value: object) -> bool:
    return isinstance(value, dict) and value.get("schema") == HISTORY_ARCHIVE_SCHEMA


def encode_assessment_history(history: list) -> dict:
    if not isinstance(history, list):
        raise HistoryViolation("invalid_assessment_history")
    entries = expand_assessment_history(history)
    if not entries:
        raise HistoryViolation("empty_history_archive")
    plain = _history_canonical(entries)
    if len(plain) > MAX_HISTORY_PLAIN_BYTES:
        raise HistoryViolation("history_archive_plain_size_exceeded")
    compressed = zlib.compress(plain, level=9)
    if len(compressed) > MAX_HISTORY_ENCODED_BYTES:
        raise HistoryViolation("history_archive_encoded_size_exceeded")
    return {
        "schema": HISTORY_ARCHIVE_SCHEMA,
        "encoding": "zlib+base64",
        "entry_count": len(entries),
        "plain_bytes": len(plain),
        "plain_sha256": hashlib.sha256(plain).hexdigest(),
        "payload_base64": base64.b64encode(compressed).decode("ascii"),
    }


def _decode_assessment_history_archive(value: dict) -> list:
    if set(value) != {"schema", "encoding", "entry_count", "plain_bytes",
                      "plain_sha256", "payload_base64"}:
        raise HistoryViolation("history_archive_envelope_changed")
    if value["encoding"] != "zlib+base64":
        raise HistoryViolation("history_archive_unknown_encoding")
    count = value["entry_count"]
    length = value["plain_bytes"]
    if type(count) is not int or count <= 0 or count > 10000:
        raise HistoryViolation("history_archive_entry_count_invalid")
    if type(length) is not int or length <= 0 or length > MAX_HISTORY_PLAIN_BYTES:
        raise HistoryViolation("history_archive_plain_size_exceeded")
    digest = value["plain_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise HistoryViolation("history_archive_sha256_invalid")
    raw = value["payload_base64"]
    if not isinstance(raw, str) or len(raw) > (MAX_HISTORY_ENCODED_BYTES * 4 // 3 + 8):
        raise HistoryViolation("history_archive_encoded_size_exceeded")
    try:
        compressed = base64.b64decode(raw, validate=True)
        if not compressed or len(compressed) > MAX_HISTORY_ENCODED_BYTES:
            raise HistoryViolation("history_archive_encoded_size_exceeded")
        decoder = zlib.decompressobj()
        plain = decoder.decompress(compressed, MAX_HISTORY_PLAIN_BYTES + 1)
        if (len(plain) != length or not decoder.eof or
                decoder.unconsumed_tail or decoder.unused_data):
            raise HistoryViolation("history_archive_truncated_or_oversized")
        if hashlib.sha256(plain).hexdigest() != digest:
            raise HistoryViolation("history_archive_sha256_mismatch")
        entries = json.loads(plain.decode("utf-8"), object_pairs_hook=_history_pairs_unique,
                             parse_constant=_reject_nonfinite, parse_float=_finite_float)
    except (binascii.Error, zlib.error, UnicodeError, json.JSONDecodeError,
            ValueError) as exc:
        if isinstance(exc, HistoryViolation):
            raise
        raise HistoryViolation("history_archive_decode_failed") from exc
    if (not isinstance(entries, list) or len(entries) != count or
            not all(isinstance(x, dict) and "schema" not in x for x in entries)):
        raise HistoryViolation("history_archive_entries_invalid")
    if _history_canonical(entries) != plain:
        raise HistoryViolation("history_archive_noncanonical_json")
    return entries


def expand_assessment_history(history: list) -> list:
    if not isinstance(history, list):
        raise HistoryViolation("invalid_assessment_history")
    expanded = []
    for index, entry in enumerate(history):
        if _history_archive_entry(entry):
            if index != 0:
                raise HistoryViolation("history_archive_must_be_prefix")
            expanded.extend(_decode_assessment_history_archive(entry))
        elif isinstance(entry, dict) and "schema" in entry and str(entry["schema"]).startswith(
                "motionwright.assessment-history-"):
            raise HistoryViolation("unknown_history_archive_schema")
        elif isinstance(entry, dict):
            expanded.append(entry)
        else:
            raise HistoryViolation("invalid_assessment_history_entry")
    return expanded


def scoped_rows(document: dict) -> dict:
    return fixed_identity_map(
        document.get("requirements", []) + document.get("cases", []) +
        document.get("expansion_cases", []), "ledger")


def review_row(before: dict, after: dict) -> bool:
    if after.get("source_record_sha256") != before.get("source_record_sha256"):
        raise HistoryViolation("canonical_row_identity_changed")
    oldhistory = expand_assessment_history(before.get("assessment_history"))
    newhistory = expand_assessment_history(after.get("assessment_history"))
    prefix(oldhistory, newhistory, "assessment_history")
    old = before.get("technical_assessment")
    new = after.get("technical_assessment")
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise HistoryViolation("missing_technical_assessment")
    oldcriteria = fixed_identity_map(old.get("criteria"), "criterion")
    newcriteria = fixed_identity_map(new.get("criteria"), "criterion")
    if set(oldcriteria) != set(newcriteria):
        raise HistoryViolation("canonical_criterion_identity_changed")
    for identity in oldcriteria:
        if oldcriteria[identity].get("source_clause_sha256") != newcriteria[identity].get("source_clause_sha256"):
            raise HistoryViolation("canonical_criterion_sha_changed")
    prevcurrency = old.get("evidence_currency", {})
    newcurrency = new.get("evidence_currency", {})
    prevhist = set(prevcurrency.get("historical_record_ids", []))
    newhist = set(newcurrency.get("historical_record_ids", []))
    if not prevhist.issubset(newhist):
        raise HistoryViolation("prior_historical_evidence_lost")
    prevcurrent = set(prevcurrency.get("current_record_ids", []))
    nextcurrent = set(newcurrency.get("current_record_ids", []))
    if not (prevcurrent - nextcurrent).issubset(newhist):
        raise HistoryViolation("current_evidence_lost_without_history")
    for assessment in (old, new):
        if type(assessment.get("schema_version")) is not int or assessment["schema_version"] != 1:
            raise HistoryViolation("unknown_assessment_schema")
    changed = not _same(old, new)
    if changed:
        added = newhistory[len(oldhistory):]
        status = old.get("technical_verification", {}).get("status")
        expected_source = old.get("source")
        def captures(value):
            if not isinstance(value, dict) or not _same(value.get("source"), expected_source):
                return False
            if not _same(value.get("prior_technical_status", value.get("prior_status")), status):
                return False
            prior_contract = old.get("contract_acceptance", {}).get("status")
            if ("prior_contract_status" in value and
                    not _same(value["prior_contract_status"], prior_contract)):
                return False
            if ("prior_current_record_ids" in value and
                    not _same(value["prior_current_record_ids"],
                              old.get("evidence_currency", {}).get("current_record_ids"))):
                return False
            # H01: a current evidence-ID list, an empty list or a plausible but
            # invented criterion list cannot prove an actual prior assessment.
            # Prefer the entire original criteria, including IDs, clause hashes,
            # statuses, evidence links and missing assertions.
            if "prior_criteria" in value:
                return _same(value["prior_criteria"], old["criteria"])
            # Retain support for older compact status-map snapshots, but only
            # when the map is complete and matches every original criterion.
            statuses = value.get("prior_criterion_statuses")
            expected_statuses = {
                criterion["id"]: criterion["status"] for criterion in old["criteria"]
            }
            return isinstance(statuses, dict) and _same(statuses, expected_statuses)
        if not any(captures(entry) for entry in added):
            raise HistoryViolation("changed_assessment_has_no_authentic_prior_snapshot")
    # Changing only a noncanonical visible title with stable original ID and
    # prior history is safe; it does not reset previously assessed coverage.
    return changed


def inspect(before: dict, after: dict) -> dict:
    old_index = before.get(INDEX)
    new_index = after.get(INDEX)
    if not isinstance(old_index, dict) or not isinstance(new_index, dict):
        raise HistoryViolation("missing_source_index")
    for index in (old_index, new_index):
        if index.get("schema") != "motionwright.technical-evidence/1" or "history_prefix" in index:
            raise HistoryViolation("unknown_source_index_schema")
        for listkey in ("records", "sufficiency_reviews", "human_reviews"):
            fixed_identity_map(index.get(listkey), "index_" + listkey)
    for immutable in ("criteria", "contracts", "normative_edges",
                      "criterion_aliases"):
        if not _same(old_index.get(immutable), new_index.get(immutable)):
            raise HistoryViolation("canonical_source_catalogue_changed")
    for listkey in ("records", "sufficiency_reviews", "human_reviews",
                    "assessment_history"):
        prefix(old_index.get(listkey), new_index.get(listkey), "index_" + listkey)
    assessed = 0
    for ledger in LEDGERS:
        old = before.get(ledger)
        new = after.get(ledger)
        if not isinstance(old, dict) or not isinstance(new, dict):
            raise HistoryViolation("missing_ledger")
        for document in (old, new):
            for version in ("schema_version", "conformance_schema_version"):
                if type(document.get(version)) is not int or document[version] != 1:
                    raise HistoryViolation("unknown_ledger_schema")
        for collection in ("requirements", "cases", "expansion_cases"):
            old_rows = fixed_identity_map(old.get(collection, []), "ledger")
            new_rows = fixed_identity_map(new.get(collection, []), "ledger")
            if set(old_rows) != set(new_rows):
                raise HistoryViolation("ledger_id_removed_renamed_or_added")
        a = scoped_rows(old)
        b = scoped_rows(new)
        if set(a) != set(b):
            raise HistoryViolation("ledger_id_removed_renamed_or_added")
        for rid in a:
            assessed += review_row(a[rid], b[rid])
    total = sum(len(scoped_rows(after[path])) for path in LEDGERS)
    return {"ledger_rows_verified":total,
            "changed_assessments_with_history":assessed,
            "historical_record_count":len(old_index["records"]),
            "result":"history_preserved",
            "product_acceptance":False, "release_authorized":False}


def _unredirected_git(repo: Path, *args: str) -> bytes:
    # H02: the caller's Git environment may silently substitute the repository,
    # its object database, reference replacements, config or worktree. A
    # 40-character commit hash alone does not protect a Git CLI read from those.
    # Suppress both inherited overrides and repository-local replace refs.
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("GIT_")}
    env.update({
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1",
    })
    try:
        proc = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(repo), *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HistoryViolation("git_source_unavailable") from exc
    if proc.returncode:
        raise HistoryViolation("git_source_unavailable")
    return proc.stdout


def git_exact(repo: Path, revision: str, filename: str) -> bytes:
    if not isinstance(revision, str) or SHA.fullmatch(revision) is None:
        raise HistoryViolation("exact_sha_required")
    if filename not in (*LEDGERS, INDEX):
        raise HistoryViolation("unrecognized_source_path")
    try:
        root = Path(repo).resolve(strict=True)
    except OSError as exc:
        raise HistoryViolation("git_repository_unavailable") from exc
    if not root.is_dir():
        raise HistoryViolation("git_repository_unavailable")
    actual = _unredirected_git(root, "rev-parse", "--show-toplevel").decode(
        "utf-8", errors="strict").strip()
    if Path(actual).resolve() != root:
        raise HistoryViolation("git_repository_root_mismatch")
    if _unredirected_git(root, "cat-file", "-t", revision).strip() != b"commit":
        raise HistoryViolation("git_exact_revision_not_a_commit")
    object_name = revision + ":" + filename
    try:
        size = int(_unredirected_git(root, "cat-file", "-s", object_name).strip())
    except ValueError as exc:
        raise HistoryViolation("invalid_git_source_size") from exc
    if size <= 0 or size > source_limit(filename):
        raise HistoryViolation("source_size_out_of_bounds")
    data = _unredirected_git(root, "show", object_name)
    if len(data) != size:
        raise HistoryViolation("git_source_size_changed")
    return data


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument("--before",required=True)
    parser.add_argument("--after",required=True)
    args=parser.parse_args()
    try:
        old={p:parse_unique(git_exact(args.repo,args.before,p),p) for p in (*LEDGERS,INDEX)}
        new={p:parse_unique(git_exact(args.repo,args.after,p),p) for p in (*LEDGERS,INDEX)}
        result=inspect(old,new)
        result.update(baseline_sha=args.before,candidate_sha=args.after)
        print(json.dumps(result,sort_keys=True))
        return 0
    except (HistoryViolation,OSError,subprocess.TimeoutExpired,UnicodeError) as error:
        print("conformance_history=blocked:"+str(error))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
