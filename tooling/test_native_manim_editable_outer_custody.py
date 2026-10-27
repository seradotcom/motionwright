"""Custody-only causal controls; synthetic originals, no native/render credit."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import ci_private_evidence as engine
import native_manim_editable_cycle as cycle


def launcher_fixture(temporary, context):
    """Explicit synthetic originals for the read-only archive/publication controls."""
    from native_manim_editable_outer import control_root, origin_record
    control = control_root(temporary, context)
    launch = control / "work/native-wrapper"
    launch.mkdir(parents=True, exist_ok=True)
    def put(path, value):
        path.write_bytes((json.dumps(value, sort_keys=True) + "\n").encode())
    origin = origin_record(context, 10_000_000_000, "synthetic-boot")
    put(control / "origin.json", origin)
    origin_binding = {"origin_sha256": hashlib.sha256((control / "origin.json").read_bytes()).hexdigest(),
                      "origin_monotonic_ns": origin["origin_monotonic_ns"],
                      "deadline_ns": origin["deadline_ns"], "work_deadline_ns": origin["work_deadline_ns"]}
    repo = temporary / "synthetic-source"
    evidence = temporary / ("manim-editable-private-" + context["source_sha"])
    sealed = temporary / ("manim-editable-sealed-" + context["source_sha"])
    intent = {"schema": "motionwright.manim.editable.outer-launch-intent/1", "label": "native-wrapper",
              "context": context, "origin": origin_binding, "cwd": str(repo),
              "argv": [str(temporary / "manim-custody-venv/bin/python"), "-B",
                       str(repo / "tooling/native_manim_e2e.py"), "--scenario", "editable-source-cycle",
                       "--evidence-root", str(evidence), "--sealed-cycle-root", str(sealed)],
              "created_utc": "2026-10-26T00:00:00+00:00", "launch_requested": False,
              "execution": "NOT_STARTED", "automatic_retry": False}
    put(launch / "intent.json", intent)
    intent_sha = hashlib.sha256((launch / "intent.json").read_bytes()).hexdigest()
    put(launch / "launch-requested.json", {"intent_sha256": intent_sha,
        "requested_monotonic_ns": 11_000_000_000, "automatic_retry": False})
    put(launch / "start.json", {"schema": "motionwright.manim.editable.outer-launch-start/1",
        "context": context, "origin": origin_binding, "intent_sha256": intent_sha,
        "pid": 27182, "started_monotonic_ns": 11_000_000_001,
        "started_utc": "2026-10-26T00:00:01+00:00", "automatic_retry": False})
    stdout = temporary / "manim-editable-cycle-wrapper.log"
    stderr = temporary / "manim-editable-cycle-wrapper.stderr.log"
    if not stdout.exists(): stdout.write_bytes(b"SYNTHETIC_STDOUT\x00\r\n")
    if not stderr.exists(): stderr.write_bytes(b"SYNTHETIC_STDERR\x00\r\n")
    streams = {name: {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
               for name, path in (("stdout", stdout), ("stderr", stderr))}
    put(launch / "terminal.json", {"schema": "motionwright.manim.editable.outer-launch-terminal/1",
        "label": "native-wrapper", "context": context, "origin": origin_binding, "intent_sha256": intent_sha,
        "status": "EXITED", "pid": 27182, "started_monotonic_ns": 11_000_000_001,
        "finished_monotonic_ns": 12_000_000_000, "finished_utc": "2026-10-26T00:00:02+00:00",
        "launch_requested": True, "process_exit_known": True, "original_exit": 0,
        "termination_requested": False, "original_error": None, "secondary_errors": [],
        "streams": streams, "native_result_claim": "NOT_ASSESSED", "automatic_retry": False})
    cycle_intent = json.loads((evidence / "cycle-intent.json").read_bytes())
    cycle_intent["outer_origin"] = origin_binding
    put(evidence / "cycle-intent.json", cycle_intent)
    return control



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
                 "wrapper-stdout": work / "manim-editable-cycle-wrapper.log",
                 "wrapper-stderr": work / "manim-editable-cycle-wrapper.stderr.log",
                 "outer-origin": work / ("manim-editable-clock-" + self.context["source_sha"]) / "origin.json",
                 **{label: work / ("manim-editable-clock-" + self.context["source_sha"])
                    / "work/native-wrapper" / filename for label, filename in (
                        ("wrapper-intent", "intent.json"), ("wrapper-requested", "launch-requested.json"),
                        ("wrapper-start", "start.json"), ("wrapper-terminal", "terminal.json"))}}

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
        launcher_fixture(work, self.context)
        originals = {label: path.read_bytes() for label, path in paths.items()}
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
                         {label + "/original" for label in case["paths"]} | {"snapshot-manifest.json"}
                         | {"outer-workflow/native-wrapper/" + name for name in (
                             "intent.json", "launch-requested.json", "start.json", "terminal.json")})
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
        for label in ("cycle-result", "edited-custody", "wrapper-stderr", "wrapper-start", "wrapper-terminal"):
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


    def test_known_failed_or_unknown_wrapper_is_retained_but_cannot_publish_success(self):
        for status, code in (("EXITED", 23), ("UNKNOWN", None), ("UNKNOWN", -15)):
            with self.subTest(status=status, code=code):
                case = self.case(status + str(code))
                path = case["paths"]["wrapper-terminal"]
                terminal = json.loads(path.read_bytes())
                terminal.update(status=status, original_exit=code, process_exit_known=code is not None)
                path.write_bytes((json.dumps(terminal, sort_keys=True) + "\n").encode())
                result = self.seal(case)
                self.assertFalse(result["selected_originals_complete"])
                self.assertIn("wrapper-terminal-validation", result["failed_labels"])
                members, _ = self.opened(case)
                self.assertEqual(members["wrapper-terminal/original"], path.read_bytes())
                with self.assertRaisesRegex(ValueError, "complete_original_context"):
                    self.verify(case)


class OuterDeadlineTests(unittest.TestCase):
    """Causal clocks/process doubles; no native process or provider is executed."""
    def setUp(self):
        from native_manim_editable_outer import OuterDeadline, origin_record
        self.temporary = tempfile.TemporaryDirectory(prefix="editable-deadline-",
            dir=os.environ.get("A4_OUTER_TEST_PARENT") or os.environ.get("RUNNER_TEMP"))
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.context = {"source_sha": "a" * 40, "semwright_sha": "b" * 40,
                        "run_id": "17", "run_attempt": "1"}
        self.now = [10_000_000_000]
        self.control = self.root / "clock"
        self.control.mkdir(); (self.control / "work").mkdir(); (self.control / "closing").mkdir()
        record = origin_record(self.context, self.now[0], "synthetic-boot")
        self.deadline = OuterDeadline(record, self.context, "synthetic-boot",
            origin_sha256="c" * 64, root=self.control, clock=lambda: self.now[0])
        for name in ("subprocess.Popen", "subprocess.run", "os.killpg"):
            guard = patch(name, side_effect=AssertionError("actual process operation forbidden"))
            guard.start(); self.addCleanup(guard.stop)

    def fake(self, code=0, *, failure=None):
        from types import SimpleNamespace
        calls = []
        waits = []
        def spawn(argv, **kwargs):
            calls.append((argv, kwargs))
            intent = json.loads((self.control / "work/native-wrapper/intent.json").read_bytes())
            self.assertEqual(intent["execution"], "NOT_STARTED")
            self.assertTrue((self.control / "work/native-wrapper/launch-requested.json").is_file())
            kwargs["stdout"].write(b"original stdout\x00\r\n")
            kwargs["stderr"].write(b"original stderr\x00\r\n")
            def wait(*, timeout):
                waits.append(timeout)
                if failure is not None and len(waits) == 1: raise failure
                return code
            return SimpleNamespace(pid=31415, wait=wait)
        return spawn, calls, waits

    def test_one_origin_consumes_preparation_and_four_phases_without_renewal(self):
        self.now[0] += 600 * 1_000_000_000
        admitted = []
        for consumed in (400, 400, 400, 300):
            admitted.append(self.deadline.remaining("work", 420))
            self.now[0] += consumed * 1_000_000_000
        self.assertEqual(admitted, [420, 420, 420, 390])
        self.assertEqual(self.deadline.remaining("work"), 90)
        self.now[0] = self.deadline.record["work_deadline_ns"]
        from native_manim_editable_outer import DeadlineExceeded
        with self.assertRaises(DeadlineExceeded): self.deadline.remaining("work", 420)
        self.assertEqual(self.deadline.remaining("terminal"), 30)
        self.assertEqual(self.deadline.remaining("seal"), 150)
        self.assertEqual(self.deadline.remaining("upload"), 330)

    def test_original_initializer_cannot_restart_or_overwrite_the_clock(self):
        import native_manim_editable_outer as outer
        env = self.root / "github.env"; env.write_bytes(b"")
        with patch.object(outer, "current_context", return_value=self.context), \
                patch.object(outer, "boot_identity", return_value="synthetic-boot"), \
                patch.dict(os.environ, {"RUNNER_TEMP": str(self.root), "GITHUB_ENV": str(env)}):
            outer.initialize(10_000_000_000)
            origin = outer.control_root(self.root, self.context) / "origin.json"
            before = origin.read_bytes()
            with self.assertRaisesRegex(ValueError, "cannot_restart"):
                outer.initialize(99_000_000_000)
            self.assertEqual(origin.read_bytes(), before)
            self.assertIn(b"MANIM_EDITABLE_OUTER_SHA256=", env.read_bytes())

    def test_missing_foreign_or_reset_clock_is_rejected_without_process_work(self):
        import copy
        import native_manim_editable_outer as outer
        record = self.deadline.record
        for mutation in ("context", "origin", "deadline", "reserve", "boot", "boolean", "float-reserve", "false-retry"):
            altered = copy.deepcopy(record)
            if mutation == "context": altered["context"]["run_id"] = "18"
            elif mutation == "origin": altered["origin_monotonic_ns"] += 1
            elif mutation == "deadline": altered["deadline_ns"] += 1
            elif mutation == "reserve": altered["upload_reserve_seconds"] = 0
            elif mutation == "boot": altered["boot_id"] = "another-boot"
            elif mutation == "boolean": altered["origin_monotonic_ns"] = True
            elif mutation == "float-reserve": altered["seal_reserve_seconds"] = 120.0
            else: altered["automatic_retry"] = 0
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                outer.OuterDeadline(altered, self.context, "synthetic-boot", origin_sha256="c" * 64,
                                    root=self.control, clock=lambda: self.now[0])
        with patch.object(outer, "current_context", return_value=self.context), \
                patch.dict(os.environ, {"RUNNER_TEMP": str(self.root)}, clear=True), \
                self.assertRaisesRegex(ValueError, "original_clock_required"):
            outer.load_deadline()

    def test_expired_preparation_prevents_first_launch_and_every_later_phase(self):
        import native_manim_editable_outer as outer
        self.now[0] = self.deadline.record["work_deadline_ns"]
        spawn, calls, _ = self.fake()
        with self.assertRaises(outer.DeadlineExceeded):
            outer.run_command(self.deadline, "native-wrapper", ["synthetic"], self.root, popen=spawn)
        self.assertEqual(calls, [])
        self.assertFalse((self.control / "work/native-wrapper").exists())
        import argparse
        args = argparse.Namespace(outer_deadline=self.deadline, evidence_root=self.root / "never",
                                  sealed_cycle_root=self.root / "never-sealed", sealed_originals=None,
                                  edited_evidence_root=None, sealed_edited=None)
        with self.assertRaises(outer.DeadlineExceeded):
            cycle.run_source_cycle(args, run_phase=lambda *a, **k: self.fail("later phase"),
                                   validate_phase=lambda *a: self.fail("new phase preflight"))

    def test_direct_launcher_retains_both_original_streams_intent_pid_and_known_exit(self):
        import native_manim_editable_outer as outer
        spawn, calls, waits = self.fake()
        receipt = outer.run_command(self.deadline, "native-wrapper", ["synthetic"], self.root, popen=spawn)
        self.assertEqual((receipt["status"], receipt["pid"], receipt["original_exit"]), ("EXITED", 31415, 0))
        self.assertEqual(waits, [2190])
        self.assertEqual(len(calls), 1)
        for stream, suffix, raw in (("stdout", ".log", b"original stdout\x00\r\n"),
                                    ("stderr", ".stderr.log", b"original stderr\x00\r\n")):
            self.assertEqual((self.root / ("manim-editable-cycle-wrapper" + suffix)).read_bytes(), raw)
            self.assertEqual(receipt["streams"][stream], {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        self.assertFalse(receipt["automatic_retry"])
        self.assertEqual(receipt["native_result_claim"], "NOT_ASSESSED")

    def test_known_original_nonzero_exit_is_retained_and_blocks_retry(self):
        import native_manim_editable_outer as outer
        spawn, calls, _ = self.fake(23)
        with self.assertRaises(outer.OriginalCommandError) as raised:
            outer.run_command(self.deadline, "native-wrapper", ["synthetic"], self.root, popen=spawn)
        self.assertTrue(raised.exception.outcome_known)
        self.assertEqual(raised.exception.receipt["original_exit"], 23)
        with self.assertRaisesRegex(ValueError, "original_stop_no_replay"):
            outer.run_command(self.deadline, "native-wrapper", ["synthetic"], self.root, popen=spawn)
        self.assertEqual(len(calls), 1)

    def test_spawn_uncertainty_keeps_intent_and_unknown_without_fabricating_pid(self):
        import native_manim_editable_outer as outer
        def spawn(*_args, **_kwargs): raise OSError("synthetic spawn observation lost")
        with self.assertRaises(outer.OriginalCommandError) as raised:
            outer.run_command(self.deadline, "native-wrapper", ["synthetic"], self.root, popen=spawn)
        receipt = raised.exception.receipt
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertIsNone(receipt["pid"])
        self.assertIsNone(receipt["original_exit"])
        self.assertFalse(raised.exception.outcome_known)
        self.assertTrue((self.control / "work/native-wrapper/intent.json").is_file())
        self.assertFalse((self.control / "work/native-wrapper/start.json").exists())

    def test_timeout_and_cancellation_keep_unknown_and_consume_only_terminal_reserve(self):
        import subprocess
        import native_manim_editable_outer as outer
        for name, failure in (("timeout", subprocess.TimeoutExpired(["synthetic"], 1)),
                              ("cancel", InterruptedError("synthetic cancellation"))):
            with self.subTest(name=name):
                separate = self.root / name; separate.mkdir(); (separate / "work").mkdir(); (separate / "closing").mkdir()
                self.control = separate
                self.deadline.root = separate
                self.now[0] = self.deadline.record["work_deadline_ns"] - 1_000_000_000
                spawn, calls, waits = self.fake(-15, failure=failure)
                signals = []
                with self.assertRaises(outer.OriginalCommandError) as raised:
                    outer.run_command(self.deadline, "native-wrapper", ["synthetic"], self.root, popen=spawn,
                                      killpg=lambda pid, sig: signals.append((pid, sig)))
                self.assertEqual(raised.exception.receipt["status"], "UNKNOWN")
                self.assertEqual(raised.exception.receipt["original_exit"], -15)
                self.assertEqual(raised.exception.receipt["native_result_claim"], "UNKNOWN")
                self.assertTrue(raised.exception.receipt["termination_requested"])
                self.assertEqual(waits, [1, 20]); self.assertEqual(len(calls), 1)
                self.assertEqual(signals[0][0], 31415)
                # Each independent synthetic fixture owns a separate original stream namespace.
                (self.root / "manim-editable-cycle-wrapper.log").unlink()
                (self.root / "manim-editable-cycle-wrapper.stderr.log").unlink()

    def test_partial_terminal_write_preserves_known_exit_without_replacing_original(self):
        import native_manim_editable_outer as outer
        spawn, calls, _ = self.fake(23)
        writer = outer.new_json
        terminal = self.control / "work/native-wrapper/terminal.json"
        def damaged(path, value):
            if path == terminal:
                path.write_bytes(b'{"partial-original":')
                raise OSError("controlled partial terminal")
            return writer(path, value)
        with patch.object(outer, "new_json", side_effect=damaged), self.assertRaises(OSError):
            outer.run_command(self.deadline, "native-wrapper", ["synthetic"], self.root, popen=spawn)
        self.assertEqual(terminal.read_bytes(), b'{"partial-original":')
        preserved = json.loads((terminal.parent / "terminal-write-error.json").read_bytes())
        self.assertEqual(preserved["original_terminal"]["original_exit"], 23)
        self.assertEqual(len(calls), 1)
        with self.assertRaisesRegex(ValueError, "original_stop_no_replay"):
            outer.run_command(self.deadline, "native-wrapper", ["synthetic"], self.root, popen=spawn)

    def test_terminal_and_stop_failure_preserve_primary_and_attempt_fallback_once(self):
        import native_manim_editable_outer as outer
        spawn, calls, waits = self.fake(23)
        writer = outer.new_json
        terminal = self.control / "work/native-wrapper/terminal.json"
        fallback = terminal.parent / "terminal-write-error.json"
        primary = OSError("controlled primary terminal write")
        attempted = []
        def damaged(path, value):
            attempted.append(path)
            if path == terminal:
                path.write_bytes(b'{"original-terminal-partial":')
                raise primary
            return writer(path, value)
        with patch.object(outer, "new_json", side_effect=damaged), \
                patch.object(outer, "stop_original", side_effect=PermissionError("controlled stop failure")) as stop, \
                self.assertRaises(OSError) as raised:
            outer.run_command(self.deadline, "native-wrapper", ["synthetic"], self.root, popen=spawn)
        self.assertIs(raised.exception, primary)
        self.assertEqual(primary.original_terminal["original_exit"], 23)
        self.assertEqual(primary.secondary_custody_errors,
                         [{"stage": "stop-original", "error": "PermissionError"}])
        self.assertEqual(terminal.read_bytes(), b'{"original-terminal-partial":')
        preserved = json.loads(fallback.read_bytes())
        self.assertEqual(preserved["original_terminal"]["original_exit"], 23)
        self.assertEqual(preserved["secondary_errors"], primary.secondary_custody_errors)
        self.assertEqual((attempted.count(terminal), attempted.count(fallback), stop.call_count), (1, 1, 1))
        self.assertEqual((len(calls), waits), (1, [2190]))
        with self.assertRaises(FileExistsError):
            outer.run_command(self.deadline, "native-wrapper", ["synthetic"], self.root, popen=spawn)
        self.assertEqual(len(calls), 1)

    def test_terminal_and_fallback_failure_preserve_primary_known_exit_and_partial_bytes(self):
        import native_manim_editable_outer as outer
        for fail_stop in (False, True):
            with self.subTest(stop_failure=fail_stop):
                separate = self.root / ("fallback-" + str(fail_stop))
                separate.mkdir(); (separate / "work").mkdir(); (separate / "closing").mkdir()
                self.control = separate; self.deadline.root = separate
                spawn, calls, waits = self.fake(0)
                writer = outer.new_json
                terminal = separate / "work/native-wrapper/terminal.json"
                fallback = terminal.parent / "terminal-write-error.json"
                primary = OSError("controlled primary terminal write")
                attempted = []
                def damaged(path, value):
                    attempted.append(path)
                    if path == terminal:
                        path.write_bytes(b'{"original-terminal-partial":')
                        raise primary
                    if fail_stop and path == separate / "work/stopped.json":
                        raise PermissionError("controlled stop failure")
                    if path == fallback:
                        path.write_bytes(b'{"original-fallback-partial":')
                        raise RuntimeError("controlled fallback failure")
                    return writer(path, value)
                with patch.object(outer, "new_json", side_effect=damaged), self.assertRaises(OSError) as raised:
                    outer.run_command(self.deadline, "native-wrapper", ["synthetic"], self.root, popen=spawn)
                self.assertIs(raised.exception, primary)
                self.assertEqual(primary.original_terminal["original_exit"], 0)
                self.assertTrue(primary.original_terminal["process_exit_known"])
                expected = ([{"stage": "stop-original", "error": "PermissionError"}] if fail_stop else [])
                self.assertEqual(primary.secondary_custody_errors,
                                 expected + [{"stage": "terminal-write-error", "error": "RuntimeError"}])
                self.assertEqual(terminal.read_bytes(), b'{"original-terminal-partial":')
                self.assertEqual(fallback.read_bytes(), b'{"original-fallback-partial":')
                self.assertEqual((attempted.count(terminal), attempted.count(fallback)), (1, 1))
                self.assertEqual((len(calls), waits), (1, [2190]))
                self.assertTrue(any('"original_exit": 0' in note for note in primary.__notes__))
                self.assertFalse(primary.original_terminal["automatic_retry"])
                with self.assertRaises((ValueError, FileExistsError)):
                    outer.run_command(self.deadline, "native-wrapper", ["synthetic"], self.root, popen=spawn)
                self.assertEqual(len(calls), 1)
                (self.root / "manim-editable-cycle-wrapper.log").unlink()
                (self.root / "manim-editable-cycle-wrapper.stderr.log").unlink()

    def test_cancellation_after_observed_wait_keeps_known_exit_without_signalling(self):
        import native_manim_editable_outer as outer
        spawn, calls, waits = self.fake(0)
        after_wait, interrupted = [False], [False]
        remaining = self.deadline.remaining
        failure = InterruptedError("controlled cancellation after original wait")
        def cancel_after_wait(stage="work", cap=None):
            if after_wait[0] and not interrupted[0]:
                interrupted[0] = True
                raise failure
            return remaining(stage, cap)
        def returning_spawn(argv, **kwargs):
            process = spawn(argv, **kwargs)
            wait = process.wait
            def observed_wait(*, timeout):
                code = wait(timeout=timeout)
                after_wait[0] = True
                return code
            process.wait = observed_wait
            return process
        signals = []
        with patch.object(self.deadline, "remaining", side_effect=cancel_after_wait), \
                self.assertRaises(outer.OriginalCommandError) as raised:
            outer.run_command(self.deadline, "native-wrapper", ["synthetic"], self.root,
                              popen=returning_spawn, killpg=lambda *args: signals.append(args))
        receipt = raised.exception.receipt
        self.assertEqual((len(calls), waits, signals), (1, [2190], []))
        self.assertEqual((receipt["status"], receipt["original_exit"]), ("UNKNOWN", 0))
        self.assertTrue(receipt["process_exit_known"])
        self.assertFalse(receipt["termination_requested"])
        self.assertEqual(receipt["original_error"], "InterruptedError")
        self.assertEqual(receipt["native_result_claim"], "UNKNOWN")
        self.assertFalse(raised.exception.outcome_known)
        self.assertEqual(json.loads((self.control / "work/native-wrapper/terminal.json").read_bytes()), receipt)
        with self.assertRaisesRegex(ValueError, "original_stop_no_replay"):
            outer.run_command(self.deadline, "native-wrapper", ["synthetic"], self.root, popen=spawn)
        self.assertEqual(len(calls), 1)

    def test_known_failure_stop_error_does_not_replace_original_command_receipt(self):
        import native_manim_editable_outer as outer
        spawn, calls, waits = self.fake(23)
        with patch.object(outer, "stop_original", side_effect=PermissionError("controlled stop failure")) as stop, \
                self.assertRaises(outer.OriginalCommandError) as raised:
            outer.run_command(self.deadline, "native-wrapper", ["synthetic"], self.root, popen=spawn)
        failure = raised.exception
        self.assertTrue(failure.outcome_known)
        self.assertEqual((failure.receipt["status"], failure.receipt["original_exit"]), ("EXITED", 23))
        self.assertEqual(failure.secondary_custody_errors,
                         [{"stage": "stop-original", "error": "PermissionError"}])
        self.assertEqual(json.loads((self.control / "work/native-wrapper/terminal.json").read_bytes()), failure.receipt)
        self.assertEqual((stop.call_count, len(calls), waits), (1, 1, [2190]))
        with self.assertRaises(FileExistsError):
            outer.run_command(self.deadline, "native-wrapper", ["synthetic"], self.root, popen=spawn)
        self.assertEqual(len(calls), 1)


    def test_action_admission_uses_remaining_whole_minutes_and_protects_upload(self):
        import native_manim_editable_outer as outer
        output = self.root / "github.output"; output.write_bytes(b"")
        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
            self.now[0] = self.deadline.record["work_deadline_ns"] - 125_000_000_000
            row = outer.admit_action(self.deadline, "sdk-checkout")
            self.assertEqual(row["timeout_minutes"], 2)
            self.now[0] = self.deadline.record["work_deadline_ns"] - 60_000_000_000
            with self.assertRaises(outer.DeadlineExceeded): outer.admit_action(self.deadline, "toolchain")
            self.now[0] = self.deadline.record["seal_deadline_ns"]
            self.assertEqual(outer.admit_action(self.deadline, "private-upload")["timeout_minutes"], 2)
        self.assertEqual(output.read_bytes(), b"minutes=2\nminutes=2\n")

    def test_actual_e2e_capture_uses_remaining_original_clock_not_fresh420(self):
        from types import SimpleNamespace
        import native_manim_e2e as wrapper
        self.now[0] = self.deadline.record["work_deadline_ns"] - 7_000_000_000
        evidence = self.root / "capture"; evidence.mkdir()
        (self.control / "work/native-wrapper").mkdir()
        waits = []
        def spawn(_argv, **kwargs):
            kwargs["stdout"].write(b'{"synthetic":true}\n')
            self.now[0] += 2_000_000_000
            return SimpleNamespace(pid=31415, wait=lambda *, timeout: waits.append(timeout) or 0)
        with patch.object(wrapper, "_EDITABLE_OUTER_DEADLINE", self.deadline), \
                patch.object(wrapper.subprocess, "Popen", side_effect=spawn):
            result = wrapper.captured_json(["synthetic"], env={}, evidence=evidence, label="native", timeout=420)
        self.assertEqual(result, {"synthetic": True})
        self.assertEqual(waits, [5])

    def test_known_zero_that_finishes_after_deadline_is_retained_but_never_admitted(self):
        import native_manim_editable_outer as outer
        spawn, calls, _ = self.fake()
        def late_spawn(argv, **kwargs):
            process = spawn(argv, **kwargs)
            def late_wait(*, timeout):
                self.now[0] = self.deadline.record["work_deadline_ns"] + 1
                return 0
            process.wait = late_wait
            return process
        with self.assertRaises(outer.OriginalCommandError) as raised:
            outer.run_command(self.deadline, "native-wrapper", ["synthetic"], self.root, popen=late_spawn)
        self.assertEqual(raised.exception.receipt["original_exit"], 0)
        self.assertEqual(raised.exception.receipt["status"], "EXITED")
        self.assertIn("DEADLINE_EXHAUSTED_AFTER_ORIGINAL_EXIT", raised.exception.receipt["secondary_errors"])
        self.assertFalse(raised.exception.receipt["termination_requested"])
        self.assertTrue(raised.exception.outcome_known)
        self.assertEqual(len(calls), 1)

    def test_early_failure_custody_has_a_bounded_seal_cap_and_keeps_upload_reserve(self):
        from types import SimpleNamespace
        import native_manim_editable_outer as outer
        waits = []
        def spawn(_argv, **kwargs):
            kwargs["stdout"].write(b"synthetic seal stdout")
            kwargs["stderr"].write(b"")
            return SimpleNamespace(pid=31415, wait=lambda *, timeout: waits.append(timeout) or 0)
        outer.stop_original(self.deadline, "native-wrapper", "UNKNOWN")
        result = outer.run_command(self.deadline, "seal-outer", ["synthetic"], self.root, popen=spawn)
        self.assertEqual(result["status"], "EXITED")
        self.assertEqual(waits, [110])
        self.assertEqual(self.deadline.record["deadline_ns"] - self.deadline.record["seal_deadline_ns"],
                         180_000_000_000)

    def test_workflow_origin_precedes_preparation_and_preserves_native_guard(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/native-manim-e2e.yml").read_text()
        native = workflow.split("  native-manim-roundtrip:\n", 1)[1]
        expected = """    needs: source-contracts
    if: >-
      ${{ github.event_name == 'workflow_dispatch' &&
          (inputs.scenario == 'static-scene' ||
           inputs.scenario == 'camera120' ||
           inputs.scenario == 'camera120-source-cycle' ||
           inputs.scenario == 'editable-source-cycle') }}
