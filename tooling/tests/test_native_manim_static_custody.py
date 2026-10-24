"""Static Manim custody source regressions; no Manim/Host render or real key.

The wrapper and snapshot reader run on real private files. Broker, native-render
and ffprobe responses in the full-route tests are explicitly synthetic process
controls. Encryption is the existing failpoint double: no RSA key is generated,
installed, admitted or represented as an independent custodian. Two dedicated
capture tests start only this test interpreter, never a native renderer.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tooling"))
import ci_private_evidence as private
import native_manim_camera_evidence as camera
import native_manim_e2e as wrapper
from tooling.tests.test_native_manim_camera_evidence import (
    CONTEXT, RECIPIENT, FakeCustody,
)

OBSERVATION = "motionwright-manim-runtime-observation.json"
NATIVE = "motionwright-manim-evidence.json"


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


class SyntheticPosixTicketMode:
    """Windows-only permission view for one synthetic Broker ticket."""
    def __init__(self, observed):
        self._observed = observed
        self.st_mode = (observed.st_mode & ~0o777) | 0o600

    def __getattr__(self, name):
        return getattr(self._observed, name)


class SnapshotCustody(FakeCustody):
    """Real exclusive writes and bounded snapshot; synthetic encryption only."""
    def __init__(self):
        super().__init__()
        self.snapshot_calls = 0
        self.archives = []
        self.roots = None

    def new_bytes(self, path, data):
        if self.write_failure == path.name:
            raise OSError("injected exclusive custody write failure")
        private.new_bytes(path, data)

    def new_json(self, path, value):
        self.new_bytes(path, private.json_bytes(value))

    def gather_snapshot(self, roots, *, preserve_partial):
        if preserve_partial is not True:
            raise AssertionError("partial original capture must be explicit")
        self.snapshot_calls += 1
        self.roots = dict(roots)
        raw, complete = private.gather_snapshot(roots, preserve_partial=True)
        self.archives.append(raw)
        return raw, complete

    def captured(self):
        with tarfile.open(fileobj=io.BytesIO(self.archives[-1]), mode="r:") as archive:
            return {member.name: archive.extractfile(member).read()
                    for member in archive.getmembers() if member.isfile()}


class StaticCustodyTests(unittest.TestCase):
    def setUp(self):
        # Local Windows G0 remains inside this checkout. Linux CI uses its
        # private temporary directory so the owner socket stays within AF_UNIX.
        self.temporary = tempfile.TemporaryDirectory(
            prefix="mw-static-unit-", dir=ROOT if os.name == "nt" else None,
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve(strict=True)
        self.root.chmod(0o700)
        self.sealed = self.root / "sealed"
        self.sealed.mkdir(mode=0o700)
        self.evidence = self.root / "evidence"
        self.args = argparse.Namespace(
            scenario="static-scene", evidence_root=self.evidence,
            sealed_originals=self.sealed / "originals.mwci",
            edited_evidence_root=None, sealed_edited=None,
        )
        self.engine = SnapshotCustody()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(
            camera, "custody_module", return_value=self.engine,
        ))
        self.stack.enter_context(mock.patch.dict(os.environ, {
            "RUNNER_TEMP": str(self.root),
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_RUN_ID": CONTEXT["run_id"],
            "PRIVATE_EVIDENCE_RECIPIENT_SPKI": RECIPIENT["spki_der_base64"],
            "PRIVATE_EVIDENCE_RECIPIENT_SHA256": RECIPIENT["sha256"],
        }))
        self.stack.enter_context(mock.patch.object(wrapper, "GITHUB_SHA", CONTEXT["source_sha"]))
        self.stack.enter_context(mock.patch.object(wrapper, "PIN", CONTEXT["semwright_sha"]))
        self.stack.enter_context(mock.patch.object(wrapper.os, "O_NOFOLLOW",
                                                   getattr(os, "O_NOFOLLOW", 0), create=True))
        self.assets = self.root / "assets"
        self.assets.mkdir(mode=0o700)
        for name in ("semwright", "semwrightd", "semwright-sandbox",
                     "native-example", "driver", "runner", "python", "ffmpeg", "ffprobe"):
            (self.assets / name).write_bytes(("unit executable fixture: " + name).encode())
        self.stack.enter_context(mock.patch.object(wrapper, "SW_BINS", self.assets))
        for field, name in (
            ("MW_EXAMPLE", "native-example"), ("MW_DRIVER", "driver"),
            ("MW_RUNNER", "runner"), ("PYTHON", "python"),
            ("FFMPEG", "ffmpeg"), ("FFPROBE", "ffprobe"),
        ):
            self.stack.enter_context(mock.patch.object(wrapper, field, self.assets / name))
        self.stack.enter_context(mock.patch.object(wrapper, "MANIM_RUNTIME", self.assets))
        self.stack.enter_context(mock.patch.object(wrapper, "FONTCONFIG", self.assets))
        self.stack.enter_context(mock.patch.object(wrapper, "semwright_version",
                                                   return_value="1.0.0"))
        self.seed = {
            "project_id": "00000000-0000-0000-0000-000000000001",
            "resource": "project:00000000-0000-0000-0000-000000000001",
            "generation": "00000000-0000-0000-0000-000000000002",
            "revision": 7,
            "scene_id": "00000000-0000-0000-0000-000000000003",
            "primitive_count": 3,
        }
        self.observation = {
            "schema": "motionwright-manim-runtime-probe-e2e/1",
            **{key: self.seed[key] for key in ("project_id", "generation", "revision")},
            "observation": {"source_unit_fixture_only": True},
            "error": None, "render_started": False,
        }
        self.media = b"\x00\x00\x00\x18ftypunit-fixture-not-native-video"
        self.native = {
            "native_manim_e2e": "PASS",
            "runtime_observation": self.observation["observation"],
            **{key: self.seed[key] for key in
               ("project_id", "resource", "generation", "revision", "scene_id")},
            "result": {"artifact": {
                "relative_path": "videos/unit.mp4",
                "sha256": camera.sha(self.media), "bytes": len(self.media),
            }},
            "source_unit_fixture_only": True,
        }
        self.mode = "success"
        self.native_calls = 0
        self.seed_calls = 0
        self.command_calls = []
        self.cleanup_observations = []
        self.timeout = subprocess.TimeoutExpired(["unit-native-example"], 420)
        self.daemon = mock.Mock(pid=41001)
        self.daemon.poll.return_value = None
        self.daemon.wait.return_value = 0
        self.daemon_stream = None
        self.session_ticket = None

    def route(self):
        if os.name == "nt":
            actual_stat = Path.stat
            def unit_ticket_stat(path, *, follow_symlinks=True):
                observed = actual_stat(path, follow_symlinks=follow_symlinks)
                if (follow_symlinks and self.session_ticket is not None
                        and path == self.session_ticket):
                    return SyntheticPosixTicketMode(observed)
                return observed
            self.stack.enter_context(mock.patch.object(
                Path, "stat", new=unit_ticket_stat,
            ))
        self.options = self.stack.enter_context(mock.patch.object(
            wrapper, "camera_options", return_value=(RECIPIENT, CONTEXT),
        ))
        self.stack.enter_context(mock.patch.object(wrapper, "wait_for_socket"))
        self.stack.enter_context(mock.patch.object(
            wrapper, "run_json", side_effect=AssertionError("uncaptured fallback is forbidden"),
        ))
        self.stack.enter_context(mock.patch.object(
            wrapper.tempfile, "TemporaryDirectory",
            side_effect=AssertionError("native owner must not be automatically removed"),
        ))
        self.spawn = self.stack.enter_context(mock.patch.object(
            wrapper.subprocess, "Popen", side_effect=self.process,
        ))
        self.stack.enter_context(mock.patch.object(
            wrapper.os, "killpg", side_effect=self.killpg, create=True,
        ))
        self.stack.enter_context(mock.patch.object(wrapper.signal, "SIGKILL", 9, create=True))

    def killpg(self, pid, _signal):
        if pid == self.daemon.pid:
            self.cleanup_observations.append({
                "runtime": (self.evidence / OBSERVATION).exists(),
                "native_stdout": (self.evidence / "native-example.stdout.log").exists(),
                "native_stderr": (self.evidence / "native-example.stderr.log").exists(),
            })

    def process(self, argv, **kwargs):
        argv = [str(value) for value in argv]
        self.command_calls.append(argv)
        if argv[0] == str(self.assets / "semwrightd"):
            self.daemon_stream = kwargs["stdout"]
            kwargs["stdout"].write(b"private unit Broker diagnostic\n")
            return self.daemon
        out, err = kwargs["stdout"], kwargs["stderr"]
        if argv[0] == str(self.assets / "native-example") and argv[1] == "seed":
            self.seed_calls += 1
            Path(argv[2]).write_bytes(b"unit persisted-store placeholder; not SQLite")
            out.write(encoded(self.seed))
            err.write(b"private unit seed stderr\n")
            if self.mode == "seed-failure":
                return mock.Mock(pid=41002, wait=mock.Mock(return_value=2))
        elif argv[0] == str(self.assets / "semwright"):
            session = Path(argv[argv.index("--session-file") + 1])
            session.write_bytes(b"unit owner ticket")
            session.chmod(0o600)
            self.assertEqual(session.name, "motionwright.session")
            self.assertTrue(session.is_relative_to(self.root))
            self.session_ticket = session
            out.write(encoded({"data": {
                "revision": 9, "next_offset": None,
                "capabilities": [{"id": "driver.manim-community." + name} for name in
                                 ("doctor", "render.start", "render.status",
                                  "render.cancel", "render.result")],
            }}))
            err.write(b"private unit catalog stderr\n")
        elif argv[0] == str(self.assets / "native-example") and argv[1] == "realize":
            self.native_calls += 1
            owner = Path(argv[2]).parent.parent
            (owner / "state" / "original-receipts.unit.json").write_bytes(
                b'{"unit_fixture_only":true,"native_outcome":"UNKNOWN"}\n',
            )
            if self.mode != "missing-observation":
                observed = dict(self.observation)
                if self.mode in ("native-failure", "native-timeout"):
                    observed["observation"] = None
                    observed["error"] = {
                        "code": "OutcomeUnknown", "message": "private unit diagnostic",
                        "outcome_known": False,
                    }
                (self.evidence / OBSERVATION).write_bytes(encoded(observed))
            if self.mode in ("native-failure", "native-timeout"):
                out.write(b'{"partial_unit_result":')
                err.write(b"private original native UNKNOWN unit diagnostic\n")
                (self.evidence / "motionwright-manim-diagnostic.json").write_bytes(
                    b'{"unit_fixture_only":true,"outcome_known":false,"receipts":["original"]}\n',
                )
                if self.mode == "native-timeout":
                    return mock.Mock(pid=41003, wait=mock.Mock(side_effect=[self.timeout, -9]))
                return mock.Mock(pid=41003, wait=mock.Mock(return_value=2))
            artifact = owner / "output" / "videos" / "unit.mp4"
            artifact.parent.mkdir()
            artifact.write_bytes(self.media)
            native_raw = encoded(self.native)
            Path(argv[4]).write_bytes(native_raw)
            out.write(native_raw)
            err.write(b"private unit native stderr\n")
        elif argv[0] == str(self.assets / "ffprobe"):
            if self.mode == "probe-failure":
                out.write(b'{"partial_probe":')
                err.write(b"private original ffprobe diagnostic\n")
                return mock.Mock(pid=41004, wait=mock.Mock(return_value=2))
            out.write(encoded({
                "streams": [{"codec_type": "video", "codec_name": "h264",
                             "width": 640, "height": 360, "avg_frame_rate": "12/1"}],
                "format": {"duration": "1.0", "format_name": "mov,mp4"},
            }))
            err.write(b"private unit ffprobe stderr\n")
        else:
            raise AssertionError("unexpected source-unit process: " + repr(argv))
        return mock.Mock(pid=41005, wait=mock.Mock(return_value=0))

    def terminal(self):
        return json.loads((self.evidence / "original-terminal.json").read_bytes())

    def test_static_route_retains_real_files_and_seals_before_legacy_pass(self):
        self.route()
        wrapper._run_single(self.args)
        self.options.assert_called_once_with(self.args, camera=False)
        self.assertEqual(self.native_calls, 1)
        self.assertEqual(self.engine.snapshot_calls, 1)
        self.assertEqual(self.engine.seal_calls, 1)
        captured = self.engine.captured()
        ticket_name = "owner/" + self.session_ticket.relative_to(
            self.engine.roots["owner"],
        ).as_posix()
        manifest = json.loads(captured["snapshot-manifest.json"])
        ticket = next(item for item in manifest["roots"]["owner"]["files"]
                      if item["archive_path"] == ticket_name)
        # Custody reads fstat/lstat, never the synthetic following stat view.
        self.assertEqual(ticket["observation"]["before"]["mode"],
                         self.session_ticket.lstat().st_mode)
        self.assertEqual(captured[ticket_name], b"unit owner ticket")
        self.assertEqual(captured["evidence/" + OBSERVATION], encoded(self.observation))
        self.assertEqual(captured["evidence/" + NATIVE], encoded(self.native))
        self.assertEqual(captured["evidence/native-example.stdout.log"], encoded(self.native))
        self.assertIn("evidence/static-seed.stderr.log", captured)
        self.assertIn("evidence/capabilities-0.stdout.log", captured)
        self.assertIn("evidence/ffprobe-original.stderr.log", captured)
        self.assertIn("owner/state/original-receipts.unit.json", captured)
        self.assertEqual(captured["evidence/semwrightd.log"], b"private unit Broker diagnostic\n")
        self.assertNotIn("evidence/result.json", captured)
        pending = json.loads(captured["evidence/static-render-verification.json"])
        self.assertEqual(pending["native_manim_e2e"], "PASS_REQUIRES_SEALED_CUSTODY")
        final = json.loads((self.evidence / "result.json").read_bytes())
        self.assertEqual(final["native_manim_e2e"], "PASS")
        self.assertEqual(final["runtime_observation_sha256"], camera.sha(encoded(self.observation)))
        self.assertEqual(final["sealed_originals_sha256"],
                         camera.sha(self.args.sealed_originals.read_bytes()))
        self.assertFalse(final["human_acceptance"])
        self.assertEqual(self.terminal()["native_result"], self.native)
        self.assertTrue(self.engine.roots["owner"].is_dir())
        self.assertTrue(self.daemon_stream.closed)
        self.assertEqual(self.cleanup_observations, [{
            "runtime": True, "native_stdout": True, "native_stderr": True,
        }])

    def test_native_failure_preserves_unknown_diagnostic_receipts_and_pre_render_observation(self):
        self.route()
        self.mode = "native-failure"
        with self.assertRaises(camera.CameraEvidenceError):
            wrapper._run_single(self.args)
        captured = self.engine.captured()
        observed = json.loads(captured["evidence/" + OBSERVATION])
        self.assertFalse(observed["error"]["outcome_known"])
        self.assertFalse(observed["render_started"])
        self.assertEqual(captured["evidence/native-example.stdout.log"], b'{"partial_unit_result":')
        self.assertIn(b"UNKNOWN", captured["evidence/native-example.stderr.log"])
        self.assertIn("owner/state/original-receipts.unit.json", captured)
        self.assertIn("evidence/motionwright-manim-diagnostic.json", captured)
        self.assertEqual(self.terminal()["original_process_observation"]["native_outcome"],
                         "NOT_INTERPRETED")
        self.assertIsNone(self.terminal()["native_result"])
        self.assertFalse((self.evidence / "result.json").exists())
        self.assertEqual(self.native_calls, 1)
        with self.assertRaises(FileExistsError):
            wrapper._run_single(self.args)
        self.assertEqual(self.native_calls, 1)

    def test_native_timeout_retains_exact_exception_unknown_and_original_bytes_without_retry(self):
        self.route()
        self.mode = "native-timeout"
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            wrapper._run_single(self.args)
        self.assertIs(caught.exception, self.timeout)
        terminal = self.terminal()
        self.assertEqual(terminal["original_process_observation"]["native_outcome"], "UNKNOWN")
        self.assertFalse(terminal["automatic_retry"])
        self.assertIsNone(terminal["native_result"])
        self.assertIn("evidence/" + OBSERVATION, self.engine.captured())
        self.assertEqual(self.native_calls, 1)
        self.assertTrue(self.engine.roots["owner"].is_dir())
        self.assertFalse((self.evidence / "result.json").exists())

    def test_missing_sibling_fails_after_native_ok_and_preserves_that_ok(self):
        self.route()
        self.mode = "missing-observation"
        with self.assertRaises(FileNotFoundError):
            wrapper._run_single(self.args)
        self.assertEqual(self.terminal()["native_result"], self.native)
        self.assertEqual(self.native_calls, 1)
        self.assertIn("evidence/" + NATIVE, self.engine.captured())
        self.assertFalse((self.evidence / "result.json").exists())

    def test_probe_failure_keeps_native_result_and_original_probe_streams(self):
        self.route()
        self.mode = "probe-failure"
        with self.assertRaises(camera.CameraEvidenceError):
            wrapper._run_single(self.args)
        self.assertEqual(self.terminal()["native_result"], self.native)
        self.assertEqual(self.terminal()["original_process_observation"]["process_returncode"], 0)
        self.assertEqual(self.engine.captured()["evidence/ffprobe-original.stderr.log"],
                         b"private original ffprobe diagnostic\n")
        self.assertFalse((self.evidence / "result.json").exists())
        self.assertEqual(self.native_calls, 1)

    def test_seed_failure_does_not_become_a_native_result_or_create_native_intent(self):
        self.route()
        self.mode = "seed-failure"
        with self.assertRaises(camera.CameraEvidenceError):
            wrapper._run_single(self.args)
        self.assertEqual(self.native_calls, 0)
        self.assertIsNone(self.terminal()["native_result"])
        self.assertIsNone(self.terminal()["original_process_observation"])
        self.assertFalse((self.evidence / "native-invocation-intent.json").exists())
        self.assertIn("evidence/static-seed.stderr.log", self.engine.captured())

    def test_real_partial_snapshot_retains_readable_originals_and_never_writes_pass(self):
        self.route()
        observed_regular = private.regular
        failures = []
        def partially_readable(path, *args, **kwargs):
            if Path(path).name == "original-receipts.unit.json":
                failures.append(Path(path))
                raise OSError("injected unreadable original receipt")
            return observed_regular(path, *args, **kwargs)
        with mock.patch.object(private, "regular", side_effect=partially_readable):
            with self.assertRaisesRegex(camera.CameraEvidenceError, "custody_incomplete"):
                wrapper._run_single(self.args)
        self.assertEqual(len(failures), 1)
        captured = self.engine.captured()
        self.assertEqual(captured["evidence/" + OBSERVATION], encoded(self.observation))
        manifest = json.loads(captured["snapshot-manifest.json"])
        self.assertFalse(manifest["selected_originals_complete"])
        failed = [item for item in manifest["roots"]["owner"]["files"]
                  if item.get("status") == "CAPTURE_FAILED"]
        self.assertEqual(len(failed), 1)
        self.assertFalse(failed[0]["automatic_retry"])
        self.assertTrue(failed[0]["original_retained_in_place"])
        self.assertEqual(self.terminal()["native_result"], self.native)
        self.assertFalse((self.evidence / "result.json").exists())
        self.assertTrue(self.engine.roots["owner"].is_dir())

    def test_seal_failure_keeps_owner_streams_and_native_ok_without_legacy_pass(self):
        self.route()
        self.engine.seal_error = OSError("injected seal failure")
        with self.assertRaisesRegex(camera.CameraEvidenceError, "custody_incomplete"):
            wrapper._run_single(self.args)
        self.assertEqual(self.native_calls, 1)
        self.assertEqual(self.engine.seal_calls, 1)
        self.assertEqual(self.terminal()["native_result"], self.native)
        self.assertTrue(self.engine.roots["owner"].is_dir())
        self.assertTrue((self.evidence / OBSERVATION).is_file())
        self.assertFalse((self.evidence / "result.json").exists())

    def test_broker_cleanup_failure_still_closes_and_retains_log_before_sealing(self):
        self.route()
        self.daemon.wait.side_effect = OSError("injected bounded cleanup failure")
        with self.assertRaisesRegex(camera.CameraEvidenceError, "custody_incomplete"):
            wrapper._run_single(self.args)
        self.assertTrue(self.daemon_stream.closed)
        self.assertEqual(self.engine.captured()["evidence/semwrightd.log"],
                         b"private unit Broker diagnostic\n")
        self.assertEqual(self.terminal()["native_result"], self.native)
        self.assertEqual(self.native_calls, 1)
        self.assertFalse((self.evidence / "result.json").exists())

    def test_changed_sibling_binding_is_rejected_without_losing_its_bytes(self):
        self.route()
        self.observation["revision"] += 1
        with self.assertRaisesRegex(camera.CameraEvidenceError, "runtime_observation_binding"):
            wrapper._run_single(self.args)
        self.assertEqual(self.engine.captured()["evidence/" + OBSERVATION],
                         encoded(self.observation))
        self.assertEqual(self.terminal()["native_result"], self.native)
        self.assertFalse((self.evidence / "result.json").exists())

    def test_postseal_result_write_failure_retains_ciphertext_and_does_not_retry(self):
        self.route()
        self.engine.write_failure = "result.json"
        with self.assertRaisesRegex(OSError, "exclusive custody write"):
            wrapper._run_single(self.args)
        self.assertTrue(self.args.sealed_originals.is_file())
        self.assertEqual(self.engine.seal_calls, 1)
        self.assertEqual(self.native_calls, 1)
        self.assertTrue(self.engine.roots["owner"].is_dir())
        self.assertFalse((self.evidence / "result.json").exists())

    def preflight(self):
        self.stack.enter_context(mock.patch.object(wrapper.sys, "platform", "linux"))
        self.stack.enter_context(mock.patch.object(wrapper.os, "geteuid", return_value=1000,
                                                   create=True))
        return self.stack.enter_context(mock.patch.object(
            wrapper.subprocess, "check_output", return_value=CONTEXT["source_sha"] + "\n",
        ))

    def test_static_preflight_does_not_require_camera_seed_binary(self):
        self.preflight()
        with mock.patch.object(wrapper, "MW_CAMERA_SEED", self.root / "absent-camera-seed"):
            recipient, current = wrapper.camera_options(self.args, camera=False)
        self.assertEqual((recipient, current), (RECIPIENT, CONTEXT))
        self.assertFalse(self.evidence.exists())

    def test_attempt_two_is_denied_before_owner_or_subprocess(self):
        git = self.preflight()
        with mock.patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": "2"}):
            with mock.patch.object(wrapper.subprocess, "Popen") as spawn:
                with self.assertRaisesRegex(camera.CameraEvidenceError, "original_dispatch"):
                    wrapper._run_single(self.args)
        spawn.assert_not_called()
        git.assert_not_called()
        self.assertFalse(self.evidence.exists())

    def test_missing_static_custody_paths_are_denied_before_effect(self):
        self.preflight()
        self.args.evidence_root = None
        self.args.sealed_originals = None
        with mock.patch.object(wrapper.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(camera.CameraEvidenceError, "custody_paths_required"):
                wrapper._run_single(self.args)
        spawn.assert_not_called()

    def test_unadmitted_recipient_is_rejected_before_evidence_or_native_effect(self):
        self.preflight()
        with mock.patch.object(self.engine, "public_key", side_effect=ValueError("key rejected")):
            with mock.patch.object(wrapper.subprocess, "Popen") as spawn:
                with self.assertRaisesRegex(ValueError, "key rejected"):
                    wrapper._run_single(self.args)
        spawn.assert_not_called()
        self.assertFalse(self.evidence.exists())

    def test_static_script_guard_never_prints_private_diagnostic(self):
        tree = ast.parse(Path(wrapper.__file__).read_text(encoding="utf-8"))
        guard = tree.body[-1]
        self.assertIsInstance(guard, ast.If)
        errors = io.StringIO()
        namespace = {
            "__name__": "__main__",
            "main": mock.Mock(side_effect=RuntimeError("PRIVATE_ORIGINAL_DIAGNOSTIC")),
            "sys": argparse.Namespace(
                stderr=errors, argv=["native_manim_e2e.py", "--scenario", "static-scene"],
            ),
        }
        with self.assertRaises(SystemExit) as caught:
            exec(compile(ast.Module(body=[guard], type_ignores=[]), wrapper.__file__, "exec"),
                 namespace)
        self.assertEqual(caught.exception.code, 2)
        self.assertNotIn("PRIVATE_ORIGINAL_DIAGNOSTIC", errors.getvalue())
        self.assertIn("No replay", errors.getvalue())

    def test_help_exit_remains_success_without_a_private_error_message(self):
        tree = ast.parse(Path(wrapper.__file__).read_text(encoding="utf-8"))
        errors = io.StringIO()
        namespace = {
            "__name__": "__main__",
            "main": mock.Mock(side_effect=SystemExit(0)),
            "sys": argparse.Namespace(stderr=errors, argv=["native_manim_e2e.py", "--help"]),
        }
        with self.assertRaises(SystemExit) as caught:
            exec(compile(ast.Module(body=[tree.body[-1]], type_ignores=[]),
                         wrapper.__file__, "exec"), namespace)
        self.assertEqual(caught.exception.code, 0)
        self.assertEqual(errors.getvalue(), "")

    def direct_session(self):
        owner = self.root / "direct-owner"
        owner.mkdir(mode=0o700)
        self.evidence.mkdir(mode=0o700)
        return camera.CameraCustody(owner, self.evidence, self.args.sealed_originals,
                                    RECIPIENT, CONTEXT, engine=self.engine)

    def test_actual_python_failure_keeps_original_streams_and_runtime_file(self):
        current = self.direct_session()
        script = (
            "from pathlib import Path; import sys; "
            "Path(sys.argv[1]).write_bytes(b'original unit runtime bytes\\n'); "
            "sys.stdout.buffer.write(b'partial unit stdout'); "
            "sys.stderr.buffer.write(b'original unit failure'); sys.exit(7)"
        )
        with self.assertRaises(camera.CameraEvidenceError):
            with current:
                current.once(lambda: wrapper.captured_json(
                    [sys.executable, "-I", "-B", "-c", script,
                     str(self.evidence / OBSERVATION)],
                    env=os.environ.copy(), evidence=self.evidence, label="native-example",
                    timeout=10, camera_custody=current,
                ))
        captured = self.engine.captured()
        self.assertEqual(captured["evidence/" + OBSERVATION], b"original unit runtime bytes\n")
        self.assertEqual(captured["evidence/native-example.stdout.log"], b"partial unit stdout")
        self.assertEqual(captured["evidence/native-example.stderr.log"], b"original unit failure")
        self.assertEqual(current.native_process["process_returncode"], 7)
        self.assertEqual(current.native_process["native_outcome"], "NOT_INTERPRETED")
        self.assertIsNone(current.native_result)
        with self.assertRaisesRegex(camera.CameraEvidenceError, "already_attempted"):
            current.once(lambda: self.fail("unexpected second process"))

    @unittest.skipUnless(sys.platform == "linux", "actual killpg/reap requires Linux")
    def test_actual_python_timeout_retains_unknown_and_reaps_only_that_process(self):
        current = self.direct_session()
        script = (
            "from pathlib import Path; import sys,time; "
            "Path(sys.argv[1]).write_bytes(b'original unit observation before timeout\\n'); "
            "sys.stdout.buffer.write(b'partial unit timeout'); sys.stdout.buffer.flush(); "
            "sys.stderr.buffer.write(b'original unit timeout'); sys.stderr.buffer.flush(); "
            "time.sleep(10)"
        )
        actual_popen = subprocess.Popen
        processes = []
        def spawn(*args, **kwargs):
            process = actual_popen(*args, **kwargs)
            processes.append(process)
            return process
        with mock.patch.object(wrapper.subprocess, "Popen", side_effect=spawn):
            with self.assertRaises(subprocess.TimeoutExpired):
                with current:
                    current.once(lambda: wrapper.captured_json(
                        [sys.executable, "-I", "-B", "-c", script,
                         str(self.evidence / OBSERVATION)],
                        env=os.environ.copy(), evidence=self.evidence, label="native-example",
                        timeout=2, camera_custody=current,
                    ))
        self.assertEqual(current.native_process["native_outcome"], "UNKNOWN")
        self.assertIn("evidence/" + OBSERVATION, self.engine.captured())
        self.assertIsNone(current.native_result)
        self.assertEqual(len(processes), 1)
        self.assertEqual(processes[0].returncode, -signal.SIGKILL)
        with self.assertRaisesRegex(camera.CameraEvidenceError, "already_attempted"):
            current.once(lambda: self.fail("unexpected second process"))


if __name__ == "__main__":
    unittest.main()
