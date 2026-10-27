"""Source-only controls for the actual editable-cycle workflow verifier.

Synthetic JSON/ciphertext fixtures are never native evidence. The real read-only
verifier is extracted from the workflow; no provider, decoder or subprocess runs.
"""
from __future__ import annotations
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/native-manim-e2e.yml"
sys.path.insert(0, str(ROOT / "tooling"))
from native_manim_editable_cycle import EVIDENCE_NAME, PHASES, seal_outer_cycle
from test_native_manim_editable_outer_custody import launcher_fixture

CURRENT = {"source_sha": "1" * 40, "semwright_sha": "2" * 40,
           "run_id": "12345", "run_attempt": "1"}
FLAGS = (
    "fresh_process_and_session_per_phase", "same_saved_store",
    "typed_edit_import_and_fresh_readback", "original_bundle_byte_preserving_native_reopen",
    "edited_bundle_byte_preserving_native_reopen",
    "all_24_decoded_frames_equal_for_both_reopens", "edited_text_observed_in_all_frames",
    "license_and_origin_retained_alongside_original_font",
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value):
    path.write_bytes((json.dumps(value, sort_keys=True) + "\n").encode())


def load_verifier():
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    label = "- name: Verify four original editable phases and sealed custody"
    hits = [i for i, line in enumerate(lines) if line.strip() == label]
    if len(hits) != 1:
        raise AssertionError("Expected exactly one original editable verifier step")
    start = hits[0]
    step_indent = len(lines[start]) - len(lines[start].lstrip())
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].lstrip().startswith("- ")
                and len(lines[i]) - len(lines[i].lstrip()) == step_indent), len(lines))
    shell = lines[start:end]
    begin = [i for i, line in enumerate(shell) if line.strip().endswith("<<'PY'")]
    finish = [i for i, line in enumerate(shell) if line.strip() == "PY"]
    if len(begin) != 1 or len(finish) != 1 or finish[0] <= begin[0]:
        raise AssertionError("Expected one complete verifier heredoc")
    source = textwrap.dedent("\n".join(shell[begin[0] + 1:finish[0]]) + "\n")
    tree = ast.parse(source, filename=str(WORKFLOW))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == "verified_editable_cycle_summary"]
    if len(functions) != 1:
        raise AssertionError("Expected the one real read-only function")
    namespace = {}
    code = ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[]))
    exec(compile(code, str(WORKFLOW), "exec"), namespace)
    return namespace["verified_editable_cycle_summary"]


class EditableWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        der = cls.private.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.recipient = {"spki_der_base64": base64.b64encode(der).decode("ascii"),
                         "sha256": digest(der)}

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.evidence = self.root / ("manim-editable-private-" + CURRENT["source_sha"])
        self.sealed = self.root / ("manim-editable-sealed-" + CURRENT["source_sha"])
        self.evidence.mkdir()
        self.sealed.mkdir()
        self.verifier = load_verifier()
        rows = []
        for phase in PHASES:
            folder = self.evidence / phase
            folder.mkdir()
            for name in (EVIDENCE_NAME, "editable-source-retention.json",
                         "native-video-admission.json"):
                write(folder / name, {"unit_fixture": True, "phase": phase, "name": name})
            result = {
                "phase": phase, "status": "PASS_REQUIRES_SEALED_CUSTODY",
                "native_effect_invocations": 1, "native_result_original_retained": True,
                "automatic_retry": False,
                "native_evidence_sha256": digest((folder / EVIDENCE_NAME).read_bytes()),
                "source_retention_sha256": digest(
                    (folder / "editable-source-retention.json").read_bytes()),
                "native_video_admission_sha256": digest(
                    (folder / "native-video-admission.json").read_bytes()),
            }
            write(folder / "result.json", result)
            seal = b"MWCISEAL1\nsynthetic-unit-not-encrypted:" + phase.encode()
            (self.sealed / (phase + ".mwseal")).write_bytes(seal)
            write(folder / "editable-custody.json", {
                "schema": "motionwright.manim.editable.sealed-custody/1",
                "status": "PASS", "context": CURRENT, "selected_originals_complete": True,
                "native_result_retained": True, "secondary_error_count": 0,
                "recipient_sha256": self.recipient["sha256"], "ciphertext_bytes": len(seal),
                "ciphertext_sha256": digest(seal),
            })
            rows.append({"phase": phase,
                         "result_sha256": digest((folder / "result.json").read_bytes()),
                         "ciphertext_sha256": digest(seal)})
        self.pixel = self.evidence / "reopen-edited" / "editable-pixel-cycle.json"
        write(self.pixel, {
            "schema": "motionwright.manim.editable.original-pixel-cycle/1",
            "status": "PASS_REQUIRES_SEALED_CUSTODY", "frame_count_per_phase": 24,
            "original_reopen_all_frames_equal": True, "edited_reopen_all_frames_equal": True,
            "saved_text_change_observed_in_all_frames": True,
            "frames": [{"index": i, "original_reopen_all_rgba_equal": True,
                        "edited_reopen_all_rgba_equal": True,
                        "original_rgba_sha256": "a" * 64, "edited_rgba_sha256": "b" * 64}
                       for i in range(24)],
            "native_video_decode_invocations": 0, "render_invocations": 0,
            "human_acceptance": False,
            "png_decoder": {"implementation": "Pillow", "version": "11.2.1",
                            "files": [{"name": "Image.py", "bytes": 123, "sha256": "c" * 64},
                                      {"name": "_imaging.unit.so", "bytes": 456,
                                       "sha256": "d" * 64}]},
            "original_media_as_edited_negative": {
                "status": "EXPECTED_PIXEL_REJECTION",
                "rejection": "editable_text_change_unobserved", "synthetic_pixels_used": False,
            },
        })
        last_result_path = self.evidence / "reopen-edited" / "result.json"
        last_result = json.loads(last_result_path.read_bytes())
        last_result["original_pixel_cycle_sha256"] = digest(self.pixel.read_bytes())
        write(last_result_path, last_result)
        rows[-1]["result_sha256"] = digest(last_result_path.read_bytes())
        self.cycle = self.evidence / "cycle-result.json"
        write(self.cycle, {
            "schema": "motionwright.manim.editable.source-cycle-observation/1",
            "status": "PASS", "context": CURRENT, "native_effect_invocations": 4,
            **{key: True for key in FLAGS}, "phase_results": rows,
            "pixel_cycle_sha256": digest(self.pixel.read_bytes()),
            "motion_canvas_equivalence_verified": False, "all_manim_features_accepted": False,
            "human_acceptance": False, "automatic_retry": False,
        })
        write(self.evidence / "cycle-intent.json", {
            "schema": "motionwright.manim.editable.source-cycle-intent/1",
            "context": CURRENT, "unit_fixture": True, "native_effect_invocations": 0,
        })
        (self.root / "manim-editable-cycle-wrapper.log").write_bytes(
            b"SYNTHETIC_EDITABLE_WRAPPER_STDOUT\r\n")
        launcher_fixture(self.root, dict(CURRENT))
        with mock.patch.dict(os.environ, {"RUNNER_TEMP": str(self.root)}):
            self.outer = seal_outer_cycle(self.evidence, self.sealed, self.recipient, dict(CURRENT))

    def check(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("forbidden process")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("forbidden process")), \
                mock.patch("native_manim_editable_pixels.compare",
                           side_effect=AssertionError("publication is read-only")):
            return self.verifier(self.evidence, self.sealed, dict(CURRENT), self.recipient["sha256"])

    def test_original_four_outcomes_produce_closed_summary_without_native_work(self):
        before = {str(path.relative_to(self.root)): path.read_bytes()
                  for path in self.root.rglob("*") if path.is_file()}
        summary = self.check()
        self.assertEqual(summary["schema"], "motionwright.manim.editable.source-cycle-public/2")
        self.assertEqual(summary["status"], "VERIFIED_SCOPED_EDITABLE_SOURCE_CYCLE")
        self.assertEqual(summary["publisher_native_effect_invocations"], 0)
        self.assertEqual([row["phase"] for row in summary["phases"]], list(PHASES))
        self.assertEqual(set(summary), {
            "schema", "status", "motionwright_sha", "semwright_sha", "profile",
            "native_effect_invocations", "publisher_native_effect_invocations",
            "decoded_frame_count_per_phase", "original_cycle_result_sha256",
            "original_pixel_cycle_sha256", "sealed_originals_recipient_sha256", "phases",
            "outer_custody",
            *FLAGS, "motion_canvas_equivalence_verified", "all_manim_features_accepted",
            "human_acceptance", "automatic_retry",
        })
        self.assertEqual(summary["outer_custody"], self.outer)
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes()
                                 for path in self.root.rglob("*") if path.is_file()})

    def test_missing_outer_ciphertext_blocks_v2_publication_without_resealing(self):
        (self.sealed / "cycle-outer.mwseal").unlink()
        before = {str(path.relative_to(self.root)): path.read_bytes()
                  for path in self.root.rglob("*") if path.is_file()}
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.check()
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes()
                                 for path in self.root.rglob("*") if path.is_file()})

    def test_missing_outer_descriptor_blocks_v2_publication_without_resealing(self):
        (self.sealed / "cycle-outer.public.json").unlink()
        before = {str(path.relative_to(self.root)): path.read_bytes()
                  for path in self.root.rglob("*") if path.is_file()}
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.check()
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes()
                                 for path in self.root.rglob("*") if path.is_file()})

    def test_coherent_outer_ciphertext_substitution_rejects_foreign_or_invalid_header(self):
        from ci_private_evidence import MAGIC, encrypt_snapshot

        foreign_private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        foreign_der = foreign_private.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        foreign_recipient = {
            "spki_der_base64": base64.b64encode(foreign_der).decode("ascii"),
            "sha256": digest(foreign_der),
        }
        variants = [("invalid_header", MAGIC + bytes(4) + b"synthetic-invalid-envelope")]
        for key, value in (("source_sha", "a" * 40), ("semwright_sha", "b" * 40),
                           ("run_id", "54321"), ("run_attempt", "2")):
            variants.append((key, encrypt_snapshot(
                b"synthetic-foreign-context", self.recipient, {**CURRENT, key: value})))
        variants.append(("recipient", encrypt_snapshot(
            b"synthetic-foreign-recipient", foreign_recipient, dict(CURRENT))))
        cipher_path = self.sealed / "cycle-outer.mwseal"
        receipt_path = self.sealed / "cycle-outer.public.json"
        original_cipher, original_receipt = cipher_path.read_bytes(), receipt_path.read_bytes()
        for kind, replacement in variants:
            with self.subTest(kind=kind):
                cipher_path.write_bytes(replacement)
                receipt = json.loads(original_receipt)
                receipt.update(ciphertext_bytes=len(replacement), ciphertext_sha256=digest(replacement))
                write(receipt_path, receipt)
                before = {str(path.relative_to(self.root)): path.read_bytes()
                          for path in self.root.rglob("*") if path.is_file()}
                try:
                    with self.assertRaisesRegex(ValueError, "editable_public_outer_seal_header_"):
                        self.check()
                    self.assertEqual(before, {
                        str(path.relative_to(self.root)): path.read_bytes()
                        for path in self.root.rglob("*") if path.is_file()})
                finally:
                    cipher_path.write_bytes(original_cipher)
                    receipt_path.write_bytes(original_receipt)

    def test_each_missing_original_seal_rejects_without_recovery_or_publication(self):
        for phase in PHASES:
            with self.subTest(phase=phase):
                path = self.sealed / (phase + ".mwseal")
                original = path.read_bytes()
                path.unlink()
                try:
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        self.check()
                finally:
                    path.write_bytes(original)

    def test_changed_original_report_rejects_even_after_updating_outer_hash(self):
        cycle_original = self.cycle.read_bytes()
        for phase in PHASES:
            with self.subTest(phase=phase):
                path = self.evidence / phase / "result.json"
                raw = path.read_bytes()
                result = json.loads(raw)
                result["native_evidence_sha256"] = "0" * 64
                write(path, result)
                cycle = json.loads(cycle_original)
                next(row for row in cycle["phase_results"] if row["phase"] == phase)[
                    "result_sha256"] = digest(path.read_bytes())
                write(self.cycle, cycle)
                try:
                    with self.assertRaisesRegex(ValueError, "editable_phase_original_result_changed"):
                        self.check()
                finally:
                    path.write_bytes(raw)
                    self.cycle.write_bytes(cycle_original)

    def test_unknown_incomplete_scope_or_later_attempt_never_produces_summary(self):
        raw = self.cycle.read_bytes()
        changes = (("status", "OutcomeUnknown"), ("native_effect_invocations", 3),
                   ("original_bundle_byte_preserving_native_reopen", False),
                   ("automatic_retry", True), ("human_acceptance", True))
        for key, value in changes:
            with self.subTest(key=key):
                changed = json.loads(raw)
                changed[key] = value
                write(self.cycle, changed)
                with self.assertRaisesRegex(ValueError, "editable_public_cycle_scope"):
                    self.check()
        self.cycle.write_bytes(raw)
        changed = json.loads(raw)
        changed["pixel_cycle_sha256"] = "0" * 64
        write(self.cycle, changed)
        with self.assertRaisesRegex(ValueError, "editable_public_fourth_original_pixel_binding"):
            self.check()
        self.cycle.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "editable_public_original_context"):
            self.verifier(self.evidence, self.sealed, {**CURRENT, "run_attempt": "2"},
                          self.recipient["sha256"])

    def test_changed_last_frame_and_missing_negative_are_rejected(self):
        raw, cycle_raw = self.pixel.read_bytes(), self.cycle.read_bytes()
        variants = []
        last = json.loads(raw)
        last["frames"][-1]["edited_reopen_all_rgba_equal"] = False
        variants.append(last)
        incomplete = json.loads(raw)
        incomplete["frames"].pop()
        variants.append(incomplete)
        absent_negative = json.loads(raw)
        absent_negative["original_media_as_edited_negative"] = {}
        variants.append(absent_negative)
        for changed in variants:
            with self.subTest(kind=len(changed["frames"]), negative=bool(
                    changed["original_media_as_edited_negative"])):
                write(self.pixel, changed)
                cycle = json.loads(cycle_raw)
                cycle["pixel_cycle_sha256"] = digest(self.pixel.read_bytes())
                last_result_path = self.evidence / "reopen-edited" / "result.json"
                last_result = json.loads(last_result_path.read_bytes())
                last_result["original_pixel_cycle_sha256"] = cycle["pixel_cycle_sha256"]
                write(last_result_path, last_result)
                cycle["phase_results"][-1]["result_sha256"] = digest(last_result_path.read_bytes())
                write(self.cycle, cycle)
                expected = ("editable_public_causal_original_negative"
                            if not changed["original_media_as_edited_negative"]
                            else "editable_public_all_original_pixels")
                with self.assertRaisesRegex(ValueError, expected):
                    self.check()

    def test_reused_seal_or_recipient_drift_is_rejected(self):
        phase = "reopen-edited"
        seal_path = self.sealed / (phase + ".mwseal")
        custody_path = self.evidence / phase / "editable-custody.json"
        custody = json.loads(custody_path.read_bytes())
        reused = (self.sealed / "original.mwseal").read_bytes()
        seal_path.write_bytes(reused)
        custody.update(ciphertext_sha256=digest(reused), ciphertext_bytes=len(reused))
        write(custody_path, custody)
        cycle = json.loads(self.cycle.read_bytes())
        cycle["phase_results"][-1]["ciphertext_sha256"] = digest(reused)
        write(self.cycle, cycle)
        with self.assertRaisesRegex(ValueError, "editable_public_distinct_original_seals"):
            self.check()
        custody["recipient_sha256"] = "4" * 64
        write(custody_path, custody)
        with self.assertRaisesRegex(ValueError, "editable_public_seal_binding"):
            self.check()


if __name__ == "__main__":
    unittest.main()
