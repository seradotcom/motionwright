"""Synthetic gate controls, disposable SQLite and PCM; never a product G2."""
import copy
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
import wave

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tooling import ci_performance_reference as gate


def shape(size="S", truncated=False):
    scenes, nodes, seconds, beats, variants, _ = gate.PROFILES[size]
    return {"scenes": scenes, "canvas_nodes": nodes - int(truncated),
            "beats": beats if beats is not None else scenes * 6,
            "width": 1920, "height": 1080, "frame_rate": "30/1",
            "voice_sample_rate_hz": 8000, "voice_seconds": seconds,
            "voice_duration": {"num": str(seconds), "den": "1"},
            "scene_duration": {"num": str(seconds), "den": "1"},
            "contiguous_scene_timeline": True, "deliverable_variants": variants or 3,
            "claims": 1, "locks": 1, "node_property_locks": scenes}


def metrics(size="S", responsiveness=True):
    count = 40 if responsiveness else 10
    required = 250 if size == "S" else None
    values = list(range(1, count + 1))
    return {"size": size, "measured_operations": [{"micros": v} for v in values],
            "operations": [{"micros": n} for n in range(1, 11)], "max_commit_micros": 10,
            "small_commit": {"samples": count, "samples_micros": values,
                "p95_micros": 38 if responsiveness else 10, "max_micros": count,
                "percentile_method": "nearest-rank", "outliers_removed": 0,
                "required_budget_ms": required, "budget_ms": required},
            "required_reopen_budget_ms": gate.PROFILES[size][5], "reopen_budget_ms": gate.PROFILES[size][5],
            "reference_open_micros": 1000, "reopen_micros": 1000,
            "final_reopen_micros": 1000, "budget_failures": []}


def negative(kind):
    truncated = kind == "truncated-profile"
    result = {"status": "FAIL", "negative_fixture": kind, "size": "S",
              "normative_reference": not truncated, "dataset": shape(truncated=truncated)}
    if truncated:
        result.update(stage="validate_persisted_reference_shape",
                      error="reference object count differs after persisted readback")
    else:
        result.update(stage="positive_cas_edit_after_reopen", reference_shape_verified=True,
                      negative_injection={"name": "negative-content-lock"},
                      post_reopen_edit={"status": "FAIL", "class": "domain_locked", "error": "domain: locked"},
                      error="domain: locked", full_project_equal_after_reopen=True,
                      claims_ids_and_all_locks_equal_after_reopen=True, cas_bytes_equal_after_reopen=True)
    return result


def authored(index):
    word = ("harbor", "museum", "cycle")[index]
    return {"id": str(index), "title": f"Title {index}",
            "brief": {"objective": word + " purpose", "claims": [{"text": word + " claim"}]},
            "scenes": [{"id": str(index), "name": str(index), "objective": word + " scene",
                        "beats": [{"objective": word + " beat", "label": str(index)}],
                        "nodes": [{"text": word + " caption 01", "id": str(index)}]}]}


class ReferenceGateControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="performance-gate-control-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_exact_plan_six_processes_fourteen_fixtures(self):
        self.assertEqual(len(gate.PLAN), 6)
        self.assertEqual([p[2] for p in gate.PLAN[1:4]], ["S", "M", "L"])
        self.assertEqual([p[3] for p in gate.PLAN[4:]], ["truncated-profile", "uneditable-reopen"])
        self.assertEqual(sum(9 if p[2] is None else 1 for p in gate.PLAN), 14)
        self.assertEqual(gate.PRODUCT_SHA, "77d56a8804457fd2120818a6f87b7ae8afa36665")

    def test_foreign_pin_rejected_before_git(self):
        with mock.patch.object(gate, "source_identity") as git:
            with self.assertRaisesRegex(gate.ContractError, "not reviewed"):
                gate.pinned_product(self.root, "a" * 40)
            git.assert_not_called()

    def test_duplicate_json_and_nonfinite_rejected(self):
        for data in ('{"status":"FAIL","status":"PASS"}', '{"micros":NaN}', '{"micros":Infinity}'):
            with self.subTest(data=data), self.assertRaises(gate.ContractError):
                gate.decode_json(data)

    def test_reference_shapes_and_explicit_truncation(self):
        for size in gate.PROFILES:
            gate.check_shape(shape(size), size)
        gate.check_shape(shape(truncated=True), "S", True)
        with self.assertRaises(gate.ContractError):
            gate.check_shape(shape(truncated=True), "S")

    def test_bad_timeline_voice_and_authored_state_rejected(self):
        for key, value in (("contiguous_scene_timeline", False), ("voice_sample_rate_hz", 48000),
                           ("scene_duration", {"num": "299", "den": "1"}), ("claims", 0)):
            candidate = shape()
            candidate[key] = value
            with self.subTest(key=key), self.assertRaises(gate.ContractError):
                gate.check_shape(candidate, "S")

    def test_valid_raw_p95_both_sample_rosters(self):
        for response in (False, True):
            for size in gate.PROFILES:
                gate.check_metrics(metrics(size, response), response)

    def test_p95_cannot_hide_slow_tail(self):
        candidate = metrics()
        for index in (37, 38, 39):
            candidate["measured_operations"][index]["micros"] = 300000
            candidate["small_commit"]["samples_micros"][index] = 300000
        candidate["small_commit"]["max_micros"] = 300000
        with self.assertRaisesRegex(gate.ContractError, "p95/max"):
            gate.check_metrics(candidate, True)

    def test_raw_samples_must_match_operations(self):
        candidate = metrics()
        candidate["small_commit"]["samples_micros"][0] = 0
        with self.assertRaisesRegex(gate.ContractError, "raw samples"):
            gate.check_metrics(candidate, True)

    def test_missing_and_empty_samples_rejected(self):
        for count in (0, 39):
            candidate = metrics()
            candidate["measured_operations"] = candidate["measured_operations"][:count]
            with self.subTest(count=count), self.assertRaises(gate.ContractError):
                gate.check_metrics(candidate, True)

    def test_s_commit_limit_is_strict(self):
        candidate = metrics()
        for op in candidate["measured_operations"]:
            op["micros"] = 250000
        candidate["small_commit"].update(samples_micros=[250000] * 40, p95_micros=250000, max_micros=250000)
        with self.assertRaisesRegex(gate.ContractError, "strict 250"):
            gate.check_metrics(candidate, True)

    def test_all_s_m_open_phases_are_strict(self):
        for size, limit in (("S", 2000000), ("M", 5000000)):
            for phase in ("reference_open_micros", "reopen_micros", "final_reopen_micros"):
                candidate = metrics(size)
                candidate[phase] = limit
                with self.subTest(size=size, phase=phase), self.assertRaisesRegex(gate.ContractError, "strict"):
                    gate.check_metrics(candidate, True)

    def test_no_invented_l_open_or_m_commit_threshold(self):
        candidate = metrics("L")
        candidate["reference_open_micros"] = 9000000000
        gate.check_metrics(candidate, True)
        candidate["reopen_budget_ms"] = 9000001
        with self.assertRaisesRegex(gate.ContractError, "open budget"):
            gate.check_metrics(candidate, True)
        candidate = metrics("M")
        candidate["small_commit"]["required_budget_ms"] = 250
        with self.assertRaisesRegex(gate.ContractError, "commit budget"):
            gate.check_metrics(candidate, True)

    def test_relaxed_budget_and_removed_outliers_rejected(self):
        for key, value in (("budget_ms", 10000), ("required_budget_ms", 10000),
                           ("outliers_removed", 1), ("percentile_method", "interpolated")):
            candidate = metrics()
            candidate["small_commit"][key] = value
            with self.subTest(key=key), self.assertRaises(gate.ContractError):
                gate.check_metrics(candidate, True)

    def test_causal_negative_requires_exact_nonzero_exit(self):
        for kind in ("truncated-profile", "uneditable-reopen"):
            gate.check_negative(negative(kind), kind, 1)
            for code in (0, 101, -9, None, True):
                with self.subTest(kind=kind, code=code), self.assertRaises(gate.ContractError):
                    gate.check_negative(negative(kind), kind, code)

    def test_generic_negative_failure_rejected(self):
        for kind in ("truncated-profile", "uneditable-reopen"):
            candidate = negative(kind)
            candidate.update(stage="starting", error="disk full")
            with self.subTest(kind=kind), self.assertRaises(gate.ContractError):
                gate.check_negative(candidate, kind, 1)

    def test_lock_class_and_promoted_truncation_rejected(self):
        candidate = negative("uneditable-reopen")
        candidate["post_reopen_edit"]["class"] = "storage_error"
        with self.assertRaisesRegex(gate.ContractError, "DomainError"):
            gate.check_negative(candidate, "uneditable-reopen", 1)
        candidate = negative("truncated-profile")
        candidate["normative_reference"] = True
        with self.assertRaisesRegex(gate.ContractError, "promoted"):
            gate.check_negative(candidate, "truncated-profile", 1)

    def test_unknown_cannot_be_negative_pass(self):
        with self.assertRaisesRegex(gate.ContractError, "UNKNOWN"):
            gate.validate_invocation(self.root, gate.PLAN[-1], {"status": "UNKNOWN"},
                                     self.root, self.root / "bin", "rustc", self.root)

    def test_revision_chain_rejects_replay(self):
        rows = [{"previous_revision": 1, "revision": 2, "micros": 10, "request_id": "reference-one"},
                {"previous_revision": 2, "revision": 3, "micros": 11, "request_id": "reference-two"}]
        self.assertEqual(gate.chain(rows, 1)[0], 3)
        rows[1]["request_id"] = rows[0]["request_id"]
        with self.assertRaisesRegex(gate.ContractError, "duplicate"):
            gate.chain(rows, 1)
        rows[1]["revision"] = 2
        with self.assertRaisesRegex(gate.ContractError, "revision"):
            gate.chain(rows, 1)

    def test_all_three_semantic_families_are_required(self):
        projects = {(brief, size): authored(i) for i, brief in enumerate(gate.BRIEFS) for size in gate.PROFILES}
        gate.check_diversity(projects)
        projects[("bike-route", "L")] = authored(0)
        with self.assertRaisesRegex(gate.ContractError, "diversity"):
            gate.check_diversity(projects)

    def test_labels_uuid_order_and_repetition_not_diversity(self):
        before = authored(0)
        after = copy.deepcopy(before)
        after.update(title="Different title", id="another-uuid")
        after["scenes"][0].update(name="different label", id="different-id")
        after["scenes"].append(copy.deepcopy(after["scenes"][0]))
        after["scenes"][0]["nodes"][0]["text"] = "harbor caption 999"
        self.assertEqual(gate.semantic_families(before), gate.semantic_families(after))
        projects = {(brief, size): copy.deepcopy(after) for brief in gate.BRIEFS for size in gate.PROFILES}
        with self.assertRaisesRegex(gate.ContractError, "diversity"):
            gate.check_diversity(projects)

    def test_closed_custody_inventory_and_hidden_files(self):
        (self.root / ".original").write_bytes(b"original")
        database = self.root / "original.sqlite3"
        database.write_bytes(b"physical bytes")
        expected = gate.inventory(self.root)
        self.assertIn(".original", expected)
        gate.verify_inventory(self.root, expected)
        extra = self.root / "extra"
        extra.write_bytes(b"unaccounted")
        with self.assertRaises(gate.ContractError):
            gate.verify_inventory(self.root, expected)
        extra.unlink()
        database.write_bytes(b"changed")
        with self.assertRaises(gate.ContractError):
            gate.verify_inventory(self.root, expected)
        database.unlink()
        with self.assertRaises(gate.ContractError):
            gate.verify_inventory(self.root, expected)

    def test_path_escape_and_relative_reference_rejected(self):
        inside = self.root / "inside"
        inside.mkdir()
        outside = self.root / "outside"
        outside.write_bytes(b"x")
        with self.assertRaisesRegex(gate.ContractError, "escapes"):
            gate.contained(inside, outside)
        with self.assertRaisesRegex(gate.ContractError, "relative"):
            gate.contained(self.root, "outside")

    def test_sqlite_bytes_preserved_and_authority_verified(self):
        original = self.root / "original.sqlite3"
        expected = {"id": "id", "generation": "generation", "revision": 11, "title": "Fixture"}
        with sqlite3.connect(original) as database:
            database.execute("CREATE TABLE projects(id TEXT,generation TEXT,revision INTEGER,title TEXT,document_json TEXT)")
            database.execute("INSERT INTO projects VALUES(?,?,?,?,?)", (*expected.values(), json.dumps(expected)))
        before = gate.digest(original)
        gate.verify_sqlite(original, expected, self.root)
        self.assertEqual(gate.digest(original), before)
        with sqlite3.connect(original) as database:
            database.execute("UPDATE projects SET revision=12")
        with self.assertRaisesRegex(gate.ContractError, "authority"):
            gate.verify_sqlite(original, expected, self.root)

    def test_history_complete_without_projection_promotion(self):
        path = self.root / "history.json"
        value = {"kind": "event_history_only", "scene_object_projection_claim": False,
                 "events": [{"revision": 1}, {"revision": 2}],
                 "pages": [{"after_revision": 0, "returned": 2}, {"after_revision": 2, "returned": 0}]}
        gate.write_json(path, value)
        gate.check_history(path, 2)
        value["scene_object_projection_claim"] = True
        gate.write_json(path, value)
        with self.assertRaisesRegex(gate.ContractError, "projection"):
            gate.check_history(path, 2)
        value["scene_object_projection_claim"] = False
        value["events"].pop()
        gate.write_json(path, value)
        with self.assertRaisesRegex(gate.ContractError, "incomplete"):
            gate.check_history(path, 2)

    def context_fixture(self):
        product, suite = self.root / "product", self.root / "suite"
        product.mkdir()
        suite.mkdir()
        binary = self.root / "retained-binary"
        binary.write_bytes(b"synthetic identity control, never executed")
        compiler = "rustc 1.98.1 (synthetic gate control)"
        context = {"source_sha_compiled": gate.PRODUCT_SHA, "source_sha_runtime": gate.PRODUCT_SHA,
                   "compiler_version": compiler, "observed_os": "linux", "hardware_description_verified": False,
                   "executable": {"path": str(binary), "sha256": gate.digest(binary), "size_bytes": binary.stat().st_size}}
        report = {"source_sha": gate.PRODUCT_SHA, "source_binding_consistent": True,
                  "source_attribution_verified": False, "source_context_before": copy.deepcopy(context),
                  "source_context_after": copy.deepcopy(context), "human_feedback": "NOT_RUN",
                  "comparative_claims": "NONE", "pending_capabilities": {n: {"status": "NOT_RUN"} for n in gate.PENDING}}
        report["pending_capabilities"]["studio_selection_s_p95"]["required_limit_ms"] = 100
        report["pending_capabilities"]["studio_scrub_or_proxy_ui_blocking"]["required_limit_ms"] = 200
        for relative in list(gate.SOURCES) + ["Cargo.lock"]:
            source = product / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(("synthetic source " + relative).encode())
        for phase in ("before", "after"):
            folder = suite / f"source-{phase}"
            folder.mkdir()
            sources = {}
            for relative in list(gate.SOURCES) + ["Cargo.lock"]:
                source = product / relative
                retained = folder / source.name
                shutil.copyfile(source, retained)
                sources[relative] = {"source": str(source), "retained": str(retained),
                                     "sha256": gate.digest(source), "bytes": source.stat().st_size}
            report[f"retained_sources_{phase}"] = sources
            gate.write_json(suite / f"source-{phase}.json", sources)
            gate.write_json(suite / f"context-{phase}.json", context)
        return report, suite, product, binary, compiler

    def test_independent_context_preserves_false_original_attribution(self):
        args = self.context_fixture()
        gate.check_context(*args)
        args[0]["source_attribution_verified"] = True
        with self.assertRaisesRegex(gate.ContractError, "promoted"):
            gate.check_context(*args)

    def test_context_other_sha_rejected(self):
        args = self.context_fixture()
        args[0]["source_sha"] = "a" * 40
        with self.assertRaisesRegex(gate.ContractError, "SHA"):
            gate.check_context(*args)

    def test_executed_binary_mutation_rejected(self):
        args = self.context_fixture()
        args[3].write_bytes(b"different")
        with self.assertRaisesRegex(gate.ContractError, "binary"):
            gate.check_context(*args)

    def test_retained_source_mutation_rejected(self):
        args = self.context_fixture()
        (args[1] / "source-before" / "Cargo.lock").write_bytes(b"changed")
        with self.assertRaisesRegex(gate.ContractError, "bytes"):
            gate.check_context(*args)

    def test_uncaptured_compiler_and_promoted_pending_capability_rejected(self):
        args = self.context_fixture()
        with self.assertRaisesRegex(gate.ContractError, "compiler"):
            gate.check_context(*args[:-1], "NOT_CAPTURED")
        args[0]["pending_capabilities"]["large_scene_object_projection_paging"]["status"] = "PASS"
        with self.assertRaisesRegex(gate.ContractError, "promoted"):
            gate.check_context(*args)

    def test_physical_wav_cas_and_truncation_controls(self):
        source = self.root / "reference-voice.wav"
        with wave.open(str(source), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(b"\0" * (300 * 8000 * 2))
        sha = gate.digest(source)
        blob = self.root / "target-store" / "blobs" / "sha256" / sha[:2] / sha
        blob.parent.mkdir(parents=True)
        shutil.copyfile(source, blob)
        project = {"assets": [{"content_sha256": sha}]}
        gate.verify_wav_and_cas(self.root, project, "S")
        with blob.open("ab") as stream:
            stream.write(b"x")
        with self.assertRaisesRegex(gate.ContractError, "CAS bytes"):
            gate.verify_wav_and_cas(self.root, project, "S")
        shutil.copyfile(source, blob)
        with source.open("r+b") as stream:
            stream.truncate(source.stat().st_size - 4)
        with self.assertRaisesRegex(gate.ContractError, "frames are truncated"):
            gate.verify_wav_and_cas(self.root, project, "S")


if __name__ == "__main__":
    unittest.main()