"""
        self.assertTrue(native.startswith(expected))
        self.assertLess(native.index("Start one editable outer clock"), native.index("Install pinned private-custody"))
        for label in ("install-custody", "validate-editable", "resolve-pin", "verify-sources", "install-system",
                      "provision-runtime", "sandbox-profile", "build-sdk", "build-product", "rust-oracles"):
            self.assertEqual(native.count("workflow-step --label " + label + " --script"), 1)
        self.assertEqual(native.count("native_manim_editable_outer.py launch-native"), 1)
        self.assertNotIn('| tee "$RUNNER_TEMP/manim-editable-cycle-wrapper.log"', native)
        self.assertIn("steps.editable_outer_origin.outcome == 'success'", native)
        self.assertIn("steps.editable_private_upload_budget.outputs.minutes", native)



@unittest.skipUnless(sys.platform == "linux", "real inert POSIX custody controls require Linux")
class R4OriginalChildCausals(unittest.TestCase):
    """Real Python children/EOF only; no Manim, broker, Cargo, renderer or network."""
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        der = cls.private.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.recipient = {"spki_der_base64": base64.b64encode(der).decode(),
                         "sha256": hashlib.sha256(der).hexdigest()}

    def setUp(self):
        import native_manim_editable_outer as outer
        import native_manim_e2e as wrapper
        self.outer, self.wrapper = outer, wrapper
        retained = os.environ.get("A6_MANIM_CAUSAL_ROOT")
        if retained:
            self.base = Path(retained).resolve(strict=True) / self._testMethodName
            self.base.mkdir(mode=0o700, exist_ok=False)
        else:
            temporary = tempfile.TemporaryDirectory(prefix="manim-inert-posix-",
                dir=os.environ.get("A4_OUTER_TEST_PARENT") or os.environ.get("RUNNER_TEMP"))
            self.addCleanup(temporary.cleanup)
            self.base = Path(temporary.name).resolve(strict=True)
        self.repo = self.base / "source-fixture"
        self.repo.mkdir(mode=0o700)
        self.context = {"source_sha": "a" * 40, "semwright_sha": "b" * 40,
                        "run_id": "17", "run_attempt": "1"}
        (self.repo / "SOURCE_LOCK.json").write_text(json.dumps({"dependencies": {
            "semwright": {"revision": self.context["semwright_sha"]}}}))
        self.control = outer.control_root(self.base, self.context)
        self.control.mkdir(mode=0o700)
        (self.control / "work").mkdir(mode=0o700)
        (self.control / "closing").mkdir(mode=0o700)
        start = time.monotonic_ns() - (outer.WORK_SECONDS - 30) * outer.NANOSECOND
        record = outer.origin_record(self.context, start, outer.boot_identity())
        digest = outer.new_json(self.control / "origin.json", record)
        self.deadline = outer.OuterDeadline(record, self.context, record["boot_id"],
            origin_sha256=digest, root=self.control)
        self.environment = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_JOB": "native-manim-roundtrip", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_RUN_ID": "17", "GITHUB_SHA": self.context["source_sha"],
            "GITHUB_WORKSPACE": str(self.repo), "RUNNER_TEMP": str(self.base),
            "MANIM_EDITABLE_OUTER_ORIGIN": str(self.control / "origin.json"),
            "MANIM_EDITABLE_OUTER_SHA256": digest, "PYTHONDONTWRITEBYTECODE": "1"}
        for name, value in (("_EDITABLE_OUTER_DEADLINE", self.deadline),
                            ("_OWNED_PHASE_CHILDREN", []), ("_DEFERRED_CANCELLATIONS", []),
                            ("_CHILD_LAUNCH_CRITICAL", 0), ("_CHILD_CLEANUP_CRITICAL", 0),
                            ("_EDITABLE_CHILD_STREAM_BYTES", 0)):
            guard = patch.object(wrapper, name, value)
            guard.start(); self.addCleanup(guard.stop)
        outer.new_json(self.base / "SOURCE_CONTROL.json", {
            "kind": "REAL_INERT_POSIX_PROCESS_CONTROL_NOT_MANIM_EVIDENCE",
            "synthetic_source_context": self.context,
            "shortened_fixture_work_window_seconds": 30, "original_terminal_reserve_seconds": 30,
            "product_renderer_executed": False, "network_used": False})

    def script(self, name, body):
        path = self.base / (name + ".py")
        header = ("import os,sys,json,time,signal\nfrom pathlib import Path\n"
                  + "sys.path.insert(0, " + repr(str(Path(__file__).resolve().parent)) + ")\n")
        path.write_text(header + body, encoding="utf-8")
        return path

    def wait_file(self, path, cap=5):
        end = time.monotonic() + cap
        while not path.is_file() and time.monotonic() < end:
            time.sleep(.01)
        self.assertTrue(path.is_file(), "inert original child did not publish readiness")

    def cleanup_original(self, process):
        # Test safety cleanup only through this test's retained Popen object.
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=2)

    def fifo(self, name):
        path = self.base / (name + ".fifo")
        os.mkfifo(path, 0o600)
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        self.addCleanup(os.close, fd)
        return path, fd

    def inert(self, name, fifo, *, resistant=False):
        ready = self.base / (name + ".ready")
        script = self.script(name, """\
