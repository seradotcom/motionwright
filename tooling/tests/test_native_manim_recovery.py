"""Focused controls for the explicitly new four-phase runtime comparison."""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import ci_private_evidence as engine
import native_manim_resume_transfer as transfer
import native_manim_recovery_gate as gate

CURRENT = {"source_sha": "c" * 40, "semwright_sha": transfer.SDK, "run_id": "12345", "run_attempt": "1"}
NOW = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)


class RetainedRuntimeSelectionTests(unittest.TestCase):
    def test_four_phase_manifest_is_explicit_and_does_not_change_old_two_phase_contract(self):
        with patch.dict(os.environ, MANIM_NATIVE_SCENARIO=transfer.RESUME_SCENARIO):
            old = transfer.manifest(CURRENT, "a" * 64)
            old_files = transfer.selected_public_gate_files()
        with patch.dict(os.environ, MANIM_NATIVE_SCENARIO=transfer.RECOVERY_SCENARIO):
            new = transfer.manifest(CURRENT, "a" * 64)
            self.assertEqual(transfer.selected_public_gate_files(), gate.PUBLIC_GATE_FILES)
        self.assertEqual(old["remaining_native_effect_invocations"], 2)
        self.assertEqual(old["remaining_phases"], ["edited", "reopen-edited"])
        self.assertNotIn("scenario", old)
        self.assertEqual(len(old_files), 3)
        self.assertEqual(new["remaining_native_effect_invocations"], 4)
        self.assertEqual(new["additional_reference_renders"], 2)
        self.assertEqual(new["project_changes_authorized"], 0)
        self.assertFalse(new["equivalence_to_old_runtime"])
        self.assertEqual(new["files"], old["files"])
        self.assertEqual(new["source_recovery"], old["source_recovery"])
        with patch.dict(os.environ, MANIM_NATIVE_SCENARIO="recovery-runtime-cycle-other"):
            with self.assertRaises(ValueError):
                transfer.manifest(CURRENT, "a" * 64)

    def test_recipient_cannot_be_reinterpreted_between_two_and_four_phase_scopes(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import base64
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        public = {"spki_der_base64": base64.b64encode(der).decode(), "sha256": engine.sha(der)}
        values = {}
        for selected in (transfer.RESUME_SCENARIO, transfer.RECOVERY_SCENARIO):
            with patch.dict(os.environ, MANIM_NATIVE_SCENARIO=selected):
                values[selected] = transfer.recipient_record(public, CURRENT, "42", NOW.isoformat(),
                    (NOW + timedelta(seconds=600)).isoformat(), "b" * 64)
                self.assertEqual(transfer.validate_recipient(values[selected], CURRENT, "42", "b" * 64, clock=NOW), public)
        for selected, foreign in ((transfer.RESUME_SCENARIO, transfer.RECOVERY_SCENARIO),
                                  (transfer.RECOVERY_SCENARIO, transfer.RESUME_SCENARIO)):
            with patch.dict(os.environ, MANIM_NATIVE_SCENARIO=selected):
                with self.assertRaises(ValueError):
                    transfer.validate_recipient(values[foreign], CURRENT, "42", "b" * 64, clock=NOW)

    def test_runtime_tar_binding_rejects_valid_but_different_published_archive(self):
        original = {"bytes": 10240, "sha256": "a" * 64}
        gate.checked_archive_binding({"archive": original}, {"tar": dict(original)})
        for changed in ({**original, "bytes": 20480}, {**original, "sha256": "b" * 64}):
            with self.assertRaisesRegex(ValueError, "retained_tar_must_equal_freeze_archive"):
                gate.checked_archive_binding({"archive": original}, {"tar": changed})

    def test_artifact_readback_requires_original_api_bytes_id_digest_and_context(self):
        api = {"id": 123, "name": gate.artifact_name(CURRENT), "expired": False,
               "size_in_bytes": 4096, "digest": "sha256:" + "d" * 64,
               "workflow_run": {"id": 12345, "head_sha": CURRENT["source_sha"]}}
        raw = json.dumps(api).encode()
        value = {"schema": "motionwright.manim.recovery-runtime.artifact-readback/1", "context": CURRENT,
                 "runtime_receipt_sha256": "e" * 64, "native_effect_invocations": 0, "automatic_retry": False,
                 "api": api, "api_original_utf8": raw.decode(), "api_original_sha256": engine.sha(raw),
                 "upload_action_artifact_id": 123, "upload_action_artifact_sha256": "d" * 64,
                 "local_tar": {"bytes": 10240, "sha256": "f" * 64}}
        self.assertEqual(gate.validate_artifact(value, CURRENT, "e" * 64)["artifact_id"], 123)
        for field, changed in (("api_original_sha256", "0" * 64), ("upload_action_artifact_id", 124),
                               ("upload_action_artifact_sha256", "0" * 64)):
            with self.assertRaises(ValueError):
                gate.validate_artifact({**value, field: changed}, CURRENT, "e" * 64)
        changed = copy.deepcopy(value)
        changed["api"]["workflow_run"]["head_sha"] = "0" * 40
        with self.assertRaises(ValueError):
            gate.validate_artifact(changed, CURRENT, "e" * 64)

    def test_two_reference_tests_require_the_actual_named_footer_and_no_ignored_case(self):
        raw = ("\n".join("test " + name + " ... ok" for name in gate.REFERENCE)
               + "\ntest result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 44 filtered out; finished in 0.01s\n").encode()
        self.assertEqual(gate.parse_reference_tests(raw)["selected_ids"], list(gate.REFERENCE))
        for changed in (raw.replace(b"2 passed", b"1 passed"), raw.replace(b"0 ignored", b"1 ignored"),
                        raw.replace(gate.REFERENCE[0].encode(), b"unselected_case")):
            with self.assertRaises(ValueError):
                gate.parse_reference_tests(changed)

    def test_native_adapter_refuses_an_unowned_new_cycle_before_any_native_input(self):
        import native_manim_e2e as native
        with self.assertRaisesRegex(ValueError, "explicit four-phase owner"):
            native._run_single(argparse.Namespace(scenario=transfer.RECOVERY_SCENARIO))
        with self.assertRaisesRegex(ValueError, "explicit four-phase owner"):
            native._run_single(argparse.Namespace(scenario=transfer.RESUME_SCENARIO), recovery_cycle={})

    def test_workflow_selects_new_cycle_without_running_old_native_or_source_rosters(self):
        text = (ROOT / ".github/workflows/native-manim-e2e.yml").read_text(encoding="utf-8")
        job = text.split("  native-manim-resume:\n", 1)[1].split("  native-manim-roundtrip:\n", 1)[0]
        self.assertIn("inputs.scenario != 'recovery-runtime-cycle'", text.split("  source-recovery-tool:\n", 1)[0])
        self.assertIn("inputs.scenario == 'recovery-runtime-cycle'", job)
        self.assertIn("MW_MANIM_RESUME_EXPECTED_SHA: ${{ inputs.resume_expected_sha }}", job)
        self.assertIn("CARGO_BUILD_JOBS: '1'", job)
        self.assertIn("shared-key: source-contracts", job)
        self.assertIn("      actions: read", job)
        self.assertLess(job.index("native_manim_frozen_runtime.py build"), job.index("Build exact Semwright Broker"))
        self.assertLess(job.index("native_manim_recovery_gate.py record-artifact"), job.index("native_manim_recovery_gate.py test"))
        self.assertLess(job.index("native_manim_recovery_gate.py test"), job.index("native_manim_resume_transfer.py create-recipient"))
        frozen = job.split("      - name: Build the single retained comparison runtime from observed distribution hashes\n", 1)[1].split("      - name: Admit retention of the frozen runtime", 1)[0]
        self.assertNotIn("pyav-wheel.json", frozen)
        self.assertIn('private / "wheel-lock.json"', frozen)
        self.assertIn('row.get("name") == "av"', frozen)
        self.assertIn('selected_wheels[0].get("version") != "15.0.0"', frozen)
        legacy = job.split("      - name: Execute only edited and reopen-edited through the official Native Host\n", 1)[1]
        self.assertTrue(legacy.startswith("        if: inputs.scenario == 'editable-source-resume'\n"))
        for name in ("original-reference", "reopen-original-reference", "edited", "reopen-edited"):
            self.assertIn("/manim-recovery-sealed-${{ github.sha }}/" + name + ".mwseal", job)


if __name__ == "__main__":
    unittest.main()
