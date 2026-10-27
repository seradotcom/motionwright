"""G0 only: scheduler, original-file bindings and source-edit admission.

These fixtures never dispatch a provider or supply physical acceptance evidence.
"""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import native_manim_editable_cycle as cycle
from native_manim_editable_outer import OuterDeadline, origin_record
from native_manim_camera_evidence import CameraCustody, CameraEvidenceError


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def uid(number):
    return f"00000000-0000-0000-0000-{number:012d}"


class UnknownOutcome(RuntimeError):
    outcome_known = False


class FakeEngine:
    """Instrument the existing custody ordering; encryption is outside G0."""
    def public_key(self, _recipient):
        return None

    def validate_context(self, _context):
        return None

    def new_json(self, path, value):
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream)

    def new_bytes(self, path, value):
        with path.open("xb") as stream:
            stream.write(value)

    def gather_snapshot(self, roots, preserve_partial=False):
        return b"test-snapshot", True

    def encrypt_snapshot(self, raw, _recipient, _context):
        return b"MWCISEAL1\n" + raw


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.root.chmod(0o700)
        self.context = {"source_sha": "a" * 40, "semwright_sha": "b" * 40,
                        "run_id": "17", "run_attempt": "1"}
        self.deadline = OuterDeadline(origin_record(self.context, 1, "synthetic-boot"), self.context,
                                      "synthetic-boot", origin_sha256="d" * 64,
                                      root=self.root / "clock", clock=lambda: 1)

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, suffix):
        return argparse.Namespace(
            evidence_root=self.root / ("evidence-" + suffix),
            sealed_cycle_root=self.root / ("sealed-" + suffix),
            sealed_originals=None, edited_evidence_root=None, sealed_edited=None,
            outer_deadline=self.deadline,
        )

    def validate(self, args):
        self.assertFalse(args.evidence_root.exists())
        self.assertFalse(args.sealed_originals.exists())
        return {"sha256": "c" * 64}, self.context

    def fake_phase(self, item, context):
        phase, index = context["phase"], len(context["history"])
        item.evidence_root.mkdir(mode=0o700)
        item.sealed_originals.write_bytes(b"MWCISEAL1\nfixture")
        native_path = item.evidence_root / cycle.EVIDENCE_NAME
        native = {"request_id": f"request-{index}",
                  "result": {"job_ref": "manim-" + f"{index + 1:032x}"}}
        write_json(native_path, native)
        value = {"phase": phase}
        write_json(item.evidence_root / "result.json", value)
        if index == 3:
            write_json(item.evidence_root / "editable-pixel-cycle.json", {"test_only": True})
        return {
            "current": self.context, "owner": self.root / "owner",
            "database": self.root / "owner" / "project.sqlite3",
            "evidence": item.evidence_root, "sealed_originals": item.sealed_originals,
            "native_path": native_path, "native": native, "phase_observation": value,
            "launch": {"session_sha256": str(index) * 64},
            "pixel_report": {"status": "PASS_REQUIRES_SEALED_CUSTODY"} if index == 3 else None,
        }

    def test_any_failed_or_unknown_original_phase_stops_before_every_later_phase(self):
        for stop in range(4):
            for failure in (ValueError("known failure"), UnknownOutcome("original unknown")):
                with self.subTest(stop=stop, failure=type(failure).__name__):
                    seen = []
                    def run(item, *, editable_cycle):
                        seen.append(editable_cycle["phase"])
                        if len(seen) == stop + 1:
                            raise failure
                        return self.fake_phase(item, editable_cycle)
                    with patch.object(cycle, "verify_phase", return_value={}):
                        with self.assertRaises(type(failure)) as raised:
                            cycle.run_source_cycle(
                                self.args(f"{stop}-{type(failure).__name__}"),
                                run_phase=run, validate_phase=self.validate,
                            )
                    self.assertIs(raised.exception, failure)
                    self.assertEqual(seen, list(cycle.PHASES[:stop + 1]))

    def test_four_explicit_once_scopes_are_ordered_and_keep_the_same_store(self):
        seen = []
        def run(item, *, editable_cycle):
            seen.append(editable_cycle["phase"])
            return self.fake_phase(item, editable_cycle)
        with patch.object(cycle, "verify_phase", return_value={}):
            result = cycle.run_source_cycle(
                self.args("complete"), run_phase=run, validate_phase=self.validate)
        self.assertEqual(seen, list(cycle.PHASES))
        self.assertEqual(result["native_effect_invocations"], 4)
        self.assertFalse(result["human_acceptance"])
        self.assertFalse(result["all_manim_features_accepted"])

    def test_reused_session_stops_before_third_native_phase(self):
        seen = []
        def run(item, *, editable_cycle):
            seen.append(editable_cycle["phase"])
            row = self.fake_phase(item, editable_cycle)
            row["launch"]["session_sha256"] = "d" * 64
            return row
        with patch.object(cycle, "verify_phase", return_value={}):
            with self.assertRaisesRegex(ValueError, "job_or_session_reused"):
                cycle.run_source_cycle(self.args("reused"), run_phase=run,
                                       validate_phase=self.validate)
        self.assertEqual(seen, list(cycle.PHASES[:2]))

    def test_preflight_recipient_drift_prevents_every_native_phase(self):
        calls = []
        def validate(args):
            calls.append(args)
            return {"sha256": str(len(calls)) * 64}, self.context
        with self.assertRaisesRegex(ValueError, "preflight_context_changed"):
            cycle.run_source_cycle(self.args("recipient"),
                                   run_phase=lambda *a, **k: self.fail("native effect"),
                                   validate_phase=validate)
        self.assertEqual(len(calls), 2)

    def test_native_phase_requires_ordered_cycle_before_any_process_or_files(self):
        import native_manim_e2e as wrapper
        args = argparse.Namespace(scenario="editable-source-cycle",
                                  evidence_root=self.root / "never-created",
                                  sealed_originals=self.root / "never-created.mwseal")
        with patch.object(wrapper.subprocess, "Popen", side_effect=AssertionError("native effect")):
            with self.assertRaisesRegex(ValueError, "ordered source-cycle"):
                wrapper._run_single(args)
        self.assertFalse(args.evidence_root.exists())
        self.assertFalse(args.sealed_originals.exists())

    def test_existing_cycle_scope_is_never_restarted(self):
        args = self.args("existing")
        args.evidence_root.mkdir(mode=0o700)
        def validate(_):
            return {}, self.context
        with self.assertRaises(FileExistsError):
            cycle.run_source_cycle(args, run_phase=lambda *a, **k: self.fail("native effect"),
                                   validate_phase=validate)

    def test_editable_custody_retains_original_return_across_later_failure(self):
        owner, evidence, seals = [self.root / name for name in ("owner", "evidence", "seals")]
        for path in (owner, evidence, seals):
            path.mkdir(mode=0o700)
        original = {"native_result": "original-value"}
        later = ValueError("pixel comparison failed")
        scope = CameraCustody(owner, evidence, seals / "one.mwseal", {"sha256": "c" * 64},
                              self.context, engine=FakeEngine(), profile="editable")
        with self.assertRaises(ValueError) as raised:
            with scope:
                self.assertIs(scope.once(lambda: original), original)
                raise later
        self.assertIs(raised.exception, later)
        self.assertIs(scope.native_result, original)
        terminal = json.loads((evidence / "original-terminal.json").read_text())
        self.assertEqual(terminal["schema"], "motionwright.manim.editable.original-terminal/1")
        self.assertEqual(terminal["native_result"], original)
        self.assertIsNone(terminal["native_error"])
        self.assertEqual(terminal["later_error"]["message"], str(later))
        self.assertTrue((evidence / "editable-custody.json").is_file())

    def test_unknown_custody_is_original_and_cannot_call_again(self):
        owner, evidence, seals = [self.root / name for name in ("owner", "evidence", "seals")]
        for path in (owner, evidence, seals):
            path.mkdir(mode=0o700)
        unknown = UnknownOutcome("original transport unknown")
        count = []
        scope = CameraCustody(owner, evidence, seals / "one.mwseal", {"sha256": "c" * 64},
                              self.context, engine=FakeEngine(), profile="editable")
        def original():
            count.append(1)
            raise unknown
        with self.assertRaises(UnknownOutcome) as raised:
            with scope:
                scope.once(original)
        self.assertIs(raised.exception, unknown)
        with self.assertRaises(CameraEvidenceError):
            scope.once(lambda: self.fail("UNKNOWN must not dispatch again"))
        self.assertEqual(count, [1])
        terminal = json.loads((evidence / "original-terminal.json").read_text())
        self.assertIsNone(terminal["native_result"])
        self.assertEqual(terminal["native_error"]["message"], str(unknown))
        self.assertFalse(terminal["automatic_retry"])


class SourceBindingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.bind = {"project_id": uid(1), "generation": uid(2), "revision": 7,
                     "scene_id": uid(3), "deliverable_id": uid(4)}
        self.seed = {**self.bind, "resource": "project:" + uid(1),
                     "primitive_count": 3, "frame_count": 24,
                     "font_sha256": cycle.FONT_SHA, "font_license_sha256": cycle.LICENSE_SHA,
                     "native_execution_performed": False}
        self.plan = {"binding": dict(self.bind), "nodes": [
            {"node_id": uid(5), "fill_hex": "#FFFFFF",
             "typography": {"text": cycle.ORIGINAL_TEXT}}]}
        self.native = {"request_id": "original-request", "original_start_receipt": {"id": 1},
                       "original_result_receipt": {"id": 2}}
        self.native_path = self.root / "native.json"
        write_json(self.native_path, self.native)
        self.bundle = self.root / "original.mwedit.zip"
        self.bundle.write_bytes(b"source-only unit fixture original bytes")
        descriptor = {"relative_path": self.bundle.name, "sha256": cycle.sha(self.bundle.read_bytes()),
                      "bytes": self.bundle.stat().st_size}
        self.original = {
            "native": self.native, "native_path": self.native_path,
            "source": {"descriptor": descriptor, "plan": self.plan, "bundle_path": self.bundle},
            "prepared": {"binding": self.bind, "edit_path": None},
            "seed": self.seed, "database": self.root / "project.sqlite3",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def edit_value(self):
        after = {**self.bind, "revision": 10}
        plan = copy.deepcopy(self.plan)
        plan["binding"] = after
        plan["nodes"][0]["typography"]["text"] = cycle.EDITED_TEXT
        value = {
            **self.seed, **after,
            "schema": "motionwright.manim.editable.source-cycle/1", "phase": "edit-import",
            "original_stamp": cycle.stamp(self.bind),
            "attached_stamp": {**cycle.stamp(self.bind), "revision": 9},
            "original_text": cycle.ORIGINAL_TEXT, "edited_text": cycle.EDITED_TEXT,
            "production_receipts_unchanged": True, "fresh_sqlite_readback": True,
            "provider_dispatch_performed": False, "native_bundle_reopen_verified": False,
            "human_acceptance": False, "original_source_asset_id": uid(6),
            "edited_source_asset_id": uid(7), "edited_node_id": uid(5),
            "original_source_sha256": self.original["source"]["descriptor"]["sha256"],
            "edited_source_sha256": "e" * 64, "prepared_plan": plan,
            "original_native": {
                "native_evidence_sha256": cycle.sha(self.native_path.read_bytes()),
                "request_id": self.native["request_id"],
                "original_start_receipt": self.native["original_start_receipt"],
                "original_result_receipt": self.native["original_result_receipt"],
                "source": self.original["source"]["descriptor"],
                "native_frame_content_verified": False,
            },
        }
        value["preview"] = {
            "expected": value["attached_stamp"],
            **{key: value[key] for key in ("original_source_asset_id", "edited_source_asset_id",
                                          "original_source_sha256", "edited_source_sha256")},
            "native_execution_performed": False, "imported_source_authority_used": False,
        }
        value["current_source_inspection"] = {
            "source_asset_id": value["edited_source_asset_id"],
            "source_sha256": value["edited_source_sha256"], "origin_binding": self.bind,
            "current_binding": after, "current_plan": plan, "native_execution_performed": False,
        }
        return value

    def test_only_the_typed_text_change_and_three_revision_advances_are_admitted(self):
        value = self.edit_value()
        self.assertEqual(cycle.checked_edit(value, self.original)["revision"], 10)
        forged = copy.deepcopy(value)
        forged["prepared_plan"]["nodes"][0]["fill_hex"] = "#000000"
        with self.assertRaisesRegex(ValueError, "only_saved_text_changed"):
            cycle.checked_edit(forged, self.original)
        forged = copy.deepcopy(value)
        forged["revision"] = 11
        with self.assertRaisesRegex(ValueError, "revision_order"):
            cycle.checked_edit(forged, self.original)

    def test_original_native_file_cannot_be_replaced_by_matching_declared_ids(self):
        value = self.edit_value()
        write_json(self.native_path, {**self.native, "request_id": "different-return"})
        with self.assertRaisesRegex(ValueError, "original_native_link"):
            cycle.checked_edit(value, self.original)

    def test_reopen_copies_original_bytes_to_closed_output_descriptor_without_seed_dispatch(self):
        evidence, output = self.root / "evidence", self.root / "output"
        evidence.mkdir()
        output.mkdir()
        prepared = cycle.new_phase_inputs(
            {"phase": "reopen-original", "history": [self.original], "current": {}},
            evidence, self.original["database"], output, {},
            capture=lambda *a, **k: self.fail("reopen must not seed or import"),
            source_binary=self.root / "native-example",
        )
        descriptor = prepared["input_descriptor"]
        self.assertEqual(set(descriptor), {"root", "relative_path", "sha256", "bytes"})
        self.assertEqual(descriptor["root"], "output")
        self.assertEqual((output / descriptor["relative_path"]).read_bytes(), self.bundle.read_bytes())
        self.assertEqual(json.loads(prepared["input_path"].read_text()), descriptor)

    def test_changed_original_export_is_rejected_before_input_copy(self):
        evidence, output = self.root / "evidence", self.root / "output"
        evidence.mkdir()
        output.mkdir()
        self.bundle.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "original_export_changed"):
            cycle.new_phase_inputs(
                {"phase": "reopen-original", "history": [self.original], "current": {}},
                evidence, self.original["database"], output, {},
                capture=lambda *a, **k: self.fail("effect"),
                source_binary=self.root / "native-example",
            )
        self.assertEqual(list(output.iterdir()), [])
        self.assertEqual(list(evidence.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