if RESISTANT:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(FIFO, 'wb', buffering=0) as writer:
    writer.write(b'R')
    Path(READY).write_text(json.dumps({'pid':os.getpid(),'sid':os.getsid(0),'kind':'INERT_SOURCE_CONTROL'}))
    print('INERT_CHILD_STDOUT', flush=True)
    print('INERT_CHILD_STDERR', file=sys.stderr, flush=True)
    end=time.monotonic()+12
    while time.monotonic()<end:
        time.sleep(.02)
""".replace("RESISTANT", repr(resistant)).replace("FIFO", repr(str(fifo))).replace("READY", repr(str(ready))))
        return script, ready

    def assert_eof(self, fd):
        end = time.monotonic() + 2
        while time.monotonic() < end:
            try:
                data = os.read(fd, 65536)
            except BlockingIOError:
                time.sleep(.01)
                continue
            if not data:
                self.outer.new_json(self.base / ("eof-%d.json" % fd), {
                    "kind": "INERT_ORIGINAL_PIPE_EOF_OBSERVATION", "read_fd": fd,
                    "original_path": os.readlink("/proc/self/fd/%d" % fd),
                    "read_result_bytes": 0, "observed_monotonic_ns": time.monotonic_ns(),
                    "writer_readiness_byte_already_observed": True})
                return
        self.fail("original inert writer EOF was not observed")

    def test_prephase_systemexit_retains_original_chain_and_traceback_in_existing_seal(self):
        marker = self.base / "forbidden-camera-custody"
        script = self.script("prephase-wrapper", """\
