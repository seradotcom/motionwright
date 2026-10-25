#!/usr/bin/env python3
"""Read-only verification of a retained PR routing review, not product acceptance.

Only --output is written. Original catalogue text, PR prose and private paths are
excluded from the report. Clause references resolve against the exact retained
technical-evidence index; differing clause digest conventions are not conflated.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from collections import Counter, defaultdict

LEDGERS = (
    "docs/acceptance/acceptance-status.json",
    "docs/acceptance/requirements-status.json",
    "docs/acceptance/technical-evidence.json",
    "docs/creative-production/requirements-status.json",
)
RECORD = re.compile(r"(?:CR-[A-Z]+-\d+|MW05-E\d{2}-\d{2}|ACC-\d{3}|A05-E\d{2}-\d{2})")
CLAIM = re.compile(r"\b(?:CR-[A-Z]+-\d+|MW05-E\d{2}-\d{2}|ACC-\d{3}|A05-E\d{2}-\d{2})\b")
SHORT_CLAIM = re.compile(r"(?<![A-Za-z0-9-])E(\d{2})-(\d{2})(?:\.\.(\d{2}))?\b")
OWNERS = {"A1": "integration", "A2": "foundation", "A3": "studio",
          "A4": "native", "A5": "conformance", "A6": "verification"}
ROLE_ACTIONS = {
    "existing_conformance_record_surface": "verify_ids_history_and_evidence_references",
    "shared_dependency_or_module_export": "verify_transitive_consumers_and_locked_inputs",
    "historical_source_bound_evidence_package": "verify_original_artifacts_and_source_applicability",
    "ci_workflow_definition": "verify_exact_run_job_attempt_and_required_jobs",
    "test_definition_not_execution": "verify_assertions_and_same_source_execution_results",
    "developer_fixture_or_actor": "verify_fixture_custody_and_authorized_execution_route",
    "verifier_or_bounded_execution_harness": "verify_observer_controls_bounds_and_originals",
    "audit_or_claim_documentation": "verify_claims_against_original_source_bound_evidence",
    "product_contract_or_scope_documentation": "verify_documented_scope_against_implementation",
    "studio_ui_or_source_contract": "verify_real_ui_service_sqlite_and_negative_paths",
    "desktop_native_boundary": "verify_trusted_context_input_bounds_and_effects",
    "creative_typed_domain_or_plan": "verify_canonical_edits_revision_and_declared_limits",
    "native_driver_or_capture_runtime": "verify_authorized_native_output_and_terminal_outcomes",
    "native_production_or_admission": "verify_source_admission_and_independent_decoded_output",
    "authoritative_service_or_storage": "verify_transactions_cas_failures_and_reopen",
    "typed_product_domain": "verify_invariants_and_affected_real_consumers",
}


class Invalid(ValueError):
    pass


def need(condition, code):
    if not condition:
        raise Invalid(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def bad_constant(_):
        raise Invalid("NONFINITE_JSON_NUMBER")
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs,
                      parse_constant=bad_constant)


def keyed(items, key, code):
    need(isinstance(items, list), code)
    result = {}
    for item in items:
        need(isinstance(item, dict) and key in item, code)
        need(item[key] not in result, code)
        result[item[key]] = item
    return result


def unique(items, code):
    need(isinstance(items, list), code)
    need(len(items) == len(set(items)), code)
    return set(items)


def digest(value, size=64):
    need(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{" + str(size) + r"}", value),
         "INVALID_DIGEST")


def repo_path(value):
    need(isinstance(value, str) and value and "\\" not in value and ":" not in value,
         "INVALID_REPOSITORY_PATH")
    p = PurePosixPath(value)
    need(not p.is_absolute() and ".." not in p.parts and str(p) == value,
         "INVALID_REPOSITORY_PATH")


def owner_domains(value):
    need(isinstance(value, str) and value, "OWNER_MISSING")
    parts = value.split("/")
    need(all(x in OWNERS for x in parts), "OWNER_UNRESOLVED")
    return sorted({OWNERS[x] for x in parts})


def verify_metadata(snapshot, review, snapshot_raw, ledger_hashes, criteria, index_source):
    """Pure validation; callers supply already hashed, read-only ledger bytes."""
    need(isinstance(criteria, dict) and criteria, "CRITERIA_MAP_MISSING")
    record_ids = {fid.split("#/", 1)[0] for fid in criteria if "#/" in fid}
    need(all(RECORD.fullmatch(x) for x in record_ids), "LEDGER_RECORD_ID_INVALID")
    for value in criteria.values():
        digest(value)
    prs = keyed(snapshot["pull_requests"], "number", "PR_ID_DUPLICATE_OR_MISSING")
    summary = snapshot["snapshot_summary"]
    inp = review["input_snapshot"]
    rs = review["summary"]
    need(set(ledger_hashes) == set(LEDGERS), "LEDGER_SET_MISMATCH")
    need(ledger_hashes == summary["public_ledger_sha256"] == inp["ledger_sha256_references"],
         "LEDGER_HASH_MISMATCH")
    need(inp["sha256"] == sha(snapshot_raw) and inp["bytes"] == len(snapshot_raw),
         "SNAPSHOT_HASH_MISMATCH")
    need(inp["original_reconciliation_sha256"] == summary["reconciliation_sha256"],
         "RECONCILIATION_HASH_MISMATCH")
    need(inp["target_ledger_source"] == summary["target_ledger_source"],
         "ASSESSMENT_SOURCE_MISMATCH")
    need(index_source == summary["target_ledger_source"], "LEDGER_ASSESSMENT_SOURCE_MISMATCH")
    digest(index_source["motionwright_sha"], 40)
    digest(summary["reconciliation_sha256"])
    for k in ("main_begin", "main_end"):
        digest(summary[k], 40)
        need(inp[k] == summary[k], "OBSERVED_MAIN_MISMATCH")
    need(summary["main_begin"] == summary["main_end"] and summary["main_stable"] is True
         and summary["pr_snapshot_stable"] is True and inp["snapshot_stability_reported"] is True,
         "OBSERVATION_NOT_STABLE")
    need(inp["observed_start_utc"] == summary["started_at_utc"]
         and inp["observed_end_utc"] == summary["ended_at_utc"], "OBSERVATION_WINDOW_MISMATCH")
    need(summary["fields"] == len(criteria), "FIELD_COUNT_MISMATCH")
    need(summary["requirements"] == sum(x.startswith(("CR-", "MW05-")) for x in record_ids)
         and summary["scenarios"] == sum(x.startswith(("ACC-", "A05-")) for x in record_ids),
         "RECORD_COUNT_MISMATCH")
    need(review["record_kind"] == "AUDIT_RECONCILIATION_NOT_PRODUCT_ACCEPTANCE", "REVIEW_KIND_INVALID")
    need(review["reviewer"]["human_identity_or_signature_claimed"] is False, "HUMAN_IDENTITY_CLAIM")
    need(review["original_contract"]["record_count"] == len(record_ids), "CONTRACT_RECORD_COUNT_MISMATCH")
    need(review["original_contract"]["normative_text_publication_performed"] is False,
         "NORMATIVE_TEXT_PUBLICATION_CLAIM")
    for key in ("product_tests_executed", "human_reviews_created", "product_verdicts_created"):
        need(type(rs[key]) is int and rs[key] == 0, "PRODUCT_OR_HUMAN_CREDIT_CLAIM")
    need(rs["release_authorized"] is False and summary["release_authorized"] is False,
         "RELEASE_AUTHORIZATION_CLAIM")
    need(summary["human_reviews_created"] == 0 and summary["new_product_test_dispatches"] == 0,
         "PRODUCT_OR_HUMAN_CREDIT_CLAIM")
    need(rs["behavioral_criterion_sufficiency_review_complete"] is False
         and rs["source_inventory_and_routing_review_complete"] is True, "AUDIT_SCOPE_INVALID")
    need(rs["unknown_original_ids"] == [] and rs["unclassified_prs"] == []
         and rs["unclassified_paths"] == [], "REVIEW_INCOMPLETE")

    def records(values):
        ids = unique(values, "RECORD_REFERENCE_DUPLICATE")
        need(ids <= record_ids, "RECORD_REFERENCE_UNRESOLVED")
        return ids

    clause_index = review["original_clause_index"]
    resolved = {}
    catalogue_hashes = set(review["original_contract"]["catalogue_sha256"].values())
    for ref, item in clause_index.items():
        rid, separator, field = ref.partition(":")
        need(separator and rid == item["record_id"] and field == item["clause_field"],
             "CLAUSE_REFERENCE_INCONSISTENT")
        records([rid])
        fid = rid + "#/" + field
        matches = sorted(x for x in criteria if x == fid or x.startswith(fid + "/"))
        need(matches, "CLAUSE_REFERENCE_UNRESOLVED")
        need(item["json_pointer"].endswith("/" + field), "CLAUSE_POINTER_MISMATCH")
        need(item["value_digest_kind"] == "canonical-json-utf8", "CLAUSE_DIGEST_KIND_UNSUPPORTED")
        digest(item["original_value_sha256"])
        need(item["source_file_sha256"] in catalogue_hashes, "CLAUSE_CATALOGUE_UNRESOLVED")
        need(review["original_contract"]["catalogue_sha256"].get(item["source_file"])
             == item["source_file_sha256"], "CLAUSE_CATALOGUE_HASH_MISMATCH")
        resolved[ref] = matches

    groups = review["semantic_groups"]
    roles = review["path_role_actions"]
    need(set(roles) == set(ROLE_ACTIONS), "ROLE_ACTION_SET_MISMATCH")
    reviewed = keyed(review["pull_request_reviews"], "pr", "PR_REVIEW_DUPLICATE_OR_MISSING")
    need(set(reviewed) == set(prs), "PR_REVIEW_COVERAGE_MISMATCH")
    need(summary["active_prs"] == len(prs) == rs["pull_requests_reviewed"], "PR_COUNT_MISMATCH")
    group_members = defaultdict(set)
    all_paths = set()
    path_occurrences = defaultdict(dict)
    claims = []
    short_claims = 0
    pr_identity_rows = []
    group_roles = defaultdict(Counter)
    all_used_refs = set()
    for number, source in prs.items():
        need(type(number) is int and number > 0, "PR_NUMBER_INVALID")
        p = reviewed[number]
        for raw_key, reviewed_key in (("headRefOid", "head_sha"), ("baseRefOid", "base_sha"),
                                     ("baseRefName", "base_ref"), ("title", "title"),
                                     ("url", "url"), ("updatedAt", "updated_at"),
                                     ("state", "state_observed"), ("isDraft", "is_draft_observed")):
            need(source[raw_key] == p[reviewed_key], "PR_IDENTITY_MISMATCH")
        digest(p["head_sha"], 40)
        digest(p["base_sha"], 40)
        need(sha(source["title"].encode("utf-8")) == p["title_sha256"]
             and sha(source["body"].encode("utf-8")) == p["body_sha256"], "PR_TEXT_HASH_MISMATCH")
        paths = unique(source["changed_paths"], "PR_CHANGED_PATH_DUPLICATE")
        need(source["changedFiles"] == len(paths), "PR_CHANGED_FILE_COUNT_MISMATCH")
        path_list_bytes = json.dumps(source["changed_paths"], separators=(",", ":")).encode("utf-8")
        need(sha(path_list_bytes) == p["changed_paths_sha256"],
             "PR_CHANGED_PATH_HASH_MISMATCH")
        unmapped = unique(source["unmapped_paths"], "PR_UNMAPPED_PATH_DUPLICATE")
        need(unmapped <= paths, "UNMAPPED_PATH_NOT_CHANGED")
        group_name = p["semantic_group"]
        need(group_name in groups and re.fullmatch(r"[a-z][a-z0-9_]{0,90}", group_name),
             "SEMANTIC_GROUP_UNRESOLVED")
        g = groups[group_name]
        group_members[group_name].add(number)
        owner_domains(p["owner"])
        for row_key, group_key in (("owner", "owner"), ("next_action", "next_action"),
                                   ("purpose", "purpose"), ("specific_acceptance_limit", "acceptance_limit")):
            need(p[row_key] == g[group_key] and p[row_key], "GROUP_DISPOSITION_MISMATCH")
        refs = unique(p["candidate_clause_refs"], "CLAUSE_REFERENCE_DUPLICATE")
        need(refs == set(g["candidate_clause_refs"]) and refs <= set(resolved),
             "GROUP_CLAUSE_REFERENCE_MISMATCH")
        all_used_refs.update(refs)
        for key in ("product_verdict_created", "ledger_statuses_copied",
                    "diff_or_execution_review_performed", "claimed_evidence_retrieved_or_validated"):
            need(p[key] is False, "PR_UNSUPPORTED_CREDIT_CLAIM")
        need(p["reviewed_disposition"] == "source_bound_change_scope_and_claims_classified",
             "PR_DISPOSITION_INVALID")
        need(p["stacked_delta"] is (source["baseRefName"] != "main"), "PR_STACK_SCOPE_MISMATCH")
        dispositions = keyed(p["changed_path_dispositions"], "path", "PATH_DISPOSITION_DUPLICATE")
        need(set(dispositions) == paths, "PATH_DISPOSITION_COVERAGE_MISMATCH")
        for path, disposition in dispositions.items():
            repo_path(path)
            role = disposition["change_role"]
            need(role in roles and disposition["role_action_ref"] == role, "PATH_ROLE_UNRESOLVED")
            need(disposition["owner"] == (roles[role]["owner"] or p["owner"]), "PATH_OWNER_MISMATCH")
            owner_domains(disposition["owner"])
            need(disposition["behavior_or_integration_inferred"] is False, "PATH_PRODUCT_CREDIT_CLAIM")
            need(disposition["existing_ledger_path_overlap"] is (path not in unmapped),
                 "PATH_OVERLAP_MISMATCH")
            need(disposition["routing_basis"] == "reviewed_pr_purpose_and_path_role_not_diff_assertion_sufficiency",
                 "PATH_ROUTING_SCOPE_INVALID")
            records(disposition["candidate_record_ids"])
            path_occurrences[path][number] = disposition
            group_roles[group_name][role] += 1
        all_paths.update(paths)
        text = source["title"] + "\n" + source["body"]
        ids = records(p["explicit_record_ids"])
        need(ids == set(CLAIM.findall(text)), "EXPLICIT_ID_CLAIM_COVERAGE_MISMATCH")
        expected_claims, expected_shorthand = [], []
        normalized_text = text.replace("\\n", "\n").replace("`n", "\n")
        for line in normalized_text.splitlines():
            line_ids = sorted(set(CLAIM.findall(line)))
            if line_ids:
                expected_claims.append({"record_ids": line_ids, "statement": line,
                                        "statement_sha256": sha(line.encode("utf-8")),
                                        "status": "attributed_pr_statement_not_admitted_evidence"})
            for match in SHORT_CLAIM.finditer(line):
                first, last = int(match[2]), int(match[3] or match[2])
                need(first <= last, "SHORTHAND_RANGE_INVALID")
                expected_shorthand.append({
                    "original_token": match[0],
                    "record_ids": [f"MW05-E{match[1]}-{i:02}" for i in range(first, last + 1)],
                    "normalization_basis": "explicit v0.5 epic/requirement shorthand in PR context",
                    "statement": line, "status": "attributed_pr_statement_not_admitted_evidence"})
        need(Counter(canonical(x) for x in p["explicit_requirement_claims"])
             == Counter(canonical(x) for x in expected_claims), "CLAIM_CONTEXT_COVERAGE_MISMATCH")
        need(Counter(canonical(x) for x in p["normalized_shorthand_claims"])
             == Counter(canonical(x) for x in expected_shorthand), "SHORTHAND_NORMALIZATION_MISMATCH")
        for claim in p["explicit_requirement_claims"]:
            need(claim["statement"] in text and sha(claim["statement"].encode("utf-8"))
                 == claim["statement_sha256"], "CLAIM_TEXT_BINDING_MISMATCH")
            need(records(claim["record_ids"]) == set(CLAIM.findall(claim["statement"])),
                 "CLAIM_RECORD_ID_MISMATCH")
            need(claim["status"] == "attributed_pr_statement_not_admitted_evidence", "AUTHOR_CLAIM_PROMOTION")
            claims.append(dict(claim, pr=number))
        for claim in p["normalized_shorthand_claims"]:
            records(claim["record_ids"])
            need(claim["statement"] in text and claim["original_token"] in claim["statement"],
                 "SHORTHAND_CLAIM_UNBOUND")
            need(claim["status"] == "attributed_pr_statement_not_admitted_evidence", "AUTHOR_CLAIM_PROMOTION")
            short_claims += 1
        pr_identity_rows.append([number, p["head_sha"], p["base_sha"], p["title_sha256"],
                                 p["body_sha256"], p["changed_paths_sha256"], group_name])
    need(set(group_members) == set(groups), "EMPTY_OR_UNCLASSIFIED_GROUP")
    for name, g in groups.items():
        need(unique(g["pull_requests"], "GROUP_PR_DUPLICATE") == group_members[name],
             "GROUP_PR_COVERAGE_MISMATCH")
    need(all_used_refs == set(resolved), "UNUSED_OR_UNDECLARED_CLAUSE_REFERENCE")
    need(Counter(canonical(x) for x in claims) == Counter(canonical(x) for x in review["explicit_original_id_claims"]),
         "CLAIM_INDEX_COVERAGE_MISMATCH")
    need(len(claims) == rs["explicit_id_claim_contexts"]
         and short_claims == rs["explicit_shorthand_claim_contexts"]
         and sum(bool(x["explicit_record_ids"]) for x in reviewed.values()) == rs["prs_with_explicit_original_ids"],
         "CLAIM_COUNT_MISMATCH")
    path_count = sum(len(x["changed_paths"]) for x in prs.values())
    need(path_count == summary["changed_path_entries"] == rs["path_occurrences_classified"]
         and len(all_paths) == rs["unique_changed_paths_classified"], "PATH_COUNT_MISMATCH")

    unmapped_paths = set().union(*(set(p["unmapped_paths"]) for p in prs.values()))
    need(unmapped_paths == unique(summary["unmapped_unique_paths"], "UNMAPPED_SUMMARY_DUPLICATE"),
         "UNMAPPED_SUMMARY_MISMATCH")
    path_reviews = keyed(review["previously_unmapped_path_reviews"], "path", "UNMAPPED_REVIEW_DUPLICATE")
    need(set(path_reviews) == unmapped_paths and len(path_reviews) == rs["previously_unmapped_unique_paths_reviewed"],
         "UNMAPPED_REVIEW_COVERAGE_MISMATCH")
    role_counts = Counter()
    for path, p in path_reviews.items():
        observed = keyed(p["observed_occurrences"], "pr", "UNMAPPED_OCCURRENCE_DUPLICATE")
        need(set(observed) == set(path_occurrences[path]), "UNMAPPED_OCCURRENCE_COVERAGE_MISMATCH")
        union_ids, union_groups = set(), set()
        for number, occurrence in observed.items():
            d = path_occurrences[path][number]
            need(d["change_role"] == p["change_role"]
                 and occurrence["semantic_group"] == reviewed[number]["semantic_group"]
                 and occurrence["was_unmapped"] is (path in prs[number]["unmapped_paths"])
                 and set(occurrence["candidate_record_ids"]) == set(d["candidate_record_ids"]),
                 "UNMAPPED_OCCURRENCE_MISMATCH")
            union_ids.update(records(occurrence["candidate_record_ids"]))
            union_groups.add(occurrence["semantic_group"])
        need(records(p["candidate_record_ids"]) == union_ids
             and unique(p["semantic_groups"], "UNMAPPED_GROUP_DUPLICATE") == union_groups
             and set(p["specific_oracle_and_limit_refs"]) == union_groups, "UNMAPPED_DISPOSITION_MISMATCH")
        need(p["change_role"] in roles and p["role_action_ref"] == p["change_role"], "UNMAPPED_ROLE_INVALID")
        need(p["diff_or_execution_review_performed"] is False
             and p["disposition"] == "reviewed_contract_routing_added_without_behavior_admission",
             "UNMAPPED_PRODUCT_CREDIT_CLAIM")
        role_owner = roles[p["change_role"]]["owner"]
        if role_owner is not None:
            need(p["owner"] == role_owner, "UNMAPPED_OWNER_MISMATCH")
            owner_domains(p["owner"])
        else:
            # An aggregate path can span several groups. Each concrete occurrence
            # already resolved its owner through its reviewed group above.
            need(p["owner"] == sorted({reviewed[n]["owner"] for n in observed}),
                 "UNMAPPED_OWNER_MISMATCH")
            for number in observed:
                owner_domains(path_occurrences[path][number]["owner"])
        role_counts[p["change_role"]] += 1
    need(dict(role_counts) == rs["unmapped_path_roles"], "UNMAPPED_ROLE_COUNT_MISMATCH")

    zero_ids = {n for n, p in prs.items() if p["path_overlap_field_count"] == 0}
    need(zero_ids == unique(summary["prs_without_field_overlap"], "ZERO_OVERLAP_DUPLICATE"),
         "ZERO_OVERLAP_SUMMARY_MISMATCH")
    zeros = keyed(review["zero_overlap_pr_dispositions"], "pr", "ZERO_OVERLAP_REVIEW_DUPLICATE")
    need(set(zeros) == zero_ids and len(zeros) == rs["zero_overlap_prs_reviewed"], "ZERO_OVERLAP_COVERAGE_MISMATCH")
    for number, zero in zeros.items():
        p = reviewed[number]
        for key in ("head_sha", "base_sha", "semantic_group", "owner", "next_action", "specific_acceptance_limit"):
            need(zero[key] == p[key], "ZERO_OVERLAP_IDENTITY_MISMATCH")
        need(set(zero["candidate_clause_refs"]) == set(p["candidate_clause_refs"]), "ZERO_OVERLAP_CLAUSE_MISMATCH")
    for relationship in review["known_relationships_from_pr_statements_not_git_equivalence"]:
        need(relationship["pr"] in prs and type(relationship["related_pr"]) is int,
             "CLAIMED_RELATIONSHIP_UNBOUND")
        need(relationship["limit"], "CLAIMED_RELATIONSHIP_LIMIT_MISSING")

    public_groups = []
    for name, g in sorted(groups.items()):
        public_groups.append({
            "group": name, "pull_requests": sorted(group_members[name]),
            "owners": owner_domains(g["owner"]),
            "criterion_ids": sorted({fid for ref in g["candidate_clause_refs"] for fid in resolved[ref]}),
            "path_occurrences_by_role": dict(sorted(group_roles[name].items())),
            "next_actions": sorted({ROLE_ACTIONS[role] for role in group_roles[name]}),
            "review_action_sha256": sha(g["next_action"].encode("utf-8")),
        })
    return {
        "schema": "motionwright.pr-routing-audit/1", "audit_result": "PASS",
        "scope": "retained_snapshot_identity_and_complete_change_routing",
        "observation": {"started_at_utc": summary["started_at_utc"], "ended_at_utc": summary["ended_at_utc"],
                        "main_begin": summary["main_begin"], "main_end": summary["main_end"],
                        "assessment_source_sha": summary["target_ledger_source"]["motionwright_sha"]},
        "input_hashes": {"snapshot_sha256": sha(snapshot_raw), "ledgers": ledger_hashes,
                         "criterion_index_sha256": sha(canonical(criteria)),
                         "original_reconciliation_sha256": summary["reconciliation_sha256"]},
        "coverage": {"pull_requests": len(prs), "changed_path_occurrences": path_count,
                     "unique_changed_paths": len(all_paths), "previously_unmapped_paths": len(path_reviews),
                     "zero_overlap_pull_requests": len(zeros), "semantic_groups": len(groups),
                     "resolved_clause_references": len(resolved), "author_claim_contexts": len(claims),
                     "normalized_shorthand_contexts": short_claims},
        "identity_digest": sha(canonical(sorted(pr_identity_rows))),
        "unmapped_path_disposition_digest": sha(canonical(review["previously_unmapped_path_reviews"])),
        "groups": public_groups,
        "assurance": {"exact_pr_metadata_bound": True, "complete_path_routing": True,
                      "ledger_clause_ids_resolved": True, "normative_text_recomputed": False,
                      "source_diff_reviewed": False, "product_tests_executed": 0,
                      "producer_claims_admitted": False, "product_admissions_created": 0,
                      "ledger_statuses_duplicated": False, "ledger_mutations": 0,
                      "human_reviews_created": 0, "product_acceptance_ready": False,
                      "release_authorized": False},
        "limits": ["Routing references identify verification scope; they do not prove behavior or integration.",
                   "Clause identifiers resolve in the retained index; catalogue-value digest conventions remain distinct.",
                   "The observation covers its recorded time window and is not a live PR or candidate acceptance claim."],
    }


def verify_files(snapshot_path, review_path, ledger_root):
    snapshot_raw, review_raw = snapshot_path.read_bytes(), review_path.read_bytes()
    snapshot, review = strict_json(snapshot_raw), strict_json(review_raw)
    blobs = {path: (ledger_root / path).read_bytes() for path in LEDGERS}
    hashes = {path: sha(blob) for path, blob in blobs.items()}
    index = strict_json(blobs["docs/acceptance/technical-evidence.json"])
    result = verify_metadata(snapshot, review, snapshot_raw, hashes, index["criteria"], index["source"])
    after = {path: sha((ledger_root / path).read_bytes()) for path in LEDGERS}
    need(hashes == after, "LEDGERS_CHANGED_DURING_VERIFICATION")
    need(snapshot_path.read_bytes() == snapshot_raw and review_path.read_bytes() == review_raw,
         "INPUTS_CHANGED_DURING_VERIFICATION")
    result["input_hashes"]["semantic_review_sha256"] = sha(review_raw)
    result["assurance"]["input_bytes_unchanged"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("snapshot", "review", "ledger-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        inputs = [args.snapshot, args.review] + [args.ledger_root / p for p in LEDGERS]
        need(args.output.resolve() not in {p.resolve() for p in inputs}, "OUTPUT_IS_INPUT")
        report = verify_files(args.snapshot, args.review, args.ledger_root)
        raw = canonical(report) + b"\n"
        if args.output.exists():
            need(args.output.read_bytes() == raw, "OUTPUT_ALREADY_EXISTS_WITH_DIFFERENT_BYTES")
        else:
            with args.output.open("xb") as stream:
                stream.write(raw)
        print(json.dumps({"audit_result": "PASS", "report_sha256": sha(raw), "bytes": len(raw),
                          "coverage": report["coverage"], "product_admissions_created": 0}))
        return 0
    except (Invalid, KeyError, TypeError, ValueError, OSError, AttributeError, IndexError) as error:
        code = str(error) if isinstance(error, Invalid) else type(error).__name__.upper()
        print(json.dumps({"audit_result": "FAIL", "error_code": code,
                          "product_admissions_created": 0, "release_authorized": False}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
