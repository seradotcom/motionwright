"""Source-bound synthetic rejection controls; no benchmark or formal verdict."""
import copy
import unittest

import ast
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tooling import performance_cache_consumer as admission

PRODUCT = "f" * 40
EXAMPLE = "e" * 64


def roster_fixture(version):
    profile = admission.SOURCE_ROSTER_PROFILES[version]
    sources = {"Cargo.lock": {"sha256": "a" * 64, "bytes": 100}}
    for label, name in (("acceptance", "acceptance_bench.rs"), ("reference", "responsiveness_bench.rs")):
        digest, size = profile[label]
        sources["crates/service/examples/" + name] = {"sha256": digest, "bytes": size}
    rows = [f"test {name} ... ok" for name in profile["tests"]]
    summary = f"test result: ok. {len(rows)} passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;"
    return "\n".join(rows + [summary]), sources


def make_phase(name, scenes, fingerprint, outcome, calls, counters):
    value = {"phase": name, "outcome": outcome, "fingerprint_sha256": fingerprint,
        "constructor_calls": calls, "constructor_scene_records": calls * scenes,
        "payload_bytes_read": 100, "validated_bytes": 100,
        "fingerprint_micros": 1, "lookup_micros": 2,
        "construction_micros": 3 if calls else 0,
        "publication_micros": 4 if calls else 0,
        "read_decode_compare_micros": 5, "error": None}
    value.update(zip(("lookup_attempts", "hits", "misses", "rejects"), counters))
    if outcome == "ERROR":
        value.update(payload_bytes_read=0, validated_bytes=0, read_decode_compare_micros=0,
                     error=admission.DIGEST_ERROR)
    value["validation_micros"] = value["lookup_micros"] + value["read_decode_compare_micros"]
    value["total_micros"] = sum(value[key] for key in admission.TIMES[:5])
    return value


def synthetic_report():
    report = {"schema": "motionwright.performance.v1", "source_sha": PRODUCT,
        "source_binding_consistent": True, "source_attribution_verified": False,
        "normative_reference": False, "dataset_family": "historical_scene_count_microbench",
        "source_context": {}, "results": []}
    for when in ("before", "after"):
        report["source_context"][when] = {"source_sha_compiled": PRODUCT, "source_sha_runtime": PRODUCT}
        report["source_context"]["retained_sources_" + when] = {
            "crates/service/examples/acceptance_bench.rs": {"sha256": EXAMPLE}}
    for brief in ("harbor-safety", "museum-night", "bike-route"):
        for size, scenes in (("S", 8), ("M", 32), ("L", 96)):
            cold = make_phase("cold", scenes, "a" * 64, "MISS_BUILT", 1, (2, 1, 1, 0))
            warm = make_phase("warm", scenes, "a" * 64, "HIT", 0, (1, 1, 0, 0))
            changed = make_phase("changed", scenes, "b" * 64, "MISS_BUILT", 1, (2, 1, 1, 0))
            corrupt = make_phase("corrupt", scenes, "a" * 64, "ERROR", 0, (1, 0, 0, 1))
            setup = make_phase("corrupt-prime", scenes, "a" * 64, "MISS_BUILT", 1, (2, 1, 1, 0))
            cache = {"consumer_schema": admission.SCHEMA, "consumer_scope": admission.SCOPE,
                "kind": "preview-index", "fingerprint_sha256": "a" * 64,
                "stored_bytes": 100, "validated_bytes": 100,
                "store_micros": cold["publication_micros"],
                "validation_micros": warm["validation_micros"],
                "avoided_work_units": scenes, "phases": [cold, warm, changed, corrupt],
                "corruption_control": {
                    "scope": "isolated derived-cache integrity fixture; no Project SQL ownership claim",
                    "setup": setup, "original_sha256": "c" * 64, "damaged_sha256": "d" * 64,
                    "byte_offset": 0, "before_byte": 123, "after_byte": 122,
                    "bytes_unchanged_after_rejection": True}}
            report["results"].append({"brief": brief, "size": size, "scene_count": scenes, "cache": cache})
    phases = [phase for row in report["results"] for phase in row["cache"]["phases"]]
    warm = [phase for phase in phases if phase["phase"] == "warm"]
    setups = [row["cache"]["corruption_control"]["setup"] for row in report["results"]]
    aggregate = {"implemented": True, "consumer_schema": admission.SCHEMA,
        "consumer_scope": admission.SCOPE, "aggregate_population": admission.POPULATION,
        "consumer_requests": len(phases), "control_setup_requests": len(setups),
        "lookup_attempts": sum(p["lookup_attempts"] for p in warm),
        "hits": sum(p["hits"] for p in warm),
        "avoided_work_count": sum(row["cache"]["avoided_work_units"] for row in report["results"]),
        "validated_bytes": sum(row["cache"]["validated_bytes"] for row in report["results"]),
        "validation_micros": sum(row["cache"]["validation_micros"] for row in report["results"]),
        "control_setup_lookup_attempts": sum(p["lookup_attempts"] for p in setups),
        "control_setup_constructor_calls": sum(p["constructor_calls"] for p in setups),
        "work_unit": "scene preview-index regeneration units"}
    for key in ("lookup_attempts", "hits", "misses", "rejects", "constructor_calls"):
        aggregate["all_phase_" + key] = sum(p[key] for p in phases)
    report["cache_evidence"] = aggregate
    return report


class AdmissionControls(unittest.TestCase):
    def test_consistent_report_is_not_original_admission(self):
        result = admission.validate_perf04_cache(synthetic_report(), PRODUCT, EXAMPLE)
        self.assertEqual(result["status"], "REPORT_CONSISTENT_ONLY")
        self.assertEqual(result["original_byte_admission"], "REQUIRED")
        self.assertFalse(result["formal_acceptance"])
        self.assertFalse(result["render_savings_measured"])

    def test_mutations_rejected(self):
        def phase(report, index):
            return report["results"][0]["cache"]["phases"][index]
        def cache(report):
            return report["results"][0]["cache"]
        mutations = [
            ("warm constructor called", lambda r: phase(r, 1).update(constructor_calls=1)),
            ("warm hidden construction", lambda r: phase(r, 1).update(construction_micros=1)),
            ("declared avoided units", lambda r: cache(r).update(avoided_work_units=999)),
            ("validation cost omitted", lambda r: phase(r, 1).update(validation_micros=2)),
            ("total omits validation", lambda r: phase(r, 1).update(total_micros=3)),
            ("stale changed fingerprint", lambda r: phase(r, 2).update(fingerprint_sha256="a"*64)),
            ("changed false hit", lambda r: phase(r, 2).update(outcome="HIT")),
            ("missing corrupt control", lambda r: cache(r)["phases"].pop()),
            ("generic negative", lambda r: phase(r, 3).update(error="disk full")),
            ("silent repair", lambda r: cache(r)["corruption_control"].update(bytes_unchanged_after_rejection=False)),
            ("unchanged corruption bytes", lambda r: cache(r)["corruption_control"].update(damaged_sha256="c"*64)),
            ("aggregate hides calls", lambda r: r["cache_evidence"].update(all_phase_lookup_attempts=9)),
            ("foreign SHA", lambda r: r.update(source_sha="0"*40)),
            ("unreviewed producer", lambda r: r["source_context"]["retained_sources_after"]["crates/service/examples/acceptance_bench.rs"].update(sha256="0"*64)),
            ("normative promotion", lambda r: r.update(normative_reference=True)),
            ("boolean count", lambda r: phase(r, 1).update(lookup_attempts=True)),
            ("false hardware scope", lambda r: r["cache_evidence"].update(consumer_scope="Studio/native renderer cache")),
        ]
        for label, mutate in mutations:
            with self.subTest(label=label):
                report = copy.deepcopy(synthetic_report())
                mutate(report)
                with self.assertRaises((ValueError, KeyError, TypeError)):
                    admission.validate_perf04_cache(report, PRODUCT, EXAMPLE)