import native_manim_e2e as wrapper
import native_manim_camera_evidence as camera
def forbidden(*args, **kwargs):
    Path(MARKER).write_text('forbidden')
    raise AssertionError('phase custody must not be constructed')
camera.CameraCustody=forbidden
original_exact_inputs=wrapper.exact_inputs
wrapper.subprocess.check_output=lambda *args,**kwargs: 'c'*40
def prephase():
    try:
        original_exact_inputs()
    except SystemExit as original:
        raise original from OSError('INERT_PRIVATE_CAUSE')
wrapper.exact_inputs=prephase
raise SystemExit(wrapper.entrypoint(['--scenario','editable-source-cycle']))
""".replace("MARKER", repr(str(marker))))
        with patch.dict(os.environ, self.environment), self.assertRaises(self.outer.OriginalCommandError) as raised:
            self.outer.run_command(self.deadline, "native-wrapper",
                [sys.executable, "-B", "-E", "-s", str(script)], self.repo)
        terminal = raised.exception.receipt
        self.assertEqual((terminal["original_exit"], terminal["original_error"]), (2, "SystemExit"))
        envelope_path = self.control / "work/native-wrapper/wrapper-error.json"
        envelope = json.loads(envelope_path.read_bytes())
        original = envelope["error"]["original"]
        self.assertEqual(original["message"],
            "Semwright checkout mismatch: expected " + self.wrapper.PIN + ", got " + "c" * 40)
        self.assertEqual(original["cause"]["message"], "INERT_PRIVATE_CAUSE")
        self.assertTrue(any(frame["function"] == "prephase" for frame in original["traceback"]))
        self.assertTrue(any(frame["function"] == "exact_inputs" for frame in original["traceback"]))
        self.assertEqual(envelope["stage"], "exact-inputs-before-phase-custody")
        self.assertEqual(envelope["pid"], terminal["pid"])
        self.assertFalse(marker.exists())
        self.assertNotIn(b"INERT_PRIVATE", (self.base / "manim-editable-cycle-wrapper.stderr.log").read_bytes())
        evidence = self.base / ("manim-editable-private-" + self.context["source_sha"])
        sealed = self.base / ("manim-editable-sealed-" + self.context["source_sha"])
        with patch.dict(os.environ, self.environment):
            receipt = cycle.seal_outer_cycle(evidence, sealed, self.recipient, self.context)
        self.assertFalse(receipt["selected_originals_complete"])
        self.assertFalse((evidence / "cycle-result.json").exists())
        raw, context = engine.decrypt_snapshot((sealed / "cycle-outer.mwseal").read_bytes(), self.private)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            self.assertEqual(archive.extractfile("outer-workflow/native-wrapper/wrapper-error.json").read(), envelope_path.read_bytes())
        self.assertEqual(context, self.context)

    def test_postspawn_deadline_cleans_original_child_and_observes_eof_once(self):
        launch = self.control / "work/native-wrapper"; launch.mkdir()
        evidence = self.base / "capture"; evidence.mkdir(mode=0o700)
        fifo, fd = self.fifo("deadline-child")
        child_script, ready = self.inert("deadline-child", fifo)
        real_popen, processes = subprocess.Popen, []
        remaining = self.deadline.remaining
        def budget(stage="work", cap=None):
            if processes and stage == "work":
                raise self.outer.DeadlineExceeded("INERT_POSTSPAWN_DEADLINE")
            return remaining(stage, cap)
        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs); processes.append(process)
            self.addCleanup(self.cleanup_original, process)
            self.wait_file(ready)
            self.assertEqual(os.read(fd, 1), b"R")
            return process
        with patch.object(self.deadline, "remaining", side_effect=budget), \
                patch.object(self.wrapper.subprocess, "Popen", side_effect=spawn), \
                self.assertRaisesRegex(self.outer.DeadlineExceeded, "INERT_POSTSPAWN_DEADLINE"):
            self.wrapper.captured_json([sys.executable, "-B", str(child_script)], env=dict(os.environ),
                evidence=evidence, label="deadline-child")
        row = json.loads((evidence / "deadline-child.process.json").read_bytes())
        self.assertEqual(len(processes), 1)
        self.assertTrue(row["process_exit_known"])
        self.assertEqual(row["process_returncode"], -signal.SIGTERM)
        self.assertEqual(row["status"], "UNKNOWN")
        self.assertEqual(row["original_error"]["original"]["type"], "DeadlineExceeded")
        self.assertEqual(len(row["termination_requests"]), 1)
        self.assert_eof(fd)

    def test_real_signal_during_popen_publication_retains_and_closes_that_original(self):
        (self.control / "work/native-wrapper").mkdir()
        evidence = self.base / "capture"; evidence.mkdir(mode=0o700)
        fifo, fd = self.fifo("publication-child")
        child_script, ready = self.inert("publication-child", fifo)
        real_popen, processes = subprocess.Popen, []
        previous = signal.signal(signal.SIGTERM, self.wrapper.editable_cancelled)
        self.addCleanup(signal.signal, signal.SIGTERM, previous)
        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs); processes.append(process)
            self.addCleanup(self.cleanup_original, process)
            self.wait_file(ready); self.assertEqual(os.read(fd, 1), b"R")
            os.kill(os.getpid(), signal.SIGTERM)
            return process
        with patch.object(self.wrapper.subprocess, "Popen", side_effect=spawn), \
                self.assertRaisesRegex(InterruptedError, "original editable wrapper cancellation"):
            self.wrapper.captured_json([sys.executable, "-B", str(child_script)], env=dict(os.environ),
                evidence=evidence, label="publication-child")
        row = json.loads((evidence / "publication-child.process.json").read_bytes())
        self.assertEqual((len(processes), row["pid"]), (1, processes[0].pid))
        self.assertEqual(row["process_returncode"], -signal.SIGTERM)
        self.assertTrue(row["process_exit_known"])
        self.assertEqual(row["original_error"]["original"]["type"], "InterruptedError")
        self.assert_eof(fd)

    def test_separate_term_resistant_client_and_broker_close_before_outer_hard_kill(self):
        client_fifo, client_fd = self.fifo("resistant-client")
        broker_fifo, broker_fd = self.fifo("resistant-broker")
        client_script, client_ready = self.inert("resistant-client", client_fifo, resistant=True)
        broker_script, broker_ready = self.inert("resistant-broker", broker_fifo, resistant=True)
        marker = self.base / "forbidden-next-phase"
        evidence = self.base / "inert-phase"
        body = """\
