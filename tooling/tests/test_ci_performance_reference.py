"""Synthetic gate controls, disposable SQLite and PCM; never a product G2."""
import copy
from contextlib import closing
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
        self.assertEqual(gate.PRODUCT_SHA, "b5bc214d281be6a7119dff059a89c834508aa4b8")

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
        with closing(sqlite3.connect(original)) as database, database:
            database.execute("CREATE TABLE projects(id TEXT,generation TEXT,revision INTEGER,title TEXT,document_json TEXT)")
            database.execute("INSERT INTO projects VALUES(?,?,?,?,?)", (*expected.values(), json.dumps(expected)))
        before = gate.digest(original)
        gate.verify_sqlite(original, expected, self.root)
        self.assertEqual(gate.digest(original), before)
        with closing(sqlite3.connect(original)) as database, database:
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



def locked_project_transition():
    before = {
        "id": "project-fixture", "revision": 11, "updated_at": "before",
        "active_branch": "active", "title": "Preserved synthetic project",
        "branches": [
            {"id": "active", "name": "Main", "head_revision": 11, "base_revision": 0},
            {"id": "other", "name": "Alternative", "head_revision": 7, "base_revision": 4},
        ],
        "locks": [
            {"id": "timing", "kind": "timing", "resource": "scene:fixture", "note": "Retained"},
            {"id": "style", "kind": "style", "resource": "project:project-fixture", "note": "Retained"},
        ],
    }
    after = copy.deepcopy(before)
    after["revision"] = 12
    after["updated_at"] = "after"
    after["branches"][0]["head_revision"] = 12
    after["locks"].append({"id": "content", "kind": "content",
                           "resource": "project:project-fixture", "note": "Controlled fixture"})
    return before, after


def source_test_output(example):
    prefix = "reference_profiles::" if example == "acceptance_bench" else ""
    names = [prefix + "support::fixture_tests::" + name for name in gate.SOURCE_TESTS]
    return ("running 10 tests\n" + "".join(f"test {name} ... ok\n" for name in names)
            + "\ntest result: ok. 10 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n")


class CausalRevisionAndSourceControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="performance-causal-control-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_valid_content_lock_case_path_accepts_active_head(self):
        before, after = locked_project_transition()
        prepared = copy.deepcopy(before)
        prepared["revision"] = 1
        prepared["branches"][0]["head_revision"] = 1
        prepared["locks"] = before["locks"][:1]
        folder = self.root / "case"
        case = negative("uneditable-reopen")
        case.update(
            required_shape={"scenes": 12, "canvas_nodes": 300, "duration_seconds": 300,
                            "beats": 80, "deliverable_variants": None,
                            "width": 1920, "height": 1080, "frame_rate": "30/1"},
            dataset_family="normative_reference_profile", requested_scene_count=12, scene_count=12,
            artifact_directory=str(folder), revision_start=1, revision_delta=10, revision_end=11,
            first_editable_scene_preserved_after_revision_ten=True,
            operations=[{"name": name, "previous_revision": index + 1, "revision": index + 2,
                         "micros": 1, "request_id": f"reference-operation-{index}"}
                        for index, name in enumerate(gate.OPERATIONS)],
            negative_injection={"name": "negative-content-lock", "previous_revision": 11,
                                "revision": 12, "micros": 1, "request_id": "reference-content-lock"})
        documents = {
            "prepared-project.json": prepared, "genesis-project.json": prepared,
            "identities-before-edits.json": {"identities": "preserved"},
            "project-after-ten.json": before, "project-after-reopen.json": after,
        }
        # Isolate the actual case-validation route; these G0 fixtures do not claim physical custody.
        with mock.patch.object(gate, "contained", return_value=folder), \
             mock.patch.object(gate, "read_json", side_effect=lambda path: documents[path.name]), \
             mock.patch.object(gate, "project_shape", return_value=shape()), \
             mock.patch.object(gate, "identities", return_value={"identities": "preserved"}), \
             mock.patch.object(gate, "verify_sqlite") as sqlite_check, \
             mock.patch.object(gate, "verify_wav_and_cas"), \
             mock.patch.object(gate, "check_history"):
            self.assertEqual(gate.check_case(case, folder, True, "uneditable-reopen", 1, self.root),
                             {"scope": "causal-negative", "cause": "uneditable-reopen"})
            self.assertEqual(sqlite_check.call_count, 2)

    def test_only_active_head_advances_without_mutating_inputs(self):
        before, after = locked_project_transition()
        originals = copy.deepcopy((before, after))
        gate.check_content_lock_transition(before, after, 12)
        self.assertEqual((before, after), originals)

    def test_old_and_new_heads_must_match_exact_revision_chain(self):
        for old_head, new_head in ((10, 12), (11, 11), (11, 13), (True, 12), (11, True)):
            before, after = locked_project_transition()
            before["branches"][0]["head_revision"] = old_head
            after["branches"][0]["head_revision"] = new_head
            with self.subTest(old_head=old_head, new_head=new_head), self.assertRaises(gate.ContractError):
                gate.check_content_lock_transition(before, after, 12)

    def test_active_branch_must_remain_unique_present_and_unchanged(self):
        for mode in ("missing", "duplicate", "changed"):
            before, after = locked_project_transition()
            if mode == "missing":
                before["active_branch"] = after["active_branch"] = "absent"
            elif mode == "duplicate":
                before["branches"].append(copy.deepcopy(before["branches"][0]))
                after["branches"].append(copy.deepcopy(after["branches"][0]))
            else:
                after["active_branch"] = "other"
            with self.subTest(mode=mode), self.assertRaises(gate.ContractError):
                gate.check_content_lock_transition(before, after, 12)

    def test_branch_roster_identity_and_order_changes_are_rejected(self):
        for mode in ("reverse", "append", "remove", "identity"):
            before, after = locked_project_transition()
            if mode == "reverse":
                after["branches"].reverse()
            elif mode == "append":
                after["branches"].append({"id": "new", "name": "New", "head_revision": 0, "base_revision": 0})
            elif mode == "remove":
                after["branches"].pop()
            else:
                after["branches"][1]["id"] = "replacement"
            with self.subTest(mode=mode), self.assertRaises(gate.ContractError):
                gate.check_content_lock_transition(before, after, 12)

    def test_inactive_head_and_other_branch_fields_are_immutable(self):
        for index, key, value in ((1, "head_revision", 8), (0, "name", "Changed"),
                                  (1, "name", "Changed"), (0, "base_revision", 1),
                                  (1, "unexpected", True)):
            before, after = locked_project_transition()
            after["branches"][index][key] = value
            with self.subTest(index=index, key=key), self.assertRaises(gate.ContractError):
                gate.check_content_lock_transition(before, after, 12)

    def test_project_key_set_and_unrelated_content_are_immutable(self):
        for mode in ("add", "remove", "change"):
            before, after = locked_project_transition()
            if mode == "add":
                after["extra"] = True
            elif mode == "remove":
                del after["title"]
            else:
                after["title"] = "Changed"
            with self.subTest(mode=mode), self.assertRaises(gate.ContractError):
                gate.check_content_lock_transition(before, after, 12)

    def test_existing_locks_and_one_new_project_content_lock_are_required(self):
        for mode in ("remove", "reorder", "duplicate", "kind", "resource"):
            before, after = locked_project_transition()
            if mode == "remove":
                after["locks"].pop(0)
            elif mode == "reorder":
                after["locks"][0], after["locks"][1] = after["locks"][1], after["locks"][0]
            elif mode == "duplicate":
                after["locks"][-1]["id"] = before["locks"][0]["id"]
            elif mode == "kind":
                after["locks"][-1]["kind"] = "style"
            else:
                after["locks"][-1]["resource"] = "project:other"
            with self.subTest(mode=mode), self.assertRaises(gate.ContractError):
                gate.check_content_lock_transition(before, after, 12)

    def test_project_revision_must_match_injection_without_gap(self):
        for before_revision, after_revision, expected in ((10, 12, 12), (11, 11, 12),
                                                         (11, 13, 13), (11, 12, 13)):
            before, after = locked_project_transition()
            before["revision"], after["revision"] = before_revision, after_revision
            with self.subTest(revisions=(before_revision, after_revision, expected)), self.assertRaises(gate.ContractError):
                gate.check_content_lock_transition(before, after, expected)

    def test_selected_source_commands_are_locked_unfiltered_and_strict(self):
        commands = gate.source_check_commands()
        self.assertEqual([name for name, _ in commands], ["acceptance_bench", "responsiveness_bench", "clippy"])
        self.assertEqual(len(gate.SOURCE_TESTS), 10)
        self.assertEqual(len(set(gate.SOURCE_TESTS)), 10)
        for example, argv in commands[:2]:
            self.assertEqual(argv, ["cargo", "test", "--locked", "--release", "-p", "motionwright-service",
                                    "--example", example, "--", "--nocapture", "--test-threads=1"])
            self.assertEqual(gate.check_source_test_roster(source_test_output(example), example)["actual_tests"], 10)
        self.assertEqual(commands[2][1], ["cargo", "clippy", "--locked", "--release", "-p", "motionwright-service",
                                         "--example", "acceptance_bench", "--example", "responsiveness_bench",
                                         "--", "-D", "warnings"])

    def test_source_roster_rejects_ignored_filtered_or_failed_results(self):
        stdout = source_test_output("responsiveness_bench")
        for changed in (stdout.replace("... ok", "... ignored", 1),
                        stdout.replace("0 ignored", "1 ignored"),
                        stdout.replace("0 filtered out", "1 filtered out"),
                        stdout.replace("0 failed", "1 failed")):
            with self.subTest(changed=changed[-150:]), self.assertRaises(gate.ContractError):
                gate.check_source_test_roster(changed, "responsiveness_bench")

    def test_source_roster_rejects_missing_duplicate_or_foreign_tests(self):
        stdout = source_test_output("responsiveness_bench")
        line = next(line for line in stdout.splitlines(True) if line.startswith("test support::"))
        for changed in (stdout.replace(line, "", 1), stdout + line,
                        stdout.replace("support::fixture_tests::", "foreign::", 1)):
            with self.subTest(changed=changed[-150:]), self.assertRaises(gate.ContractError):
                gate.check_source_test_roster(changed, "responsiveness_bench")

    def test_unknown_source_check_stops_without_retry_or_remaining_execution(self):
        with mock.patch.object(gate, "run_process", return_value={"status": "UNKNOWN", "returncode": None}) as runner:
            result = gate.run_selected_source_checks(self.root, self.root, {}, gate.time.monotonic() + 60)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(runner.call_count, 1)
        self.assertEqual([item["status"] for item in result["commands"]], ["UNKNOWN", "NOT_RUN", "NOT_RUN"])
        self.assertEqual(result["actual_test_invocations"], 0)

    def test_strict_clippy_failure_prevents_source_admission(self):
        def run(argv, directory, *_):
            directory.mkdir()
            label = directory.name
            (directory / "stdout.log").write_text(
                source_test_output(label) if label != "clippy" else "", encoding="utf-8")
            return {"status": "COMPLETED", "returncode": 1 if label == "clippy" else 0}
        with mock.patch.object(gate, "run_process", side_effect=run):
            result = gate.run_selected_source_checks(self.root, self.root, {}, gate.time.monotonic() + 60)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["actual_test_invocations"], 20)
        self.assertEqual(result["commands"][-1]["status"], "FAIL")

    def test_complete_selected_source_admission_keeps_twenty_invocations_separate(self):
        def run(argv, directory, *_):
            directory.mkdir()
            label = directory.name
            (directory / "stdout.log").write_text(
                source_test_output(label) if label != "clippy" else "", encoding="utf-8")
            return {"status": "COMPLETED", "returncode": 0}
        with mock.patch.object(gate, "run_process", side_effect=run) as runner:
            result = gate.run_selected_source_checks(self.root, self.root, {}, gate.time.monotonic() + 60)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["expected_unique_contracts"], 10)
        self.assertEqual(result["actual_test_invocations"], 20)
        self.assertEqual(runner.call_count, 3)
        self.assertEqual(sum(9 if plan[2] is None else 1 for plan in gate.PLAN), 14)


if __name__ == "__main__":
    unittest.main()
