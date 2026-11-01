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


def workflow_lit_wiring():
    source = (ROOT / ".github/workflows/native-blender-e2e.yml").read_text()
    marker = '        cat > "$RUNNER_TEMP/blender-lit-wiring.py" <<\'PY\'\n'
    if source.count(marker) != 1:
        raise AssertionError("exact lit workflow wiring is required")
    body = source.split(marker, 1)[1].split("        PY\n", 1)[0]
    module = types.ModuleType("_lit_diagnostic_custody_workflow_unit")
    exec(compile(textwrap.dedent(body), "native-blender-e2e.yml:lit-custody", "exec"),
         module.__dict__)
    return module


@unittest.skipUnless(sys.platform == "linux", "Linux descriptor custody controls")
class LitPhaseDiagnosticCustodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wiring = workflow_lit_wiring()
        cls.context = {"source_sha": "1" * 40, "semwright_sha": "2" * 40,
                       "run_id": "123", "run_attempt": "1"}

    def fixture(self, root, phases=("create",)):
        owner, control = root / "owner", root / "control"
        owner.mkdir()
        control.mkdir()
        originals = {}
        for phase in phases:
            evidence = owner / phase / "evidence"
            evidence.mkdir(parents=True)
            for name in self.wiring.PHASE_DIAGNOSTIC_FILES:
                raw = b"" if name == "native-phase.stderr" else (phase + ":" + name).encode() + b"\x00\xff\n"
                relative = phase + "/evidence/" + name
                (owner / relative).write_bytes(raw)
                originals[relative] = raw
        producer = {"schema": "motionwright.blender.lit-still-producer/1", "context": self.context,
                    "state": "INCOMPLETE",
                    "phases": [{"phase": phase, "state": "ATTEMPTED", "command": {
                        "terminal": {"status": "PROCESS_TIMEOUT", "outcome_known": False}}} for phase in phases]}
        return owner, control, producer, originals

    def capture(self, owner, control, producer):
        return self.wiring.capture_phase_diagnostics(
            owner, control, self.context, self.wiring.canonical(producer))

    def test_only_three_closed_logs_of_observed_phases_are_retained_exactly(self):
        with tempfile.TemporaryDirectory(prefix="lit-phase-logs-") as directory:
            owner, control, producer, originals = self.fixture(Path(directory))
            (owner / "create/evidence/session.ticket").write_bytes(b"unselected synthetic ticket")
            (owner / "edit/evidence").mkdir(parents=True)
            (owner / "edit/evidence/semwrightd.log").write_bytes(b"unobserved phase")
            result = self.capture(owner, control, producer)
            self.assertTrue(result["all_observed_logs_captured"])
            self.assertEqual(result["observed_phases"], ["create"])
            self.assertEqual(len(result["files"]), 3)
            for row in result["files"]:
                raw = originals[row["source_relative_path"]]
                self.assertEqual(row["state"], "CAPTURED")
                self.assertEqual(row["bytes"], len(raw))
                self.assertEqual(row["sha256"], hashlib.sha256(raw).hexdigest())
                self.assertEqual((control / row["retained_relative_path"]).read_bytes(), raw)
                self.assertEqual((owner / row["source_relative_path"]).read_bytes(), raw)
                self.assertEqual((control / row["retained_relative_path"]).stat().st_nlink, 1)
            self.assertEqual({p.name for p in (control / "phase-diagnostics").iterdir()},
                             {"observation.json", "create.native-phase.stdout",
                              "create.native-phase.stderr", "create.semwrightd.log"})
            self.assertFalse(result["native_terminal_inferred_from_logs"])
            self.assertFalse(result["automatic_retry"])

    def test_all_four_phases_have_finite_file_and_receipt_budgets(self):
        with tempfile.TemporaryDirectory(prefix="lit-phase-bounds-") as directory:
            owner, control, producer, _ = self.fixture(
                Path(directory), self.wiring.PHASE_DIAGNOSTIC_PHASES)
            boundary = owner / "create/evidence/native-phase.stdout"
            with boundary.open("r+b") as stream:
                stream.truncate(self.wiring.PHASE_DIAGNOSTIC_FILE_BYTES)
            result = self.capture(owner, control, producer)
            self.assertTrue(result["all_observed_logs_captured"])
            self.assertEqual(len(result["files"]), 12)
            self.assertEqual(result["limits"], {"phases": 4, "files": 12,
                "file_bytes": 4 * 1024 * 1024, "total_file_bytes": 48 * 1024 * 1024,
                "receipt_bytes": 64 * 1024})
            self.assertEqual((control / "phase-diagnostics/create.native-phase.stdout").read_bytes(),
                             boundary.read_bytes())
            receipt = (control / "phase-diagnostics/observation.json").read_bytes()
            self.assertLessEqual(len(receipt), 64 * 1024)
            self.assertEqual(json.loads(receipt), result)

    def test_unbound_or_noncanonical_phase_rosters_do_not_open_diagnostics(self):
        with tempfile.TemporaryDirectory(prefix="lit-phase-rosters-") as directory:
            root = Path(directory)
            cases = [None, b"{", b"[]"]
            base = {"schema": "motionwright.blender.lit-still-producer/1",
                    "context": self.context, "phases": [{"phase": "create", "state": "ATTEMPTED"}]}
            for change in ({"schema": "foreign"}, {"context": {**self.context, "run_id": "124"}},
                           {"phases": None}, {"phases": [{"phase": "../escape", "state": "ATTEMPTED"}]},
                           {"phases": [{"phase": "edit", "state": "ATTEMPTED"}]},
                           {"phases": [{"phase": "create", "state": "UNKNOWN"}]},
                           {"phases": [{"phase": "create", "state": "ATTEMPTED"}] * 2},
                           {"phases": [{"phase": "create", "state": "ATTEMPTED"}] * 5}):
                cases.append(self.wiring.canonical({**base, **change}))
            for index, raw in enumerate(cases):
                with self.subTest(index=index):
                    control = root / str(index)
                    control.mkdir()
                    with patch.object(self.wiring, "read_phase_diagnostic",
                                      side_effect=AssertionError("unbound logs must not be opened")):
                        result = self.wiring.capture_phase_diagnostics(root / "absent-owner",
                                                                      control, self.context, raw)
                    self.assertEqual(result["selection"]["state"], "UNAVAILABLE")
                    self.assertEqual(result["files"], [])
                    self.assertFalse(result["all_observed_logs_captured"])
            control = root / "empty"
            control.mkdir()
            result = self.capture(root / "absent-owner", control, {**base, "phases": []})
            self.assertEqual(result["selection"]["state"], "NO_OBSERVED_PHASES")
            self.assertFalse(result["all_observed_logs_captured"])

    def test_missing_linked_nonregular_and_oversize_logs_are_real_negatives(self):
        for kind in ("missing", "symlink", "hardlink", "directory", "fifo", "oversize", "parent-link"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(prefix="lit-phase-negative-") as directory:
                root = Path(directory)
                owner, control, producer, _ = self.fixture(root)
                target = owner / "create/evidence/native-phase.stdout"
                outside = root / "outside"
                outside.write_bytes(b"unselected synthetic outside bytes")
                if kind == "missing":
                    target.unlink()
                elif kind == "symlink":
                    target.unlink()
                    target.symlink_to(outside)
                elif kind == "hardlink":
                    os.link(target, root / "second-link")
                elif kind == "directory":
                    target.unlink()
                    target.mkdir()
                elif kind == "fifo":
                    target.unlink()
                    os.mkfifo(target)
                elif kind == "oversize":
                    with target.open("r+b") as stream:
                        stream.truncate(self.wiring.PHASE_DIAGNOSTIC_FILE_BYTES + 1)
                else:
                    evidence = target.parent
                    moved = owner / "moved-evidence"
                    evidence.rename(moved)
                    evidence.symlink_to(moved, target_is_directory=True)
                result = self.capture(owner, control, producer)
                self.assertFalse(result["all_observed_logs_captured"])
                first = result["files"][0]
                self.assertEqual(first["state"], "UNAVAILABLE")
                self.assertNotIn("sha256", first)
                self.assertFalse((control / first["retained_relative_path"]).exists())
                self.assertEqual(outside.read_bytes(), b"unselected synthetic outside bytes")
                if kind != "parent-link":
                    self.assertTrue(all(row["state"] == "CAPTURED" for row in result["files"][1:]))

    def test_file_change_during_read_never_becomes_captured(self):
        with tempfile.TemporaryDirectory(prefix="lit-phase-change-") as directory:
            owner, control, producer, _ = self.fixture(Path(directory))
            target = owner / "create/evidence/native-phase.stdout"
            real_fstat = os.fstat
            expected = target.stat()
            calls = 0
            def changed(descriptor):
                nonlocal calls
                observed = real_fstat(descriptor)
                if (observed.st_dev, observed.st_ino) == (expected.st_dev, expected.st_ino):
                    calls += 1
                    if calls == 2:
                        with target.open("ab") as stream:
                            stream.write(b"changed during read")
                        return real_fstat(descriptor)
                return observed
            with patch.object(self.wiring.os, "fstat", side_effect=changed):
                result = self.capture(owner, control, producer)
            self.assertFalse(result["all_observed_logs_captured"])
            self.assertEqual(result["files"][0]["state"], "UNAVAILABLE")
            self.assertFalse((control / result["files"][0]["retained_relative_path"]).exists())

    def test_file_replaced_before_open_cannot_follow_a_link_or_block_on_fifo(self):
        for kind in ("symlink", "fifo"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(prefix="lit-phase-open-race-") as directory:
                root = Path(directory)
                owner, control, producer, _ = self.fixture(root)
                target = owner / "create/evidence/native-phase.stdout"
                outside = root / "outside"
                outside.write_bytes(b"must not be read")
                real_open = os.open
                changed = False
                def replaced(path, flags, *args, **kwargs):
                    nonlocal changed
                    if path == target.name and kwargs.get("dir_fd") is not None and not changed:
                        changed = True
                        target.unlink()
                        if kind == "symlink":
                            target.symlink_to(outside)
                        else:
                            os.mkfifo(target)
                    return real_open(path, flags, *args, **kwargs)
                with patch.object(self.wiring.os, "open", side_effect=replaced):
                    result = self.capture(owner, control, producer)
                self.assertTrue(changed)
                self.assertFalse(result["all_observed_logs_captured"])
                self.assertEqual(result["files"][0]["state"], "UNAVAILABLE")
                self.assertFalse((control / result["files"][0]["retained_relative_path"]).exists())

    def test_parent_aba_cannot_redirect_the_held_directory_descriptor(self):
        with tempfile.TemporaryDirectory(prefix="lit-phase-parent-aba-") as directory:
            root = Path(directory)
            owner, control, producer, originals = self.fixture(root)
            evidence = owner / "create/evidence"
            moved = owner / "create/original-evidence"
            foreign = root / "foreign-evidence"
            foreign.mkdir()
            name = "native-phase.stdout"
            (foreign / name).write_bytes(b"foreign bytes must never be captured")
            real_stat, real_open = os.stat, os.open
            changed = restored = False
            file_stats = 0
            opened_bytes = []
            def swapped_stat(path, *args, **kwargs):
                nonlocal changed, restored, file_stats
                if path == name and kwargs.get("dir_fd") is not None:
                    file_stats += 1
                    if file_stats == 1:
                        evidence.rename(moved)
                        evidence.symlink_to(foreign, target_is_directory=True)
                        changed = True
                    observed = real_stat(path, *args, **kwargs)
                    if file_stats == 2:
                        evidence.unlink()
                        moved.rename(evidence)
                        restored = True
                    return observed
                return real_stat(path, *args, **kwargs)
            def observe_open(path, flags, *args, **kwargs):
                descriptor = real_open(path, flags, *args, **kwargs)
                if path == name and kwargs.get("dir_fd") is not None:
                    opened_bytes.append(os.pread(descriptor, 256, 0))
                return descriptor
            with patch.object(self.wiring.os, "stat", side_effect=swapped_stat), \
                 patch.object(self.wiring.os, "open", side_effect=observe_open):
                result = self.capture(owner, control, producer)
            self.assertTrue(changed and restored)
            self.assertFalse(evidence.is_symlink())
            self.assertEqual(opened_bytes, [originals["create/evidence/" + name]])
            self.assertEqual((foreign / name).read_bytes(), b"foreign bytes must never be captured")
            self.assertFalse(result["all_observed_logs_captured"])
            self.assertEqual(result["files"][0]["state"], "UNAVAILABLE")
            self.assertFalse((control / result["files"][0]["retained_relative_path"]).exists())
            self.assertTrue(all(row["state"] == "CAPTURED" for row in result["files"][1:]))

    def test_copy_readback_failure_is_kept_as_unavailable(self):
        with tempfile.TemporaryDirectory(prefix="lit-phase-readback-") as directory:
            owner, control, producer, originals = self.fixture(Path(directory))
            real_write = self.wiring.write_new
            def corrupt(path, raw):
                if path.name == "create.native-phase.stdout":
                    raw += b"copy corruption"
                return real_write(path, raw)
            with patch.object(self.wiring, "write_new", side_effect=corrupt):
                result = self.capture(owner, control, producer)
            self.assertFalse(result["all_observed_logs_captured"])
            self.assertEqual(result["files"][0]["state"], "UNAVAILABLE")
            self.assertNotIn("sha256", result["files"][0])
            self.assertEqual((owner / "create/evidence/native-phase.stdout").read_bytes(),
                             originals["create/evidence/native-phase.stdout"])
            self.assertTrue(all(row["state"] == "CAPTURED" for row in result["files"][1:]))

    def test_existing_capture_directory_is_never_replaced(self):
        with tempfile.TemporaryDirectory(prefix="lit-phase-exclusive-") as directory:
            owner, control, producer, _ = self.fixture(Path(directory))
            destination = control / "phase-diagnostics"
            destination.mkdir()
            original = b"existing custody must not be replaced"
            (destination / "observation.json").write_bytes(original)
            with patch.object(self.wiring, "read_phase_diagnostic",
                              side_effect=AssertionError("no recapture")):
                with self.assertRaises(FileExistsError):
                    self.capture(owner, control, producer)
            self.assertEqual((destination / "observation.json").read_bytes(), original)

    def test_seal_keeps_phase_logs_private_and_preserves_unknown_native_outcome(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        der = key.public_key().public_bytes(serialization.Encoding.DER,
                                            serialization.PublicFormat.SubjectPublicKeyInfo)
        recipient = {"spki_der_base64": base64.b64encode(der).decode(),
                     "sha256": hashlib.sha256(der).hexdigest()}
        crypto_raw = (ROOT / "tooling/ci_private_evidence.py").read_bytes()
        for missing in (False, True):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory(prefix="lit-phase-seal-") as directory:
                root = Path(directory)
                temp, workspace = root / "runner", root / "workspace"
                temp.mkdir()
                (workspace / "tooling").mkdir(parents=True)
                (workspace / "tooling/ci_private_evidence.py").write_bytes(crypto_raw)
                owner, control, producer, originals = self.fixture(temp)
                owner = owner.rename(temp / "mw-lit-123")
                control = control.rename(temp / "blender-lit-control")
                if missing:
                    (owner / "create/evidence/native-phase.stdout").unlink()
                producer_raw = self.wiring.canonical(producer)
                (owner / "producer-record.json").write_bytes(producer_raw)
                source = {"synthetic": "source-bound unit fixture"}
                crypto.new_json(control / "preflight.json", {"context": self.context, "source": source,
                                "recipient_spki_sha256": recipient["sha256"]})
                crypto.new_json(control / "attempt-intent.json", {"context": self.context, "source": source,
                                "binary": {}, "automatic_retry": False})
                crypto.new_json(control / "execution.json", {"context": self.context,
                                "state": "FAILED_NO_RETRY", "exit_code": 1})
                (temp / "blender-lit-g0").mkdir()
                (temp / "blender-lit-g1").mkdir()
                output = root / "github-output"
                with patch.dict(os.environ, {"GITHUB_WORKSPACE": str(workspace), "RUNNER_TEMP": str(temp),
                                "GITHUB_OUTPUT": str(output), "NATIVE_STEP_OUTCOME": "failure"}), \
                     patch.object(self.wiring, "requested_context", return_value=self.context), \
                     patch.object(self.wiring, "source_state", return_value=source), \
                     patch.object(self.wiring, "recipient_from", return_value=recipient), \
                     patch.object(self.wiring.subprocess, "check_output", return_value=crypto_raw), \
                     patch.object(self.wiring.subprocess, "run", side_effect=AssertionError("no Native")):
                    self.assertEqual(self.wiring.main("seal"), 1)
                stage = workspace / "verification/native-blender-lit-still-staged"
                self.assertEqual({p.name for p in stage.iterdir()}, {"private-evidence.mwseal", "public-evidence.json"})
                public_raw = (stage / "public-evidence.json").read_bytes()
                public = json.loads(public_raw)
                self.assertEqual(public["status"], "UNKNOWN_NO_RETRY")
                self.assertFalse(public["formal_acceptance"])
                self.assertFalse(public["original_native_terminal_replaced"])
                self.assertEqual(public["private_plaintext_format"], "motionwright.ci.private-snapshot/1")
                self.assertNotIn(b"phase-diagnostics", public_raw)
                plaintext, actual_context = crypto.decrypt_snapshot((stage / "private-evidence.mwseal").read_bytes(), key)
                self.assertEqual(actual_context, self.context)
                with tarfile.open(fileobj=io.BytesIO(plaintext), mode="r:") as archive:
                    recovered = {row.name: archive.extractfile(row).read() for row in archive.getmembers()}
                manifest = json.loads(recovered["snapshot-manifest.json"])
                self.assertEqual(set(manifest["roots"]), {"a-selected", "control", "g-zero", "g-one"})
                report = json.loads(recovered["control/phase-diagnostics/observation.json"])
                self.assertEqual(report["all_observed_logs_captured"], not missing)
                self.assertFalse(report["native_terminal_inferred_from_logs"])
                for relative, raw in originals.items():
                    name = "control/phase-diagnostics/create." + Path(relative).name
                    if missing and Path(relative).name == "native-phase.stdout":
                        self.assertNotIn(name, recovered)
                    else:
                        self.assertEqual(recovered[name], raw)
                self.assertEqual(recovered["a-selected/producer-record.json"], producer_raw)
                self.assertEqual((owner / "producer-record.json").read_bytes(), producer_raw)
                self.assertEqual(output.read_text(), "sealed=true\n")


if __name__ == "__main__":
    unittest.main()