import native_manim_e2e as wrapper
import native_manim_editable_cycle as cycle
wrapper.exact_inputs=lambda: None
original_retain=wrapper.OriginalPhaseChild.retain
def retain_after_all_terminals(self,*args,**kwargs):
    originals=[child for child in wrapper._OWNED_PHASE_CHILDREN if child.process is not None]
    assert len(originals)==2 and all(type(child.code) is int for child in originals), 'custody before original child cleanup'
    audit=Path(EVIDENCE)/'cleanup-before-custody.json'
    if not audit.exists():
        audit.write_text(json.dumps({'originals':[{'pid':child.process.pid,'exit':child.code} for child in originals],
                                    'actual_terminals_observed_before_custody':True}))
    return original_retain(self,*args,**kwargs)
wrapper.OriginalPhaseChild.retain=retain_after_all_terminals
def inert_phase(*args, **kwargs):
    evidence=Path(EVIDENCE); evidence.mkdir(mode=0o700)
    log=evidence/'broker.log'; stream=log.open('wb',buffering=0)
    owner=wrapper.OriginalPhaseChild('broker',evidence)
    try:
        owner.launch([sys.executable,'-B',BROKER],env=dict(os.environ),stdin=-3,
                     stdout=stream,stderr=stream,start_new_session=True)
        wrapper.captured_json([sys.executable,'-B',CLIENT],env=dict(os.environ),
                              evidence=evidence,label='phase-client')
        Path(MARKER).write_text('forbidden')
    finally:
        primary=sys.exc_info()[1]
        owner.stop(primary)
        stream.flush(); os.fsync(stream.fileno()); stream.close()
        owner.retain({'combined':log},primary)
