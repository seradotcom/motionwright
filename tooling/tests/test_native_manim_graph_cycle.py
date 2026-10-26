"""Causal contract tests for Graph caller handoff and original process custody.

These are unit fixtures only; they do not create a Host, native Ref, admission,
Graph save, Manim render, video decode, or human acceptance.
"""
from __future__ import annotations
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_manim_graph_cycle as graph
import manim_native_readback as readback


def pending():
    project = {"id": "fixture-project", "generation": "fixture-generation",
               "active_branch": "fixture-branch", "revision": 7}
    return {"pending": {
        "schema": "motionwright.manim.graph-cycle-pending/1", "phase": "initial",
        "project_before": project,
        "graph": {"authorship": {"source_snapshot": graph.scope(project)}},
        "native_execution_performed": False, "graph_write_performed": False,
    }}


def application():
    provenance = {"provider": "driver:motionwright", "source": "driver",
                  "provider_generation": 3, "provider_version": "0.1.0",
                  "descriptor_sha256": "a" * 64, "capability_version": "1.0.0"}
    envelope = {"ok": True, "data": {"binding_ref": "unit-observed-binding",
        "admission_current": True, "admission_state": "current", "result": {
        "ok": True,
        "execution": {"backend": "driver:motionwright", "provenance": provenance},
        "data": {"replayed": False, "request_id": "unit-source-operation"},
    }}}
    capabilities = [{"id": graph.GRAPH_COMMAND, "version": "1.0.0", "provenance": {
        "provider": "driver:motionwright", "source": "driver",
        "source_version": "0.1.0", "descriptor_sha256": "a" * 64,
    }}]
    return envelope, capabilities, {"binding_ref": "unit-observed-binding", "provider_generation": 3}


class SourceScopeTests(unittest.TestCase):
    def test_scope_preserves_the_project_branch_and_numeric_revision(self):
        source = pending()
        accepted = graph.checked_pending(source, "initial")
        self.assertEqual(accepted["graph"]["authorship"]["source_snapshot"],
                         {"project_id": "fixture-project", "generation": "fixture-generation",
                          "branch_id": "fixture-branch", "revision": 7})
        for revision in (True, -1, 2**64, "7"):
            with self.subTest(revision=revision):
                wrong = copy.deepcopy(source)
                wrong["pending"]["project_before"]["revision"] = revision
                with self.assertRaises(ValueError):
                    graph.checked_pending(wrong, "initial")

    def test_wrong_branch_or_write_claim_is_rejected_as_source_data(self):
        for field in ("branch", "native_execution_performed", "graph_write_performed"):
            with self.subTest(field=field):
                wrong = pending()
                if field == "branch":
                    wrong["pending"]["graph"]["authorship"]["source_snapshot"]["branch_id"] = "foreign"
                else:
                    wrong["pending"][field] = True
                with self.assertRaises(ValueError):
                    graph.checked_pending(wrong, "initial")

    def test_equal_boolean_and_numeric_json_values_are_not_the_same_binding(self):
        self.assertFalse(graph.same({"revision": True}, {"revision": 1}))
        self.assertFalse(graph.same({"revision": 7.0}, {"revision": 7}))
        self.assertTrue(graph.same({"revision": 7}, {"revision": 7}))

    def test_provider_generation_descriptor_version_and_replay_are_closed(self):
        baseline, rows, mounted = application()
        self.assertEqual(graph.application_result(baseline, rows, mounted)["request_id"], "unit-source-operation")
        for field, replacement in (
            ("provider", "driver:other"), ("provider_generation", True), ("provider_generation", 4),
            ("descriptor_sha256", "b" * 64), ("provider_version", "changed"),
            ("capability_version", "changed"),
        ):
            with self.subTest(field=field):
                wrong = copy.deepcopy(baseline)
                wrong["data"]["result"]["execution"]["provenance"][field] = replacement
                with self.assertRaises(ValueError):
                    graph.application_result(wrong, rows, mounted)
        wrong = copy.deepcopy(baseline)
        wrong["data"]["result"]["data"]["replayed"] = True
        with self.assertRaises(ValueError):
            graph.application_result(wrong, rows, mounted)
        for field, replacement in (("binding_ref", "foreign-binding"),
                                   ("admission_current", False), ("admission_state", "expired")):
            with self.subTest(field=field):
                wrong = copy.deepcopy(baseline)
                wrong["data"][field] = replacement
                with self.assertRaises(ValueError):
                    graph.application_result(wrong, rows, mounted)
        wrong = copy.deepcopy(baseline)
        wrong["data"]["result"]["ok"] = False
        with self.assertRaises(ValueError):
            graph.application_result(wrong, rows, mounted)