def bound_fixture():
    report = synthetic_report()
    source = "/runner/source"
    root = "/runner/cache-consumer-evidence/fixtures"
    evidence = root + "/historical-source-unique"
    executable = {"path": "/runner/cache-consumer-evidence/bin/acceptance_bench",
                  "sha256": "7" * 64, "size_bytes": 1000}
    sources = {name: {"sha256": EXAMPLE if name.endswith("acceptance_bench.rs") else "8" * 64,
                      "bytes": 100} for name in admission.SOURCES}
    bindings = {"source_sha": PRODUCT, "source_tree": "a" * 40, "source_directory": source,
                "compiler_version": "rustc 1.98.1 (example 2026-10-01)",
                "executable": executable, "sources": sources, "artifact_root": root}
    report.update(artifact_directory=evidence, generated_by="motionwright-service/examples/acceptance_bench",
                  briefs=3, datasets=9, latency_budget_ms=2000,
                  human_feedback="NOT_RUN: synthetic fixture", comparative_claims="NONE: synthetic fixture")
    for when in ("before", "after"):
        report["source_context"][when].update(compiler_version=bindings["compiler_version"],
            executable=copy.deepcopy(executable), observed_os="linux", hardware_description_verified=False)
        report["source_context"]["retained_sources_" + when] = {
            name: {**value, "source": source + "/" + name,
                   "retained": evidence + "/source-" + when + "/" + Path(name).name}
            for name, value in sources.items()}
    for row in report["results"]:
        directory = root + "/" + row["brief"] + "-" + row["size"]
        row.update(artifact_directory=directory, revision_start=row["scene_count"]+1,
            revision_end=row["scene_count"]+11, revision_delta=10, event_count=row["scene_count"]+11,
            fresh_store_open_micros=1, fresh_project_create_micros=1, first_editable_scene_micros=1,
            seed_micros=2, revision_ten_micros=10, reopen_micros=1, database_bytes=4096,
            first_scene_preserved_after_revision_ten=True,
            operations=[{"name": name, "micros": 1} for name in admission.OPERATIONS], max_commit_micros=1)
        row["cache"].update(input_project_before=directory+"/cache-input-project.json",
                            input_project_changed=directory+"/cache-changed-input-project.json")
        control = row["cache"]["corruption_control"]
        control.update(artifact_directory=directory+"/cache-corrupt-control",
                       original_source=directory+"/cache-corrupt-control/corrupt-prime-preview-index.json",
                       damaged_blob=directory+"/cache-corrupt-control/blobs/sha256/cc/"+"c"*64)
    return report, bindings


def original_source(name):
    if "RAM_SOURCES" in globals():
        return RAM_SOURCES[name]
    return (ROOT / name).read_text(encoding="utf-8")

def runner_namespace():
    workflow = original_source(".github/workflows/performance-acceptance.yml")
    body = workflow.split("python3 -B - <<'PY_CACHE_CONSUMER'\n", 1)[1].split("          PY_CACHE_CONSUMER", 1)[0]
    body = "\n".join(line[10:] if line.startswith("          ") else line for line in body.splitlines())
    tree = ast.parse(body)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run"]
    if len(functions) != 1:
        raise AssertionError("exactly one owned process function required")
    # The captured workflow runs on Linux; its controls may be discovered on Windows.
    # Keep these primitives explicit and isolated from the test host's os/signal modules.
    linux_os = SimpleNamespace(killpg=mock.Mock(side_effect=AssertionError("scenario must own the signal mock")))
    linux_signal = SimpleNamespace(SIGTERM=15, SIGKILL=9)
    namespace = {"subprocess": __import__("subprocess"), "time": __import__("time"),
                 "os": linux_os, "signal": linux_signal,
                 "output": FakePath("output"), "source": "/source", "write": mock.Mock(),
                 "state": {"commands": []}, "inventory": mock.Mock(), "print": mock.Mock(),
                 "json": json}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "<workflow-owned-run>", "exec"), namespace)
    outer = [node for node in tree.body if isinstance(node, ast.Try)]
    if len(outer) != 1 or len(outer[0].handlers) != 1:
        raise AssertionError("exactly one outer custody handler required")
    handler = ast.FunctionDef(name="_outer_handler", args=ast.arguments(
        posonlyargs=[], args=[ast.arg(arg="error")], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=copy.deepcopy(outer[0].handlers[0].body), decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[])),
                 "<workflow-outer-handler>", "exec"), namespace)
    finalizer = ast.FunctionDef(name="_finalizer", args=ast.arguments(
        posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=copy.deepcopy(outer[0].finalbody), decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[finalizer], type_ignores=[])),
                 "<workflow-finalizer>", "exec"), namespace)
    return namespace

class FakePath:
    def __init__(self, value):
        self.value = value
    def __truediv__(self, value):
        return FakePath(self.value + "/" + value)
    def __str__(self):
        return self.value
    @property
    def name(self):
        return self.value.rsplit("/", 1)[-1]
    def mkdir(self):
        pass
    def open(self, mode):
        return io.BytesIO()