cycle.run_source_cycle=inert_phase
raise SystemExit(wrapper.entrypoint(['--scenario','editable-source-cycle']))
"""
        for name, value in (("EVIDENCE", evidence), ("BROKER", broker_script), ("CLIENT", client_script), ("MARKER", marker)):
            body = body.replace(name, repr(str(value)))
        script = self.script("inert-wrapper", body)
        real_popen, processes, signals = subprocess.Popen, [], []
        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs); processes.append(process)
            self.addCleanup(self.cleanup_original, process)
            original_wait, first = process.wait, [True]
            def wait(*, timeout):
                if first[0]:
                    first[0] = False
                    self.wait_file(client_ready); self.wait_file(broker_ready)
                    self.assertEqual(os.read(client_fd, 1), b"R")
                    self.assertEqual(os.read(broker_fd, 1), b"R")
                    raise InterruptedError("INERT_OUTER_CANCELLATION")
                return original_wait(timeout=timeout)
            process.wait = wait
            return process
        def signal_original(pid, sig):
            signals.append((pid, sig)); os.killpg(pid, sig)
        before = time.monotonic()
        with patch.dict(os.environ, self.environment), self.assertRaises(self.outer.OriginalCommandError) as raised:
            self.outer.run_command(self.deadline, "native-wrapper",
                [sys.executable, "-B", "-E", "-s", str(script)], self.repo,
                popen=spawn, killpg=signal_original)
        self.assertGreater(time.monotonic() - before, 5)
        self.assertEqual(len(processes), 1)
        self.assertEqual(signals, [(processes[0].pid, signal.SIGTERM)])
        self.assertEqual(raised.exception.receipt["original_exit"], 2)
        self.assertEqual(raised.exception.receipt["original_error"], "InterruptedError")
        for name, ready in (("phase-client", client_ready), ("broker", broker_ready)):
            row = json.loads((evidence / (name + ".process.json")).read_bytes())
            identity = json.loads(ready.read_bytes())
            self.assertEqual(row["pid"], identity["pid"])
            self.assertEqual(identity["pid"], identity["sid"])
            self.assertNotEqual(identity["sid"], processes[0].pid)
            self.assertTrue(row["process_exit_known"])
            self.assertEqual(row["process_returncode"], -signal.SIGKILL)
            self.assertEqual(row["status"], "UNKNOWN")
            self.assertEqual([item["signal"] for item in row["termination_requests"]], [signal.SIGTERM, signal.SIGKILL])
            self.assertEqual(row["original_error"]["original"]["type"], "InterruptedError")
        self.assertFalse(marker.exists())
        self.assertTrue(json.loads((evidence / "cleanup-before-custody.json").read_bytes())["actual_terminals_observed_before_custody"])
        self.assert_eof(client_fd); self.assert_eof(broker_fd)
        child_records = list((self.control / "work/native-wrapper/children").glob("*/terminal.json"))
        self.assertEqual(len(child_records), 2)

    def test_reaped_original_nonzero_is_never_signalled_or_reclassified_by_late_cancellation(self):
        (self.control / "work/native-wrapper").mkdir()
        evidence = self.base / "capture"; evidence.mkdir(mode=0o700)
        script = self.script("nonzero-original", "print('INERT_ORIGINAL_NONZERO',flush=True)\nraise SystemExit(23)\n")
        output = evidence / "original.log"
        child = self.wrapper.OriginalPhaseChild("nonzero", evidence)
        with output.open("wb", buffering=0) as stream:
            process = child.launch([sys.executable, "-B", str(script)], env=dict(os.environ),
                stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, start_new_session=True)
            self.addCleanup(self.cleanup_original, process)
            self.assertEqual(child.observe_wait(5), 23)
        with patch.object(os, "killpg", side_effect=AssertionError("reaped PID must not be signalled")):
            child.stop(InterruptedError("INERT_LATE_CANCELLATION"))
            child.retain({"combined": output})
        self.assertEqual((child.terminal["status"], child.terminal["process_returncode"]), ("EXITED", 23))
        self.assertTrue(child.terminal["original_wait_completed_before_error"])
        self.assertEqual(child.terminal["termination_requests"], [])

    def test_deferred_cancel_during_normal_cleanup_is_retained_and_blocks_next_launch(self):
        (self.control / "work/native-wrapper").mkdir()
        evidence = self.base / "cleanup"; evidence.mkdir(mode=0o700)
        fifo, fd = self.fifo("normal-cleanup-child")
        child_script, ready = self.inert("normal-cleanup-child", fifo)
        owner = self.wrapper.OriginalPhaseChild("normal-cleanup-child", evidence)
        previous = signal.signal(signal.SIGTERM, self.wrapper.editable_cancelled)
        self.addCleanup(signal.signal, signal.SIGTERM, previous)
        real_popen, processes = subprocess.Popen, []
        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs); processes.append(process)
            self.addCleanup(self.cleanup_original, process)
            return process
        with patch.object(self.wrapper.subprocess, "Popen", side_effect=spawn), \
                (evidence / "combined.log").open("xb", buffering=0) as stream:
            owner.launch([sys.executable, "-B", str(child_script)], env=dict(os.environ),
                stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, start_new_session=True)
            self.wait_file(ready); self.assertEqual(os.read(fd, 1), b"R")
            real_wait, cancellation_sent = owner.observe_wait, []
            def wait_with_cancellation(timeout, *, cleanup=False):
                if cleanup and not cancellation_sent:
                    cancellation_sent.append(True)
                    os.kill(os.getpid(), signal.SIGTERM)
                return real_wait(timeout, cleanup=cleanup)
            with patch.object(owner, "observe_wait", side_effect=wait_with_cancellation):
                owner.stop()
            self.assertEqual(owner.code, -signal.SIGTERM)
            self.assertEqual(self.wrapper._DEFERRED_CANCELLATIONS, [signal.SIGTERM])
        owner.retain({"combined": evidence / "combined.log"})
        row = json.loads((evidence / "normal-cleanup-child.process.json").read_bytes())
        self.assertTrue(row["process_exit_known"])
        self.assertEqual(row["process_returncode"], -signal.SIGTERM)
        self.assertEqual(len(row["termination_requests"]), 1)
        self.assert_eof(fd)
        with self.assertRaisesRegex(InterruptedError, "original editable wrapper cancellation") as original:
            self.wrapper.raise_deferred_cancellation()
        blocked = self.wrapper.OriginalPhaseChild("blocked-next-child", evidence)
        with patch.object(self.wrapper.subprocess, "Popen") as forbidden, \
                self.assertRaisesRegex(InterruptedError, "original editable wrapper cancellation"):
            blocked.launch(["forbidden-next-original"])
        forbidden.assert_not_called()
        self.assertIsNone(blocked.process)
        self.assertFalse((blocked.folder / "intent.json").exists())
        self.assertEqual(len(processes), 1)
        self.outer.new_json(self.base / "cleanup-cancellation.json", {
            "original_error": self.outer.private_exception(original.exception),
            "original_process_terminal_retained_before_error": True,
            "next_original_launch_attempted": False, "automatic_retry": False})

    def test_partial_prephase_envelope_keeps_primary_and_one_private_successor(self):
        launch = self.control / "work/native-wrapper"; launch.mkdir()
        primary = SystemExit("INERT_PRIMARY_MESSAGE_" + "x" * 20000)
        original_write = self.outer.new_bytes
        attempted = []
        def damaged(path, raw):
            attempted.append(path.name)
            if path.name == "wrapper-error.json":
                original_write(path, raw[:11])
                raise OSError("INERT_PRIMARY_CUSTODY_WRITE_FAILURE")
            return original_write(path, raw)
        with patch.object(self.outer, "new_bytes", side_effect=damaged):
            self.outer.retain_wrapper_error(self.deadline, primary, "exact-inputs-before-phase-custody")
        self.assertEqual(attempted, ["wrapper-error.json", "wrapper-error-successor.json"])
        self.assertEqual((launch / "wrapper-error.json").stat().st_size, 11)
        successor = json.loads((launch / "wrapper-error-successor.json").read_bytes())
        self.assertEqual(successor["error"]["original"]["type"], "SystemExit")
        self.assertTrue(successor["error"]["text_truncated"])
        self.assertEqual(successor["first_custody_error"]["original"]["type"], "OSError")
        self.assertFalse(successor["automatic_retry"])
        self.assertEqual(str(primary), "INERT_PRIMARY_MESSAGE_" + "x" * 20000)


if __name__ == "__main__":
    unittest.main()
