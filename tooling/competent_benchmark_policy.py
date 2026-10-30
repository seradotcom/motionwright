"""Validate a preregistered, complete, comparable 12-brief/3-arm benchmark.

This is an independent mechanical audit of *submitted* evidence, not an
executor, a paid competitor connector, a human/creative review, or a contract
PASS. The original private benchmark corpus and ten-change protocol are
caller-supplied and SHA-checked; no private source is hardcoded here.

A/B/C: competent direct native pipeline, Motionwright on the same engine,
and a task-capable competitor. A failed actual execution stays in the
dataset. Missing, biased or unexplained execution remains a named blocker.
"""
from __future__ import annotations
from collections import Counter
import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import sys
import uuid

CORPUS_SCHEMA = "motionwright.private-benchmark-manifest-append/1"
RESULT_SCHEMA = "motionwright.competent-comparison-executions/1"
REPORT_SCHEMA = "motionwright.competent-comparison-preflight/1"
ROLES = ("native_competent", "motionwright_same_engine", "capable_competitor")
IDS = tuple(f"{family}{i:02d}" for family in ("P", "D", "T")
            for i in range(1, 5))
CHANGES = tuple(f"E09-CHANGE-{i:02d}" for i in range(1, 11))
MAX_JSON_BYTES = 12 * 1024 * 1024
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024


class ComparisonError(ValueError):
    pass


