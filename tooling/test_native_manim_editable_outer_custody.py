"""Custody-only causal controls; synthetic originals, no native/render credit."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import ci_private_evidence as engine
import native_manim_editable_cycle as cycle


class OuterCustodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        der = cls.private.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.recipient = {"spki_der_base64": base64.b64encode(der).decode("ascii"),
                         "sha256": hashlib.sha256(der).hexdigest()}
        cls.context = {"source_sha": "a" * 40, "semwright_sha": "b" * 40,
                       "run_id": "17", "run_attempt": "1"}

    def setUp(self):
        parent = os.environ.get("A4_OUTER_TEST_PARENT")
        if parent is None and os.environ.get("GITHUB_ACTIONS") == "true":
            parent = os.environ.get("RUNNER_TEMP")
        if parent is None:
            raise RuntimeError("Set A4_OUTER_TEST_PARENT to the existing private E: test directory")
        parent = Path(parent).resolve(strict=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="outer-causal-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve(strict=True)
        for name in ("subprocess.Popen", "subprocess.run"):
            guard = patch(name, side_effect=AssertionError("native/process execution forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def case(self, name="one"):
        work = self.base / name
        work.mkdir(mode=0o700)
        evidence = work / ("manim-editable-private-" + self.context["source_sha"])
        sealed = work / ("manim-editable-sealed-" + self.context["source_sha"])
        phases = ("original", "reopen-original", "edited", "reopen-edited")
        paths = {"cycle-intent": evidence / "cycle-intent.json",
                 "cycle-result": evidence / "cycle-result.json",
                 **{p + "-custody": evidence / p / "editable-custody.json" for p in phases},
                 "wrapper-stdout": work / "manim-editable-cycle-wrapper.log"}
        originals = {}
        for label, path in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            raw = (json.dumps({"label": label, "private": "PRIVATE_SYNTHETIC_π"},
                              ensure_ascii=False, indent=2) + "\r\n").encode("utf-8")
            if label == "wrapper-stdout":
                raw = b"PRIVATE_SYNTHETIC_STDOUT\r\n\x00original-bytes\n"
            with path.open("xb") as stream:
                stream.write(raw)
            originals[label] = raw
        return {"work": work, "evidence": evidence, "sealed": sealed,
                "paths": paths, "originals": originals}

    def seal(self, case, *, context=None, evidence=None):
        with patch.dict(os.environ, {"RUNNER_TEMP": str(case["work"])}):
            return cycle.seal_outer_cycle(
                case["evidence"] if evidence is None else evidence, case["sealed"],
                self.recipient, dict(self.context if context is None else context))

    def verify(self, case, *, context=None, recipient=None):
        return cycle.verified_outer_cycle(
            case["evidence"], case["sealed"],
            dict(self.context if context is None else context),
            self.recipient["sha256"] if recipient is None else recipient)

    def opened(self, case):
        ciphertext = (case["sealed"] / "cycle-outer.mwseal").read_bytes()
        raw, context = engine.decrypt_snapshot(ciphertext, self.private)
        members = {}
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for member in archive:
                self.assertTrue(member.isfile())
                self.assertNotIn(member.name, members)
                members[member.name] = archive.extractfile(member).read()
        return members, context

    def unchanged(self, case):
        for label, path in case["paths"].items():
            self.assertEqual(path.read_bytes(), case["originals"][label])

    def test_real_crypto_tar_preserves_originals_and_public_projection(self):
        case = self.case()
        self.assertEqual(cycle.PHASES,
                         ("original", "reopen-original", "edited", "reopen-edited"))
        result = self.seal(case)
        self.assertEqual(self.verify(case), result)
        members, context = self.opened(case)
        self.assertEqual(context, self.context)
        self.assertEqual(set(members),
                         {label + "/original" for label in case["paths"]} | {"snapshot-manifest.json"})
        for label, raw in case["originals"].items():
            self.assertEqual(members[label + "/original"], raw)
        manifest = json.loads(members["snapshot-manifest.json"])
        self.assertIs(manifest["selected_originals_complete"], True)
        public = (case["sealed"] / "cycle-outer.public.json").read_bytes()
        self.assertNotIn(b"PRIVATE_SYNTHETIC", public)
        self.assertNotIn(str(case["work"]).encode(), public)
        self.assertEqual(result["native_effect_invocations"], 0)
        self.unchanged(case)

    def test_missing_cycle_or_custody_never_publishes_complete(self):
        for label in ("cycle-result", "edited-custody"):
            with self.subTest(label=label):
                case = self.case(label)
                case["paths"][label].unlink()
                result = self.seal(case)
                self.assertIs(result["selected_originals_complete"], False)
                self.assertIn(label, result["failed_labels"])
                members, _ = self.opened(case)
                manifest = json.loads(members["snapshot-manifest.json"])
                self.assertIs(manifest["selected_originals_complete"], False)
                self.assertEqual(manifest["roots"][label]["status"], "ABSENT")
                self.assertNotIn(label + "/original", members)
                with self.assertRaisesRegex(ValueError, "complete_original_context"):
                    self.verify(case)

    def test_partial_copy_is_retained_and_common_manifest_is_incomplete(self):
        case = self.case()
        target = (case["work"] / ("manim-editable-outer-" + self.context["source_sha"])
                  / "edited-custody" / "original")
        actual_write = engine.new_bytes
        def write(path, raw, **kwargs):
            if Path(path) == target:
                actual_write(path, raw[:7], **kwargs)
                raise OSError("controlled partial copy")
            return actual_write(path, raw, **kwargs)
        with patch.object(engine, "new_bytes", side_effect=write):
            result = self.seal(case)
        self.assertIs(result["selected_originals_complete"], False)
        members, _ = self.opened(case)
        self.assertEqual(members["edited-custody-partial/original"],
                         case["originals"]["edited-custody"][:7])
        self.assertNotIn("edited-custody/original", members)
        self.assertIs(json.loads(members["snapshot-manifest.json"])["selected_originals_complete"],
                      False)
        with self.assertRaisesRegex(ValueError, "complete_original_context"):
            self.verify(case)
        self.unchanged(case)

    def test_partial_ciphertext_write_retains_bytes_without_public_receipt(self):
        case = self.case()
        target = case["sealed"] / "cycle-outer.mwseal"
        actual_write = engine.new_bytes
        def write(path, raw, **kwargs):
            if Path(path) == target:
                actual_write(path, raw[:32], **kwargs)
                raise OSError("controlled partial ciphertext")
            return actual_write(path, raw, **kwargs)
        with patch.object(engine, "new_bytes", side_effect=write):
            with self.assertRaisesRegex(OSError, "controlled partial ciphertext"):
                self.seal(case)
        original_partial = target.read_bytes()
        self.assertEqual(len(original_partial), 32)
        self.assertTrue(original_partial.startswith(engine.MAGIC))
        self.assertFalse((case["sealed"] / "cycle-outer.public.json").exists())
        with self.assertRaises(ValueError):
            engine.decrypt_snapshot(original_partial, self.private)
        with self.assertRaisesRegex(ValueError, "no_overwrite"):
            self.seal(case)
        self.assertEqual(target.read_bytes(), original_partial)
        self.unchanged(case)

    def test_changed_original_is_rejected_without_resealing(self):
        case = self.case()
        self.seal(case)
        cipher = (case["sealed"] / "cycle-outer.mwseal").read_bytes()
        path = case["paths"]["cycle-result"]
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "original_changed"):
            self.verify(case)
        self.assertEqual((case["sealed"] / "cycle-outer.mwseal").read_bytes(), cipher)

    def test_foreign_context_recipient_key_and_changed_ciphertext_rejected(self):
        case = self.case()
        self.seal(case)
        with self.assertRaisesRegex(ValueError, "complete_original_context"):
            self.verify(case, context={**self.context, "run_id": "18"})
        wrong_recipient = ("0" if self.recipient["sha256"][0] != "0" else "1") + self.recipient["sha256"][1:]
        with self.assertRaisesRegex(ValueError, "complete_original_context"):
            self.verify(case, recipient=wrong_recipient)
        path = case["sealed"] / "cycle-outer.mwseal"
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        with self.assertRaisesRegex(ValueError, "recovery_key_differs"):
            engine.decrypt_snapshot(path.read_bytes(), other_key)
        altered = bytearray(path.read_bytes())
        altered[-1] ^= 1
        path.write_bytes(altered)
        with self.assertRaisesRegex(ValueError, "ciphertext_changed"):
            self.verify(case)
        with self.assertRaises(InvalidTag):
            engine.decrypt_snapshot(bytes(altered), self.private)

    def test_foreign_root_or_later_attempt_has_no_output(self):
        for name in ("foreign-root", "later-attempt", "invalid-context"):
            with self.subTest(name=name):
                case = self.case(name)
                context = dict(self.context)
                evidence = case["evidence"]
                if name == "foreign-root":
                    evidence = case["work"] / "unrelated"
                elif name == "later-attempt":
                    context["run_attempt"] = "2"
                else:
                    context["run_id"] = "0"
                with self.assertRaises(ValueError):
                    self.seal(case, context=context, evidence=evidence)
                self.assertFalse(case["sealed"].exists())
                self.assertFalse((case["work"] / ("manim-editable-outer-" + self.context["source_sha"])).exists())
                self.unchanged(case)

    def test_second_seal_cannot_overwrite_or_change_original_scope(self):
        case = self.case()
        self.seal(case)
        def inventory():
            return {p.relative_to(case["work"]).as_posix(): p.read_bytes()
                    for p in case["work"].rglob("*") if p.is_file()}
        original = inventory()
        with self.assertRaisesRegex(ValueError, "no_overwrite"):
            self.seal(case)
        self.assertEqual(inventory(), original)
        self.unchanged(case)


if __name__ == "__main__":
    unittest.main()
