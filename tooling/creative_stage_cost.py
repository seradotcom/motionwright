#!/usr/bin/env python3
"""Measure the already-executed S/M/L creative edit stages from exact-SHA CI.

NEVER treats synthetic edits or cache units as actual render time saved.
This consumer cannot launch a provider, create a project or fabricate a user.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sys

SCHEMA = "motionwright.v05-creative-stage-cost/1"
SOURCE_SCHEMA = "motionwright.performance.v1"
SHA40 = re.compile(r"[a-f0-9]{40}\Z")
SHA64 = re.compile(r"[a-f0-9]{64}\Z")
MAX_SOURCE_BYTES = 2_000_000
MAX_MEASUREMENT_MICROS = 10**12
BRIEFS = {"harbor-safety", "museum-night", "bike-route"}
SIZES = {"S": 8, "M": 32, "L": 96}
STAGES = (
    ("concept_and_intent", ("01-rename", "02-narrative-premise", "03-scene-objective")),
    ("shot_timing_and_review", ("04-scene-status", "05-ripple-duration",
                                "06-second-renderer", "07-marker")),
    ("source_safety_and_revision", ("08-project-lock", "09-project-unlock",
                                    "10-final-objective")),
)


class EvidenceInvalid(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def require(valid: bool, code: str) -> None:
    if not valid:
        raise EvidenceInvalid(code)


def no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    found: dict = {}
    for key, value in pairs:
        require(key not in found, "duplicate_json_key")
        found[key] = value
    return found


def read_source(path: Path) -> dict:
    try:
        require(path.is_file() and 0 < path.stat().st_size <= MAX_SOURCE_BYTES,
                "input_size_invalid")
        with path.open("r", encoding="utf-8") as stream:
            source = json.load(stream, object_pairs_hook=no_duplicates)
    except (OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, EvidenceInvalid):
            raise
        raise EvidenceInvalid("input_json_invalid") from exc
    require(isinstance(source, dict), "input_json_invalid")
    return source


def bounded_number(value: object, code: str, *, strictly_positive: bool = False) -> int:
    require(type(value) is int and
            (0 < value if strictly_positive else 0 <= value <= MAX_MEASUREMENT_MICROS)
            and value <= MAX_MEASUREMENT_MICROS, code)
    return value


def measured_ratio(numerator: int, denominator: int) -> str:
    # Explicit data, not a rounded percentage or a claim about speedup.
    return f"{numerator}/{denominator}"


def checked_input(report: dict, source_sha: str) -> list[dict]:
    require(SHA40.fullmatch(source_sha) is not None, "expected_source_sha_invalid")
    require(report.get("schema") == SOURCE_SCHEMA, "source_schema_invalid")
    require(report.get("source_sha") == source_sha, "source_sha_mismatch")
    require(report.get("generated_by") == "motionwright-service/examples/acceptance_bench",
            "untrusted_generator")
    require(report.get("briefs") == 3 and report.get("datasets") == 9,
            "dataset_contract_invalid")
    require(isinstance(report.get("human_feedback"), str)
            and report["human_feedback"].startswith("NOT_RUN"),
            "human_evidence_not_independent")
    require(isinstance(report.get("comparative_claims"), str)
            and report["comparative_claims"].startswith("NONE"),
            "unverified_comparison")
    bounded_number(report.get("latency_budget_ms"), "invalid_latency_budget",
                   strictly_positive=True)
    rows = report.get("results")
    require(isinstance(rows, list) and len(rows) == 9, "dataset_contract_invalid")

    expected_operations = tuple(name for _, names in STAGES for name in names)
    seen = set()
    avoided_total = validated_total = 0
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get("brief"), str)
                and row["brief"] in BRIEFS and row.get("size") in SIZES,
                "dataset_identity_invalid")
        identity = (row["brief"], row["size"])
        require(identity not in seen, "duplicate_dataset")
        seen.add(identity)
        require(row.get("scene_count") == SIZES[row["size"]]
                and type(row.get("scene_count")) is int,
                "dataset_shape_invalid")
        for key in ("seed_micros", "revision_ten_micros",
                    "max_commit_micros", "reopen_micros", "database_bytes"):
            bounded_number(row.get(key), "invalid_measurement")
        for key in ("revision_start", "revision_end", "event_count"):
            bounded_number(row.get(key), "invalid_revision")
        require(row.get("revision_delta") == 10
                and type(row["revision_delta"]) is int
                and row["revision_end"] - row["revision_start"] == 10,
                "revision_ten_not_reproduced")
        operations = row.get("operations")
        require(isinstance(operations, list) and len(operations) == 10,
                "operation_count_invalid")
        names = [op.get("name") for op in operations if isinstance(op, dict)]
        require(tuple(names) == expected_operations, "operation_grammar_invalid")
        micros = [bounded_number(op.get("micros"), "operation_measurement_invalid")
                  for op in operations]
        require(max(micros) == row["max_commit_micros"],
                "max_commit_mismatch")
        require(sum(micros) <= row["revision_ten_micros"],
                "revision_stage_walltime_conflict")
        cache = row.get("cache")
        require(isinstance(cache, dict)
                and cache.get("kind") == "preview-index"
                and isinstance(cache.get("fingerprint_sha256"), str)
                and SHA64.fullmatch(cache["fingerprint_sha256"]) is not None,
                "cache_identity_invalid")
        for field in ("stored_bytes", "validated_bytes", "store_micros",
                      "validation_micros", "avoided_work_units"):
            bounded_number(cache.get(field), "cache_measurement_invalid")
        require(cache["validated_bytes"] == cache["stored_bytes"]
                and cache["stored_bytes"] > 0
                and cache["avoided_work_units"] == row["scene_count"],
                "cache_validation_inconsistent")
        avoided_total += cache["avoided_work_units"]
        validated_total += cache["validated_bytes"]
    require(seen == {(brief,size) for brief in BRIEFS for size in SIZES},
            "dataset_matrix_incomplete")
    cache_evidence = report.get("cache_evidence")
    require(isinstance(cache_evidence, dict)
            and cache_evidence.get("implemented") is True
            and cache_evidence.get("lookup_attempts") == 9
            and cache_evidence.get("hits") == 9
            and cache_evidence.get("avoided_work_count") == avoided_total
            and cache_evidence.get("work_unit") ==
                "scene preview-index regeneration units"
            and cache_evidence.get("validated_bytes") == validated_total,
            "cache_aggregate_invalid")
    return rows


def build_stage_report(report: dict, exact_sha: str) -> dict:
    rows = checked_input(report, exact_sha)
    normalized_rows = []
    stage_totals: dict[str, list[int]] = defaultdict(list)
    cache_validation_cost = 0
    cache_storage_cost = 0
    cache_avoided_units = 0
    for source in sorted(rows, key=lambda r: (r["brief"], r["size"])):
        ops = {op["name"]: op["micros"] for op in source["operations"]}
        stage_durations = {}
        for stage_name, names in STAGES:
            total = sum(ops[name] for name in names)
            stage_durations[stage_name] = total
            stage_totals[stage_name].append(total)
        cache = source["cache"]
        cache_validation_cost += cache["validation_micros"]
        cache_storage_cost += cache["store_micros"]
        cache_avoided_units += cache["avoided_work_units"]
        normalized_rows.append({
            "brief": source["brief"],
            "size": source["size"],
            "scenes": source["scene_count"],
            "revision_delta": source["revision_delta"],
            "stage_commit_micros": stage_durations,
            "total_measured_commit_micros": sum(stage_durations.values()),
            "revision_ten_elapsed_micros": source["revision_ten_micros"],
            "seed_elapsed_micros": source["seed_micros"],
            "reopen_elapsed_micros": source["reopen_micros"],
            "cache_validation_micros": cache["validation_micros"],
            "cache_store_micros": cache["store_micros"],
            "validated_cache_bytes": cache["validated_bytes"],
            "avoided_preview_index_work_units": cache["avoided_work_units"],
        })
    summary = {}
    for stage, durations in stage_totals.items():
        summary[stage] = {
            "samples": len(durations),
            "total_commit_micros": sum(durations),
            "min_commit_micros": min(durations),
            "max_commit_micros": max(durations),
            "median_commit_micros": sorted(durations)[len(durations)//2],
            "measurement": "observed_local_sqlite_cas_commit_only",
        }

    return {
        "schema": SCHEMA,
        "source_sha": exact_sha,
        "source_generator": report["generated_by"],
        "status": "BOUNDED_EDIT_COST_OBSERVED",
        "evidence_scope": "nine_synthetic_SML_projects_three_briefs_exactly_ten_edits_each",
        "dataset_count": 9,
        "human_interaction_evaluated": "NOT_RUN",
        "real_native_renderer_evaluated": "NOT_RUN",
        "audiovisual_master_evaluated": "NOT_RUN",
        "direct_vs_mediated_performance": "NOT_MEASURED",
        "creative_quality_evaluated": "NOT_RUN",
        "stage_definitions": {
            stage: list(names) for stage, names in STAGES
        },
        "stage_measurements": summary,
        "per_dataset": normalized_rows,
        "cache_evidence": {
            "kind": "verified_source_bound_preview_index",
            "cache_validation_total_micros": cache_validation_cost,
            "cache_storage_total_micros": cache_storage_cost,
            "avoided_work_units": cache_avoided_units,
            "avoided_work_unit_definition": "scene_preview_index_regeneration",
            "actual_renderer_frames_avoided": None,
            "wallclock_render_time_saved_micros": None,
            "cash_savings": None,
            "note": "Cache lookup reads and verifies existing CAS data; no slow-path "
                    "recomputation walltime was measured, so time-saved claims are prohibited.",
        },
        "limitations": [
            "Stage names are editorial groupings of ten existing synthetic operations, not stages of a complete native production run.",
            "Per-operation Instant measurements exclude human planning, UI drawing, native renderer frame generation and audio mastering.",
            "No unobserved baseline has been filled in for full re-rendering or repeat render after caching.",
            "Sample counts are nine source projects, not independent real users or evidence of quality or competitive superiority.",
            "This report may be used to decide which stage needs further measurement, not to claim v0.5 complete or release ready.",
        ],
        "claims": {
            "v05_requirement_complete": False,
            "release_ready": False,
            "native_sdk_pin_changed": False,
            "human_acceptance": "NOT_RUN",
        },
    }


def write_new_report(path: Path, document: dict) -> None:
    require(document.get("schema") == SCHEMA, "report_schema_invalid")
    try:
        with path.open("x", encoding="utf-8") as out:
            json.dump(document, out, indent=2, sort_keys=True)
            out.write("\n")
    except OSError as exc:
        raise EvidenceInvalid("receipt_not_written") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True,
                        help="Existing exact-source measured acceptance_bench JSON")
    parser.add_argument("--source-sha", required=True,
                        help="Exact 40-character checkout SHA, not a branch or tag")
    parser.add_argument("--output", type=Path,
                        help="New JSON receipt only; existing files are never overwritten")
    args = parser.parse_args(argv)
    try:
        source = read_source(args.input)
        result = build_stage_report(source, args.source_sha)
        if args.output is not None:
            write_new_report(args.output, result)
        else:
            print(json.dumps(result, sort_keys=True))
    except EvidenceInvalid as error:
        print("creative-stage-cost=blocked reason=" + error.code, file=sys.stderr)
        return 2
    print("creative-stage-cost=observed nine_projects=9 native_renderer=NOT_RUN "
          "wallclock_render_savings=NOT_MEASURED", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
