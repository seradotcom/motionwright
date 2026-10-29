"""Synthetic source/custody controls using the existing real mwseal API.

No native process, user project, runner, upload or physical acceptance is used.
The fresh RSA private key exists only in this unit-test process for readback.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import types
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import ci_private_evidence as crypto


def workflow_wiring():
    source = (ROOT / ".github/workflows/native-blender-e2e.yml").read_text()
    marker = '        cat > "$RUNNER_TEMP/blender-static-custody.py" <<\'PY\'\n'
    if source.count(marker) != 1:
        raise AssertionError("exact static workflow wiring is required")
    body = source.split(marker, 1)[1].split("        PY\n", 1)[0]
    module = types.ModuleType("_static_custody_workflow_unit")
    exec(compile(textwrap.dedent(body), "native-blender-e2e.yml:static-custody", "exec"),
         module.__dict__)
    return module


class StaticCustodyWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wiring = workflow_wiring()
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        der = cls.key.public_key().public_bytes(serialization.Encoding.DER,
                                               serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.recipient = {"spki_der_base64": base64.b64encode(der).decode(),
                         "sha256": hashlib.sha256(der).hexdigest()}
        cls.context = {"source_sha": "1" * 40, "semwright_sha": "2" * 40,
                       "run_id": "123", "run_attempt": "1"}

    def paths(self, root, *, evidence=True):
        control = root / "control"
        control.mkdir()
        (control / "deadline.original.json").write_bytes(b'{"synthetic_control_only":true}\n')
        (control / "wiring.original.py").write_bytes(b"# synthetic selected wiring; never executed\n")
        crypto.new_json(control / "preflight.json", {"context": self.context,
                        "source": {"synthetic": "source-bound fixture"},
                        "recipient_sha256": self.recipient["sha256"]})
        native = root / "verification/native-blender-e2e" / self.context["source_sha"]
        if evidence:
            native.mkdir(parents=True)
        return control, native, root / "verification/native-blender-static-staged"

    def seal(self, root, control, stage, *, outcome="success", stable=True):
        return self.wiring.seal_static(root, control, stage, self.context,
                                       self.recipient, crypto, outcome, stable)

    def decode(self, stage):
        raw = (stage / "private-evidence.mwseal").read_bytes()
        plaintext, context = crypto.decrypt_snapshot(raw, self.key)
        self.assertEqual(context, self.context)
        with tarfile.open(fileobj=io.BytesIO(plaintext), mode="r:") as archive:
            return {row.name: archive.extractfile(row).read() for row in archive.getmembers()}

    def test_exact_selected_sources_and_media_are_recoverable_without_owner_tickets(self):
        with tempfile.TemporaryDirectory(prefix="static-seal-exact-") as directory:
            root = Path(directory)
            control, evidence, stage = self.paths(root)
            originals = {"static-seed-originals/motionwright.sqlite3": b"original DB\x00\xff",
                         "static-realize-originals/motionwright.sqlite3-wal": b"original WAL",
                         "motionwright-contribution.glb": b"original rejected or admitted GLB",
                         "static-realize-originals/stderr.original": b"raw stderr\n"}
            for name, data in originals.items():
                path = evidence / name
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(data)
            owner = root / "unselected-owner"
            owner.mkdir()
            (owner / "motionwright.session").write_bytes(b"must never enter selected ciphertext")
            result = self.seal(root, control, stage)
            recovered = self.decode(stage)
            self.assertTrue(result["ready"])
            self.assertFalse(result["formal_acceptance"])
            for name, data in originals.items():
                self.assertEqual(recovered["evidence/" + name], data)
                self.assertEqual((evidence / name).read_bytes(), data)
            self.assertFalse(any("session" in name or "owner" in name for name in recovered))
            self.assertTrue(json.loads(recovered["snapshot-manifest.json"])["selected_originals_complete"])

    def test_unknown_partial_streams_are_kept_exactly_and_never_become_ready(self):
        with tempfile.TemporaryDirectory(prefix="static-seal-unknown-") as directory:
            root = Path(directory)
            control, evidence, stage = self.paths(root)
            unknown = evidence / "static-realize-unconfirmed"
            unknown.mkdir()
            receipt = b'{"native_operation_result":"UNKNOWN","native_process_terminal_observed":false}\n'
            (unknown / "receipt.json").write_bytes(receipt)
            (unknown / "stdout.partial.original").write_bytes(b"partial\x00original\xff")
            result = self.seal(root, control, stage)
            recovered = self.decode(stage)
            self.assertFalse(result["ready"])
            self.assertEqual(recovered["evidence/static-realize-unconfirmed/receipt.json"], receipt)
            self.assertEqual(recovered["evidence/static-realize-unconfirmed/stdout.partial.original"], b"partial\x00original\xff")
            observation = json.loads(recovered["control/seal-observation.json"])
            self.assertEqual(observation["native_state"], "UNKNOWN_NO_RETRY")
            self.assertFalse(observation["native_retry_executed"])

    def test_native_failure_does_not_prevent_sealing_or_inherit_success(self):
        with tempfile.TemporaryDirectory(prefix="static-seal-failure-") as directory:
            root = Path(directory)
            control, evidence, stage = self.paths(root)
            (evidence / "motionwright-blender-diagnostic.json").write_bytes(b'{"state":"FAIL"}\n')
            result = self.seal(root, control, stage, outcome="failure")
            self.assertFalse(result["ready"])
            self.assertIn("evidence/motionwright-blender-diagnostic.json", self.decode(stage))

    def test_partial_capture_keeps_readable_originals_and_does_not_follow_symlink(self):
        with tempfile.TemporaryDirectory(prefix="static-seal-partial-") as directory:
            root = Path(directory)
            control, evidence, stage = self.paths(root)
            (evidence / "available-original").write_bytes(b"retain this original")
            outside = root / "outside-source"
            outside.write_bytes(b"unselected")
            (evidence / "unadmitted-link").symlink_to(outside)
            result = self.seal(root, control, stage)
            recovered = self.decode(stage)
            self.assertFalse(result["selected_originals_complete"])
            self.assertFalse(result["ready"])
            self.assertEqual(recovered["evidence/available-original"], b"retain this original")
            self.assertNotIn("evidence/unadmitted-link", recovered)
            self.assertEqual(outside.read_bytes(), b"unselected")

    def test_missing_evidence_is_archived_as_missing_without_native_claim(self):
        with tempfile.TemporaryDirectory(prefix="static-seal-missing-") as directory:
            root = Path(directory)
            control, _, stage = self.paths(root, evidence=False)
            result = self.seal(root, control, stage, outcome="skipped")
            recovered = self.decode(stage)
            self.assertFalse(result["ready"])
            self.assertEqual(json.loads(recovered["snapshot-manifest.json"])["roots"]["evidence"]["status"], "ABSENT")
            self.assertEqual(json.loads(recovered["control/seal-observation.json"])["native_state"], "NOT_RUN")

    def test_existing_ciphertext_is_never_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="static-seal-existing-") as directory:
            root = Path(directory)
            control, _, stage = self.paths(root)
            stage.mkdir()
            original = b"previous independent ciphertext"
            (stage / "private-evidence.mwseal").write_bytes(original)
            with self.assertRaises(FileExistsError):
                self.seal(root, control, stage)
            self.assertEqual((stage / "private-evidence.mwseal").read_bytes(), original)

    def test_binding_rejects_retries_foreign_scopes_sources_and_jobs_before_import(self):
        valid = {"GITHUB_EVENT_NAME": "workflow_dispatch", "VERIFICATION_SCOPE": "static-glb",
                 "GITHUB_RUN_ATTEMPT": "1", "EXPECTED_SOURCE": "1" * 40, "GITHUB_SHA": "1" * 40,
                 "GITHUB_REPOSITORY": "seradotcom/motionwright", "GITHUB_JOB": "native-blender-roundtrip",
                 "GITHUB_WORKFLOW_REF": "seradotcom/motionwright/.github/workflows/native-blender-e2e.yml@refs/heads/test",
                 "GITHUB_REF": "refs/heads/test"}
        for key, value in (("GITHUB_RUN_ATTEMPT", "2"), ("VERIFICATION_SCOPE", "product-scene"),
                           ("GITHUB_SHA", "3" * 40), ("GITHUB_JOB", "another-job"),
                           ("GITHUB_REPOSITORY", "another/repository"),
                           ("GITHUB_WORKFLOW_REF", "another-workflow")):
            with self.subTest(key=key), patch.object(self.wiring, "source_bytes", side_effect=AssertionError("no source import")):
                with self.assertRaises(ValueError):
                    self.wiring.binding(Path("/unopened"), {**valid, key: value})

    def test_real_git_source_objects_and_selected_sdk_must_match(self):
        with tempfile.TemporaryDirectory(prefix="static-seal-git-") as directory:
            root = Path(directory)
            for name in self.wiring.SOURCE_PATHS:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"synthetic committed source\n")
            (root / "SOURCE_LOCK.json").write_text(json.dumps({"dependencies": {"semwright": {"revision": "2" * 40}}}))
            def git(*args):
                return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE).decode().strip()
            git("init", "--quiet")
            git("config", "user.name", "Synthetic CI Control")
            git("config", "user.email", "synthetic-control@example.invalid")
            git("add", ".")
            git("-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Synthetic source object control")
            context = {**self.context, "source_sha": git("rev-parse", "HEAD")}
            before = self.wiring.source_state(root, context)
            self.assertEqual(set(before), set(self.wiring.SOURCE_PATHS))
            with self.assertRaisesRegex(ValueError, "sdk-pin-changed"):
                self.wiring.source_state(root, {**context, "semwright_sha": "4" * 40})
            (root / "tooling/native_blender_e2e.py").write_bytes(b"different uncommitted source")
            with self.assertRaisesRegex(ValueError, "differs-from-commit"):
                self.wiring.source_state(root, context)

    def test_failed_seal_gate_still_emits_upload_output_after_cipher_write(self):
        with tempfile.TemporaryDirectory(prefix="static-seal-output-") as directory:
            root = Path(directory)
            temp = root / "runner-temp"
            temp.mkdir()
            control = temp / "blender-static-custody-123-1"
            control.mkdir()
            source = {"synthetic": "source-bound fixture"}
            crypto.new_json(control / "preflight.json", {"context": self.context, "source": source,
                            "capacity": self.wiring.capacity_plan(crypto),
                            "header_max_bytes": self.wiring.verify_header_budget(
                                crypto, self.context, self.recipient, self.wiring.capacity_plan(crypto)),
                            "recipient_sha256": self.recipient["sha256"]})
            output = root / "github-output"
            with patch.dict(os.environ, {"GITHUB_WORKSPACE": str(root), "RUNNER_TEMP": str(temp),
                            "GITHUB_OUTPUT": str(output), "NATIVE_STEP_OUTCOME": "failure"}), \
                 patch.object(self.wiring, "binding", return_value=(self.context, self.recipient, crypto)), \
                 patch.object(self.wiring, "original_deadline", side_effect=self.wiring.StaticDeadlineDenied("synthetic stale clock")), \
                 patch.object(self.wiring, "source_state", return_value=source):
                self.assertEqual(self.wiring.main("seal"), 1)
            self.assertEqual(output.read_text(), "sealed=true\n")
            self.assertTrue((root / "verification/native-blender-static-staged/private-evidence.mwseal").is_file())

    def test_closed_capacity_is_admitted_before_preflight_files_or_native_effects(self):
        plan = self.wiring.capacity_plan(crypto)
        self.assertEqual(plan["payload_max_bytes"], 247537665)
        self.assertEqual(plan["snapshot_members_max"], 30)
        self.assertEqual(plan["cipher_max_bytes"], 256067649)
        self.assertEqual(crypto.MAX_FILE - plan["cipher_max_bytes"], 12367807)
        self.assertFalse(plan["compression_assumed"])
        self.assertFalse(plan["source_complete_from_budget_claimed"])
        with tempfile.TemporaryDirectory(prefix="static-admission-") as directory:
            root = Path(directory)
            temp = root / "runner-temp"
            temp.mkdir()
            output = root / "github-output"
            control = temp / "blender-static-custody-123-1"
            control.mkdir()
            deadline_raw = b'{"synthetic_original_clock":true}\n'
            (control / "deadline.original.json").write_bytes(deadline_raw)
            (control / "wiring.original.py").write_bytes(b"# synthetic original wiring\n")
            with patch.dict(os.environ, {"GITHUB_WORKSPACE": str(root), "RUNNER_TEMP": str(temp),
                            "GITHUB_OUTPUT": str(output)}), \
                 patch.object(self.wiring, "binding", return_value=(self.context, self.recipient, crypto)), \
                 patch.object(self.wiring, "original_deadline", return_value={"original_deadline_boottime_ns": 2700000000000}), \
                 patch.object(self.wiring, "verify_wiring_source"), \
                 patch.object(self.wiring, "source_state", return_value={"synthetic": "fixture"}), \
                 patch.object(self.wiring, "verify_writer_limits") as writers:
                with patch.object(crypto, "MAX_FILE", plan["cipher_max_bytes"]):
                    with self.assertRaisesRegex(ValueError, "capacity-not-admitted"):
                        self.wiring.main("preflight")
                self.assertEqual({path.name for path in control.iterdir()}, {"deadline.original.json", "wiring.original.py"})
                self.assertEqual((control / "deadline.original.json").read_bytes(), deadline_raw)
                self.assertFalse(output.exists())
                self.assertEqual(self.wiring.main("preflight"), 0)
                self.assertEqual(writers.call_count, 2)
                self.assertEqual(output.read_text(), "bound=true\n")
                receipt = json.loads((control / "preflight.json").read_bytes())
                self.assertEqual(receipt["capacity"], plan)
                self.assertEqual(receipt["deadline_original_sha256"], hashlib.sha256(deadline_raw).hexdigest())
                self.assertFalse(receipt["native_effects"])

    def test_cancelled_step_and_late_clock_keep_originals_and_never_claim_upload(self):
        with tempfile.TemporaryDirectory(prefix="static-cancel-custody-") as directory:
            root = Path(directory)
            control, evidence, stage = self.paths(root)
            raw = b"synthetic original before cancellation\x00\xff"
            (evidence / "motionwright-contribution.glb").write_bytes(raw)
            proof = (control / "deadline.original.json").read_bytes()
            timing = {"full_custody_reserve_observed": False, "deadline_renewed": False,
                      "upload_completion_claimed": False, "secondary_deadline_error": "synthetic stale clock"}
            result = self.wiring.seal_static(root, control, stage, self.context, self.recipient,
                                             crypto, "cancelled", True, timing)
            recovered = self.decode(stage)
            self.assertFalse(result["ready"])
            self.assertEqual(recovered["evidence/motionwright-contribution.glb"], raw)
            self.assertEqual(recovered["control/deadline.original.json"], proof)
            observation = json.loads(recovered["control/seal-observation.json"])
            self.assertEqual(observation["native_state"], "UNKNOWN_NO_RETRY")
            self.assertEqual(observation["native_step_outcome"], "cancelled")
            self.assertEqual(observation["original_deadline_observation"], timing)
            self.assertFalse(observation["native_retry_executed"])

    def test_admission_checks_actual_python_and_rust_writer_limits(self):
        sources = {name: (ROOT / name).read_bytes() for name in (
            "tooling/native_blender_e2e.py", "crates/native/examples/native-blender-e2e.rs")}
        def read(_root, _source, name):
            return sources[name]
        with patch.object(self.wiring, "source_bytes", side_effect=read):
            self.wiring.verify_writer_limits(ROOT, self.context)
            name = "tooling/native_blender_e2e.py"
            original = sources[name]
            sources[name] = original.replace(b"STATIC_JSON_BYTES = 8 * 1024 * 1024",
                                            b"STATIC_JSON_BYTES = 9 * 1024 * 1024")
            with self.assertRaisesRegex(ValueError, "python-writer-limits-changed"):
                self.wiring.verify_writer_limits(ROOT, self.context)
            sources[name] = original
            name = "crates/native/examples/native-blender-e2e.rs"
            sources[name] = sources[name].replace(b"8 * 1024 * 1024;", b"9 * 1024 * 1024;", 1)
            with self.assertRaisesRegex(ValueError, "rust-writer-limits-changed"):
                self.wiring.verify_writer_limits(ROOT, self.context)

    def test_closed_roster_rejects_extra_names_both_stage_branches_and_stage_overflow(self):
        with tempfile.TemporaryDirectory(prefix="static-roster-") as directory:
            root = Path(directory)
            control, evidence, _ = self.paths(root)
            stage = evidence / "static-seed-originals"
            stage.mkdir()
            sizes = {"stdout.original": 4, "stderr.original": 4, "motionwright.sqlite3": 32,
                     "motionwright.sqlite3-wal": 16, "motionwright.sqlite3-shm": 1,
                     "motionwright.sqlite3-journal": 7}
            for name, mib in sizes.items():
                with (stage / name).open("xb") as stream:
                    stream.truncate(mib * 1024 * 1024)
            self.assertTrue(self.wiring.roster_observation(evidence, control))
            with (stage / "motionwright.sqlite3-journal").open("r+b") as stream:
                stream.truncate(7 * 1024 * 1024 + 1)
            self.assertFalse(self.wiring.roster_observation(evidence, control))
            with (stage / "motionwright.sqlite3-journal").open("r+b") as stream:
                stream.truncate(7 * 1024 * 1024)
            (evidence / "static-seed-unconfirmed").mkdir()
            self.assertFalse(self.wiring.roster_observation(evidence, control))
            (evidence / "static-seed-unconfirmed").rmdir()
            (evidence / "unexpected-original").write_bytes(b"cannot expand the admitted roster")
            self.assertFalse(self.wiring.roster_observation(evidence, control))

    def test_incomplete_daemon_log_is_recoverable_but_never_ready(self):
        with tempfile.TemporaryDirectory(prefix="static-log-custody-") as directory:
            root = Path(directory)
            control, evidence, stage = self.paths(root)
            raw = b"all captured bytes including overflow sentinel"
            (evidence / "semwrightd.log").write_bytes(raw)
            crypto.new_json(evidence / "semwrightd-capture.json", {
                "capture_complete": False, "native_operation_result": "UNKNOWN_NO_RETRY",
                "retained_file_sha256": hashlib.sha256(raw).hexdigest()})
            result = self.seal(root, control, stage)
            self.assertFalse(result["ready"])
            recovered = self.decode(stage)
            self.assertEqual(recovered["evidence/semwrightd.log"], raw)
            self.assertEqual(json.loads(recovered["control/seal-observation.json"])["native_state"], "UNKNOWN_NO_RETRY")

    def test_header_budget_is_rejected_before_encrypt_or_any_output(self):
        with tempfile.TemporaryDirectory(prefix="static-header-budget-") as directory:
            root = Path(directory)
            control, _, stage = self.paths(root)
            context = {**self.context, "run_id": "9" * 9000}
            with patch.object(crypto, "encrypt_snapshot", side_effect=AssertionError("encryption must not start")):
                with self.assertRaisesRegex(ValueError, "header-byte-budget"):
                    self.wiring.seal_static(root, control, stage, context, self.recipient,
                                            crypto, "success", True)
            self.assertFalse((control / "seal-observation.json").exists())
            self.assertFalse(stage.exists())

    def test_manifest_and_member_budget_are_checked_on_the_actual_snapshot(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for name, raw in (("snapshot-manifest.json", b"{}\n"), ("evidence/original", b"x")):
                row = tarfile.TarInfo(name)
                row.size = len(raw)
                archive.addfile(row, io.BytesIO(raw))
        snapshot = stream.getvalue()
        plan = self.wiring.capacity_plan(crypto)
        self.wiring.verify_snapshot_size(snapshot, plan)
        for field, value, code in (("snapshot_members_max", 1, "member-budget"),
                                   ("snapshot_manifest_max_bytes", 2, "manifest-budget"),
                                   ("cipher_max_bytes", len(snapshot), "snapshot-byte-budget")):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, code):
                self.wiring.verify_snapshot_size(snapshot, {**plan, field: value})

    def test_unexpected_envelope_size_never_reaches_exclusive_cipher_writer(self):
        with tempfile.TemporaryDirectory(prefix="static-envelope-budget-") as directory:
            root = Path(directory)
            control, _, stage = self.paths(root)
            real_encrypt = crypto.encrypt_snapshot
            def unexpected_tail(*args):
                return real_encrypt(*args) + b"unadmitted tail"
            with patch.object(crypto, "encrypt_snapshot", side_effect=unexpected_tail):
                with self.assertRaisesRegex(ValueError, "envelope-framing-budget"):
                    self.seal(root, control, stage)
            self.assertFalse(stage.exists())


if __name__ == "__main__":
    unittest.main()
