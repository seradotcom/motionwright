#!/usr/bin/env python3
"""Bounded time-to-FIRST EDITABLE SCENE proxy + ten-revision persistence.

Consumes the existing *actual* source-bound acceptance_bench measurement.
No human user, installed desktop, native video or creative usefulness is
observed; the report never promotes these measures to human acceptance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from creative_stage_cost import (
    EvidenceInvalid, bounded_number, checked_input, read_source,
)

SCHEMA = "motionwright.v05-first-scene-reuse/1"
MAX_MICROS = 10**12


def require(ok: bool, code: str) -> None:
    if not ok:
        raise EvidenceInvalid(code)


def first_scene_report(source: dict, source_sha: str) -> dict:
    rows = checked_input(source, source_sha)
    ordered = sorted(rows, key=lambda r: (r["brief"], r["size"]))
    samples = []
    first_latencies = []
    cold_store_latencies = []
    reopen_latencies = []
    verified_cache_work = 0
    for row in ordered:
        for field in ("fresh_store_open_micros", "fresh_project_create_micros",
                      "first_editable_scene_micros"):
            bounded_number(row.get(field), "first_scene_measurement_invalid",
                           strictly_positive=True)
        require(row.get("first_scene_preserved_after_revision_ten") is True,
                "first_scene_persistence_not_verified")
        require(row["first_editable_scene_micros"] <= row["seed_micros"],
                "first_scene_after_seed_end")
        # The new file store and project creation are measured separately
        # because adding them to the scene clock would be an unstated
        # interval with unmeasured process/IO overhead.
        first_latencies.append(row["first_editable_scene_micros"])
        cold_store_latencies.append(row["fresh_store_open_micros"])
        reopen_latencies.append(row["reopen_micros"])
        verified_cache_work += row["cache"]["avoided_work_units"]
        samples.append({
            "brief": row["brief"],
            "size": row["size"],
            "scene_count": row["scene_count"],
            "new_store_open_micros": row["fresh_store_open_micros"],
            "create_project_micros": row["fresh_project_create_micros"],
            "first_persisted_editable_scene_after_new_project_micros":
                row["first_editable_scene_micros"],
            "first_scene_survived_ten_revisions_and_sqlite_reopen": True,
            "source_edit_revision_count": 10,
            "reopen_micros": row["reopen_micros"],
            "validated_preview_index_work_units": row["cache"]["avoided_work_units"],
        })

    def summary(values: list[int]) -> dict:
        values = sorted(values)
        return {
            "observations": len(values),
            "min_micros": values[0],
            "median_micros": values[4],
            "max_micros": values[-1],
            "measurement_scope": "synthetic_source_sqlite_operation_only",
        }

    return {
        "schema": SCHEMA,
        "source_sha": source_sha,
        "source_generator": source["generated_by"],
        "status": "TECHNICAL_FIRST_EDITABLE_SCENE_PROXY_MEASURED",
        "projects": len(samples),
        "briefs": 3,
        "sizes": ["S", "M", "L"],
        "scene_sizes": [8, 32, 96],
        "recorded_revisions_per_project": 10,
        "first_scene_identity_preserved_across_reopen": True,
        "measurements": {
            "new_sqlite_store_open": summary(cold_store_latencies),
            "first_editable_scene_after_project_creation": summary(first_latencies),
            "source_project_reopen_after_ten_revisions": summary(reopen_latencies),
        },
        "per_dataset": samples,
        "reuse": {
            "source_fingerprint_verified_preview_index_work_units": verified_cache_work,
            "native_frames_regenerated_or_avoided": None,
            "renderer_saved_wallclock_micros": None,
            "audience_reuse_or_adoption": "NOT_MEASURED",
        },
        "human_first_use": "NOT_RUN",
        "installed_desktop_first_frame": "NOT_MEASURED",
        "rendered_first_useful_video": "NOT_MEASURED",
        "end_user_first_useful_result": "NOT_MEASURED",
        "human_revisions_and_preference": "NOT_RUN",
        "claims": {
            "v05_requirement_complete": False,
            "release_ready": False,
            "user_value_demonstrated": False,
            "provider_performance_demonstrated": False,
        },
        "limitations": [
            "Fresh SQLite file creation in a CI runner is not an installed desktop application launch.",
            "The clock to first scene starts AFTER project creation and includes one synthetic brief edit.",
            "Ten source CAS revisions and SQLite reopen prove persistence, not ten real human creative revisions.",
            "Validated preview-index skipped work units do not prove saved native renderer frames or wallclock.",
            "No person evaluated usefulness, production quality, adoption, repeat purchase or creative preference.",
        ],
    }


def write_receipt(path: Path, receipt: dict) -> None:
    require(receipt.get("schema") == SCHEMA, "receipt_schema_invalid")
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except OSError as exc:
        raise EvidenceInvalid("receipt_write_refused") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        source = read_source(args.input)
        report = first_scene_report(source, args.source_sha)
        if args.output:
            write_receipt(args.output, report)
        else:
            print(json.dumps(report, sort_keys=True))
    except EvidenceInvalid as exc:
        print("first-scene-reuse=blocked reason=" + exc.code, file=sys.stderr)
        return 2
    print("first-scene-reuse=measured projects=9 human=NOT_RUN "
          "first_useful_result=NOT_MEASURED", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