class BoundReportControls(unittest.TestCase):
    def test_complete_report_still_requires_original_bytes_and_sqlite(self):
        report, bindings = bound_fixture()
        result = admission.validate_bound_report(report, PRODUCT, EXAMPLE, bindings)
        self.assertEqual(result["status"], "REPORT_CONSISTENT_ONLY")
        self.assertEqual(result["sqlite_admission"], "REQUIRED")
        self.assertTrue(all(value == "REQUIRED" for value in result["required_admissions"].values()))
        self.assertEqual(result["expected_main_events"], 507)
        self.assertEqual(result["expected_main_apply_receipts"], 507)
        self.assertEqual(result["expected_main_databases"], 9)
        self.assertEqual(result["expected_isolated_control_databases"], 9)
        self.assertEqual(len(result["required_originals"]), 9)
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["normative_performance_profile_claim"])
        self.assertFalse(result["native_cache_claim"])
        self.assertFalse(result["wallclock_speedup_claim"])

    def test_exact_source_compiler_binary_and_snapshot_bindings_required(self):
        for label, mutate in (
            ("compiler", lambda r,b: b.update(compiler_version="rustc other")),
            ("binary", lambda r,b: b["executable"].update(sha256="0"*64)),
            ("source", lambda r,b: b["sources"]["Cargo.lock"].update(sha256="0"*64)),
            ("source path", lambda r,b: b.update(source_directory="/other")),
            ("before/after context", lambda r,b: r["source_context"]["after"].update(observed_os="windows")),
            ("attribution promotion", lambda r,b: r.update(source_attribution_verified=True)),
        ):
            report, bindings = bound_fixture()
            mutate(report, bindings)
            with self.subTest(label=label), self.assertRaises(ValueError):
                admission.validate_bound_report(report, PRODUCT, EXAMPLE, bindings)

    def test_original_paths_cannot_escape_or_substitute_causal_payloads(self):
        for label, mutate in (
            ("input", lambda r: r["results"][0]["cache"].update(input_project_before="/other/project.json")),
            ("damaged key", lambda r: r["results"][0]["cache"]["corruption_control"].update(damaged_blob="/other/blob")),
            ("source", lambda r: r["results"][0]["cache"]["corruption_control"].update(original_source="/other/source.json")),
            ("duplicate", lambda r: r["results"][1].update(artifact_directory=r["results"][0]["artifact_directory"])),
            ("relative", lambda r: r["results"][0].update(artifact_directory="relative")),
        ):
            report, bindings = bound_fixture()
            mutate(report)
            with self.subTest(label=label), self.assertRaises(ValueError):
                admission.validate_bound_report(report, PRODUCT, EXAMPLE, bindings)

    def test_historical_defaults_ten_operations_and507_events_remain(self):
        for label, mutate in (
            ("budget", lambda r: r.update(latency_budget_ms=2001)),
            ("seed revision", lambda r: r["results"][0].update(revision_start=1)),
            ("missing event", lambda r: r["results"][0].update(event_count=1)),
            ("wrong operation", lambda r: r["results"][0]["operations"][0].update(name="replacement")),
            ("clock", lambda r: r["results"][0]["operations"][0].update(micros=True)),
            ("human", lambda r: r.update(human_feedback="PASS")),
        ):
            report, bindings = bound_fixture()
            mutate(report)
            with self.subTest(label=label), self.assertRaises(ValueError):
                admission.validate_bound_report(report, PRODUCT, EXAMPLE, bindings)

    def test_zero_cost_counters_cannot_hide_constructor_or_setup(self):
        report = synthetic_report()
        report["results"][0]["cache"]["corruption_control"]["setup"]["constructor_calls"] = 0
        with self.assertRaises(ValueError):
            admission.validate_perf04_cache(report, PRODUCT, EXAMPLE)
        report = synthetic_report()
        report["cache_evidence"]["control_setup_lookup_attempts"] -= 1
        with self.assertRaises(ValueError):
            admission.validate_perf04_cache(report, PRODUCT, EXAMPLE)
        report = synthetic_report()
        report["results"][0]["cache"]["avoided_work_units"] = 8.0
        with self.assertRaises(ValueError):
            admission.validate_perf04_cache(report, PRODUCT, EXAMPLE)
        report = synthetic_report()
        setup = report["results"][0]["cache"]["corruption_control"]["setup"]
        setup["payload_bytes_read"] += 1
        setup["validated_bytes"] += 1
        with self.assertRaises(ValueError):
            admission.validate_perf04_cache(report, PRODUCT, EXAMPLE)

    def test_exact15_source_tests_no_skips_duplicates_extra_or_filtered(self):
        stdout, sources = roster_fixture("retained-reference-v1")
        rows, summary = stdout.splitlines()[:-1], stdout.splitlines()[-1]
        self.assertEqual(admission.source_test_roster(stdout, source_sha=PRODUCT, sources=sources)["actual_tests"], 15)
        for value in (stdout.replace(" ... ok", " ... ignored", 1), "\n".join(rows[1:]+[summary]),
                      stdout+"\n"+rows[0], stdout.replace("0 filtered", "1 filtered"),
                      stdout.replace("15 passed", "14 passed")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                admission.source_test_roster(value, source_sha=PRODUCT, sources=sources)

    def test_exact22_paged_reference_roster_is_bound_to_its_source_profile(self):
        stdout, sources = roster_fixture("paged-reference-v2")
        result = admission.source_test_roster(stdout, source_sha=PRODUCT, sources=sources)
        self.assertEqual(result["schema"], "motionwright.performance-source-test-roster/2")
        self.assertEqual(result["source_profile"], "paged-reference-v2")
        self.assertEqual(result["source_sha"], PRODUCT)
        self.assertEqual(result["source_inputs"], sources)
        self.assertEqual((result["actual_tests"], result["reference_fixture_tests"],
                          result["cache_consumer_tests"]), (22, 17, 5))
        self.assertEqual((result["original_contract_tests"], result["added_reference_fixture_tests"]), (10, 7))
        self.assertFalse(result["benchmark_executed_by_this_check"])

    def test_paged_reference_roster_rejects_each_new_test_omission(self):
        stdout, sources = roster_fixture("paged-reference-v2")
        for name in set(admission.PROJECTION_SOURCE_TESTS) - set(admission.SOURCE_TESTS):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "exact source-profile test names"):
                missing = stdout.replace(f"test {name} ... ok\n", "").replace("22 passed", "21 passed")
                admission.source_test_roster(missing, source_sha=PRODUCT, sources=sources)

    def test_paged_reference_roster_rejects_duplicate_extra_and_unknown_names(self):
        stdout, sources = roster_fixture("paged-reference-v2")
        rows = stdout.splitlines()
        variants = ["\n".join([rows[0], *rows[0:-2], rows[-1]]),
                    stdout + "\ntest arbitrary_extra ... ok",
                    stdout.replace(admission.PROJECTION_SOURCE_TESTS[-1], "arbitrary_replacement")]
        for value in variants:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "exact source-profile test names"):
                admission.source_test_roster(value, source_sha=PRODUCT, sources=sources)

    def test_paged_reference_roster_rejects_skips_filters_and_forged_summary(self):
        stdout, sources = roster_fixture("paged-reference-v2")
        for value in (stdout.replace(" ... ok", " ... ignored", 1),
                      stdout.replace(" ... ok", " ... FAILED", 1),
                      stdout.replace("0 filtered", "1 filtered"),
                      stdout.replace("22 passed", "21 passed"),
                      stdout + "\n" + stdout.splitlines()[-1]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                admission.source_test_roster(value, source_sha=PRODUCT, sources=sources)

    def test_old_and_paged_rosters_cannot_exchange_source_profiles(self):
        old, old_sources = roster_fixture("retained-reference-v1")
        new, new_sources = roster_fixture("paged-reference-v2")
        for stdout, sources in ((old, new_sources), (new, old_sources)):
            with self.subTest(stdout=stdout), self.assertRaisesRegex(ValueError, "exact source-profile test names"):
                admission.source_test_roster(stdout, source_sha=PRODUCT, sources=sources)

    def test_source_roster_rejects_unreviewed_missing_or_malformed_source_identity(self):
        stdout, sources = roster_fixture("paged-reference-v2")
        for name in sources:
            for field, value in (("sha256", "0" * 64), ("bytes", 1), ("bytes", True)):
                if name == "Cargo.lock" and value is not True:
                    continue
                changed = copy.deepcopy(sources)
                changed[name][field] = value
                with self.subTest(name=name, field=field, value=value), self.assertRaises(ValueError):
                    admission.source_test_roster(stdout, source_sha=PRODUCT, sources=changed)
        for name in sources:
            changed = copy.deepcopy(sources)
            del changed[name]
            with self.subTest(missing=name), self.assertRaises(ValueError):
                admission.source_test_roster(stdout, source_sha=PRODUCT, sources=changed)
        for source in ("", "f" * 39, "F" * 40, None, True):
            with self.subTest(source=source), self.assertRaises(ValueError):
                admission.source_test_roster(stdout, source_sha=source, sources=sources)

    def test_actual_rust_source_defines_the_complete_reviewed_imported_test_roster(self):
        source = original_source("crates/service/examples/acceptance_bench.rs")
        self.assertIn('#[path = "responsiveness_bench.rs"]\nmod reference_profiles;', source)
        cache_tests = set(re.findall(r"#\[test\]\s+fn (\w+)\(", source))
        expected = {name.split("::")[-1] for name in admission.SOURCE_TESTS if name.startswith("cache_consumer_tests::")}
        self.assertEqual(cache_tests, expected)
        reference = original_source("crates/service/examples/responsiveness_bench.rs")
        reference_tests = set(re.findall(r"#\[test\]\s+fn (\w+)\(", reference))
        sources = {"Cargo.lock": {"sha256": "a" * 64, "bytes": 100}}
        for name, raw in (("acceptance_bench.rs", source.encode("utf-8")),
                          ("responsiveness_bench.rs", reference.encode("utf-8"))):
            sources["crates/service/examples/" + name] = {"sha256": hashlib.sha256(raw).hexdigest(),
                                                          "bytes": len(raw)}
        names = sorted("cache_consumer_tests::" + name for name in cache_tests)
        names += sorted("reference_profiles::support::fixture_tests::" + name for name in reference_tests)
        stdout = "\n".join([*(f"test {name} ... ok" for name in names),
                            f"test result: ok. {len(names)} passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;"])
        result = admission.source_test_roster(stdout, source_sha=PRODUCT, sources=sources)
        self.assertEqual(result["test_names"], sorted(names))
        self.assertIn('metric.constructor_calls += 1;', source)
        self.assertIn('metric.constructor_scene_records += 1;', source)
        self.assertIn('let input_project_before = temp.join("cache-input-project.json");', source)
        self.assertIn('let input_project_changed = temp.join("cache-changed-input-project.json");', source)

    def test_workflow_preserves_four_jobs_defaults_and_explicit_narrow_selection(self):
        workflow = original_source(".github/workflows/performance-acceptance.yml")
        jobs = re.findall(r"^  ([a-z][a-z0-9-]+):\n", workflow.split("\njobs:\n", 1)[1], re.MULTILINE)
        self.assertEqual(set(jobs), {"revision-ten", "waveform-lazy", "responsiveness-service",
                                   "ui-selection", "cache-source-g0", "cache-consumer"})
        self.assertIn("options: [historical, cache-consumer, normative]\n        default: historical", workflow)
        self.assertIn("options: [all, S, M, L]\n        default: all", workflow)
        self.assertEqual(workflow.count("inputs.selection == 'historical'"), 4)
        self.assertIn("if: github.event_name == 'workflow_dispatch' && inputs.selection == 'cache-consumer'", workflow)
        self.assertIn("needs: cache-source-g0", workflow)
        self.assertIn('expected == state["source_sha"]', workflow)
        self.assertIn('"--example", "acceptance_bench", "--", "--nocapture", "--test-threads=1"', workflow)
        self.assertNotIn("cargo build", workflow.split("  cache-source-g0:",1)[1].split("  cache-consumer:",1)[0])
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertEqual(workflow.count('state["commands"].append'), 1)
        revision = workflow.split("  revision-ten:\n", 1)[1].split("  waveform-lazy:\n", 1)[0]
        self.assertIn("needs: cache-source-g0", revision)
        self.assertIn("inputs.selection == 'normative'", revision)
        self.assertIn('unset MOTIONWRIGHT_REFERENCE_PROFILE', revision)
        self.assertEqual(revision.count("env.MOTIONWRIGHT_REFERENCE_PROFILES == 'historical'"), 4)
        self.assertIn('source_sha=expected, sources=before["sources"]', workflow)
        self.assertIn('source_tests=roster["actual_tests"]', workflow)

    def test_json_rejects_duplicate_nonfinite_overflow(self):
        for value in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                admission.decode_json(value)

    def test_completed_receipt_is_not_unknown_or_retried(self):
        receipt = {"status":"COMPLETED","returncode":0,"argv":["example"],"cwd":"/source",
                   "retry_count":0,"timeout_policy":"UNKNOWN; preserve originals; no retry","pid":10,
                   "timeout_seconds":300,"started_unix":1,"finished_unix":2,"elapsed_seconds":1,
                   "schema":"motionwright.performance-cache-consumer-execution/2",
                   "process_boundary":"OWNED_POSIX_SESSION_GROUP",
                   "owned_process_group_after_wait":"ABSENT",
                   "descendants_outside_owned_group":"NOT_OBSERVED","cleanup":[]}
        admission.require_complete_execution(receipt, ["example"], "/source")
        for key,value in (("status","UNKNOWN"),("returncode",True),("retry_count",1),("elapsed_seconds",float("inf"))):
            altered = dict(receipt)
            altered[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                admission.require_complete_execution(altered, ["example"], "/source")

    def test_owned_timeout_is_preserved_unknown_without_second_start(self):
        for escalate in (False, True):
            with self.subTest(escalate_to_sigkill=escalate):
                ns = runner_namespace()
                process = mock.Mock(pid=4242)
                process.wait.side_effect = [ns["subprocess"].TimeoutExpired(["example"], 1)] + (
                    [ns["subprocess"].TimeoutExpired(["example"], 5)] if escalate else []) + [0]
                process.poll.return_value = None
                process.returncode = -9 if escalate else -15
                with mock.patch.object(ns["subprocess"], "Popen", return_value=process) as start, \
                     mock.patch.object(ns["os"], "killpg", side_effect=(
                         [None, None, None, ProcessLookupError] if escalate else [None, ProcessLookupError])) as kill:
                    result = ns["run"]("test", ["example"], {}, 1)
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertEqual(result["retry_count"], 0)
                self.assertEqual(start.call_count, 1)
                expected_signals = [mock.call(4242, ns["signal"].SIGTERM), mock.call(4242, 0)]
                expected_waits = [mock.call(timeout=1), mock.call(timeout=5)]
                if escalate:
                    expected_signals.extend([mock.call(4242, ns["signal"].SIGKILL), mock.call(4242, 0)])
                    expected_waits.append(mock.call(timeout=5))
                self.assertEqual(kill.call_args_list, expected_signals)
                self.assertEqual(process.wait.call_args_list, expected_waits)
                self.assertEqual(result["returncode_after_stop"], -9 if escalate else -15)
                with self.assertRaises(ValueError):
                    admission.require_complete_execution(result, ["example"], "/source")

    def test_owned_process_exit_race_does_not_lose_unknown_receipt(self):
        ns = runner_namespace()
        process = mock.Mock(pid=4242)
        process.wait.side_effect = [__import__("subprocess").TimeoutExpired(["example"],1),0]
        process.poll.return_value = None
        process.returncode = 0
        with mock.patch.object(ns["subprocess"],"Popen",return_value=process) as start, \
             mock.patch.object(ns["os"],"killpg",side_effect=ProcessLookupError):
            result = ns["run"]("test",["example"],{},1)
        self.assertEqual(result["status"],"UNKNOWN")
        self.assertEqual(start.call_count,1)
        self.assertGreaterEqual(ns["write"].call_count,4)

    def test_interrupted_stop_keeps_registered_unknown_in_outer_capture(self):
        ns = runner_namespace()
        process = mock.Mock(pid=4242)
        process.wait.side_effect = [ns["subprocess"].TimeoutExpired(["example"], 1),
                                    InterruptedError("second interruption during stop"), 0]
        process.poll.return_value = None
        process.returncode = 0
        with mock.patch.object(ns["subprocess"], "Popen", return_value=process) as start, \
             mock.patch.object(ns["os"], "killpg", side_effect=[None, None, None, ProcessLookupError]) as kill:
            result = ns["run"]("test", ["example"], {}, 1)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn("second interruption during stop", result["stop_observation"])
        self.assertEqual(start.call_count, 1)
        self.assertEqual(kill.call_args_list, [mock.call(4242, ns["signal"].SIGTERM), mock.call(4242, 0),
                                               mock.call(4242, ns["signal"].SIGKILL), mock.call(4242, 0)])
        self.assertEqual(process.wait.call_count, 3)
        self.assertIn("wait_error", result["cleanup"][0])
        self.assertEqual(ns["state"]["commands"], [{"label": "test", "execution": result}])
        self.assertIs(ns["state"]["commands"][0]["execution"], result)
        with self.assertRaises(ValueError) as error:
            admission.require_complete_execution(result, ["example"], "/source")
        ns["_outer_handler"](error.exception)
        self.assertEqual(ns["state"]["status"], "UNKNOWN")

    def test_post_spawn_receipt_failure_stops_owned_process_and_keeps_unknown(self):
        ns = runner_namespace()
        process = mock.Mock(pid=4242)
        process.poll.return_value = None
        process.wait.return_value = -15
        process.returncode = -15
        ns["write"].side_effect = [None, OSError("cannot persist RUNNING receipt"), None, None]
        with mock.patch.object(ns["subprocess"], "Popen", return_value=process) as start, \
             mock.patch.object(ns["os"], "killpg", side_effect=[None, ProcessLookupError]) as kill:
            result = ns["run"]("test", ["example"], {}, 1)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["pid"], 4242)
        self.assertEqual(result["retry_count"], 0)
        self.assertIn("cannot persist RUNNING receipt", result["reason"])
        self.assertEqual(start.call_count, 1)
        self.assertEqual(kill.call_args_list, [mock.call(4242, ns["signal"].SIGTERM), mock.call(4242, 0)])
        process.wait.assert_called_once_with(timeout=5)
        self.assertEqual(ns["state"]["commands"], [{"label": "test", "execution": result}])
        with self.assertRaises(ValueError) as error:
            admission.require_complete_execution(result, ["example"], "/source")
        ns["_outer_handler"](error.exception)
        self.assertEqual(ns["state"]["status"], "UNKNOWN")

    def test_known_spawn_failure_is_distinct_from_started_unknown_process(self):
        ns = runner_namespace()
        with mock.patch.object(ns["subprocess"], "Popen", side_effect=FileNotFoundError("missing executable")) as start, \
             mock.patch.object(ns["os"], "killpg") as kill:
            result = ns["run"]("test", ["example"], {}, 1)
        self.assertEqual(result["status"], "START_FAILED")
        self.assertNotIn("pid", result)
        self.assertEqual(result["retry_count"], 0)
        self.assertEqual(start.call_count, 1)
        kill.assert_not_called()
        self.assertEqual(ns["state"]["commands"], [{"label": "test", "execution": result}])
        with self.assertRaises(ValueError) as error:
            admission.require_complete_execution(result, ["example"], "/source")
        ns["_outer_handler"](error.exception)
        self.assertEqual(ns["state"]["status"], "NOT_READY")

    def test_sealing_failure_never_downgrades_existing_unknown(self):
        for scenario in ("inventory error", "manifest write", "inventory changed", "known incomplete"):
            with self.subTest(scenario=scenario):
                ns = runner_namespace()
                unknown = scenario != "known incomplete"
                ns["state"].update(status="UNKNOWN" if unknown else "CAPTURE_COMPLETE_REPORT_CONSISTENT_ONLY",
                    source_sha=PRODUCT, run_id="1", run_attempt="1",
                    commands=[{"label": "benchmark", "execution": {"status": "UNKNOWN" if unknown else "COMPLETED"}}])
                first = {"members": {}, "directories": [], "total_bytes": 0}
                if scenario in ("inventory error", "known incomplete"):
                    ns["inventory"].side_effect = OSError("inventory unavailable")
                elif scenario == "manifest write":
                    ns["inventory"].return_value = first
                    ns["write"].side_effect = [None, OSError("custody manifest unavailable"), None]
                else:
                    ns["inventory"].side_effect = [first, {**first, "total_bytes": 1}]
                ns["_finalizer"]()
                expected = "UNKNOWN" if unknown else "NOT_READY"
                self.assertEqual(ns["state"]["status"], expected)
                self.assertIn("custody_error", ns["state"])
                self.assertEqual(ns["write"].call_args.args[1]["status"], expected)

    def test_final_receipt_write_failure_cannot_claim_persisted_completion(self):
        ns = runner_namespace()
        process = mock.Mock(pid=4242)
        process.wait.return_value = 0
        process.returncode = 0
        ns["write"].side_effect = [None, None, OSError("final receipt unavailable")]
        with mock.patch.object(ns["subprocess"], "Popen", return_value=process) as start, \
             mock.patch.object(ns["os"], "killpg", side_effect=ProcessLookupError) as kill:
            with self.assertRaises(OSError) as error:
                ns["run"]("test", ["example"], {}, 1)
        self.assertEqual(start.call_count, 1)
        process.wait.assert_called_once_with(timeout=1)
        kill.assert_called_once_with(4242, 0)
        result = ns["state"]["commands"][0]["execution"]
        self.assertEqual(result["observed_status_before_receipt_failure"], "COMPLETED")
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["receipt_persistence"], "UNKNOWN")
        self.assertEqual(result["retry_count"], 0)
        self.assertIn("final receipt unavailable", result["receipt_write_error"])
        with self.assertRaises(ValueError):
            admission.require_complete_execution(result, ["example"], "/source")
        ns["_outer_handler"](error.exception)
        self.assertEqual(ns["state"]["status"], "UNKNOWN")

    def test_root_zero_exit_with_owned_descendant_is_unknown_and_blocks_followup(self):
        ns = runner_namespace()
        process = mock.Mock(pid=4242)
        process.wait.return_value = 0
        process.poll.return_value = 0
        process.returncode = 0
        # Root has exited, the inherited group survives TERM, then disappears
        # after KILL. Neither the root exit nor cleanup can admit this capture.
        with mock.patch.object(ns["subprocess"], "Popen", return_value=process) as start, \
             mock.patch.object(ns["os"], "killpg", side_effect=[None, None, None, None, ProcessLookupError]) as kill:
            result = ns["run"]("test", ["example"], {}, 1)
            with self.assertRaisesRegex(RuntimeError, "UNKNOWN prevents"):
                ns["run"]("must-not-start", ["another"], {}, 1)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["owned_process_group_after_wait"], "PRESENT")
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["returncode_after_stop"], 0)
        self.assertEqual(result["retry_count"], 0)
        self.assertEqual(result["descendants_outside_owned_group"], "NOT_OBSERVED")
        self.assertEqual(start.call_count, 1)
        self.assertEqual(len(ns["state"]["commands"]), 1)
        self.assertEqual(kill.call_args_list, [mock.call(4242, 0), mock.call(4242, ns["signal"].SIGTERM),
                                             mock.call(4242, 0), mock.call(4242, ns["signal"].SIGKILL), mock.call(4242, 0)])
        self.assertEqual(process.wait.call_args_list, [mock.call(timeout=1), mock.call(timeout=5), mock.call(timeout=5)])
        with self.assertRaises(ValueError):
            admission.require_complete_execution(result, ["example"], "/source")
        ns["_outer_handler"](RuntimeError(result["reason"]))
        self.assertEqual(ns["state"]["status"], "UNKNOWN")

    def test_root_completion_requires_observed_group_absence(self):
        ns = runner_namespace()
        process = mock.Mock(pid=4242, returncode=0)
        process.wait.return_value = 0
        with mock.patch.object(ns["subprocess"], "Popen", return_value=process) as start, \
             mock.patch.object(ns["os"], "killpg", side_effect=ProcessLookupError) as kill:
            result = ns["run"]("test", ["example"], {}, 1)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["owned_process_group_after_wait"], "ABSENT")
        self.assertEqual(result["cleanup"], [])
        self.assertEqual(start.call_count, 1)
        kill.assert_called_once_with(4242, 0)
        admitted = admission.require_complete_execution(result, ["example"], "/source")
        self.assertEqual(admitted["status"], "COMMAND_COMPLETED")
        self.assertEqual(admitted["descendants_outside_owned_group"], "NOT_OBSERVED")

    def test_group_probe_failure_after_root_exit_remains_unknown(self):
        ns = runner_namespace()
        process = mock.Mock(pid=4242, returncode=0)
        process.wait.return_value = 0
        def signal_or_probe(pid, value):
            if value == 0:
                raise PermissionError("group observation unavailable")
        with mock.patch.object(ns["subprocess"], "Popen", return_value=process) as start, \
             mock.patch.object(ns["os"], "killpg", side_effect=signal_or_probe):
            result = ns["run"]("test", ["example"], {}, 1)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["owned_process_group_after_wait"], "NOT_OBSERVED")
        self.assertIn("group observation unavailable", result["reason"])
        self.assertTrue(all("group_probe_error" in event for event in result["cleanup"]))
        self.assertEqual(len(result["cleanup"]), 2)
        self.assertEqual(start.call_count, 1)
        with self.assertRaises(ValueError):
            admission.require_complete_execution(result, ["example"], "/source")

    def test_old_or_invented_group_completion_is_not_admitted(self):
        receipt = {"status":"COMPLETED","returncode":0,"argv":["example"],"cwd":"/source",
                   "retry_count":0,"timeout_policy":"UNKNOWN; preserve originals; no retry","pid":10,
                   "timeout_seconds":300,"started_unix":1,"finished_unix":2,"elapsed_seconds":1}
        with self.assertRaisesRegex(ValueError, "group completion observation"):
            admission.require_complete_execution(receipt, ["example"], "/source")
        receipt.update(schema="motionwright.performance-cache-consumer-execution/2",
            process_boundary="OWNED_POSIX_SESSION_GROUP", owned_process_group_after_wait="ABSENT",
            descendants_outside_owned_group="NOT_OBSERVED", cleanup=[])
        admission.require_complete_execution(receipt, ["example"], "/source")
        for key, value in (("owned_process_group_after_wait", "PRESENT"),
                           ("owned_process_group_after_wait", "NOT_OBSERVED"),
                           ("descendants_outside_owned_group", "ABSENT"),
                           ("process_boundary", "ROOT_PROCESS_ONLY"),
                           ("schema", "motionwright.performance-cache-consumer-execution/1")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                admission.require_complete_execution({**receipt, key: value}, ["example"], "/source")

    def test_stop_signal_interruption_keeps_error_and_escalates_owned_group(self):
        ns = runner_namespace()
        process = mock.Mock(pid=4242, returncode=0)
        process.wait.side_effect = [ns["subprocess"].TimeoutExpired(["example"], 1), 0, 0]
        with mock.patch.object(ns["subprocess"], "Popen", return_value=process) as start, \
             mock.patch.object(ns["os"], "killpg", side_effect=[KeyboardInterrupt("signal interrupted"), None, None, ProcessLookupError]) as kill:
            result = ns["run"]("test", ["example"], {}, 1)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn("signal interrupted", result["stop_observation"])
        self.assertEqual(result["cleanup"][0]["signal_error"]["type"], "KeyboardInterrupt")
        self.assertEqual(result["cleanup"][1]["group_after_cleanup"], "ABSENT")
        self.assertEqual(kill.call_args_list, [mock.call(4242, ns["signal"].SIGTERM), mock.call(4242, 0),
                                             mock.call(4242, ns["signal"].SIGKILL), mock.call(4242, 0)])
        self.assertEqual(start.call_count, 1)
        self.assertEqual(result["retry_count"], 0)
        with self.assertRaises(ValueError):
            admission.require_complete_execution(result, ["example"], "/source")

def performance_inline_namespace(marker):
    workflow = original_source(".github/workflows/performance-acceptance.yml")
    opening = "<<'" + marker + "'\n"
    if workflow.count(opening) != 1:
        raise AssertionError("one original inline verifier is required")
    body = workflow.split(opening, 1)[1].split("          " + marker, 1)[0]
    body = "\n".join(line[10:] if line.startswith("          ") else line for line in body.splitlines())
    parsed = ast.parse(body)
    constants = {"REFERENCE_PROFILES", "REFERENCE_BRIEFS", "REFERENCE_OPERATIONS"}
    nodes = [node for node in parsed.body if isinstance(node, ast.FunctionDef)
             or (isinstance(node, ast.Assign) and len(node.targets) == 1
                 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in constants)]
    namespace = {"contract": admission, "json": json, "hashlib": hashlib, "Path": Path, "os": __import__("os"), "sys": sys,
                 "re": re, "stat": __import__("stat"), "Fraction": __import__("fractions").Fraction}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<original-" + marker + ">", "exec"), namespace)
    return namespace


def normative_fixture(profile):
    ns = performance_inline_namespace("PY_NORMATIVE_REFERENCE")
    labels = ["S", "M", "L"] if profile == "all" else [profile]
    bindings = {"source_sha": PRODUCT, "compiler_version": "rustc synthetic",
        "executable": {"sha256": "b" * 64, "size_bytes": 100},
        "sources": {name: {"sha256": "c" * 64, "bytes": 100} for name in admission.SOURCES}}
    report = {"schema": "motionwright.performance.reference.v1", "source_sha": PRODUCT,
        "generated_by": "motionwright-service/examples/acceptance_bench",
        "dataset_family": "normative_reference_profile", "source_binding_consistent": True,
        "source_attribution_verified": False, "status": "PASS", "briefs": 3,
        "datasets": 3 * len(labels), "results": [], "human_feedback": "NOT_RUN: synthetic",
        "comparative_claims": "NONE: synthetic", "pending_capabilities": {name: {"status": "NOT_RUN"}
            for name in ("studio_selection_s_p95", "studio_scrub_or_proxy_ui_blocking",
                         "installed_first_use", "native_render_cache_avoided_work")}}
    for when in ("before", "after"):
        report["source_context_" + when] = {"source_sha_compiled": PRODUCT, "source_sha_runtime": PRODUCT,
            "compiler_version": bindings["compiler_version"], "executable": dict(bindings["executable"]),
            "reference_profile_raw": None if profile == "all" else profile,
            "reference_profile_effective": "S,M,L" if profile == "all" else profile,
            "hardware_description_verified": False}
        report["retained_sources_" + when] = copy.deepcopy(bindings["sources"])
    for brief in ("harbor-safety", "museum-night", "bike-route"):
        for label in labels:
            scenes, nodes, seconds, beats, variants, reopen = ns["REFERENCE_PROFILES"][label]
            row = {"brief": brief, "size": label, "requested_scene_count": scenes, "scene_count": scenes,
                "status": "PASS", "stage": "complete", "normative_reference": True,
                "dataset_family": "normative_reference_profile", "reference_shape_verified": True,
                "negative_fixture": None, "budget_failures": [], "revision_delta": 10,
                "first_editable_scene_preserved_after_revision_ten": True,
                "full_project_equal_after_reopen": True, "claims_ids_and_all_locks_equal_after_reopen": True,
                "cas_bytes_equal_after_reopen": True, "revision_start": 5, "revision_end": 15,
                "final_revision": 16, "post_reopen_edit": {"status": "PASS", "previous_revision": 15, "revision": 16},
                "required_shape": {"scenes": scenes, "canvas_nodes": nodes, "duration_seconds": seconds,
                    "voice_fixture_seconds": 300, "beats": beats, "deliverable_variants": variants,
                    "width": 1920, "height": 1080, "frame_rate": "30/1"},
                "dataset": {"scenes": scenes, "canvas_nodes": nodes, "scene_duration": {"num": seconds, "den": 1},
                    "contiguous_scene_timeline": True, "voice_seconds": 300, "voice_duration": {"num": 300, "den": 1},
                    "voice_sample_rate_hz": 8000, "width": 1920, "height": 1080, "frame_rate": "30/1",
                    "beats": beats if beats is not None else scenes * 6,
                    "deliverable_variants": variants if variants is not None else 1},
                "operations": [{"name": name, "micros": 1, "previous_revision": 5 + index, "revision": 6 + index}
                               for index, name in enumerate(ns["REFERENCE_OPERATIONS"])],
                "small_commit": {"samples": 10, "samples_micros": [1] * 10, "p95_micros": 1, "max_micros": 1,
                    "budget_ms": 250 if label == "S" else None, "required_budget_ms": 250 if label == "S" else None,
                    "percentile_method": "nearest-rank", "outliers_removed": 0},
                "reopen_budget_ms": reopen, "required_reopen_budget_ms": reopen,
                "reference_open_micros": 1, "reopen_micros": 1, "final_reopen_micros": 1,
                "scene_object_projection": {"status": "PASS", "kind": "service_scene_and_node_pages",
                    "scope": "Exact paged service readback; no UI latency or virtualization claim",
                    "scene_limit": 32, "node_limit": 16, "scenes_read": scenes, "nodes_read": nodes,
                    "scene_pages": (scenes + 31) // 32, "node_pages": scenes * ((nodes // scenes + 15) // 16)}}
            projection = row["scene_object_projection"]
            projection["originals"] = [{"synthetic_index": i} for i in
                range(1 + projection["scene_pages"] + projection["node_pages"])]
            report["results"].append(row)
    large = 3 if "L" in labels else 0
    report["service_projection_coverage"] = {"status": "MEASURED_SERVICE_PAGES" if large else "NOT_RUN",
        "selected_large_cases": large, "completed_large_cases": large,
        "scope": "Per-case service paging after full Project readback; no cold installed UI, virtualization or render evidence"}
    return report, bindings


def single_entry_original_fixture(root):
    """One synthetic scene/node exercises original-byte validation, not a product benchmark."""
    artifact = root / "originals"
    suite = artifact / "reference-suite-synthetic"
    folder = suite / "harbor-safety-S"
    pages = folder / "project-projection"
    pages.mkdir(parents=True)
    node = {"id": "synthetic-node", "text": "Retained authored contents"}
    scene = {"id": "synthetic-scene", "name": "Synthetic scene", "start": {"num": 0, "den": 1},
             "duration": {"num": 300, "den": 1}, "renderer": "motion_canvas", "status": "draft",
             "beats": [], "nodes": [node]}
    project = {"id": "synthetic-project", "generation": "synthetic-generation", "revision": 2,
               "title": "Edited project", "scenes": [scene]}
    identity = {"project_id": project["id"], "generation": project["generation"], "revision": "2"}
    scene_ref = {"ordinal": 0, "id": scene["id"]}
    values = {
        "head.json": {"identity": identity, "title": project["title"], "scene_count": 1},
        "scenes-0000.json": {"identity": identity, "total_scenes": 1, "start_ordinal": 0, "next_cursor": None,
            "items": [{"ordinal": 0, **{key: scene[key] for key in ("id", "name", "start", "duration", "renderer", "status")},
                       "beat_count": 0, "node_count": 1}]},
        "nodes-0000-0000.json": {"identity": identity, "scene": scene_ref, "total_nodes": 1,
            "start_ordinal": 0, "items": [node], "next_cursor": None}}
    def write(path, value):
        raw = (json.dumps(value, sort_keys=True) + "\n").encode()
        path.write_bytes(raw)
        return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    originals = [write(pages / name, value) for name, value in values.items()]
    for name in ("final-project.json", "project-edited-after-reopen.json",
                 "project-after-ten.json", "project-after-reopen.json"):
        write(folder / name, project)
    row = {"brief": "harbor-safety", "size": "S", "artifact_directory": str(folder), "scene_count": 1,
           "dataset": {"canvas_nodes": 1}, "required_shape": {"duration_seconds": 300},
           "scene_object_projection": {"identity": identity, "originals": originals}}
    report = {"artifact_directory": str(suite), "results": [row],
              "retained_sources_before": {}, "retained_sources_after": {}}
    def seal():
        write(folder / "case-report.json", row)
        write(suite / "report.json", report)
    seal()
    return artifact, report, row, folder, values, write, seal


class NormativeWorkflowControls(unittest.TestCase):
    def test_selection_preserves_defaults_and_selects_three_briefs_per_profile(self):
        select = performance_inline_namespace("PY_PERFORMANCE_SELECTION")["performance_selection"]
        for event in ("push", "pull_request", "schedule"):
            self.assertEqual(select(event, "", "", "", PRODUCT)["selection"], "historical")
        for selection in ("historical", "cache-consumer"):
            result = select("workflow_dispatch", selection, "all", PRODUCT, PRODUCT)
            self.assertEqual(result["normative_datasets"], 0)
        for profile, datasets in (("all", 9), ("S", 3), ("M", 3), ("L", 3)):
            self.assertEqual(select("workflow_dispatch", "normative", profile, PRODUCT, PRODUCT)["normative_datasets"], datasets)

    def test_selection_rejects_unreviewed_source_invalid_profile_and_wrong_mode(self):
        select = performance_inline_namespace("PY_PERFORMANCE_SELECTION")["performance_selection"]
        cases = [("workflow_dispatch", "normative", profile, PRODUCT, PRODUCT)
                 for profile in ("", "s", "S,M", "XL")]
        cases += [("workflow_dispatch", "normative", "all", "0" * 40, PRODUCT),
                  ("workflow_dispatch", "cache-consumer", "all", "", PRODUCT),
                  ("workflow_dispatch", "historical", "S", PRODUCT, PRODUCT),
                  ("workflow_dispatch", "cache-consumer", "L", PRODUCT, PRODUCT),
                  ("workflow_dispatch", "unknown", "all", PRODUCT, PRODUCT),
                  ("push", "normative", "S", PRODUCT, PRODUCT),
                  ("unexpected-event", "", "", "", PRODUCT)]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                select(*values)

    def test_all_and_each_normative_profile_have_exact_rosters_without_release_claim(self):
        check = performance_inline_namespace("PY_NORMATIVE_REFERENCE")["validate_normative_report"]
        for profile, datasets in (("all", 9), ("S", 3), ("M", 3), ("L", 3)):
            with self.subTest(profile=profile):
                report, bindings = normative_fixture(profile)
                result = check(report, profile, bindings)
                self.assertEqual(result["datasets"], datasets)
                self.assertEqual(result["status"], "NORMATIVE_REPORT_CONSISTENT_ONLY")
                self.assertEqual(result["original_admission"], "REQUIRED")
                self.assertFalse(result["formal_acceptance"])
                self.assertFalse(result["release_authorized"])

    def test_normative_rejects_other_schemas_sources_and_environment_substitution(self):
        mutations = [("historical", lambda r: r.update(schema="motionwright.performance.v1")),
            ("single responsiveness", lambda r: r.update(schema="motionwright.responsiveness.v1")),
            ("foreign source", lambda r: r.update(source_sha="0" * 40)),
            ("foreign compiled source", lambda r: r["source_context_before"].update(source_sha_compiled="0" * 40)),
            ("all literal is not unset", lambda r: r["source_context_after"].update(reference_profile_raw="all")),
            ("empty is not unset", lambda r: r["source_context_before"].update(reference_profile_raw="")),
            ("forged producer", lambda r: r.update(generated_by="historical-cache")),
            ("human attribution", lambda r: r.update(source_attribution_verified=True))]
        self.assert_mutations_rejected(mutations)

    def assert_mutations_rejected(self, mutations):
        check = performance_inline_namespace("PY_NORMATIVE_REFERENCE")["validate_normative_report"]
        for label, mutate in mutations:
            report, bindings = normative_fixture("all")
            mutate(report)
            with self.subTest(label=label), self.assertRaises((ValueError, KeyError, TypeError)):
                check(report, "all", bindings)

    def test_normative_rejects_missing_duplicate_extra_or_failed_cases(self):
        self.assert_mutations_rejected([
            ("omitted", lambda r: r["results"].pop()),
            ("duplicate", lambda r: r["results"].__setitem__(1, copy.deepcopy(r["results"][0]))),
            ("extra", lambda r: r["results"].append(copy.deepcopy(r["results"][0]))),
            ("skipped", lambda r: r["results"][0].update(status="NOT_RUN")),
            ("partial", lambda r: r["results"][0].update(stage="starting")),
            ("omitted operation", lambda r: r["results"][0]["operations"].pop()),
            ("false complete count", lambda r: r.update(datasets=True))])

    def test_normative_keeps_bounded_voice_separate_from_full_timelines(self):
        self.assert_mutations_rejected([
            ("M timeline collapsed to voice", lambda r: r["results"][1]["dataset"].update(scene_duration={"num": 300, "den": 1})),
            ("L timeline collapsed", lambda r: r["results"][2]["required_shape"].update(duration_seconds=300)),
            ("M invented full narration", lambda r: r["results"][1]["dataset"].update(voice_seconds=1800)),
            ("L invented full narration", lambda r: r["results"][2]["dataset"].update(voice_duration={"num": 7200, "den": 1})),
            ("timeline gap", lambda r: r["results"][0]["dataset"].update(contiguous_scene_timeline=False))])

    def test_normative_rejects_event_history_as_projection_and_incomplete_page_evidence(self):
        self.assert_mutations_rejected([
            ("history substituted", lambda r: r["results"][0]["scene_object_projection"].update(kind="event_history_only")),
            ("original omitted", lambda r: r["results"][2]["scene_object_projection"]["originals"].pop()),
            ("projection not run", lambda r: r["results"][0]["scene_object_projection"].update(status="NOT_RUN")),
            ("wrong page limits", lambda r: r["results"][0]["scene_object_projection"].update(scene_limit=64)),
            ("large coverage incomplete", lambda r: r["service_projection_coverage"].update(completed_large_cases=2)),
            ("UI scope promoted", lambda r: r["service_projection_coverage"].update(scope="measured installed UI"))])
        report, bindings = normative_fixture("S")
        report["service_projection_coverage"].update(status="MEASURED_SERVICE_PAGES", selected_large_cases=3, completed_large_cases=3)
        with self.assertRaises(ValueError):
            performance_inline_namespace("PY_NORMATIVE_REFERENCE")["validate_normative_report"](report, "S", bindings)

    def test_normative_preserves_timing_budgets_and_actual_sample_roster(self):
        self.assert_mutations_rejected([
            ("S budget relaxed", lambda r: r["results"][0]["small_commit"].update(budget_ms=5000)),
            ("M reopen too slow", lambda r: r["results"][1].update(final_reopen_micros=5_000_000)),
            ("L invented budget", lambda r: r["results"][2].update(reopen_budget_ms=5000)),
            ("sample omitted", lambda r: r["results"][0]["small_commit"]["samples_micros"].pop()),
            ("false p95", lambda r: r["results"][0]["small_commit"].update(p95_micros=0)),
            ("reopen edit omitted", lambda r: r["results"][0]["post_reopen_edit"].update(status="NOT_RUN")),
            ("replayed revision", lambda r: r["results"][0]["operations"][0].update(revision=5))])

    def test_original_reader_rejects_unsupported_platform_before_open(self):
        ns = performance_inline_namespace("PY_NORMATIVE_REFERENCE")
        ns["sys"] = SimpleNamespace(platform="win32")
        with mock.patch.object(ns["os"], "open", side_effect=AssertionError("no unsupported open")) as opened:
            with self.assertRaisesRegex(ValueError, "requires the Linux component-dirfd backend"):
                ns["original_bytes"](Path("/unopened"), Path("/unopened/original.json"), 100)
        opened.assert_not_called()


@unittest.skipUnless(sys.platform == "linux", "actual Linux dirfd original-readback controls; NOT_RUN on this platform")
class NormativeOriginalsLinuxControls(unittest.TestCase):
    def test_original_readback_accepts_complete_synthetic_single_entry_pages(self):
        with tempfile.TemporaryDirectory(prefix="performance-page-control-") as name:
            artifact, report, *_ = single_entry_original_fixture(Path(name).resolve())
            check = performance_inline_namespace("PY_NORMATIVE_REFERENCE")["verify_normative_originals"]
            self.assertEqual(check(report, artifact), 3)

    def test_original_reader_rejects_parent_swap_before_component_open(self):
        ns = performance_inline_namespace("PY_NORMATIVE_REFERENCE")
        real_os = ns["os"]
        with tempfile.TemporaryDirectory(prefix="performance-parent-swap-") as name:
            temporary = Path(name).resolve()
            root = temporary / "owned"
            root.mkdir()
            inside, outside = root / "inside", temporary / "outside"
            inside.mkdir()
            outside.mkdir()
            (inside / "leaf.json").write_bytes(b'{"kind":"owned"}\n')
            (outside / "leaf.json").write_bytes(b'{"kind":"foreign"}\n')
            descriptors, closed, reads, swapped = [], [], [], False
            def attacked_open(path, flags, **kwargs):
                nonlocal swapped
                if path == "inside" and kwargs.get("dir_fd") is not None and not swapped:
                    inside.rename(root / "retained-inside")
                    inside.symlink_to(outside, target_is_directory=True)
                    swapped = True
                result = real_os.open(path, flags, **kwargs)
                descriptors.append(result)
                return result
            def observed_read(fd, size):
                reads.append(fd)
                return real_os.read(fd, size)
            def observed_close(fd):
                closed.append(fd)
                return real_os.close(fd)
            ns["os"] = SimpleNamespace(**{key: getattr(real_os, key) for key in (
                "O_RDONLY", "O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC", "fstat", "stat")},
                open=attacked_open, read=observed_read, close=observed_close,
                supports_dir_fd={attacked_open})
            try:
                with self.assertRaises(OSError) as raised:
                    ns["original_bytes"](root, inside / "leaf.json", 100)
                self.assertIn(raised.exception.errno, (__import__("errno").ELOOP, __import__("errno").ENOTDIR))
            finally:
                if swapped:
                    inside.unlink()
                    (root / "retained-inside").rename(inside)
            self.assertTrue(swapped)
            self.assertEqual(reads, [])
            self.assertEqual(sorted(closed), sorted(descriptors))
            self.assertEqual(len(closed), len(set(closed)))
            self.assertEqual((outside / "leaf.json").read_bytes(), b'{"kind":"foreign"}\n')

    def test_original_reader_allows_only_access_time_to_change_during_its_read(self):
        ns = performance_inline_namespace("PY_NORMATIVE_REFERENCE")
        with tempfile.TemporaryDirectory(prefix="performance-atime-control-") as name:
            root = Path(name).resolve()
            path = root / "original.json"
            path.write_bytes(b"{}\n")
            real_fstat = ns["os"].fstat
            def changed_access_time(fd):
                observed = real_fstat(fd)
                fields = {key: getattr(observed, key) for key in
                    ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")}
                return SimpleNamespace(**fields, st_atime=observed.st_atime + 123,
                                       st_atime_ns=observed.st_atime_ns + 123000000000)
            with mock.patch.object(ns["os"], "fstat", side_effect=changed_access_time):
                self.assertEqual(ns["original_bytes"](root, path, 100), b"{}\n")

    def test_original_reader_rejects_content_change_during_read(self):
        ns = performance_inline_namespace("PY_NORMATIVE_REFERENCE")
        with tempfile.TemporaryDirectory(prefix="performance-read-mutation-") as name:
            root = Path(name).resolve()
            path = root / "original.json"
            path.write_bytes(b"{}\n")
            real_read = ns["os"].read
            mutated = False
            def changed_content(fd, size):
                nonlocal mutated
                raw = real_read(fd, size)
                if not mutated:
                    mutated = True
                    with path.open("ab") as stream:
                        stream.write(b" ")
                return raw
            with mock.patch.object(ns["os"], "read", side_effect=changed_content):
                with self.assertRaisesRegex(ValueError, "original changed during readback"):
                    ns["original_bytes"](root, path, 100)
            self.assertTrue(mutated)

    def test_original_readback_rejects_hash_omission_duplicate_and_foreign_path(self):
        for label in ("changed bytes", "missing page", "duplicate", "foreign path"):
            with self.subTest(label=label), tempfile.TemporaryDirectory(prefix="performance-page-negative-") as name:
                artifact, report, row, folder, values, write, seal = single_entry_original_fixture(Path(name).resolve())
                entries = row["scene_object_projection"]["originals"]
                if label == "changed bytes":
                    Path(entries[-1]["path"]).write_bytes(b"{}\n")
                elif label == "missing page":
                    entries.pop()
                elif label == "duplicate":
                    entries.append(dict(entries[-1]))
                else:
                    entries[-1]["path"] = str(Path(name).resolve() / "outside.json")
                seal()
                check = performance_inline_namespace("PY_NORMATIVE_REFERENCE")["verify_normative_originals"]
                with self.assertRaises((ValueError, KeyError, OSError)):
                    check(report, artifact)

    def test_original_readback_rejects_changed_content_with_rehashed_page(self):
        with tempfile.TemporaryDirectory(prefix="performance-page-content-") as name:
            artifact, report, row, folder, values, write, seal = single_entry_original_fixture(Path(name).resolve())
            page = copy.deepcopy(values["nodes-0000-0000.json"])
            page["items"][0]["text"] = "Substituted content"
            row["scene_object_projection"]["originals"][-1] = write(folder / "project-projection/nodes-0000-0000.json", page)
            seal()
            with self.assertRaisesRegex(ValueError, "physical node page contents"):
                performance_inline_namespace("PY_NORMATIVE_REFERENCE")["verify_normative_originals"](report, artifact)

    def test_original_readback_rejects_physical_timeline_collapsed_after_reopen(self):
        with tempfile.TemporaryDirectory(prefix="performance-timeline-negative-") as name:
            artifact, report, row, folder, values, write, seal = single_entry_original_fixture(Path(name).resolve())
            project = json.loads((folder / "final-project.json").read_text())
            project["scenes"][0]["duration"]["num"] = 1
            write(folder / "final-project.json", project)
            write(folder / "project-edited-after-reopen.json", project)
            with self.assertRaisesRegex(ValueError, "physical timeline duration"):
                performance_inline_namespace("PY_NORMATIVE_REFERENCE")["verify_normative_originals"](report, artifact)


if __name__ == "__main__":
    unittest.main()