class SupervisionFailureProcess:
    """Popen boundary only; the real bounded supervisor drains these pipes."""
    def __init__(self, original, stdout, stderr):
        self.original = original
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = None
        self.wait_calls = 0
        self.kill_calls = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.returncode is None:
            raise self.original
        return self.returncode

    def kill(self):
        self.kill_calls += 1
        self.returncode = -9


class OriginalProcessCustodyTests(unittest.TestCase):
    def test_missing_capture_fields_are_not_fabricated_as_empty_observed_pipes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            graph.retain_capture(root, "empty", {}, secondary=lambda *_: None)
            self.assertFalse((root / "empty.stdout.original").exists())
            self.assertFalse((root / "empty.stderr.original").exists())
            self.assertEqual(json.loads((root / "empty.process.original.json").read_bytes()), {})

    def test_not_started_state_preserves_initial_buffers_without_claiming_pipe_observation(self):
        captured = {"state": "NOT_STARTED", "stdout": b"", "stderr": b"",
                    "startup_error_type": "FileNotFoundError",
                    "terminal": {"exit_code": None, "whole_process_completed": False}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            graph.retain_capture(root, "not-started", captured, secondary=lambda *_: None)
            value = json.loads((root / "not-started.process.original.json").read_bytes())
            self.assertEqual(value["state"], "NOT_STARTED")
            self.assertFalse(value["terminal"]["whole_process_completed"])
            self.assertNotIn("pipes_closed", value["terminal"])
            self.assertEqual((root / "not-started.stdout.original").read_bytes(), b"")

    def test_second_stream_is_attempted_when_first_stream_custody_fails(self):
        writes, secondary = [], []
        primary = OSError("first stream custody failed")
        def write(path, raw):
            writes.append((path.name, raw))
            if path.name.endswith("stdout.original"):
                raise primary
        with mock.patch.object(graph, "private_bytes", side_effect=write), \
             mock.patch.object(graph, "private_json"):
            with self.assertRaises(OSError) as observed:
                graph.retain_capture(Path("/unused-unit"), "capture",
                    {"state": "TERMINAL_OBSERVED", "stdout": b"out", "stderr": b"err"},
                    secondary=lambda *row: secondary.append(row))
        self.assertIs(observed.exception, primary)
        self.assertEqual(writes, [("capture.stdout.original", b"out"), ("capture.stderr.original", b"err")])
        self.assertEqual(len(secondary), 1)

    def test_original_sdk_unknown_survives_process_and_custody_failures_without_retry(self):
        envelope = {"ok": False, "error": {
            "code": "TIMEOUT", "message": "original native wait",
            "outcome_known": False, "progress": {"completed": 1},
            "native_diagnostic": {"request": "original"}, "future_field": {"retained": True},
        }}
        raw = json.dumps(envelope).encode()
        process_error = readback.BoundedProcessExitError("nonzero original process")
        calls = []
        def run(argv, maximum, timeout, *, capture):
            calls.append(argv)
            capture.update(state="TERMINAL_OBSERVED", stdout=raw, stderr=b"native stderr",
                           terminal={"exit_code": 1, "whole_process_completed": True,
                                     "pipes_closed": True, "stdin_complete": True,
                                     "timed_out": False, "output_budget_exceeded": False,
                                     "pipe_errors": []})
            raise process_error
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with mock.patch("manim_native_readback.run_bounded", side_effect=run), \
                 mock.patch.object(graph, "retain_capture", side_effect=OSError("custody failed")):
                with self.assertRaises(graph.OriginalOwnerOperationError) as observed:
                    graph.owner_json(Path("/unit-cli"), Path("/unit-socket"), Path("/unit-session"),
                                     "providers.execute_bound", {}, root, "source-save",
                                     secondary=lambda *_: None)
        self.assertEqual(len(calls), 1)
        self.assertEqual(observed.exception.original_envelope, envelope)
        self.assertIs(observed.exception.original_process_error, process_error)
        self.assertIs(observed.exception.__cause__, process_error)

    def test_parseable_error_prefix_cannot_replace_an_incomplete_capture_error(self):
        # Deliberately parseable JSON does not establish a complete response.
        # These boundary fixtures launch no process or native operation.
        raw = json.dumps({"ok": False, "error": {
            "code": "PERMISSION_DENIED", "message": "parseable error prefix",
            "outcome_known": True, "future_field": {"retained": True},
        }}).encode()
        cases = (
            ("timeout_after_pipe_cleanup", {"timed_out": True},
             TimeoutError("original wait deadline")),
            ("stderr_overflow", {"output_budget_exceeded": True},
             ValueError("original output budget")),
            ("pipes_open", {"whole_process_completed": False, "pipes_closed": False},
             TimeoutError("original pipe deadline")),
            ("read_error", {"pipe_errors": ["read:OSError"]},
             OSError("original pipe read")),
            ("stdin_error", {"stdin_complete": False, "pipe_errors": ["write:OSError"]},
             OSError("original stdin write")),
            ("close_error", {"pipe_errors": ["close:OSError"]},
             OSError("original pipe close")),
            ("terminal_unknown", {"exit_code": None, "whole_process_completed": False},
             TimeoutError("original child terminal unknown")),
            ("zero_exit_prior_error", {"exit_code": 0},
             RuntimeError("original supervisory error")),
        )
        for name, changed, primary in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                terminal = {"exit_code": 1, "whole_process_completed": True,
                            "pipes_closed": True, "stdin_complete": True,
                            "timed_out": False, "output_budget_exceeded": False,
                            "pipe_errors": []}
                terminal.update(changed)
                def run(argv, maximum, timeout, *, capture):
                    capture.update(state="TERMINAL_OBSERVED", stdout=raw,
                                   stderr=b"original stderr", terminal=copy.deepcopy(terminal))
                    raise primary
                with mock.patch("manim_native_readback.run_bounded", side_effect=run) as invocation:
                    with self.assertRaises(type(primary)) as observed:
                        graph.owner_json(Path("/unit-cli"), Path("/unit-socket"), Path("/unit-session"),
                                         "providers.execute_bound", {}, root, "source-save",
                                         secondary=lambda *_: None)
                self.assertIs(observed.exception, primary)
                self.assertEqual(invocation.call_count, 1)
                self.assertEqual((root / "source-save.stdout.original").read_bytes(), raw)
                retained = json.loads((root / "source-save.process.original.json").read_bytes())
                self.assertEqual(retained["terminal"], terminal)

    def test_incomplete_error_prefix_and_failed_custody_keep_the_same_primary(self):
        primary = TimeoutError("original owner wait remains unknown")
        custody_error = OSError("stdout original could not be retained")
        raw = json.dumps({"ok": False, "error": {
            "code": "PERMISSION_DENIED", "outcome_known": True,
        }}).encode()
        secondary, writes = [], []
        original_writer = graph.private_bytes
        def run(argv, maximum, timeout, *, capture):
            capture.update(state="TERMINAL_OBSERVED", stdout=raw, stderr=b"original stderr",
                           terminal={"exit_code": None, "whole_process_completed": False,
                                     "pipes_closed": False, "stdin_complete": True,
                                     "timed_out": True, "output_budget_exceeded": False,
                                     "pipe_errors": []})
            raise primary
        def retain(path, payload):
            writes.append(path.name)
            if path.name.endswith(".stdout.original"):
                raise custody_error
            return original_writer(path, payload)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with mock.patch("manim_native_readback.run_bounded", side_effect=run) as invocation, \
                 mock.patch.object(graph, "private_bytes", side_effect=retain):
                with self.assertRaises(TimeoutError) as observed:
                    graph.owner_json(Path("/unit-cli"), Path("/unit-socket"), Path("/unit-session"),
                                     "providers.execute_bound", {}, root, "source-save",
                                     secondary=lambda *row: secondary.append(row))
            self.assertIs(observed.exception, primary)
            self.assertEqual(invocation.call_count, 1)
            self.assertEqual(writes, ["source-save.stdout.original", "source-save.stderr.original"])
            self.assertEqual((root / "source-save.stderr.original").read_bytes(), b"original stderr")
            retained = json.loads((root / "source-save.process.original.json").read_bytes())
            self.assertTrue(retained["terminal"]["timed_out"])
            self.assertFalse(retained["terminal"]["whole_process_completed"])
            self.assertEqual(len(secondary), 1)
            self.assertEqual(secondary[0][0], "graph-owner-process-custody")
            self.assertIs(secondary[0][1], custody_error)

    def test_complete_nonzero_capture_keeps_original_supervision_errors(self):
        # Exercise the actual helper: wait fails, stop observes exit -9, and
        # both real reader threads finish. Complete JSON is still not the cause
        # of the supervisor's original exception.
        raw = json.dumps({"ok": False, "error": {
            "code": "PERMISSION_DENIED", "outcome_known": True,
        }}).encode()
        for original in (OSError("original wait failed"),
                         KeyboardInterrupt("original supervision interrupted")):
            with self.subTest(kind=type(original).__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                process = SupervisionFailureProcess(original, raw, b"original stderr")
                with mock.patch.object(readback.subprocess, "Popen", return_value=process) as start:
                    with self.assertRaises(type(original)) as observed:
                        graph.owner_json(Path("/unit-cli"), Path("/unit-socket"), Path("/unit-session"),
                                         "providers.execute_bound", {}, root, "source-save",
                                         secondary=lambda *_: None)
                self.assertIs(observed.exception, original)
                self.assertFalse(isinstance(observed.exception, readback.BoundedProcessExitError))
                self.assertFalse(hasattr(observed.exception, "original_envelope"))
                self.assertEqual(start.call_count, 1)
                self.assertEqual(process.wait_calls, 1)
                self.assertEqual(process.kill_calls, 1)
                self.assertEqual((root / "source-save.stdout.original").read_bytes(), raw)
                self.assertEqual((root / "source-save.stderr.original").read_bytes(), b"original stderr")
                retained = json.loads((root / "source-save.process.original.json").read_bytes())
                terminal = retained["terminal"]
                self.assertEqual(retained["state"], "TERMINAL_OBSERVED")
                self.assertEqual(terminal["exit_code"], -9)
                self.assertTrue(terminal["whole_process_completed"])
                self.assertTrue(terminal["pipes_closed"])
                self.assertTrue(terminal["stdin_complete"])
                self.assertFalse(terminal["timed_out"])
                self.assertFalse(terminal["output_budget_exceeded"])
                self.assertEqual(terminal["pipe_errors"], [])

    def test_complete_nonzero_capture_and_failed_custody_keep_supervision_error(self):
        original = KeyboardInterrupt("original interruption survives custody failure")
        custody_error = OSError("stdout original retention failed")
        raw = json.dumps({"ok": False, "error": {"code": "TIMEOUT", "outcome_known": False}}).encode()
        process = SupervisionFailureProcess(original, raw, b"original stderr")
        secondary = []
        write_original = graph.private_bytes
        def retain(path, payload):
            if path.name.endswith(".stdout.original"):
                raise custody_error
            return write_original(path, payload)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with mock.patch.object(readback.subprocess, "Popen", return_value=process) as start, \
                 mock.patch.object(graph, "private_bytes", side_effect=retain):
                with self.assertRaises(KeyboardInterrupt) as observed:
                    graph.owner_json(Path("/unit-cli"), Path("/unit-socket"), Path("/unit-session"),
                                     "providers.execute_bound", {}, root, "source-save",
                                     secondary=lambda *row: secondary.append(row))
            self.assertIs(observed.exception, original)
            self.assertFalse(hasattr(observed.exception, "original_envelope"))
            self.assertEqual(start.call_count, 1)
            self.assertEqual(process.wait_calls, 1)
            self.assertEqual(process.kill_calls, 1)
            self.assertEqual((root / "source-save.stderr.original").read_bytes(), b"original stderr")
            retained = json.loads((root / "source-save.process.original.json").read_bytes())
            self.assertEqual(retained["terminal"]["exit_code"], -9)
            self.assertTrue(retained["terminal"]["whole_process_completed"])
            self.assertEqual(retained["stdout_bytes"], len(raw))
            self.assertEqual(retained["stdout_sha256"], graph.sha(raw))
            self.assertEqual(len(secondary), 1)
            self.assertEqual(secondary[0][0], "graph-owner-process-custody")
            self.assertIs(secondary[0][1], custody_error)

    def test_run_bounded_classifies_only_its_final_nonzero_exit(self):
        # One inert Python child exercises the actual final check. This is a
        # process-supervision control, not an SDK, Host, or native-render probe.
        stdout = b'{"ok":false,"error":{"code":"UNIT_ONLY"}}\n'
        stderr = b"unit nonzero diagnostic\n"
        code = ("import sys; sys.stdout.buffer.write(" + repr(stdout)
                + "); sys.stderr.buffer.write(" + repr(stderr) + "); sys.exit(7)")
        capture = {}
        with self.assertRaises(readback.BoundedProcessExitError) as observed:
            readback.run_bounded([sys.executable, "-c", code], 4096, 10, capture=capture)
        self.assertIs(type(observed.exception), readback.BoundedProcessExitError)
        self.assertIsInstance(observed.exception, readback.ReadbackError)
        self.assertEqual(str(observed.exception), "readonly_decoder_failed:7:unit nonzero diagnostic\n")
        self.assertEqual(capture["stdout"], stdout)
        self.assertEqual(capture["stderr"], stderr)
        self.assertEqual(capture["state"], "TERMINAL_OBSERVED")
        terminal = capture["terminal"]
        self.assertEqual(terminal["exit_code"], 7)
        self.assertTrue(terminal["whole_process_completed"])
        self.assertTrue(terminal["pipes_closed"])
        self.assertTrue(terminal["stdin_complete"])
        self.assertFalse(terminal["timed_out"])
        self.assertFalse(terminal["output_budget_exceeded"])
        self.assertEqual(terminal["pipe_errors"], [])

    def test_unselected_command_is_refused_before_process_or_intent_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with mock.patch("manim_native_readback.run_bounded") as run:
                with self.assertRaises(ValueError):
                    graph.owner_json(Path("/unit-cli"), Path("/unit-socket"), Path("/unit-session"),
                                     "shell.execute", {}, root, "source-save", secondary=lambda *_: None)
                run.assert_not_called()
            self.assertEqual(list(root.iterdir()), [])

    def test_process_failure_without_terminal_does_not_become_a_known_native_denial(self):
        primary = TimeoutError("original wait deadline")
        def run(argv, maximum, timeout, *, capture):
            capture.update(state="RESERVED", stdout=b"", stderr=b"", stdin_bytes=0, stdin_sha256=None)
            raise primary
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with mock.patch("manim_native_readback.run_bounded", side_effect=run):
                with self.assertRaises(TimeoutError) as observed:
                    graph.owner_json(Path("/unit-cli"), Path("/unit-socket"), Path("/unit-session"),
                                     "providers.execute_bound", {}, root, "source-save",
                                     secondary=lambda *_: None)
            self.assertIs(observed.exception, primary)
            retained = json.loads((root / "source-save.process.original.json").read_bytes())
            self.assertEqual(retained["state"], "RESERVED")
            self.assertNotIn("terminal", retained)
            self.assertNotIn("outcome_known", retained)


class PixelBoundaryTests(unittest.TestCase):
    def test_reused_phase_root_fails_before_any_png_decoder(self):
        with mock.patch("native_manim_editable_pixels.load_phase") as load:
            with self.assertRaises(ValueError):
                graph.pixels([Path("/same")] * 4, Path("/unused-edit"))
            load.assert_not_called()

    def test_wrong_saved_label_fails_before_any_png_decoder(self):
        edited = {"prepared_plan": {"source": {"content": {"vertices": [
            {"label": "not the edited label"}, {"label": "Authored concept"},
        ]}}}}
        with mock.patch.object(graph, "read_object", return_value=edited), \
             mock.patch.object(graph, "checked_saved", return_value={"graph_id": "unit"}), \
             mock.patch("native_manim_editable_pixels.load_phase") as load:
            with self.assertRaises(ValueError):
                graph.pixels([Path(f"/unit-phase-{i}") for i in range(4)], Path("/unit-edit"))
            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
