"""Focused controls for the explicitly new four-phase runtime comparison."""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import ci_private_evidence as engine
import native_manim_resume_transfer as transfer
import native_manim_recovery_gate as gate
import native_manim_resume_resources as resources

CURRENT = {"source_sha": "c" * 40, "semwright_sha": transfer.SDK, "run_id": "12345", "run_attempt": "1"}
NOW = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)


class RetainedRuntimeSelectionTests(unittest.TestCase):
    def test_cache_admission_requires_the_original_sdk_exit_peak_and_nonroot_entry(self):
        raw = (Path(__file__).with_name("fixtures") / "manim-sdk-guard-42365799758.json").read_bytes()
        self.assertEqual(engine.sha(raw), "ea350336388b893af0c5b73abec9b844c3d7308d75e4e553c4e456128ad06af9")
        value = json.loads(raw)
        current, origin = value["context"], value["origin"]
        resources.check_cache_sdk_guard(value, current, origin)
        changes = (
            (("original_child_exit",), None), (("original_child_exit",), True), (("original_child_exit",), 1),
            (("original_child_exit_known",), False), (("cgroup_quiescent",), False),
            (("resource_after", "peak_bytes"), 1611550720), (("resource_after", "oom_kill"), 1),
            (("resource_after", "limit_bytes"), resources.LIMIT),
            (("minimum_physical_available_bytes",), resources.RESERVE - 1),
            (("original_cgroup_entry", "uid"), 0),
            (("original_cgroup_entry", "privileges_dropped_before_exec"), False),
            (("original_cgroup_entry", "controller_pid"), 1), (("context", "source_sha"), "0" * 40),
        )
        for keys, changed in changes:
            altered, owner = copy.deepcopy(value), None
            owner = altered
            for key in keys[:-1]:
                owner = owner[key]
            owner[keys[-1]] = changed
            with self.subTest(keys=keys), self.assertRaisesRegex(ValueError, "recovery_cache"):
                resources.check_cache_sdk_guard(altered, current, origin)

    def test_new_cgroup_headroom_is_required_without_changing_the_old_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = {"memory.current": "1000", "memory.peak": "1000",
                "memory.max": str(1472 * 1024 * 1024), "memory.high": str(1408 * 1024 * 1024),
                "memory.events": "oom 0\noom_kill 0\n", "memory.swap.max": "0", "pids.max": "256"}
            for name, value in values.items():
                (root / name).write_text(value)
            with patch.dict(os.environ, MANIM_NATIVE_SCENARIO=transfer.RECOVERY_SCENARIO):
                observed = resources.observe(root)
                self.assertEqual(observed["limit_bytes"], 1472 * 1024 * 1024)
                self.assertEqual(observed["high_bytes"], 1408 * 1024 * 1024)
                self.assertEqual(resources.policy_fields()["physical_peak_budget_bytes"], 1536 * 1024 * 1024)
                for name, changed in (("memory.max", str(1536 * 1024 * 1024)), ("memory.high", "max"),
                                      ("memory.high", str(1472 * 1024 * 1024))):
                    (root / name).write_text(changed)
                    try:
                        with self.subTest(name=name, changed=changed), self.assertRaises(ValueError):
                            resources.observe(root)
                    finally:
                        (root / name).write_text(values[name])
            with patch.dict(os.environ, MANIM_NATIVE_SCENARIO=transfer.RESUME_SCENARIO):
                (root / "memory.max").write_text(str(1536 * 1024 * 1024))
                (root / "memory.high").unlink()
                self.assertEqual(resources.policy_fields(), {})
                self.assertEqual(resources.observe(root)["limit_bytes"], 1536 * 1024 * 1024)

    def test_actual_sdk_peak_failure_remains_failed_after_the_headroom_fix(self):
        # Original authenticated run42361964881: Cargo exit0 and no OOM.
        before = {"peak_bytes": 1610612736, "limit_bytes": 1610612736, "oom": 0, "oom_kill": 0}
        after = {"peak_bytes": 1611550720, "limit_bytes": 1610612736, "oom": 0, "oom_kill": 0}
        status, reason = resources.classify_completion(0, None, None, before, after, True, 14748495872)
        self.assertEqual(status, "RESOURCE_OR_COMMAND_FAILED_NO_RETRY")
        self.assertEqual(reason, "OBSERVED_PEAK_EXCEEDS_PHYSICAL_BUDGET")
        headroom_before = {**before, "limit_bytes": 1472 * 1024 * 1024, "high_bytes": 1408 * 1024 * 1024}
        within = {**headroom_before, "peak_bytes": 1536 * 1024 * 1024}
        self.assertEqual(resources.classify_completion(0, None, None, headroom_before, within, True, 512 * 1024 * 1024),
                         ("EXITED_WITHIN_LIMITS", None))
        for code, error, quiescent, reserve, observation in (
            (None, None, True, 14748495872, within), (True, None, True, 14748495872, within),
            (0, {"type": "UnknownObservation"}, True, 14748495872, within),
            (0, None, False, 14748495872, within), (0, None, True, 512 * 1024 * 1024 - 1, within),
            (0, None, True, 14748495872, {**within, "oom_kill": 1}),
        ):
            self.assertEqual(resources.classify_completion(code, None, error, headroom_before, observation, quiescent, reserve)[0],
                             "RESOURCE_OR_COMMAND_FAILED_NO_RETRY")

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

    def test_independent_reader_binds_restore_records_to_original_bytes(self):
        from native_manim_recovery_lineage import checked_reuse_originals
        def row(raw, inline=False):
            result = {"bytes": len(raw), "sha256": engine.sha(raw)}
            if inline:
                result["original_utf8"] = raw.decode()
            return result
        # This is a G0 byte-join fixture, with no Native/source result claimed.
        originals, reuse = {}, {"commands": {}}
        for label in ("inspect-python", "install-wheels", "observe-native"):
            reuse["commands"][label] = {}
            for kind in ("intent", "spawn", "terminal", "stdout", "stderr"):
                raw = (engine.json_bytes({"fixture": label + ":" + kind})
                       if kind not in ("stdout", "stderr") or (label, kind) == ("inspect-python", "stdout") else b"")
                originals["runtime-reuse-" + label + "-" + kind] = raw
                reuse["commands"][label][kind] = row(raw, kind not in ("stdout", "stderr"))
        reuse["python_inspection_original"] = row(originals["runtime-reuse-inspect-python-stdout"], True)
        installed = engine.json_bytes({"g0_roster_fixture": True})
        originals["runtime-installed-files-observed"] = installed
        reuse["installed_manifest"] = row(installed)
        native = engine.json_bytes({"g0_observation_fixture": True})
        originals["runtime-reuse-native-observation"] = native
        reuse["native_observation_original"] = row(native, True)
        download = engine.json_bytes({"pid": 42, "streams": {"stderr": {"written_bytes": 0, "sha256": engine.sha(b"")}}})
        originals["runtime-reuse-download-terminal"] = download
        reuse["download_terminal"] = row(download, True)
        reuse["origin_artifact"] = {"artifact_id": 321, "artifact_zip_bytes": 10240}
        originals["runtime-reuse-download-intent"] = engine.json_bytes({
            "command": ["/usr/bin/gh", "api", "--hostname", "github.com", "--method", "GET",
                "/repos/" + transfer.REPOSITORY + "/actions/artifacts/321/zip"],
            "attempts_authorized": 1, "automatic_retry": False, "maximum_stdout_bytes": 10240})
        originals["runtime-reuse-download-spawn"] = engine.json_bytes({"pid": 42, "automatic_retry": False})
        originals["runtime-reuse-download-stderr"] = b""
        checked_reuse_originals(reuse, originals)
        for key in ("runtime-reuse-inspect-python-stdout", "runtime-reuse-install-wheels-stdout", "runtime-reuse-observe-native-terminal",
                    "runtime-reuse-native-observation", "runtime-installed-files-observed",
                    "runtime-reuse-download-terminal", "runtime-reuse-download-stderr"):
            with self.subTest(changed=key), self.assertRaises(ValueError):
                checked_reuse_originals(reuse, {**originals, key: b"changed"})

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
        raw = ("running 2 tests\n" + "\n".join("test " + name + " ... ok" for name in gate.REFERENCE)
               + "\ntest result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 44 filtered out; finished in 0.01s\n").encode()
        self.assertEqual(gate.parse_reference_tests(raw)["selected_ids"], list(gate.REFERENCE))
        for changed in (raw.replace(b"2 passed", b"1 passed"), raw.replace(b"0 ignored", b"1 ignored"),
                        raw.replace(gate.REFERENCE[0].encode(), b"unselected_case")):
            with self.assertRaises(ValueError):
                gate.parse_reference_tests(changed)

    def test_reference_nocapture_form_rejects_unfinished_duplicate_or_failed_blocks(self):
        # A parser shape control only: these bytes do not claim two executed Rust tests.
        raw = ("running 2 tests\n" + "\n".join("test " + name + ' ... {"g0_parser_fixture":true}\nok'
               for name in gate.REFERENCE) + "\ntest result: ok. 2 passed; 0 failed; 0 ignored; "
               "0 measured; 37 filtered out; finished in 0.01s\n").encode()
        self.assertEqual(gate.parse_reference_tests(raw)["selected_ids"], list(gate.REFERENCE))
        for changed in (
            raw.replace(b"\nok\n", b"\n", 1), raw.replace(b"\nok\n", b"\nFAILED\n", 1),
            raw.replace(b"\nok\n", b"\nignored\n", 1), raw + b"ok\n", raw + raw,
            raw.replace(gate.REFERENCE[0].encode(), gate.REFERENCE[1].encode(), 1),
            raw.replace(gate.REFERENCE[0].encode(), b"unselected_case", 1),
            raw.replace(b"running 2 tests", b"", 1), raw.replace(b"2 passed", b"1 passed", 1),
            raw.replace(b"0 ignored", b"1 ignored", 1), raw.rsplit(b"\ntest result:", 1)[0],
        ):
            with self.subTest(sha=engine.sha(changed)), self.assertRaisesRegex(ValueError, "two_actual"):
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
        self.assertIn("format('source-contracts-manim-sdk-{0}', steps.pin.outputs.sha)", job)
        self.assertIn("|| 'source-contracts'", job)
        self.assertIn("          RUSTUP_HOME: ''", job)
        self.assertIn("cmd-format: 'env RUSTUP_HOME=${{ runner.temp }}/manim-native-rustup-${{ github.run_id }}-${{ github.run_attempt }} {0}'", job)
        self.assertIn("cache-on-failure: 'false'", job)
        self.assertLess(job.index("native_manim_resume_resources.py admit-cache-save"),
                        job.index("native_manim_recovery_gate.py test"))
        self.assertIn("      actions: read", job)
        self.assertLess(job.index("native_manim_frozen_runtime_restore.py restore"), job.index("Build exact Semwright Broker"))
        self.assertLess(job.index("native_manim_recovery_gate.py record-reuse"), job.index("native_manim_recovery_gate.py test"))
        self.assertLess(job.index("native_manim_recovery_gate.py test"), job.index("native_manim_resume_transfer.py create-recipient"))
        self.assertNotIn("frozen_runtime_upload", job)
        frozen = job.split("      - name: Restore the retained comparison runtime without source builds\n", 1)[1].split("      - name: Admit reuse of the original runtime artifact", 1)[0]
        self.assertLess(frozen.index("unset GH_TOKEN"), frozen.index('PYTHONPATH="$runtime/site-packages"'))
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
