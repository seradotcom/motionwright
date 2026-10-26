"""Source-bound conformance evidence impact report.

Compare canonical requirements, acceptance cases and their evidence with one
exact committed product revision. Historical results are never silently
reclassified as a PASS on a later checkout.

Read-only usage:
    python -B tooling/conformance_currency_impact.py --target HEAD
    python -B tooling/conformance_currency_impact.py --target HEAD --json report.json
    python -B tooling/conformance_currency_impact.py --target HEAD --strict

--strict exits 2 if the assessed source does not match the target, or a PASS
criterion lacks sufficient exact-source evidence. The tool does not edit a
ledger, run a renderer or authorize release.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

from conformance_policy import (DIGEST, SHA, load_evidence_index, load_json,
                                validate_catalog_links)

LEDGERS = (
    "docs/acceptance/requirements-status.json",
    "docs/creative-production/requirements-status.json",
    "docs/acceptance/acceptance-status.json",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str, input_data: bytes | None = None) -> bytes:
    # A commit name alone is not a source boundary: inherited Git overrides
    # and repository-local replace refs can substitute another object database
    # or different bytes while preserving the requested commit label.
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
    options = {} if input_data is None else {"input": input_data}
    process = subprocess.run(command, capture_output=True, env=env,
                             timeout=80, check=False, **options)
    if process.returncode:
        raise ValueError("Git source lookup failed: " + " ".join(args[:2]) +
                         ": " + process.stderr.decode("utf-8", "replace")[-180:])
    return process.stdout


def commit_sha(root: Path, spec: str) -> str:
    # Never interpolate commit specs into a shell, and reject revision expressions.
    try:
        root = Path(root).resolve(strict=True)
    except OSError as error:
        raise ValueError("source repository is unavailable") from error
    actual = git(root, "rev-parse", "--show-toplevel").decode("utf-8", "strict").strip()
    if Path(actual).resolve() != root:
        raise ValueError("source repository root differs from the requested root")
    if spec == "HEAD":
        resolved = git(root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    elif SHA.fullmatch(spec):
        resolved = git(root, "rev-parse", "--verify", spec + "^{commit}").decode().strip()
        if resolved != spec:
            raise ValueError("source lookup unexpectedly resolved another commit")
    else:
        raise ValueError("target must be HEAD or one full lowercase commit SHA")
    if not SHA.fullmatch(resolved):
        raise ValueError("target could not resolve to one full source commit")
    return resolved


def source_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("source reference needs an unambiguous repository-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ("", ".", "..") for p in value.split("/")):
        raise ValueError("source reference cannot escape Git tree")
    return value


# One invocation is bounded before launching any batch process. The observed
# canonical inventory is below 20 MiB over 1068 objects; these are fail-closed
# resource bounds, not permission to omit a source or reuse a historical PASS.
MAX_SOURCE_OBJECTS = 16384
MAX_SOURCE_BLOB_BYTES = 16 * 1024 * 1024
MAX_SOURCE_TOTAL_BYTES = 256 * 1024 * 1024
SOURCE_BATCH_BYTES = 8 * 1024 * 1024


def source_batch_blobs(data: bytes, expected: list[tuple[str, int]]) -> dict[str, bytes]:
    """Verify closed cat-file framing and the raw Git object identity."""
    if (len(expected) > MAX_SOURCE_OBJECTS or
            len(expected) != len({oid for oid, _ in expected}) or
            any(not SHA.fullmatch(oid) or type(size) is not int or
                not 0 <= size <= MAX_SOURCE_BLOB_BYTES
                for oid, size in expected)):
        raise ValueError("invalid closed source batch inventory")
    if sum(size for _, size in expected) > MAX_SOURCE_TOTAL_BYTES:
        raise ValueError("source batch total exceeds bounded inventory")
    headers = [(oid + " blob " + str(size) + "\n").encode("ascii")
               for oid, size in expected]
    wire_size = sum(len(header) + size + 1
                    for header, (_, size) in zip(headers, expected))
    if len(data) != wire_size:
        raise ValueError("Git batch output is truncated or contains extra bytes")
    result = {}
    offset = 0
    for header, (oid, size) in zip(headers, expected):
        if data[offset:offset + len(header)] != header:
            raise ValueError("Git batch object, type or size differs from source tree")
        begin = offset + len(header)
        end = begin + size
        blob = data[begin:end]
        if data[end:end + 1] != b"\n":
            raise ValueError("Git batch object has no exact terminal delimiter")
        actual = hashlib.sha1(b"blob " + str(size).encode("ascii") + b"\0" + blob).hexdigest()
        if actual != oid:
            raise ValueError("Git batch raw object identity differs from committed tree")
        result[oid] = blob
        offset = end + 1
    return result



class GitSnapshot:
    """Exact committed Git UTF-8 text; checkout state and symlinks cannot substitute."""

    def __init__(self, root: Path, source: str):
        self.root = Path(root).resolve(strict=True)
        self.source = commit_sha(self.root, source)
        self.modes: dict[str, str] = {}
        self.cache: dict[str, str | None] = {}
        self.non_text: dict[str, tuple] = {}
        self.objects: dict[str, tuple[str, int]] = {}
        tree = git(self.root, "ls-tree", "-rlz", "--full-tree", self.source)
        for row in tree.split(b"\0"):
            if not row:
                continue
            descriptor, path = row.split(b"\t", 1)
            mode, kind, oid, size = descriptor.decode("ascii").split()
            if kind == "blob":
                name = path.decode("utf-8", "strict")
                self.modes[name] = mode
                self.objects[name] = (oid, int(size))

    def prefetch(self, names: list[str]) -> None:
        """Read exact immutable blobs in bounded batches; publish no partial cache.

        Each entry still passes the path/mode guard. Requests contain only the
        object IDs read from this commit's tree, never caller-supplied revision
        expressions. Bounds reject the operation instead of dropping references.
        """
        paths = {source_path(name) for name in names}
        pending = sorted(name for name in paths
                         if name not in self.cache and name not in self.non_text)
        if len(pending) > MAX_SOURCE_OBJECTS:
            raise ValueError("source batch reference count exceeds bounded inventory")
        values: dict[str, str | None] = {}
        errors: dict[str, tuple] = {}
        objects: dict[str, tuple[int, list[str]]] = {}
        for name in pending:
            if self.modes.get(name) not in ("100644", "100755"):
                values[name] = None
                continue
            oid, size = self.objects[name]
            if not SHA.fullmatch(oid) or not 0 <= size <= MAX_SOURCE_BLOB_BYTES:
                raise ValueError("source blob exceeds bounded exact-object contract")
            if oid in objects:
                if objects[oid][0] != size:
                    raise ValueError("one source object has inconsistent sizes")
                objects[oid][1].append(name)
            else:
                objects[oid] = (size, [name])
        if sum(item[0] for item in objects.values()) > MAX_SOURCE_TOTAL_BYTES:
            raise ValueError("source batch total exceeds bounded inventory")
        batches: list[list[tuple[str, int]]] = []
        current: list[tuple[str, int]] = []
        size = 0
        for oid, (length, _) in objects.items():
            if current and size + length > SOURCE_BATCH_BYTES:
                batches.append(current)
                current, size = [], 0
            current.append((oid, length))
            size += length
        if current:
            batches.append(current)
        for batch in batches:
            request = b"".join((oid + "\n").encode("ascii") for oid, _ in batch)
            output = git(self.root, "cat-file", "--batch", input_data=request)
            for oid, data in source_batch_blobs(output, batch).items():
                try:
                    data.decode("utf-8", "strict")
                except UnicodeDecodeError as error:
                    for name in objects[oid][1]:
                        errors[name] = error.args
                else:
                    value = sha256(data.replace(b"\r\n", b"\n"))
                    for name in objects[oid][1]:
                        values[name] = value
        # A corrupt, truncated or extra frame invalidates the entire prefetch.
        # Existing cached lookups remain intact, and no new prefix is admitted.
        self.cache.update(values)
        self.non_text.update(errors)


    def digest(self, name: str) -> str | None:
        name = source_path(name)
        if name in self.cache:
            return self.cache[name]
        if name in self.non_text:
            raise UnicodeDecodeError(*self.non_text[name])
        mode = self.modes.get(name)
        # Git symlink and submodule file contents are not an acceptable oracle.
        if mode not in ("100644", "100755"):
            self.cache[name] = None
            return None
        data = git(self.root, "show", self.source + ":" + name)
        data.decode("utf-8", "strict")
        result = sha256(data.replace(b"\r\n", b"\n"))
        self.cache[name] = result
        return result


def record_currency(record: dict, target: str, lock: str | None, snapshot: GitSnapshot) -> dict:
    source = record["source"]
    inputs = []
    for relation in ("source_inputs", "definitions"):
        for ref in record[relation]:
            name = source_path(ref["path"])
            expected = ref["sha256"]
            if not DIGEST.fullmatch(expected):
                raise ValueError("reference must include one exact SHA-256")
            try:
                actual = snapshot.digest(name)
            except UnicodeDecodeError:
                # A git-text-lf binding cannot be renewed with a raw binary hash.
                # Retain the unverified reference and still inspect the others.
                actual, status = None, "non_text"
            else:
                status = "same" if expected == actual else ("missing_or_link" if actual is None else "changed")
            inputs.append({"path": name, "kind": relation[:-1], "expected_sha256": expected,
                           "target_sha256": actual, "status": status})
    same = all(x["status"] == "same" for x in inputs)
    declared_source = source["motionwright_sha"]
    declared_lock = source["source_lock_sha256"]
    # Matching source blobs are not proof that an OLD Git checkout executed on NEW main.
    if declared_source != target:
        category = "historical_same_inputs" if same and declared_lock == lock else "historical_changed_or_unknown"
    elif declared_lock != lock or not same:
        category = "current_source_invalidated"
    elif record["execution"]["status"] == "pass":
        category = "current_execution_candidate"
    else:
        category = "current_nonpassing_execution"
    return {"id": record["id"], "declared_source_sha": declared_source,
            "recorded_source_lock_sha256": declared_lock, "current_source_relation": category,
            "profile": record["execution"]["profile"],
            "execution_kind": record["execution"]["kind"],
            "execution_status": record["execution"]["status"],
            "covered_criteria": list(record["criterion_ids"]),
            "complete_criteria": list(record["complete_criterion_ids"]),
            "changed_references": [x for x in inputs if x["status"] != "same"],
            "exact_source_inputs": same, "referenced_files": len(inputs)}


def classify_criterion(field: dict, assessment: dict, target: str, lock: str | None,
                       evidence: dict[str, dict], records: dict[str, dict],
                       reviews: list[dict], stale_review_ids: set[str]) -> tuple[str, list[str]]:
    """Never promote a historical execution, an old review, or an incomplete field."""
    identity = field["id"]
    citations = field["evidence_ids"]
    current = [key for key in citations if key in evidence and
               evidence[key]["current_source_relation"] == "current_execution_candidate"]
    valid_reviews = [review["id"] for review in reviews
                     if review["decision"] == "sufficient"
                     and review["criterion_id"] == identity
                     and review["source"]["motionwright_sha"] == target
                     and review["source"]["source_lock_sha256"] == lock
                     and review["id"] not in stale_review_ids
                     and bool(review["evidence_record_sha256"])
                     and set(review["evidence_record_sha256"]).issubset(set(current))]
    complete = (field["status"] == "pass" and
                assessment["source"]["motionwright_sha"] == target and
                assessment["source"]["source_lock_sha256"] == lock and
                bool(current) and bool(valid_reviews) and
                any(identity in records[key]["complete_criterion_ids"] for key in current))
    if complete:
        return "sufficient_current_source", valid_reviews
    if field["status"] == "pass":
        return "pass_requires_reassessment", valid_reviews
    if current:
        return "current_partial_or_not_admitted", valid_reviews
    return ("historical_or_stale_evidence" if citations else "not_evidenced"), valid_reviews


def inspect(root: Path, target_spec: str) -> dict:
    root = Path(root).resolve()
    target = commit_sha(root, target_spec)
    snapshot = GitSnapshot(root, target)
    lock = snapshot.digest("SOURCE_LOCK.json")
    documents = [load_json(root / path) for path in LEDGERS]
    descriptors = [doc["technical_evidence_index"] for doc in documents]
    if any(desc != descriptors[0] for desc in descriptors):
        raise ValueError("public ledgers disagree about their technical evidence index")
    index, byid = load_evidence_index(root, descriptors[0])
    rows: dict[str, dict] = {}
    for document in documents:
        if document["current_assessment_source"] != index["source"]:
            raise ValueError("ledger source differs from evidence index")
        for kind in ("requirements", "cases", "expansion_cases"):
            for row in document.get(kind, []):
                rid = row["id"]
                if rid in rows:
                    raise ValueError("duplicate canonical ID in public ledgers: " + rid)
                rows[rid] = row
    if len(rows) != 396 or len(index["criteria"]) != 1652 or len(index["normative_edges"]) != 506:
        raise ValueError("original 396-row, 1652-field and 506-link coverage required")
    validate_catalog_links(index, rows.values())
    for rid, row in rows.items():
        fields = row["technical_assessment"]["criteria"]
        actual = {field["id"]: field["source_clause_sha256"] for field in fields}
        expected = {k: v for k, v in index["criteria"].items() if k.startswith(rid + "#/")}
        if actual != expected or len(actual) != len(fields):
            raise ValueError("assessed criterion identities do not equal canonical originals: " + rid)

    snapshot.prefetch([ref["path"] for record in index["records"]
                       for relation in ("source_inputs", "definitions")
                       for ref in record[relation]])
    evidence = [record_currency(record, target, lock, snapshot) for record in index["records"]]
    by_evidence = {x["id"]: x for x in evidence}
    reviews_by_field: dict[str, list[dict]] = collections.defaultdict(list)
    for review in index["sufficiency_reviews"]:
        reviews_by_field[review["criterion_id"]].append(review)
    counts = collections.Counter()
    recorded_implementation = collections.Counter()
    recorded_integration = collections.Counter()
    recorded_contract_status = collections.Counter()
    danger = []
    field_state = []
    result_rows = []
    for rid, row in sorted(rows.items()):
        assessment = row["technical_assessment"]
        recorded_implementation[assessment["implementation"]["status"]] += 1
        recorded_integration[assessment["integration"]["status"]] += 1
        recorded_contract_status[assessment["contract_acceptance"]["status"]] += 1
        fields = []
        for field in assessment["criteria"]:
            fid = field["id"]
            citations = field["evidence_ids"]
            currency, valid_reviews = classify_criterion(
                field, assessment, target, lock, by_evidence, byid,
                reviews_by_field[fid], set(index.get("_stale_sufficiency_reviews", {})))
            if currency == "pass_requires_reassessment":
                danger.append(fid)
            counts[currency] += 1
            state = {"id": fid, "recorded_status": field["status"], "currency": currency,
                     "cited_record_ids": citations, "valid_current_review_ids": valid_reviews}
            fields.append(state)
            field_state.append(state)
        entire = (all(item["currency"] == "sufficient_current_source" for item in fields)
                  and assessment["implementation"]["status"] == "implemented"
                  and assessment["integration"]["status"] == "main"
                  and assessment["source"]["motionwright_sha"] == target)
        result_rows.append({"id": rid,
                            "implementation_status": assessment["implementation"]["status"],
                            "integration_status": assessment["integration"]["status"],
                            "contract_acceptance_status": assessment["contract_acceptance"]["status"],
                            "recorded_technical_status": assessment["technical_verification"]["status"],
                            "current_technical_complete": entire,
                            "full_contract_accepted": False,  # No automatic human/product/release promotion
                            "field_count": len(fields), "fields": fields})
    record_counts = dict(collections.Counter(item["current_source_relation"] for item in evidence))
    # Prioritize independent reruns by exact changed source/oracle path, not by
    # arbitrary PR order. One path may invalidate many evidence records.
    changed_paths: dict[str, dict] = {}
    for record in evidence:
        for item in record["changed_references"]:
            path = item["path"]
            slot = changed_paths.setdefault(path, {
                "path": path, "record_ids": set(), "criterion_ids": set(),
                "status_counts": collections.Counter()})
            slot["record_ids"].add(record["id"])
            slot["criterion_ids"].update(record["covered_criteria"])
            slot["status_counts"][item["status"]] += 1
    hotspots = [
        {"path": path, "record_count": len(slot["record_ids"]),
         "criterion_count": len(slot["criterion_ids"]),
         "record_ids": sorted(slot["record_ids"]),
         "affected_canonical_row_ids": sorted({fid.split("#/", 1)[0] for fid in slot["criterion_ids"]}),
         "source_status_counts": dict(slot["status_counts"])}
        for path, slot in changed_paths.items()
    ]
    hotspots.sort(key=lambda slot: (-slot["record_count"], slot["path"]))
    affected = sorted({fid.split("#/", 1)[0] for fid in danger})
    acceptance_edges = index["normative_edges"]
    followups = []
    for rid in affected:
        row = next(r for r in result_rows if r["id"] == rid)
        linked = sorted({edge["case_id"] if edge["requirement_id"] == rid else edge["requirement_id"]
                         for edge in acceptance_edges
                         if edge["requirement_id"] == rid or edge["case_id"] == rid})
        followups.append({"row": rid,
                          "formerly_sufficient_fields": [f["id"] for f in row["fields"]
                                                        if f["currency"] == "pass_requires_reassessment"],
                          "linked_normative_cases_or_requirements": linked,
                          "required_current_evidence": ("Capture exact target-SHA execution of the original "
                                                       "assertions, verify source and oracle blobs, and add "
                                                       "a separate source-matching sufficiency review.")})
    return {"schema": "motionwright.source-currency-impact/1",
            "target_source_sha": target, "target_source_lock_sha256": lock,
            "declared_assessment_source": index["source"]["motionwright_sha"],
            "declared_assessment_lock": index["source"]["source_lock_sha256"],
            "ledger_source_matches_target": index["source"]["motionwright_sha"] == target and
            index["source"]["source_lock_sha256"] == lock,
            "counts": {"requirements": 272, "acceptance_cases": 124,
                       "original_criteria": len(index["criteria"]), "normative_links": len(index["normative_edges"]),
                       "evidence_records": len(evidence), "sufficiency_reviews": len(index["sufficiency_reviews"]),
                       "human_review_records": len(index["human_reviews"])},
            "record_currency_counts": record_counts, "criterion_currency_counts": dict(counts),
            "recorded_implementation_counts": dict(recorded_implementation),
            "recorded_integration_counts": dict(recorded_integration),
            "recorded_contract_acceptance_counts": dict(recorded_contract_status),
            "changed_source_path_hotspots": hotspots,
            "source_reassessment_followups": followups,
            "recorded_pass_criteria_requiring_reassessment": sorted(danger),
            "affected_canonical_rows": affected, "row_statuses": result_rows,
            "record_statuses": evidence,
            "currently_technical_complete_requirements": [row["id"] for row in result_rows
                        if row["current_technical_complete"] and row["id"].startswith(("CR-", "MW05-"))],
            "automatically_accepted_cases": [],
            "release_authorized": False,
            "scope_note": ("Observed file/definition identity and present source labels are not "
                           "independent proof that historical jobs ran on a changed checkout. "
                           "Human/installed/native acceptance and media quality require separate oracles.")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--target", default="HEAD", help="HEAD or exact full commit SHA")
    ap.add_argument("--json", type=Path, help="Write a JSON receipt; caller controls destination")
    ap.add_argument("--strict", action="store_true", help="Exit 2 when recorded current evidence is source-stale")
    args = ap.parse_args(argv)
    try:
        report = inspect(args.repo, args.target)
        serialized = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(serialized, encoding="utf-8")
        overview = {"source": report["target_source_sha"], "declared_assessment": report["declared_assessment_source"],
                    "counts": report["counts"], "source_matches": report["ledger_source_matches_target"],
                    "record_currency": report["record_currency_counts"], "fields": report["criterion_currency_counts"],
                    "historical_pass_risks": len(report["recorded_pass_criteria_requiring_reassessment"]),
                    "currently_technical_complete": len(report["currently_technical_complete_requirements"]),
                    "accepted_cases": 0, "release": False}
        print(json.dumps(overview, sort_keys=True))
        if args.strict and (not report["ledger_source_matches_target"] or
                            report["recorded_pass_criteria_requiring_reassessment"]):
            return 2
        return 0
    except (ValueError, KeyError, UnicodeDecodeError, OSError, subprocess.TimeoutExpired) as error:
        print("conformance source impact could not be trusted: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