def need(condition: bool, message: str) -> None:
    if not condition:
        raise ComparisonError(message)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canon(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf8")


def parse_unique(pairs):
    value = {}
    for key, item in pairs:
        need(key not in value, "duplicate JSON property")
        value[key] = item
    return value


def read_json(path: Path) -> tuple[dict, bytes]:
    need(path.is_file() and path.stat().st_size <= MAX_JSON_BYTES,
         "bounded JSON source absent")
    raw = path.read_bytes()
    try:
        result = json.loads(raw, object_pairs_hook=parse_unique,
                            parse_constant=lambda x: need(False, "nonfinite numeric value"))
    except (UnicodeError, TypeError, ValueError) as exc:
        raise ComparisonError("unparseable bounded input") from exc
    need(type(result) is dict, "JSON source must be an object")
    return result, raw


def source_corpus(original: dict) -> dict:
    need(original.get("schema") == CORPUS_SCHEMA,
         "wrong original private benchmark manifest")
    need(original.get("operation") == "APPEND_ONLY",
         "original append-only event changed")
    need(original["append_contract"]["materialization_status"]
         == "PREPARED_NOT_WRITTEN", "original event is not untouched")
    need(original["scope"]["requirement_id"] == "MW05-E09-04"
         and original["scope"]["acceptance_id"] == "A05-E09-04",
         "wrong original benchmark contract")
    need(original["catalogue_source"]["original_fixture_count"] == 12,
         "original 12-fixture roster changed")
    items = original.get("manifests")
    need(type(items) is list and len(items) == 12,
         "all twelve original benchmark fixtures required")
    tasks = {}
    for entry in items:
        code = entry.get("original_record", {}).get("id")
        need(code in IDS and code not in tasks,
             "missing, duplicate or changed original benchmark fixture")
        need(entry.get("manifest_id") == "MW05-E09-04:" + code,
             "original manifest fixture identity changed")
        need(entry.get("source_sha256") ==
             original["catalogue_source"]["sha256"],
             "source fixture no longer binds original authored catalog")
        need(entry.get("results") is None and
             entry.get("execution_status") == "NOT_RUN",
             "original proposal falsely rewritten with executions")
        record = entry["original_record"]
        need(type(record.get("brief")) is str and record["brief"].strip(),
             "original benchmark task brief missing")
        tasks[code] = {
            "id": code, "manifest_id": entry["manifest_id"],
            "family": record["family"], "original_sha256": digest(canon(record))
        }
    need(set(tasks) == set(IDS), "original 12 benchmark tasks differ")
    family_counts = Counter(item["family"] for item in tasks.values())
    need(sorted(family_counts.values()) == [4, 4, 4],
         "original benchmark family coverage changed")
    changes = original["shared_protocol"]["ten_changes"]
    need(type(changes) is list and len(changes) == 10
         and tuple(x["id"] for x in changes) == CHANGES,
         "original ten revision operations not preserved")
    need(all(type(x["original_text"]) is str and x["original_text"].strip()
             for x in changes), "original revision not substantive")
    need(original["shared_protocol"]["application"]["budget_status"]
         == "DEFINE_AND_FREEZE_BEFORE_COMPARISON",
         "original budget freeze must still be distinguished from executions")
    need(len(original["legacy_manifests"]) == 3,
         "inherited original v0.1 performance/acceptance cases lost")
    return {"tasks":tasks, "change_ids":list(CHANGES),
            "legacy_count":len(original["legacy_manifests"]),
            "families":dict(family_counts)}


def timestamp(value: str) -> dt.datetime:
    need(type(value) is str and 5 < len(value) <= 45,
         "timestamp must be bounded and explicit")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise ComparisonError("invalid actual zoned timestamp") from e
    need(parsed.utcoffset() is not None, "zoned UTC timestamp required")
    return parsed.astimezone(dt.timezone.utc)


def artifact(root: Path, record: dict, *, allow_empty: bool = False) -> dict:
    need(type(record) is dict and set(record) == {"path", "bytes", "sha256"},
         "original artifact path/bytes/hash identity required")
    relative = record["path"]
    need(type(relative) is str and relative and "\\" not in relative and
         not PureWindowsPath(relative).drive and ":" not in relative,
         "unsafe artifact pointer")
    p = Path(relative)
    need(not p.is_absolute() and ".." not in p.parts,
         "artifact pointer escapes controlled root")
    parent = root.resolve(strict=True)
    for part in p.parts:
        parent = parent / part
        try:
            st = parent.lstat()
        except OSError as e:
            raise ComparisonError("unavailable original benchmark artifact") from e
        need(not parent.is_symlink() and
             not getattr(st, "st_file_attributes", 0) & 0x400,
             "linked/reparse benchmark artifact forbidden")
    need(parent.is_file() and type(record["bytes"]) is int and
         (0 if allow_empty else 1) <= record["bytes"] <= MAX_ARTIFACT_BYTES and
         parent.stat().st_size == record["bytes"],
         "original artifact type/bytes mismatch")
    expected = record["sha256"]
    need(type(expected) is str and len(expected) == 64
         and all(c in "0123456789abcdef" for c in expected),
         "artifact SHA-256 invalid")
    value = hashlib.sha256()
    with parent.open("rb") as fd:
        while part := fd.read(1024 * 1024):
            value.update(part)
    need(value.hexdigest() == expected,
         "physical benchmark original artifact bytes changed")
    return {"bytes":record["bytes"], "sha256":expected}


# These adapters check retained declarations and their physical correspondence.
# They do not authenticate a producer, a participant, or a creative decision.
REVISION_BINDING_SCHEMA = "motionwright.competent-revision-binding/1"
LOCK_OBSERVATION_SCHEMA = "motionwright.competent-lock-observation/1"
LOCK_SNAPSHOT_SCHEMA = "motionwright.competent-lock-snapshot/1"
REVIEW_PROTOCOL_SCHEMA = "motionwright.competent-blind-review-protocol/1"
REVIEW_RECORD_SCHEMA = "motionwright.competent-blind-review-record/1"
OWNER_DECISION_SCHEMA = "motionwright.competent-owner-decision/1"
OPERATION_KEYS = {"project_id", "generation", "revision", "request_id",
                  "request_sha256", "command"}


def bounded_text(value, label, limit=4096):
    need(type(value) is str and 0 < len(value) <= limit
         and all(ord(c) >= 32 for c in value), label)
    return value


def closed_record(value, fields, label):
    need(type(value) is dict and set(value) == set(fields), label)
    return value


def binding_json(root, descriptor):
    artifact(root, descriptor)
    need(descriptor["bytes"] <= MAX_JSON_BYTES, "bounded binding JSON required")
    value, raw = read_json(root / descriptor["path"])
    need(len(raw) == descriptor["bytes"]
         and digest(raw) == descriptor["sha256"], "binding bytes changed while read")
    artifact(root, descriptor)
    return value


def operation_identity(value):
    closed_record(value, OPERATION_KEYS, "immutable operation identity required")
    for key in ("project_id", "generation"):
        try:
            valid = type(value[key]) is str and str(uuid.UUID(value[key])) == value[key]
        except (ValueError, AttributeError):
            valid = False
        need(valid, "operation project/generation UUID required")
    need(type(value["revision"]) is int and 0 <= value["revision"] < 2**63,
         "operation revision outside stored receipt range")
    bounded_text(value["request_id"], "original request ID required", 256)
    bounded_text(value["command"], "original command required", 256)
    h = value["request_sha256"]
    need(type(h) is str and len(h) == 64
         and all(c in "0123456789abcdef" for c in h), "original request hash required")
    return value


def revision_context(submitted, task, role, attempt, change):
    return {"original_manifest_sha256": submitted["original_manifest_sha256"],
            "candidate_source_sha": submitted["candidate_source_sha"],
            "source_lock_sha256": submitted["source_lock_sha256"],
            "task_id": task, "arm_role": role, "attempt_id": attempt,
            "change_id": change}


def validate_revision_binding(root, revision, context):
    """F79-01: relate an immutable receipt to this exact submitted revision."""
    if revision["status"] == "not_run":
        need(not any(k in revision for k in
                     ("binding", "lock_observation", "editable_source", "render")),
             "unattempted revision has fictional binding or observation")
        return {"state": "NOT_RUN", "reason": revision["reason"]}
    binding = revision.get("binding")
    if binding is None:
        return {"state": "HOLD_REVISION_BINDING_NOT_SUBMITTED"}
    closed_record(binding, ("operation", "correspondence"),
                  "revision binding operation and correspondence required")
    operation = operation_identity(binding["operation"])
    receipt = binding_json(root, revision["receipt"])
    closed_record(receipt, OPERATION_KEYS | {"id", "stage", "payload", "created_at"},
                  "retained ProductionReceipt shape differs")
    need(operation_identity({k: receipt[k] for k in OPERATION_KEYS}) == operation,
         "ProductionReceipt belongs to another immutable request or source revision")
    bounded_text(receipt["id"], "original receipt ID required", 64)
    bounded_text(receipt["stage"], "original receipt stage required", 64)
    timestamp(receipt["created_at"])
    relation = binding_json(root, binding["correspondence"])
    closed_record(relation, ("schema", "context", "operation", "production_receipt_sha256",
                             "status", "editable_source", "render", "reason"),
                  "closed revision correspondence required")
    need(relation["schema"] == REVISION_BINDING_SCHEMA
         and relation["context"] == context and relation["operation"] == operation,
         "revision correspondence transplanted from another revision or generation")
    need(relation["production_receipt_sha256"] == revision["receipt"]["sha256"],
         "revision correspondence uses another production receipt")
    need(relation["status"] == revision["status"], "revision outcome was relabeled")
    if revision["status"] != "pass":
        bounded_text(relation["reason"], "original failed/unknown revision reason required")
    else:
        need(relation["reason"] is None, "successful revision cannot hide an error reason")
    stamp = {k: operation[k] for k in ("project_id", "generation", "revision")}
    pair = {}
    for label in ("editable_source", "render"):
        observed = relation[label]
        if observed is None:
            need(revision["status"] != "pass", "successful revision lacks editable/render pair")
            pair[label] = None
            continue
        fields = {"artifact", "source_stamp"}
        if label == "render":
            fields.add("editable_source_sha256")
        closed_record(observed, fields, "source/render observation shape differs")
        need(observed["source_stamp"] == stamp,
             "editable/render source stamp belongs to another revision")
        artifact(root, observed["artifact"])
        pair[label] = observed["artifact"]
    if pair["render"] is not None:
        need(pair["editable_source"] is not None
             and relation["render"]["editable_source_sha256"]
             == pair["editable_source"]["sha256"],
             "render is not associated with this editable source")
    # Identical source hashes can be valid for an actual no-op. Identity and
    # retained operation semantics, not a universal changed-hash rule, bind it.
    return {"state": "BOUND_DECLARATIONS_AND_BYTES_CHECKED",
            "operation": operation, "status": relation["status"],
            "pair": pair, "reason": relation["reason"],
            "production_receipt_sha256": revision["receipt"]["sha256"],
            "producer_authentication": "NOT_PERFORMED"}


def validate_lock_observation(root, revision, context, binding):
    """F79-02: retain applicable lock outcomes without deriving them from loss."""
    if revision["status"] == "not_run":
        return {"state": "NOT_RUN", "reason": revision["reason"]}
    descriptor = revision.get("lock_observation")
    if descriptor is None:
        return {"state": "HOLD_LOCK_OBSERVATION_NOT_SUBMITTED"}
    need("operation" in binding, "lock observation needs the bound operation")
    value = binding_json(root, descriptor)
    closed_record(value, ("schema", "context", "operation", "outcome", "reason",
                          "lock", "before", "after", "rejection"),
                  "closed per-revision lock observation required")
    need(value["schema"] == LOCK_OBSERVATION_SCHEMA
         and value["context"] == context and value["operation"] == binding["operation"],
         "lock observation belongs to another revision or operation")
    outcome = value["outcome"]
    need(outcome in ("preserved", "violation", "refused", "unknown", "not_applicable"),
         "lock observation cannot invent verified authority")
    bounded_text(value["reason"], "lock observation reason required")
    if outcome == "not_applicable":
        need(all(value[k] is None for k in ("lock", "before", "after", "rejection")),
             "not-applicable lock cannot contain a fabricated applicable scope")
    else:
        lock = value["lock"]
        if lock is not None:
            closed_record(lock, ("id", "resource", "kind", "note"),
                          "actual scoped ProjectLock fields required")
            bounded_text(lock["id"], "retained lock identity required", 64)
            bounded_text(lock["resource"], "retained lock resource required")
            need(lock["kind"] in ("content", "timing", "position", "style", "renderer"),
                 "retained lock kind differs")
            need(type(lock["note"]) is str, "retained lock note required")
        need(outcome == "unknown" or lock is not None,
             "applicable outcome requires an actual scoped lock")
        snapshots = {}
        for label in ("before", "after"):
            ref = value[label]
            if ref is None:
                snapshots[label] = None
                continue
            snap = binding_json(root, ref)
            closed_record(snap, ("schema", "context", "operation", "lock",
                                 "protected_values"), "closed lock snapshot required")
            need(snap["schema"] == LOCK_SNAPSHOT_SCHEMA
                 and snap["context"] == context and snap["operation"] == binding["operation"]
                 and snap["lock"] == lock and lock is not None,
                 "lock snapshot belongs to another source or scope")
            values = snap["protected_values"]
            need(type(values) is dict and 0 < len(values) <= 256
                 and all(type(k) is str and 0 < len(k) <= 256 for k in values),
                 "declared protected-field snapshot required")
            snapshots[label] = values
        if outcome in ("preserved", "violation"):
            before, after = snapshots["before"], snapshots["after"]
            need(before is not None and after is not None and set(before) == set(after),
                 "lock preservation needs corresponding before/after fields")
            equal = canon(before) == canon(after)
            need(equal == (outcome == "preserved"),
                 "claimed lock outcome contradicts retained protected fields")
            need(value["rejection"] is None, "applied lock outcome hides a refusal")
        elif outcome == "refused":
            need(value["rejection"] is not None, "refusal requires original rejection receipt")
        if value["rejection"] is not None:
            refusal = binding_json(root, value["rejection"])
            closed_record(refusal, ("context", "operation", "lock", "code", "reason"),
                          "closed source-bound refusal receipt required")
            need(refusal["context"] == context and refusal["operation"] == binding["operation"]
                 and refusal["lock"] == lock and lock is not None,
                 "refusal belongs to another source or lock")
            bounded_text(refusal["code"], "original refusal code required", 256)
            bounded_text(refusal["reason"], "original refusal reason required")
    return {"state": outcome.upper(), "reason": value["reason"],
            "lock": value["lock"], "observation_sha256": descriptor["sha256"],
            "scope_completeness_and_actor_authentication": "NOT_PERFORMED",
            "not_derived_from_changes_lost": True}


def validate_benchmark_review(root, submitted, reviewable):
    """F79-03: validate a declared protocol; never create a human verdict."""
    inputs = submitted.get("independent_review")
    if inputs is None:
        return {"state": "HOLD_INDEPENDENT_REVIEW_NOT_SUBMITTED",
                "owner_decision": "PENDING_EXTERNAL_INPUT"}
    closed_record(inputs, ("protocol", "record"), "retained review protocol and record required")
    protocol = binding_json(root, inputs["protocol"])
    context = {k: submitted[k] for k in
               ("original_manifest_sha256", "candidate_source_sha", "source_lock_sha256")}
    closed_record(protocol, ("schema", "context", "blind_required", "rubric", "targets"),
                  "closed benchmark review protocol required")
    need(protocol["schema"] == REVIEW_PROTOCOL_SCHEMA and protocol["context"] == context,
         "review protocol belongs to another benchmark or source")
    need(protocol["blind_required"] is True, "selected blind protocol cannot waive blinding")
    rubric = protocol["rubric"]
    need(type(rubric) is list and 0 < len(rubric) <= 64, "bounded declared rubric required")
    categories = {}
    for row in rubric:
        closed_record(row, ("id", "minimum", "maximum"), "declared rubric bounds required")
        key = bounded_text(row["id"], "rubric category ID required", 256)
        need(key not in categories and all(type(row[k]) in (int, float)
             and math.isfinite(row[k]) for k in ("minimum", "maximum"))
             and row["minimum"] < row["maximum"], "rubric bounds or identity differ")
        categories[key] = (row["minimum"], row["maximum"])
    targets = protocol["targets"]
    need(type(targets) is list and len(targets) <= 4096, "bounded review targets required")
    available = {(v["task_id"], v["arm_role"], v["attempt_id"]): v for v in reviewable}
    blind_ids, covered, operators, finishes = set(), set(), set(), []
    for target in targets:
        closed_record(target, ("blind_id", "task_id", "arm_role", "attempt_id",
                               "artifact", "operator_id"), "review target binding required")
        bid = bounded_text(target["blind_id"], "blind target ID required", 256)
        key = (target["task_id"], target["arm_role"], target["attempt_id"])
        need(bid not in blind_ids and key not in covered and key in available,
             "review target arm/attempt identity differs")
        need(target["artifact"] == available[key]["artifact"],
             "review target artifact belongs to another arm or attempt")
        artifact(root, target["artifact"])
        operators.add(bounded_text(target["operator_id"], "actual declared operator required", 256))
        blind_ids.add(bid)
        covered.add(key)
        finishes.append(timestamp(available[key]["finished_at_utc"]))
    record = binding_json(root, inputs["record"])
    closed_record(record, ("schema", "context", "protocol_sha256", "state",
                           "reviews", "owner_decision"), "closed retained review record required")
    need(record["schema"] == REVIEW_RECORD_SCHEMA and record["context"] == context
         and record["protocol_sha256"] == inputs["protocol"]["sha256"],
         "review record belongs to another protocol or benchmark")
    state = record["state"]
    need(state in ("not_run", "pending", "completed", "declined"), "explicit review state required")
    reviews = record["reviews"]
    need(type(reviews) is list and len(reviews) <= 64, "bounded review records required")
    need(state != "completed" or (bool(reviews) and bool(targets)),
         "completed review lacks actual declared reviewers or targets")
    need(not reviews or bool(targets), "review observations lack actual targets")
    need(state != "not_run" or not reviews, "not-run review has fictional observations")
    seen = set()
    for review in reviews:
        closed_record(review, ("reviewer_id", "independent", "blind_order_attested",
                               "reviewed_at_utc", "scores", "limitations"),
                      "closed actual review row required")
        reviewer = bounded_text(review["reviewer_id"], "declared reviewer identity required", 256)
        need(reviewer not in seen and reviewer not in operators and review["independent"] is True,
             "duplicate or non-independent review cannot satisfy protocol")
        need(review["blind_order_attested"] is True,
             "unblinded declaration cannot satisfy selected blind protocol")
        need(timestamp(review["reviewed_at_utc"]) >= max(finishes),
             "review predates its retained target execution")
        scores = review["scores"]
        need(type(scores) is dict and set(scores) == blind_ids, "review score target coverage differs")
        for values in scores.values():
            need(type(values) is dict and set(values) == set(categories),
                 "review rubric coverage differs")
            for key, score in values.items():
                lo, hi = categories[key]
                need(type(score) in (int, float) and math.isfinite(score) and lo <= score <= hi,
                     "review score outside declared rubric")
        need(type(review["limitations"]) is list and len(review["limitations"]) <= 256
             and all(type(v) is str and 0 < len(v) <= 4096 for v in review["limitations"]),
             "review limitations must remain explicit")
        seen.add(reviewer)
    owner = closed_record(record["owner_decision"], ("state", "receipt"),
                          "external owner decision state required")
    need(owner["state"] in ("pending", "declined", "needs_changes", "accepted"),
         "owner decision state differs")
    if owner["state"] == "pending":
        need(owner["receipt"] is None, "pending owner decision cannot invent a decision receipt")
    else:
        decision = binding_json(root, owner["receipt"])
        closed_record(decision, ("schema", "context", "protocol_sha256", "targets_sha256",
                                 "state", "owner_id", "decided_at_utc", "rationale"),
                      "retained external owner decision required")
        need(decision["schema"] == OWNER_DECISION_SCHEMA and decision["context"] == context
             and decision["protocol_sha256"] == inputs["protocol"]["sha256"]
             and decision["targets_sha256"] == digest(canon(targets))
             and decision["state"] == owner["state"], "owner decision binding differs")
        bounded_text(decision["owner_id"], "declared external owner identity required", 256)
        bounded_text(decision["rationale"], "external owner rationale required")
        timestamp(decision["decided_at_utc"])
    need(state != "not_run" or owner["state"] == "pending",
         "not-run review cannot carry a fictional owner decision")
    need(owner["state"] != "accepted"
         or (state == "completed" and covered == set(available) and bool(available)),
         "owner acceptance declaration lacks the completed selected comparison")
    # Cardinalities follow the retained protocol, not an old universal
    # eight-category/two-reviewer quota. Unfavorable scores remain unchanged.
    return {"state": state.upper(), "protocol_sha256": inputs["protocol"]["sha256"],
            "reviewed_artifact_targets": len(covered),
            "unreviewed_successful_artifact_targets": len(set(available) - covered),
            "reviews": reviews, "owner_decision": owner["state"],
            "participant_and_owner_authentication": "NOT_PERFORMED",
            "creative_acceptance": False}


def positive_budget(value: dict) -> None:
    need(type(value) is dict and set(value) == {
        "max_wall_seconds", "max_known_cost_usd", "max_human_active_seconds"},
         "all three shared budget limits must be declared")
    need(all(type(x) in (int, float) and math.isfinite(x) and x > 0
             for x in value.values()), "benchmark budget must be finite positive")


def examine(original: dict, original_sha: str,
            submitted: dict | None, evidence_root: Path | None = None) -> dict:
    if not (type(original_sha) is str and len(original_sha) == 64
            and all(c in "0123456789abcdef" for c in original_sha)):
        raise ComparisonError("pretrusted original SHA-256 required")
    corpus = source_corpus(original)
    result = {"schema": REPORT_SCHEMA,
        "original_event_sha256": original_sha,
        "benchmark_contract_ids": ["MW05-E09-04", "A05-E09-04"],
        "legacy_related_acceptance_cases_not_automatically_satisfied":
            ["ACC-047", "ACC-048"],
        "total_original_tasks": len(corpus["tasks"]),
        "original_task_families": corpus["families"],
        "required_arms": list(ROLES),
        "revisions_per_task": len(CHANGES),
        "original_v01_benchmark_protocols_preserved": corpus["legacy_count"],
        "task_execution": [], "actual_arms_observed": 0,
        "revision_reconciliation": [],
        "independent_review_binding": {"state": "NOT_RUN",
                                       "owner_decision": "PENDING_EXTERNAL_INPUT"},
        "functional_reconciliation_credit": False,
        "actual_attempts_observed": 0,
        "actual_original_streams_checked": 0,
        "actual_artifact_references_checked": 0,
        "artifact_reference_count_semantics": (
            "Validated declared artifact references, including repeated references; "
            "not unique files or independent executions."),
        "unattempted_original_revisions": 0,
        "unknown_cost_attempts": 0,
        "user_creative_quality_independently_reviewed": False,
        "comparator_competence_independently_reviewed": False,
        "source_rights_issuer_authentication": "NOT_PERFORMED",
        "source_run_execution_authentication": "NOT_PERFORMED",
        "new_product_contract_PASS": 0,
        "release_authorized": False}
    if submitted is None:
        for id in IDS:
            result["task_execution"].append({
                "id": id, "state": "NOT_RUN",
                "missing": ["frozen original inputs/rights/budget",
                            "competent native A, same-engine Motionwright B",
                            "comparable competitor C, ten actual revisions",
                            "real per-arm original streams/outputs",
                            "independent quality and comparator review"]})
        result["status"] = "BLOCKED_ORIGINAL_TWELVE_NOT_EXECUTED"
        return result

    need(submitted.get("schema") == RESULT_SCHEMA,
         "not an admitted comparison experiment schema")
    need(submitted.get("original_manifest_sha256") == original_sha,
         "comparison attempted to substitute a different original benchmark")
    need(type(submitted.get("candidate_source_sha")) is str and
         len(submitted["candidate_source_sha"]) == 40 and
         all(c in "0123456789abcdef" for c in submitted["candidate_source_sha"]),
         "actual product source pin missing")
    need(type(submitted.get("source_lock_sha256")) is str and
         len(submitted["source_lock_sha256"]) == 64 and
         all(c in "0123456789abcdef" for c in submitted["source_lock_sha256"]),
         "actual product source lock pin missing")
    preregistered = timestamp(submitted.get("preregistered_at_utc"))
    need(evidence_root is not None, "actual original evidence root required")
    tasks = submitted.get("tasks")
    need(type(tasks) is list and len(tasks) == 12,
         "all twelve original tasks must remain visible, even failed")
    arm_count = 0
    attempt_count = 0
    stream_count = 0
    artifact_count = 0
    reviewable = []
    for i, task in enumerate(tasks):
        code = IDS[i]
        need(type(task) is dict and task.get("id") == code,
             "benchmark task order or ID differs from original")
        original_record = corpus["tasks"][code]
        need(task.get("original_record_sha256") == original_record["original_sha256"],
             "task source brief changed")
        need(task.get("change_ids") == list(CHANGES),
             "all ten original changes and their order are required")
        need(type(task.get("generative")) is bool,
             "original generative condition must be declared per task")
        features = task.get("necessary_features")
        need(type(features) is list and features and
             all(type(f) is str and f.strip() for f in features)
             and len(set(features)) == len(features),
             "competent comparison needs exact feature list")
        positive_budget(task.get("budget"))
        rights = task.get("source_rights")
        need(type(rights) is dict and rights.get("status") == "authorized",
             "source rights not positively admitted")
        rights_receipt = artifact(evidence_root, rights.get("receipt"))
        artifact_count += 1
        sources = task.get("input_assets")
        need(type(sources) is list and sources,
             "original source inputs absent")
        for source in sources:
            artifact(evidence_root, source)
            artifact_count += 1
        arms = task.get("arms")
        need(type(arms) is list and len(arms) == 3,
             "three genuine competent comparison arms needed")
        engines = []
        trials = []
        for j, arm in enumerate(arms):
            role = ROLES[j]
            need(type(arm) is dict and arm.get("role") == role,
                 "baseline A/B/C role missing, reordered or duplicated")
            for key in ("engine", "engine_version", "model_configuration",
                        "operator_experience", "instructions_original_sha256"):
                value = arm.get(key)
                need(type(value) is str and bool(value.strip()),
                     "comparator original version, competence or instructions absent")
            need(len(arm["instructions_original_sha256"]) == 64,
                 "comparator authored instruction digest invalid")
            instructions = artifact(evidence_root, arm.get("instructions_artifact"))
            need(instructions["sha256"] == arm["instructions_original_sha256"],
                 "benchmark instruction digest is not the actual retained prompt")
            artifact(evidence_root, arm.get("configuration_artifact"))
            artifact_count += 2
            capabilities = arm.get("available_features")
            need(type(capabilities) is list and
                 all(type(f) is str for f in capabilities) and
                 len(set(capabilities)) == len(capabilities),
                 "competitor capability list invalid")
            if set(features).difference(capabilities):
                raise ComparisonError(
                    "incapable comparator for required features; disclose missing "
                    "capabilities, do not claim a fair comparison")
            engines.append((arm["engine"], arm["engine_version"]))
            attempts = arm.get("attempts")
            need(type(attempts) is list and attempts and
                 (not task["generative"] or len(attempts) >= 2),
                 "generative task needs multiple original runs, including failures")
            found = set()
            arm_count += 1
            for attempt in attempts:
                need(type(attempt) is dict, "actual attempt must be an object")
                aid = attempt.get("id")
                need(type(aid) is str and aid and aid not in found,
                     "actual attempt has absent or duplicate ID")
                found.add(aid)
                start = timestamp(attempt.get("started_at_utc"))
                stop = timestamp(attempt.get("finished_at_utc"))
                need(preregistered <= start <= stop,
                     "benchmark budget or task was not frozen before execution")
                state = attempt.get("status")
                need(state in ("pass", "fail", "unknown"),
                     "attempt status may not silently drop failures or UNKNOWN")
                for label in ("stdout", "stderr", "terminal"):
                    is_stream = label in ("stdout", "stderr")
                    artifact(evidence_root, attempt.get(label), allow_empty=is_stream)
                    artifact_count += 1
                    stream_count += int(is_stream)
                observed = attempt.get("revisions")
                need(type(observed) is list and len(observed) == 10
                     and [o["id"] for o in observed] == list(CHANGES),
                     "all ten original revisions must be attempted and reported")
                observed_done = []
                for rev in observed:
                    rev_status = rev.get("status")
                    need(rev_status in ("pass", "fail", "unknown", "not_run"),
                         "revision outcome must be an explicit result or not_run")
                    if rev_status == "not_run":
                        need(state != "pass" and
                             type(rev.get("reason")) is str and rev["reason"].strip() and
                             "receipt" not in rev,
                             "unattempted revision requires a reason and no fictional receipt")
                        result["unattempted_original_revisions"] += 1
                    else:
                        artifact(evidence_root, rev.get("receipt"))
                        artifact_count += 1
                    context = revision_context(submitted, code, role, aid, rev["id"])
                    bound = validate_revision_binding(evidence_root, rev, context)
                    lock = validate_lock_observation(evidence_root, rev, context, bound)
                    result["revision_reconciliation"].append({
                        "context": context, "editable_render": bound, "lock": lock})
                    observed_done.append(rev_status)
                need(state != "pass" or all(r == "pass" for r in observed_done),
                     "a completed successful task cannot hide failed or missing revisions")
                metrics = attempt.get("metrics")
                need(type(metrics) is dict and set(metrics) == {
                    "wall_seconds", "known_cost_usd", "human_active_seconds",
                    "changes_rebuilt", "changes_lost"},
                     "comparable measured metrics required")
                need(type(metrics["wall_seconds"]) in (int, float) and
                     math.isfinite(metrics["wall_seconds"]) and metrics["wall_seconds"] >= 0 and
                     (metrics["known_cost_usd"] is None or
                      (type(metrics["known_cost_usd"]) in (int, float) and
                       math.isfinite(metrics["known_cost_usd"]) and metrics["known_cost_usd"] >= 0)) and
                     type(metrics["human_active_seconds"]) in (int, float) and
                     math.isfinite(metrics["human_active_seconds"]) and metrics["human_active_seconds"] >= 0 and
                     type(metrics["changes_rebuilt"]) is int and metrics["changes_rebuilt"] >= 0 and
                     type(metrics["changes_lost"]) is int and metrics["changes_lost"] >= 0,
                     "measured budget/cost/loss invalid")
                if metrics["known_cost_usd"] is None:
                    result["unknown_cost_attempts"] += 1
                budget_exceeded = (
                    metrics["wall_seconds"] > task["budget"]["max_wall_seconds"] or
                    (metrics["known_cost_usd"] is not None and
                     metrics["known_cost_usd"] > task["budget"]["max_known_cost_usd"]) or
                    metrics["human_active_seconds"] > task["budget"]["max_human_active_seconds"])
                need(not budget_exceeded or state != "pass",
                     "an over-budget attempt cannot be silently reported as PASS")
                if state == "pass":
                    artifact(evidence_root, attempt.get("editable_source"))
                    artifact(evidence_root, attempt.get("master"))
                    artifact_count += 2
                    reviewable.append({"task_id": code, "arm_role": role,
                                       "attempt_id": aid, "artifact": attempt["master"],
                                       "finished_at_utc": attempt["finished_at_utc"]})
                elif not (type(attempt.get("failure_or_unknown_reason")) is str
                          and attempt["failure_or_unknown_reason"].strip()):
                    raise ComparisonError(
                        "failed or uncertain attempt lacks original causal limitation")
                trials.append({"role": role, "status": state,
                               "reported_revision_cells": len(observed),
                               "completed_revisions": sum(r == "pass" for r in observed_done),
                               "unattempted_revisions": observed_done.count("not_run"),
                               "cost_usd": metrics["known_cost_usd"],
                               "wall_seconds": metrics["wall_seconds"],
                               "changes_lost": metrics["changes_lost"],
                               "over_budget": budget_exceeded})
                attempt_count += 1
        need(engines[0] == engines[1],
             "baseline A and Motionwright B must share the exact native engine/version")
        result["task_execution"].append({"id": code,
            "state": "ORIGINAL_ATTEMPTS_PRESENT_REVIEW_REQUIRED",
            "original_rights_receipt_sha256": rights_receipt["sha256"],
            "attempts": trials,
            "human_fairness_review": "NOT_AUTHENTICATED",
            "product_criterion_PASS": False})
    result["independent_review_binding"] = validate_benchmark_review(
        evidence_root, submitted, reviewable)
    result["revision_binding_states"] = dict(Counter(
        row["editable_render"]["state"] for row in result["revision_reconciliation"]))
    result["lock_observation_states"] = dict(Counter(
        row["lock"]["state"] for row in result["revision_reconciliation"]))
    # Existing structural status describes the base experiment shape only.
    # Missing bindings remain named HOLDs; none authenticates an execution,
    # a protected-field scope, a participant, or an owner decision.
    result["binding_reference_counts_are_separate"] = True
    result["actual_arms_observed"] = arm_count
    result["actual_attempts_observed"] = attempt_count
    result["actual_original_streams_checked"] = stream_count
    result["actual_artifact_references_checked"] = artifact_count
    result["status"] = (
        "STRUCTURAL_DATA_WITH_INCOMPLETE_REVISIONS_OR_UNKNOWN_COST"
        if result["unattempted_original_revisions"] or result["unknown_cost_attempts"]
        else "STRUCTURALLY_COMPLETE_FOR_INDEPENDENT_COMPARISON_REVIEW")
    # Even complete-looking physical files may be fabricated by the operator.
    # A human or independent evaluator must actually witness quality, budgets,
    # experiment comparability and subjective usability. Never auto-accept.
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--original-sha256", required=True)
    parser.add_argument("--submitted", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        data, raw = read_json(args.original)
        need(digest(raw) == args.original_sha256,
             "out-of-band original corpus digest mismatch")
        results = read_json(args.submitted)[0] if args.submitted else None
        outcome = examine(data, args.original_sha256, results, args.evidence_root)
        with args.output.open("x", encoding="utf8") as stream:
            json.dump(outcome, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
        print("competent-comparison-preflight=" + outcome["status"] +
              " original_tasks=" + str(outcome["total_original_tasks"]) +
              " original_arms_observed=" + str(outcome["actual_arms_observed"]))
        return 0
    except (ComparisonError, OSError, ValueError, TypeError, KeyError) as error:
        # Public source-control output carries no private briefs, paths or
        # operator names. The original diagnostic remains private.
        print("competent-comparison-preflight=failed code=" +
              type(error).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
