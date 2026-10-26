"""PERF04 report checks for a reviewed successor; original-byte admission is separate.

The validators perform no I/O. The caller must bind run/job/source/binary,
retain the original JSON, and independently admit the per-phase receipts, cache
manifests and source/CAS bytes. A consistent report is never a product G2 PASS.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re


SCOPE = "historical benchmark preview-index consumer; not Studio or native renderer cache"
SCHEMA = "motionwright.preview-cache-consumer.v2"
POPULATION = "one warm request per dataset; all other requests and control setup are separately retained"
DIGEST_ERROR = "invalid project backup: content-addressed blob failed digest verification"
PHASES = ("cold", "warm", "changed", "corrupt")
TIMES = ("fingerprint_micros", "lookup_micros", "construction_micros",
         "publication_micros", "read_decode_compare_micros", "validation_micros",
         "total_micros")
COUNTS = ("lookup_attempts", "hits", "misses", "rejects", "constructor_calls",
          "constructor_scene_records", "payload_bytes_read", "validated_bytes")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def count(value, label):
    require(type(value) is int and value >= 0, "invalid nonnegative integer: " + label)
    return value


def phase_contract(value, name, scenes, outcome, calls, lookups):
    require(value.get("phase") == name and value.get("outcome") == outcome,
            "phase identity/outcome differs: " + name)
    for key in COUNTS + TIMES:
        count(value.get(key), name + "." + key)
    require(re.fullmatch(r"[0-9a-f]{64}", value.get("fingerprint_sha256", "")) is not None,
            "invalid phase fingerprint")
    require((value["lookup_attempts"], value["hits"], value["misses"], value["rejects"]) == lookups,
            "actual lookup roster differs: " + name)
    require(value["constructor_calls"] == calls
            and value["constructor_scene_records"] == scenes * calls,
            "observed constructor work differs: " + name)
    require(value["validation_micros"] == value["lookup_micros"] + value["read_decode_compare_micros"],
            "validation cost omits a phase")
    require(value["total_micros"] >= sum(value[key] for key in TIMES[:5]),
            "consumer total omits measured work")
    if calls == 0:
        require(value["construction_micros"] == 0 and value["publication_micros"] == 0,
                "zero constructor phase still publishes or constructs")
    if outcome == "ERROR":
        require(value.get("error") == DIGEST_ERROR, "negative has wrong physical cause")
        require(value["payload_bytes_read"] == value["validated_bytes"] == 0
                and value["read_decode_compare_micros"] == 0,
                "corrupt request continued past digest rejection")
    else:
        require(value.get("error") is None and value["payload_bytes_read"] > 0
                and value["validated_bytes"] == value["payload_bytes_read"],
                "completed consumer lacks admitted/read bytes")


def validate_perf04_cache(report, expected_product_sha, expected_example_sha256):
    require(re.fullmatch(r"[0-9a-f]{40}", expected_product_sha) is not None,
            "reviewed product SHA is required")
    require(re.fullmatch(r"[0-9a-f]{64}", expected_example_sha256) is not None,
            "reviewed example hash is required")
    require(report.get("schema") == "motionwright.performance.v1"
            and report.get("source_sha") == expected_product_sha
            and report.get("source_binding_consistent") is True
            and report.get("source_attribution_verified") is False,
            "source binding differs")
    require(report.get("normative_reference") is False
            and report.get("dataset_family") == "historical_scene_count_microbench",
            "historical cache consumer promoted to normative profile")
    context = report["source_context"]
    for when in ("before", "after"):
        original = context[when]
        require(original["source_sha_compiled"] == original["source_sha_runtime"] == expected_product_sha,
                "compiled/runtime identity differs")
        require(context["retained_sources_" + when]["crates/service/examples/acceptance_bench.rs"]["sha256"]
                == expected_example_sha256, "unreviewed example bytes")
    rows = report["results"]
    require(type(rows) is list and len(rows) == 9, "nine measured historical datasets required")
    expected = {(brief, size, scenes)
                for brief in ("harbor-safety", "museum-night", "bike-route")
                for size, scenes in (("S", 8), ("M", 32), ("L", 96))}
    require({(row["brief"], row["size"], row["scene_count"]) for row in rows} == expected,
            "historical dataset inventory differs")
    warm_rows, all_rows, setups, avoided, validated, validation_time = [], [], [], 0, 0, 0
    for row in rows:
        scenes = count(row["scene_count"], "scene_count")
        cache = row["cache"]
        require(cache["consumer_schema"] == SCHEMA and cache["consumer_scope"] == SCOPE
                and cache["kind"] == "preview-index", "consumer scope differs")
        for field in ("stored_bytes", "validated_bytes", "store_micros",
                      "validation_micros", "avoided_work_units"):
            count(cache[field], "cache." + field)
        phases = cache["phases"]
        require([value["phase"] for value in phases] == list(PHASES), "missing/reordered/extra phase")
        cold, warm, changed, corrupt = phases
        phase_contract(cold, "cold", scenes, "MISS_BUILT", 1, (2, 1, 1, 0))
        phase_contract(warm, "warm", scenes, "HIT", 0, (1, 1, 0, 0))
        phase_contract(changed, "changed", scenes, "MISS_BUILT", 1, (2, 1, 1, 0))
        phase_contract(corrupt, "corrupt", scenes, "ERROR", 0, (1, 0, 0, 1))
        require(cold["fingerprint_sha256"] == warm["fingerprint_sha256"]
                == corrupt["fingerprint_sha256"] == cache["fingerprint_sha256"],
                "unpaired cold/warm/corrupt fingerprint")
        require(changed["fingerprint_sha256"] != cold["fingerprint_sha256"],
                "changed inputs reused old fingerprint")
        require(cache["avoided_work_units"] == cold["constructor_scene_records"]
                - warm["constructor_scene_records"], "avoided work is not observed cold-minus-warm")
        require(cache["stored_bytes"] == cache["validated_bytes"] == warm["validated_bytes"],
                "warm payload identity size differs")
        require(cache["store_micros"] == cold["publication_micros"]
                and cache["validation_micros"] == warm["validation_micros"],
                "legacy summaries omit or substitute measured phase cost")
        control = cache["corruption_control"]
        require(control["scope"] == "isolated derived-cache integrity fixture; no Project SQL ownership claim",
                "corruption fixture scope differs")
        setup = control["setup"]
        phase_contract(setup, "corrupt-prime", scenes, "MISS_BUILT", 1, (2, 1, 1, 0))
        require(setup["fingerprint_sha256"] == cold["fingerprint_sha256"],
                "corruption setup did not prime the same input")
        require(cold["payload_bytes_read"] == warm["payload_bytes_read"]
                == setup["payload_bytes_read"] == cache["stored_bytes"],
                "paired cold/warm/control payload lengths differ")
        for key in ("original_sha256", "damaged_sha256"):
            require(re.fullmatch(r"[0-9a-f]{64}", control[key]) is not None, "corruption hash missing")
        require(control["original_sha256"] != control["damaged_sha256"],
                "corruption did not change physical bytes")
        require(type(control["byte_offset"]) is int and control["byte_offset"] == 0
                and type(control["before_byte"]) is int and 0 <= control["before_byte"] < 256
                and type(control["after_byte"]) is int
                and control["after_byte"] == (control["before_byte"] ^ 1)
                and control["bytes_unchanged_after_rejection"] is True,
                "physical injection or no-repair observation differs")
        warm_rows.append(warm)
        all_rows.extend(phases)
        setups.append(setup)
        avoided += cache["avoided_work_units"]
        validated += cache["validated_bytes"]
        validation_time += cache["validation_micros"]
    aggregate = report["cache_evidence"]
    require(aggregate["implemented"] is True and aggregate["consumer_schema"] == SCHEMA
            and aggregate["consumer_scope"] == SCOPE and aggregate["aggregate_population"] == POPULATION,
            "aggregate scope differs")
    totals = {
        "consumer_requests": len(all_rows),
        "control_setup_requests": len(setups),
        "lookup_attempts": sum(row["lookup_attempts"] for row in warm_rows),
        "hits": sum(row["hits"] for row in warm_rows),
        "avoided_work_count": avoided, "validated_bytes": validated,
        "validation_micros": validation_time,
        "control_setup_lookup_attempts": sum(row["lookup_attempts"] for row in setups),
        "control_setup_constructor_calls": sum(row["constructor_calls"] for row in setups),
    }
    for field in ("lookup_attempts", "hits", "misses", "rejects", "constructor_calls"):
        totals["all_phase_" + field] = sum(row[field] for row in all_rows)
    for key, value in totals.items():
        require(count(aggregate[key], key) == value, "aggregate differs: " + key)
    require(aggregate["work_unit"] == "scene preview-index regeneration units",
            "work unit contract differs")
    return {"schema": "motionwright.performance-cache-consumer-report-check/1",
            "status": "REPORT_CONSISTENT_ONLY", "datasets": len(rows),
            "consumer_requests": len(all_rows), "original_byte_admission": "REQUIRED",
            "scope": SCOPE, "formal_acceptance": False, "render_savings_measured": False}


SOURCE_TESTS = tuple("reference_profiles::support::fixture_tests::" + name for name in (
    "reference_briefs_have_distinct_brief_content_after_sqlite_reopen",
    "reference_briefs_have_distinct_scene_and_beat_content_after_sqlite_reopen",
    "reference_briefs_have_distinct_node_text_after_sqlite_reopen",
    "changing_labels_only_preserves_authored_content_after_sqlite_reopen",
    "supplied_content_is_independent_of_a_known_title_and_slug",
    "every_declared_profile_satisfies_domain_shape_and_timing_contract",
    "medium_derived_profiles_preserve_localized_contract_after_sqlite_reopen",
    "medium_localized_content_is_independent_of_title_and_slug",
    "medium_derivation_without_notes_is_rejected_by_domain",
    "medium_localization_missing_text_is_rejected_by_domain",
)) + tuple("cache_consumer_tests::" + name for name in (
    "cold_miss_counts_real_construction_and_retains_original",
    "warm_hit_skips_constructor_and_times_read_decode_comparison",
    "changed_fingerprint_misses_and_constructs_distinct_bytes",
    "physical_corruption_rejects_exact_cause_without_build_or_repair",
    "wrong_warm_oracle_is_rejected_by_timed_consumer_without_rebuild",
))
PROJECTION_SOURCE_TESTS = SOURCE_TESTS + tuple(
    "reference_profiles::support::fixture_tests::" + name for name in (
        "large_profile_prepares_real_voice_without_changing_two_hour_timeline",
        "bounded_voice_and_full_reference_timelines_are_independent",
        "reference_selection_keeps_all_three_authored_briefs_and_exact_shapes",
        "selected_or_incomplete_runs_do_not_claim_large_projection_coverage",
        "paged_scene_and_node_readback_retains_exact_current_contents",
        "paged_readback_rejects_changed_expected_node_content",
        "paged_readback_rejects_stale_reference_snapshot",
    )
)
# Closed source profiles; an unreviewed input cannot select a shorter roster.
# These hashes identify the test-bearing example bytes, not a product verdict.
SOURCE_ROSTER_PROFILES = {
    "retained-reference-v1": {
        "acceptance": ("96d00ee1f7fd04b53720d04332ea975f4c460eed111c4ad6d5e249ae085decfa", 42728),
        "reference": ("cd88ab9e23cdef912d9868de3a1b63360c71a3f80a5ae14f7065517a07d14acb", 74458),
        "tests": SOURCE_TESTS,
    },
    "paged-reference-v2": {
        "acceptance": ("96d00ee1f7fd04b53720d04332ea975f4c460eed111c4ad6d5e249ae085decfa", 42728),
        "reference": ("baec2bcffe0e1eaa24ec463b70dfa562804110e6e992691237a7a1f54e25ae92", 92652),
        "tests": PROJECTION_SOURCE_TESTS,
    },
}
OPERATIONS = ("01-rename", "02-narrative-premise", "03-scene-objective",
              "04-scene-status", "05-ripple-duration", "06-second-renderer",
              "07-marker", "08-project-lock", "09-project-unlock", "10-final-objective")
SOURCES = ("Cargo.lock", "crates/service/examples/acceptance_bench.rs",
           "crates/service/examples/responsiveness_bench.rs")
REQUIRED_ADMISSIONS = (
    "run_job_source_tool_and_binary_originals",
    "complete_source_tar_and_closed_file_directory_inventory",
    "main_sqlite_projects_history_and_apply_receipts",
    "phase_receipt_originals_and_cache_manifests",
    "cold_changed_source_payloads_and_actual_CAS_bytes",
    "isolated_corruption_source_damaged_blob_and_no_repair",
)
MAX_JSON_BYTES = 16 * 1024**2

def json_pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result

def nonfinite(value):
    raise ValueError("nonfinite JSON value: " + value)

def finite_float(value):
    number = float(value)
    require(math.isfinite(number), "nonfinite JSON float")
    return number

def decode_json(data):
    require(len(data) <= MAX_JSON_BYTES, "JSON exceeds report read bound")
    return json.loads(data, object_pairs_hook=json_pairs, parse_constant=nonfinite, parse_float=finite_float)

def same(first, second):
    if isinstance(first, bool) or isinstance(second, bool):
        return type(first) is type(second) and first == second
    if isinstance(first, (int, float)) and isinstance(second, (int, float)):
        return math.isfinite(first) and math.isfinite(second) and first == second
    if type(first) is not type(second):
        return False
    if isinstance(first, dict):
        return first.keys() == second.keys() and all(same(first[key], second[key]) for key in first)
    if isinstance(first, list):
        return len(first) == len(second) and all(same(a, b) for a, b in zip(first, second))
    return first == second

def source_test_contract(*, source_sha, sources):
    require(isinstance(source_sha, str) and re.fullmatch(r"[0-9a-f]{40}", source_sha) is not None,
            "exact checkout source SHA is required for the test roster")
    require(isinstance(sources, dict) and set(sources) == set(SOURCES),
            "complete source input identity is required for the test roster")
    for name, original in sources.items():
        require(isinstance(original, dict) and set(original) == {"sha256", "bytes"}
                and isinstance(original["sha256"], str)
                and re.fullmatch(r"[0-9a-f]{64}", original["sha256"]) is not None
                and type(original["bytes"]) is int and original["bytes"] > 0,
                "invalid source input identity for the test roster: " + name)
    acceptance = sources["crates/service/examples/acceptance_bench.rs"]
    reference = sources["crates/service/examples/responsiveness_bench.rs"]
    matches = [(version, profile) for version, profile in SOURCE_ROSTER_PROFILES.items()
               if (acceptance["sha256"], acceptance["bytes"]) == profile["acceptance"]
               and (reference["sha256"], reference["bytes"]) == profile["reference"]]
    require(len(matches) == 1, "source test roster has no exact reviewed source profile")
    version, profile = matches[0]
    return {"schema": "motionwright.performance-source-test-contract/1",
            "status": "SOURCE_INPUTS_REVIEWED_ONLY", "source_sha": source_sha,
            "scope": "exact test-bearing source profile; no product source admission",
            "source_profile": version,
            "source_inputs": {name: dict(value) for name, value in sources.items()},
            "test_names": sorted(profile["tests"]), "benchmark_executed_by_this_check": False}


def source_test_roster(stdout, *, source_sha, sources):
    selected = source_test_contract(source_sha=source_sha, sources=sources)
    expected = selected["test_names"]
    total = len(expected)
    rows = re.findall(r"^test ([A-Za-z0-9_:]+) \.\.\. ([^\r\n]+)$", stdout, re.MULTILINE)
    require(len(rows) == total and {name for name, _ in rows} == set(expected)
            and all(result == "ok" for _, result in rows),
            "acceptance_bench must execute the exact source-profile test names with no omission")
    summaries = re.findall(r"^test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored; "
                           r"(\d+) measured; (\d+) filtered out;", stdout, re.MULTILINE)
    require(len(summaries) == 1 and tuple(map(int, summaries[0])) == (total, 0, 0, 0, 0),
            "test summary contains failure, skip, filter or an incomplete roster")
    return {**selected, "schema": "motionwright.performance-source-test-roster/2",
            "status": "SOURCE_TEST_ROSTER_COMPLETE", "actual_tests": total,
            "original_contract_tests": 10, "cache_consumer_tests": 5,
            "reference_fixture_tests": total - 5, "added_reference_fixture_tests": total - 15,
            "test_names": sorted(expected), "failed": 0, "ignored": 0, "filtered": 0,
            "benchmark_executed_by_this_check": False}

def absolute_posix(value):
    require(isinstance(value, str) and value.startswith("/") and not value.startswith("//")
            and not any(char in value for char in ("\\", "\0", ":")),
            "expected original absolute Linux path")
    require(all(part not in ("", ".", "..") for part in value[1:].split("/")),
            "noncanonical original path")
    return value

def child_path(root, value):
    root, value = absolute_posix(root), absolute_posix(value)
    require(value.startswith(root + "/"), "original path escapes retained fixture root")
    return value

def validate_bound_report(report, expected_source_sha, expected_example_sha256, bindings):
    """Pure checks only; bindings are retained observations requiring later admission."""
    result = validate_perf04_cache(report, expected_source_sha, expected_example_sha256)
    require(bindings["source_sha"] == expected_source_sha
            and re.fullmatch(r"[a-f0-9]{40}", bindings["source_tree"]),
            "runner source observation differs")
    require(report["generated_by"] == "motionwright-service/examples/acceptance_bench"
            and count(report["briefs"], "brief count") == 3 and count(report["datasets"], "dataset count") == 9,
            "producer identity or dataset cardinality differs")
    require(report["human_feedback"].startswith("NOT_RUN")
            and report["comparative_claims"].startswith("NONE"), "unsupported human/comparative claim")
    compiler = bindings["compiler_version"]
    require(isinstance(compiler, str) and compiler.startswith("rustc "), "actual compiler observation missing")
    executable = bindings["executable"]
    require(re.fullmatch(r"[a-f0-9]{64}", executable["sha256"])
            and count(executable["size_bytes"], "executable size") > 0, "invalid binary observation")
    absolute_posix(executable["path"])
    require(set(bindings["sources"]) == set(SOURCES), "source observation roster differs")
    absolute_posix(bindings["source_directory"])
    root = absolute_posix(bindings["artifact_root"])
    source_root = child_path(root, report["artifact_directory"])
    context = report["source_context"]
    for when in ("before", "after"):
        value = context[when]
        require(value["compiler_version"] == compiler and same(value["executable"], executable),
                "runtime binary/compiler differs from retained runner observation")
        require(value["observed_os"] == "linux" and value["hardware_description_verified"] is False,
                "producer platform or hardware assertion differs")
        sources = context["retained_sources_" + when]
        require(set(sources) == set(SOURCES), "before/after source roster differs")
        for name, item in sources.items():
            observed = bindings["sources"][name]
            require(re.fullmatch(r"[a-f0-9]{64}", observed["sha256"])
                    and count(observed["bytes"], "observed source bytes") > 0,
                    "invalid original source hash or size")
            require(count(item["bytes"], "source bytes") > 0
                    and {"sha256": item["sha256"], "bytes": item["bytes"]} == observed,
                    "source before/after hash or size differs")
            require(item["source"] == bindings["source_directory"] + "/" + name,
                    "source references another checkout")
            require(item["retained"] == source_root + "/source-" + when + "/" + PurePosixPath(name).name,
                    "retained source references another original")
    require(same(context["before"], context["after"]), "source context changed during measurement")
    require(bindings["sources"][SOURCES[1]]["sha256"] == expected_example_sha256,
            "example observation differs from reviewed source bytes")
    budget = count(report["latency_budget_ms"], "historical commit budget")
    require(budget == 2000, "narrow positive run must preserve the historical default2000ms budget")
    directories, required, revisions = set(), [], 0
    for row in report["results"]:
        directory = child_path(root, row["artifact_directory"])
        require(directory != source_root and directory not in directories, "duplicate dataset original directory")
        directories.add(directory)
        require(count(row["revision_start"], "seed revision") == row["scene_count"] + 1
                and count(row["revision_delta"], "revision delta") == 10
                and count(row["revision_end"], "revision end") == row["revision_start"] + 10
                and count(row["event_count"], "event count") == row["revision_end"],
                "historical durable revision interval differs")
        operations = row["operations"]
        require([operation["name"] for operation in operations] == list(OPERATIONS),
                "historical ten-change operation roster differs")
        micros = [count(operation["micros"], "commit timing") for operation in operations]
        require(count(row["max_commit_micros"], "max commit") == max(micros)
                and row["max_commit_micros"] <= budget * 1000,
                "original historical commit-budget contract failed")
        for name in ("fresh_store_open_micros", "fresh_project_create_micros", "first_editable_scene_micros",
                     "seed_micros", "revision_ten_micros", "reopen_micros", "database_bytes"):
            count(row[name], name)
        require(row["first_editable_scene_micros"] <= row["seed_micros"]
                and row["first_scene_preserved_after_revision_ten"] is True,
                "first scene/reopen claim differs")
        cache = row["cache"]
        require(cache["input_project_before"] == directory + "/cache-input-project.json"
                and cache["input_project_changed"] == directory + "/cache-changed-input-project.json",
                "complete cache input originals are missing or outside the case")
        control = cache["corruption_control"]
        control_root = directory + "/cache-corrupt-control"
        require(control["artifact_directory"] == control_root
                and control["original_source"] == control_root + "/corrupt-prime-preview-index.json",
                "corruption fixture originals differ")
        require(control["damaged_blob"] == control_root + "/blobs/sha256/" +
                control["original_sha256"][:2] + "/" + control["original_sha256"],
                "negative does not reference the original content-addressed key")
        required.append({
            "case_directory": directory,
            "main_sqlite": directory + "/motionwright.sqlite3",
            "control_sqlite": control_root + "/control.sqlite3",
            "expected_main_revision": row["revision_end"],
            "expected_control_project_count": 0,
            "input_project_before": cache["input_project_before"],
            "input_project_changed": cache["input_project_changed"],
            "phase_observations": [directory + "/" + phase + "-observation.json"
                                   for phase in ("cold", "warm", "changed")] +
                                  [control_root + "/" + phase + "-observation.json"
                                   for phase in ("corrupt-prime", "corrupt")],
            "cold_payload": directory + "/cold-preview-index.json",
            "changed_payload": directory + "/changed-preview-index.json",
            "uncorrupted_negative_source": control["original_source"],
            "damaged_negative_blob": control["damaged_blob"],
            "note": "Original full cache manifests, positive CAS and all SQLite sidecars must also be admitted.",
        })
        revisions += row["event_count"]
    require(revisions == 507, "historical dataset SQL expectation differs")
    result.update(source_sha=expected_source_sha, example_sha256=expected_example_sha256,
                  source_tree=bindings["source_tree"],
                  original_byte_admission="REQUIRED", sqlite_admission="REQUIRED",
                  required_admissions={name: "REQUIRED" for name in REQUIRED_ADMISSIONS},
                  expected_main_databases=9, expected_isolated_control_databases=9,
                  expected_main_events=507, expected_main_apply_receipts=507,
                  expected_control_projects=0, required_originals=required,
                  historical_warm_requests=9, observed_phase_requests=36, separate_control_setup_requests=9,
                  total_computation_avoided_claim=False, wallclock_speedup_claim=False,
                  normative_performance_profile_claim=False, native_cache_claim=False,
                  formal_acceptance=False, release_authorized=False)
    return result

def require_complete_execution(receipt, argv, cwd):
    require(receipt["status"] == "COMPLETED" and type(receipt["returncode"]) is int
            and receipt["returncode"] == 0, "execution failed, was omitted or is UNKNOWN")
    require(receipt["argv"] == argv and receipt["cwd"] == cwd,
            "execution command or source directory differs")
    require(type(receipt["retry_count"]) is int and receipt["retry_count"] == 0
            and receipt["timeout_policy"] == "UNKNOWN; preserve originals; no retry",
            "execution used an unreviewed retry policy")
    require(count(receipt["pid"], "owned process id") > 0
            and count(receipt["timeout_seconds"], "timeout") > 0, "invalid process ownership/budget")
    for key in ("started_unix", "finished_unix", "elapsed_seconds"):
        value = receipt[key]
        require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
                "invalid execution clock")
    require(receipt["finished_unix"] >= receipt["started_unix"], "reversed execution clock")
    # This is the future capture contract, not a migration of old receipts.
    # An old root-only completion cannot be assigned an unobserved group state.
    require(receipt.get("schema") == "motionwright.performance-cache-consumer-execution/2"
            and receipt.get("process_boundary") == "OWNED_POSIX_SESSION_GROUP"
            and receipt.get("owned_process_group_after_wait") == "ABSENT"
            and receipt.get("descendants_outside_owned_group") == "NOT_OBSERVED",
            "owned process group completion observation is missing or unresolved")
    require(receipt.get("cleanup") == [] and "returncode_after_stop" not in receipt
            and "reason" not in receipt and "stop_observation" not in receipt,
            "stopped execution was promoted to success")
    return {"status": "COMMAND_COMPLETED", "retry_count": 0,
            "owned_process_group_after_wait": "ABSENT",
            "descendants_outside_owned_group": "NOT_OBSERVED"}

def read_input(path):
    require(path.is_file() and not path.is_symlink(), "regular input file required")
    size = path.stat().st_size
    require(size <= MAX_JSON_BYTES, "input exceeds bounded report size")
    data = path.read_bytes()
    require(len(data) == size, "input changed size")
    return data

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--example-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    require(not args.output.exists(), "output already exists; preserve original observations")
    original = read_input(args.input)
    bindings_original = read_input(args.bindings)
    result = validate_bound_report(decode_json(original), args.source_sha, args.example_sha256,
                                   decode_json(bindings_original))
    result["input_report_sha256"] = hashlib.sha256(original).hexdigest()
    result["bindings_sha256"] = hashlib.sha256(bindings_original).hexdigest()
    result["input_report_bytes"] = len(original)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2, sort_keys=True, allow_nan=False)
        output.write("\n")
    print(json.dumps({"status": result["status"], "datasets": result["datasets"],
                      "original_byte_admission": "REQUIRED", "sqlite_admission": "REQUIRED",
                      "source_sha": args.source_sha, "formal_acceptance": False}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
