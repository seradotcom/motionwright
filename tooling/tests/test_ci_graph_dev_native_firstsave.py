"""GN1 real-Graph first-Save selection checks, without Native execution or G2."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tooling import ci_graph_dev_native_firstsave as save


def source():
    pid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    gen = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    branch = "11111111-1111-4111-8111-111111111111"
    scene = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    profile = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    project = {"id": pid, "generation": gen, "active_branch": branch, "revision": 2,
               "scenes": [{"id": scene}], "deliverables": [{"id": profile}]}
    graph = {"id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
             "scene_id": scene, "variant_profile_ref": profile,
             "authorship": {"source_snapshot": {
                 "project_id": pid, "generation": gen,
                 "branch_id": branch, "revision": 2}}}
    return {"schema": "motionwright.graph-owner-fixture-source/1",
            "native_authority": False, "graph_persisted": False,
            "resource": "project:" + pid, "project": project, "graph": graph}


class SourceGuardTests(unittest.TestCase):
    def test_exact_explicit_second_grant_and_closed_denial(self):
        self.assertFalse(save.selected("deny"))
        self.assertTrue(save.selected("allow-one-original-save"))
        for value in ("", None, 1, "allow", "allow-one-original",
                      "allow-one-original-save ", "true"):
            with self.subTest(value=value), self.assertRaises(save.FirstSaveFailure):
                save.selected(value)

    def test_only_authored_unpersisted_graph_fixture_is_admitted(self):
        fixture = source()
        self.assertEqual(save.checked_source_fixture(fixture)["id"], fixture["project"]["id"])
        for changed in (
            {**fixture, "native_authority": True},
            {**fixture, "graph_persisted": True},
            {**fixture, "resource": "project:foreign"},
            {**fixture, "graph": {**fixture["graph"], "authorship": {}}},
            {**fixture, "project": {**fixture["project"], "revision": 3}},
            {**fixture, "project": {**fixture["project"], "active_branch": "foreign-branch"}},
            {**fixture, "graph": {**fixture["graph"], "scene_id": "foreign-scene"}},
            {**fixture, "graph": {**fixture["graph"], "variant_profile_ref": "foreign-profile"}},
            {**fixture, "graph": {**fixture["graph"], "authorship": {
                "source_snapshot": {**fixture["graph"]["authorship"]["source_snapshot"],
                                    "revision": 1}}}},
            {**fixture, "graph": {**fixture["graph"], "authorship": {
                "source_snapshot": {**fixture["graph"]["authorship"]["source_snapshot"],
                                    "unrecognized": "never accepted"}}}},
        ):
            with self.assertRaises(save.FirstSaveFailure):
                save.checked_source_fixture(changed)

    def test_existing_workflow_defaults_to_no_save_and_no_auto_event(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root / ".github/workflows/graph-native-startup-dev.yml").read_text()
        runner = (root / "tooling/ci_graph_dev_native_startup.py").read_text()
        self.assertIn("one_original_save:", workflow)
        self.assertIn("options: [deny, allow-one-original-save]", workflow)
        self.assertIn("GN1_FIRST_SAVE: " + chr(36) + "{{ inputs.one_original_save }}", workflow)
        self.assertIn("GN1_ONE_ORIGINAL_OWNER: " + chr(36) + "{{ inputs.one_original_owner }}", workflow)
        self.assertIn("if: inputs.one_original_save == 'allow-one-original-save'", workflow)
        self.assertIn("one_original_save = first_save_selected(", runner)
        self.assertIn("GN1_EXPLICIT_OWNER_SELECTION_REQUIRED", runner)
        self.assertIn('mark("FIRST_GRAPH_SAVE")', runner)
        self.assertIn('output = mkdir(private, "gn1-first-save-output")', runner)
        self.assertIn("GN1_REAL_FIRST_SAVE_REOPENED", runner)
        self.assertIn('public["formal_G2_granted"] = False', runner)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertNotIn("\n  push:", workflow)
        self.assertNotIn("\n  pull_request:", workflow)

    def test_provider_only_release_precedes_sqlite_handoff_and_independent_reader(self):
        base = Path(__file__).resolve().parents[1]
        native = (base / "ci_graph_dev_native_firstsave.py").read_text()
        runner = (base / "ci_graph_dev_native_startup.py").read_text()
        self.assertEqual(save.PRIVATE_BYTES, 8 * 1024 * 1024)
        self.assertLess(save.PRIVATE_BYTES, 16 * 1024 * 1024)
        self.assertLess(save.PRIVATE_BYTES, 32 * 1024 * 1024)
        native_rust = (base.parent / "crates/native/examples/native-graph-review-session.rs").read_text()
        self.assertEqual(native_rust.count(
            "MAX_PRIVATE_RECORD_BYTES.min(self.maximum_bytes / 2)"), 2)
        self.assertIn("let within_hold =", native_rust)
        self.assertIn("smaller_parent_refuses_unreserved_oversized_result_without_publishing",
                      native_rust)
        self.assertLess(native.index("released = release_provider()"),
                        native.index('"writer-handoff", "prepare_read_only_handoff"'))
        self.assertIn('"providers.release_bound"', runner)
        self.assertIn('fresh.get("connection") == mounted["connection"]', runner)
        self.assertIn('fresh.get("provider_generation") == mounted["provider_generation"]', runner)
        self.assertIn('not release_used', runner)
        self.assertIn('read_only=True', runner)
        self.assertLess(runner.index('mark("OWNER_CLOSE")'),
                        runner.index('read_only=True'))
        self.assertIn('native_authoring_in_reader": False', runner)
        self.assertIn('require_same_opened_sqlite_witness(', runner)
        self.assertIn('"independent-opened-sqlite"', runner)
        self.assertIn('"opened_sqlite_identity_witness_match": True', runner)
        self.assertLess(runner.index('"independent-opened-sqlite"'),
                        runner.index('new_json(private / "independent-readonly-reopen.original.json"'))
        self.assertIn('"formal_G2_granted": False', runner)

    def test_only_one_real_save_mutation_and_originals_remain_private(self):
        text = (Path(__file__).resolve().parents[1] /
                "ci_graph_dev_native_firstsave.py").read_text()
        self.assertIn('MOTIONWRIGHT_GRAPH_CALLBACK_CAP="0" if read_only else "2"', text)
        self.assertIn("MOTIONWRIGHT_GRAPH_STORE_FILE_BYTES", text)
        self.assertIn('exact_null = command in (', text)
        self.assertIn('"opened_store_identity_witness"', text)
        self.assertEqual(text.count('"first-graph-save", "apply_change"'), 1)
        self.assertIn('"reopen", "reopen"', text)
        self.assertIn('"writer-handoff", "prepare_read_only_handoff"', text)
        self.assertIn('"formal_G2_granted": False', text)

    def test_writer_and_independent_opened_pager_witness_are_exact_and_unforgeable(self):
        source_witness = "a" * 64
        self.assertTrue(save.require_same_opened_sqlite_witness(
            source_witness, {"ok": True, "result": source_witness}))
        for value in ("A" * 64, "a" * 63, "a" * 64 + " ", None,
                      {"identity": source_witness}):
            with self.subTest(value=value), self.assertRaisesRegex(
                    save.FirstSaveFailure, "OPENED_SQLITE_WITNESS_INVALID"):
                save.closed_opened_sqlite_witness({"ok": True, "result": value})
        with self.assertRaisesRegex(
                save.FirstSaveFailure,
                "GN1_FIRST_SAVE_INDEPENDENT_OPENED_SQLITE_IDENTITY_CHANGED") as observed:
            save.require_same_opened_sqlite_witness(
                source_witness, {"ok": True, "result": "b" * 64})
        self.assertTrue(observed.exception.unknown)
        with self.assertRaisesRegex(
                save.FirstSaveFailure, "WRITER_OPENED_SQLITE_WITNESS_MISSING"):
            save.require_same_opened_sqlite_witness(
                "A" * 64, {"ok": True, "result": source_witness})


class BoundedOriginalChildTests(unittest.TestCase):
    """Real tiny Python children from the selected interpreter; no Native effects."""

    def setUp(self):
        root = os.environ.get("MOTIONWRIGHT_TEST_ROOT") or None
        self.temp = tempfile.TemporaryDirectory(dir=root, prefix="gn1-bounded-child-")
        self.addCleanup(self.temp.cleanup)
        self.private = Path(self.temp.name) / "private"
        self.private.mkdir()

    def invoke(self, label, code, timeout=4):
        return save.bounded_process(
            [sys.executable, "-B", "-S", "-c", code],
            env=dict(os.environ), private=self.private, label=label,
            timeout=timeout)

    def receipt(self, label):
        return json.loads(
            (self.private / (label + ".terminal.original.json")).read_text())

    def test_large_stdout_is_bounded_unknown_and_never_replayed(self):
        code = ("import os,time;"
                f"os.write(1,b'S'*({save.MAX_ORIGINAL}+65536));"
                "time.sleep(2)")
        with self.assertRaises(save.FirstSaveFailure) as observed:
            self.invoke("huge-out", code)
        self.assertTrue(observed.exception.unknown)
        self.assertEqual(observed.exception.code,
                         "GN1_FIRST_SAVE_CHILD_STREAM_OVER_BUDGET")
        receipt = self.receipt("huge-out")
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertTrue(receipt["streams"]["stdout"]["truncated"])
        self.assertGreater(receipt["streams"]["stdout"]["observed_bytes_at_least"],
                           save.MAX_ORIGINAL)
        self.assertEqual(
            (self.private / "huge-out.partial-stdout.original").stat().st_size,
            save.MAX_ORIGINAL)
        self.assertIsNone(receipt["streams"]["stderr"]["retained"])
        self.assertFalse((self.private / "huge-out.partial-stderr.original").exists())
        self.assertFalse((self.private / "huge-out.stdout.original").exists())

    def test_large_stderr_does_not_mask_unknown_as_custody_admission(self):
        code = ("import os,time;"
                f"os.write(2,b'E'*({save.MAX_ORIGINAL}+65536));"
                "time.sleep(2)")
        with self.assertRaises(save.FirstSaveFailure) as observed:
            self.invoke("huge-err", code)
        self.assertTrue(observed.exception.unknown)
        self.assertEqual(observed.exception.code,
                         "GN1_FIRST_SAVE_CHILD_STREAM_OVER_BUDGET")
        receipt = self.receipt("huge-err")
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertTrue(receipt["streams"]["stderr"]["truncated"])
        self.assertEqual(
            (self.private / "huge-err.partial-stderr.original").stat().st_size,
            save.MAX_ORIGINAL)
        self.assertIsNone(receipt["streams"]["stdout"]["retained"])
        self.assertFalse((self.private / "huge-err.partial-stdout.original").exists())

    def test_timeout_with_no_observed_bytes_preserves_unknown_and_no_fake_newline(self):
        with self.assertRaises(save.FirstSaveFailure) as observed:
            self.invoke("sleeping", "import time;time.sleep(2)", timeout=0.2)
        self.assertTrue(observed.exception.unknown)
        self.assertEqual(observed.exception.code,
                         "GN1_FIRST_SAVE_ORIGINAL_PROCESS_TIMEOUT")
        receipt = self.receipt("sleeping")
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertIsNone(receipt["streams"]["stdout"]["retained"])
        self.assertIsNone(receipt["streams"]["stderr"]["retained"])
        self.assertEqual(receipt["streams"]["stdout"]["observed_bytes_at_least"], 0)
        self.assertFalse((self.private / "sleeping.partial-stdout.original").exists())
        self.assertFalse((self.private / "sleeping.partial-stderr.original").exists())

    def test_returned_json_is_bound_and_empty_stderr_is_not_fabricated(self):
        result = self.invoke(
            "normal", "import sys;sys.stdout.write('{\"kind\":\"bounded\"}\\n')",
            timeout=4)
        self.assertEqual(result, {"kind": "bounded"})
        receipt = self.receipt("normal")
        self.assertEqual(receipt["status"], "RETURNED")
        self.assertEqual(receipt["exit_code"], 0)
        self.assertTrue(receipt["streams"]["stdout"]["eof"])
        self.assertTrue(receipt["streams"]["stderr"]["eof"])
        self.assertIsNone(receipt["streams"]["stderr"]["retained"])
        self.assertFalse((self.private / "normal.stderr.original").exists())


class OneSaveLifecycleTests(unittest.TestCase):
    def setUp(self):
        directory = os.environ.get("MOTIONWRIGHT_TEST_ROOT") or None
        self.temp = tempfile.TemporaryDirectory(dir=directory, prefix="gn1-first-save-source-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.private = self.root / "private"
        self.output = self.root / "output"
        self.private.mkdir()
        self.output.mkdir()
        self.database = self.root / "inert.sqlite3"
        self.database.write_bytes(b"NOT_A_SQLITE_DATABASE")
        self.session = self.root / "inert.session"
        self.session.write_bytes(b"INERT")
        self.src = save.checked_source_fixture(source())
        self.trace = []

    def inert_terminal_session(self, *, read_only=False):
        # Simulate the source-owned pipe after the child has exited. Windows
        # select() cannot inspect os.pipe; intercept the exact read boundary.
        session = save.NativeSession(
            self.root / "no-native-binary", self.database,
            self.root / "no-real-connection", self.private, {}, read_only=read_only)
        session.stderr = (self.private / (session.label + ".stderr.original")).open("xb")
        self.addCleanup(session.stderr.close)
        def complete_inert_stderr():
            # This fixture has NO process or Native Host. Model only a real
            # empty original pipe EOF after its exclusive sink is synchronized.
            session.stderr.flush()
            os.fsync(session.stderr.fileno())
            session.stderr.close()
            return {"observed_bytes_at_least": 0, "retained_prefix_bytes": 0,
                    "eof": True, "over_cap": False, "read_error": None,
                    "reader_finished": True, "complete": True,
                    "automatic_retry": False}
        session.stderr_capture = SimpleNamespace(
            finish=complete_inert_stderr, uncertain=lambda: False)
        output = SimpleNamespace(fileno=lambda: 8, close=lambda: None)
        session.child = SimpleNamespace(
            stdin=None, stdout=output, wait=lambda timeout: 0)
        return session, output

    def test_failed_native_child_start_never_leaks_private_launcher_error(self):
        session = save.NativeSession(
            self.root / "not-launched-native", self.database,
            self.root / "no-real-connection", self.private, {})
        with patch.object(save.subprocess, "Popen",
                          side_effect=OSError("private OS process path")) as spawn:
            with self.assertRaisesRegex(
                    save.FirstSaveFailure,
                    "GN1_FIRST_SAVE_NATIVE_CHILD_START_UNCERTAIN") as failure:
                session.__enter__()
        spawn.assert_called_once()
        self.assertTrue(failure.exception.unknown)
        self.assertTrue(session.unknown_request_latched)
        self.assertFalse(session.started)
        self.assertIsNone(session.child)
        self.assertTrue(session.stderr.closed)
        self.assertFalse(
            (self.private / "graph-native-session.spawn.original.json").exists())

    def test_failed_spawn_original_cannot_abandon_live_native_child(self):
        # Source-only failpoint on the receipt AFTER a child was admitted.
        # The child is an inert object, never a Native Host or real process.
        session = save.NativeSession(
            self.root / "not-launched-native", self.database,
            self.root / "no-real-connection", self.private, {})
        observed = []
        class InertChild:
            pid = 424242
            def __init__(self):
                self.stdin = SimpleNamespace(close=lambda: observed.append("stdin-close"))
                self.stdout = SimpleNamespace(close=lambda: observed.append("stdout-close"))
                self.stderr = SimpleNamespace(close=lambda: observed.append("stderr-pipe-close"))
                self.killed = False
            def poll(self):
                return None if not self.killed else -9
            def kill(self):
                self.killed = True
                observed.append("child-kill")
            def wait(self, *, timeout):
                observed.append("child-wait")
                return -9
        fake = InertChild()
        class InertCapture:
            def __init__(self, file):
                self.file = file
            def finish(self):
                self.file.close()
                observed.append("stderr-capture-finished")
        def capture_factory(child, file):
            self.assertIs(child, fake)
            return InertCapture(file)
        original_json = save.private_json
        def fail_spawn_receipt(path, value):
            if path.name.endswith(".spawn.original.json"):
                raise OSError("private spawn receipt not durable")
            return original_json(path, value)
        def stop_owned_group(pid, _signal):
            self.assertEqual(pid, fake.pid)
            fake.killed = True
            observed.append("owned-group-kill")
        with patch.object(save.subprocess, "Popen", return_value=fake) as started, \
                patch.object(save, "OriginalNativeStderr",
                             side_effect=capture_factory), \
                patch.object(save, "private_json", side_effect=fail_spawn_receipt), \
                patch.object(save.os, "killpg", side_effect=stop_owned_group,
                             create=True):
            try:
                with self.assertRaisesRegex(
                        save.FirstSaveFailure,
                        "GN1_FIRST_SAVE_NATIVE_SPAWN_CUSTODY_UNCERTAIN") as denied:
                    session.__enter__()
                self.assertTrue(denied.exception.unknown)
            finally:
                if session.stderr and not session.stderr.closed:
                    session.stderr.close()
        started.assert_called_once()
        self.assertEqual(started.call_args.kwargs["stderr"], subprocess.PIPE)
        self.assertTrue(fake.killed)
        self.assertIn("child-wait", observed)
        self.assertIn("stdin-close", observed)
        self.assertIn("stdout-close", observed)
        self.assertIn("stderr-capture-finished", observed)
        self.assertFalse(session.started)
        self.assertFalse(
            (self.private / "graph-native-session.spawn.original.json").exists())
        self.assertTrue(
            (self.private / "graph-native-session.intent.original.json").exists())

    def test_original_native_stderr_is_bounded_during_actual_child_output(self):
        # One inert Python child, NOT the original Native Host or a Save.
        # A direct-to-file stderr previously exceeded MAX_ORIGINAL before the
        # GN1 source checked that file at close.
        session = save.NativeSession(
            self.root / "never-started-native", self.database,
            self.root / "no-real-connection", self.private, {})
        real_popen = subprocess.Popen
        chunk_bytes = save.MAX_ORIGINAL + 65536
        command = [sys.executable, "-I", "-c",
                   "import sys;sys.stderr.buffer.write(b'X'*"
                   + str(chunk_bytes) + ");sys.stderr.buffer.flush()"]
        def spawn_inert(original_argv, **kwargs):
            self.assertEqual(original_argv, session.command)
            return real_popen(
                command, stdin=kwargs["stdin"], stdout=kwargs["stdout"],
                stderr=kwargs["stderr"], env=kwargs["env"])
        with patch.object(save.subprocess, "Popen", side_effect=spawn_inert):
            with self.assertRaisesRegex(
                    save.FirstSaveFailure, "GN1_FIRST_SAVE_SESSION_CLOSE_UNCERTAIN") as failure:
                with session:
                    pass
        self.assertTrue(failure.exception.unknown)
        retained = self.private / "graph-native-session.stderr.original"
        self.assertLessEqual(retained.stat().st_size, save.MAX_ORIGINAL)
        self.assertEqual(retained.read_bytes(), b"X" * save.MAX_ORIGINAL)
        terminal = json.loads(
            (self.private / "graph-native-session.terminal.original.json").read_text())
        self.assertTrue(terminal["stderr_over_cap"])
        self.assertFalse(terminal["stderr_retained_complete"])
        self.assertTrue(session.stderr_capture.uncertain())
        self.assertGreater(terminal["stderr_observed_bytes_at_least"],
                           save.MAX_ORIGINAL)
        self.assertFalse(terminal["original_reply_complete"])
        self.assertFalse(terminal["automatic_retry"])

    def test_actual_inert_child_normal_stderr_is_retained_complete(self):
        # Positive original stream capture, still an inert Python child.
        session = save.NativeSession(
            self.root / "never-started-native", self.database,
            self.root / "no-real-connection", self.private, {})
        original = b"one original bounded stderr diagnostic\\n"
        real_popen = subprocess.Popen
        command = [sys.executable, "-I", "-c",
                   "import sys;sys.stderr.buffer.write(" + repr(original)
                   + ");sys.stderr.buffer.flush()"]
        def spawn_inert(_argv, **kwargs):
            return real_popen(
                command, stdin=kwargs["stdin"], stdout=kwargs["stdout"],
                stderr=kwargs["stderr"], env=kwargs["env"])
        with patch.object(save.subprocess, "Popen", side_effect=spawn_inert):
            with session:
                pass
        retained = self.private / "graph-native-session.stderr.original"
        self.assertEqual(retained.read_bytes(), original)
        terminal = json.loads(
            (self.private / "graph-native-session.terminal.original.json").read_text())
        self.assertTrue(terminal["stderr_retained_complete"])
        self.assertFalse(terminal["stderr_over_cap"])
        self.assertFalse(session.stderr_capture.uncertain())
        self.assertEqual(terminal["stderr_bytes"], len(original))
        self.assertEqual(terminal["stderr_sha256"], save.digest(original))
        self.assertTrue(terminal["original_reply_complete"])
        self.assertFalse(terminal["automatic_retry"])

    def test_close_timeout_raced_process_exit_cannot_be_promoted_to_success(self):
        # Original child wait first times out, then the process group has
        # disappeared by the time SIGKILL is issued. Even a reported 0 from
        # the second wait is NOT proof of a clean original terminal.
        session, _ = self.inert_terminal_session()
        session.child.pid = 424242
        calls = []
        def wait(*, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                raise subprocess.TimeoutExpired(["inert-original"], timeout)
            return 0
        session.child.wait = wait
        complete = {"raw": b"", "eof": True, "over_cap": False,
                    "reader_finished": True, "deadline_exceeded": False,
                    "read_error": False}
        with patch.object(save.os, "killpg",
                          side_effect=ProcessLookupError("already gone"),
                          create=True), \
                patch.object(session.child, "kill",
                             side_effect=ProcessLookupError("already gone"),
                             create=True), \
                patch.object(save, "read_terminal_stdout",
                             return_value=complete):
            with self.assertRaisesRegex(
                    save.FirstSaveFailure,
                    "GN1_FIRST_SAVE_SESSION_CLOSE_UNCERTAIN") as error:
                session.__exit__(None, None, None)
        self.assertTrue(error.exception.unknown)
        self.assertEqual(calls, [20, 10])
        self.assertTrue(
            (self.private / "graph-native-session.close-timeout.original.json").exists())
        receipt = json.loads(
            (self.private / "graph-native-session.terminal.original.json").read_text())
        self.assertEqual(receipt["exit_code"], 0)
        self.assertTrue(receipt["close_timed_out"])
        self.assertFalse(receipt["original_reply_complete"])
        self.assertFalse(receipt["automatic_retry"])

    def test_close_timeout_unfinished_child_cannot_prove_terminal(self):
        session, _ = self.inert_terminal_session()
        session.child.pid = 424242
        seen = []
        def wait(*, timeout):
            seen.append(timeout)
            raise subprocess.TimeoutExpired(["inert-never-finished"], timeout)
        session.child.wait = wait
        terminated = []
        session.child.kill = lambda: terminated.append("direct-child-only")
        complete = {"raw": b"", "eof": True, "over_cap": False,
                    "reader_finished": True, "deadline_exceeded": False,
                    "read_error": False}
        with patch.object(save.os, "killpg", return_value=None, create=True), \
                patch.object(save, "read_terminal_stdout", return_value=complete):
            with self.assertRaisesRegex(save.FirstSaveFailure,
                                        "GN1_FIRST_SAVE_SESSION_CLOSE_UNCERTAIN"):
                session.__exit__(None, None, None)
        self.assertEqual(seen, [20, 10, 2])
        self.assertEqual(terminated, ["direct-child-only"] if os.name == "posix"
                         else ["direct-child-only", "direct-child-only"])
        terminal = json.loads(
            (self.private / "graph-native-session.terminal.original.json").read_text())
        self.assertIsNone(terminal["exit_code"])
        self.assertTrue(terminal["close_wait_unfinished"])
        self.assertTrue(terminal["close_timed_out"])
        self.assertFalse(terminal["original_reply_complete"])
        captured = json.loads(
            (self.private / "graph-native-session.close-timeout.original.json").read_text())
        self.assertTrue(captured["close_wait_unfinished"])
        self.assertTrue(captured["unknown"])

    def test_close_timeout_original_failure_survives_missing_timeout_receipt(self):
        session, _ = self.inert_terminal_session()
        session.child.pid = 424242
        times = []
        def wait(*, timeout):
            times.append(timeout)
            return 0 if len(times) == 2 else (
                (_ for _ in ()).throw(
                    subprocess.TimeoutExpired(["inert"], timeout)))
        session.child.wait = wait
        session.child.kill = lambda: None
        original = save.FirstSaveFailure("ORIGINAL_MUTATION_UNKNOWN", unknown=True)
        complete = {"raw": b"", "eof": True, "over_cap": False,
                    "reader_finished": True, "deadline_exceeded": False,
                    "read_error": False}
        private_json = save.private_json
        def write_receipt(path, payload):
            if path.name.endswith(".close-timeout.original.json"):
                raise OSError("secondary original disk write failure")
            return private_json(path, payload)
        with patch.object(save.os, "killpg", return_value=None, create=True), \
                patch.object(save, "read_terminal_stdout", return_value=complete), \
                patch.object(save, "private_json", side_effect=write_receipt):
            self.assertFalse(session.__exit__(type(original), original, None))
        self.assertEqual(original.code, "ORIGINAL_MUTATION_UNKNOWN")
        self.assertEqual(times, [20, 10])
        terminal = json.loads(
            (self.private / "graph-native-session.terminal.original.json").read_text())
        self.assertTrue(terminal["close_timed_out"])
        self.assertTrue(terminal["close_timeout_capture_failed"])
        self.assertFalse(terminal["original_reply_complete"])

    def test_failed_writer_stdin_close_is_unknown_with_original_terminal_retained(self):
        session, output = self.inert_terminal_session()
        class OriginalPipeCloseError:
            def close(self):
                raise BrokenPipeError("original writer stdin pipe closed unexpectedly")
        session.child.stdin = OriginalPipeCloseError()
        complete = {"raw": b"", "eof": True, "over_cap": False,
                    "reader_finished": True, "deadline_exceeded": False,
                    "read_error": False}
        with patch.object(save, "read_terminal_stdout", return_value=complete):
            with self.assertRaisesRegex(
                    save.FirstSaveFailure, "GN1_FIRST_SAVE_SESSION_CLOSE_UNCERTAIN") as denied:
                session.__exit__(None, None, None)
        self.assertTrue(denied.exception.unknown)
        terminal = json.loads(
            (self.private / "graph-native-session.terminal.original.json").read_text())
        self.assertTrue(terminal["stdin_close_failed"])
        self.assertFalse(terminal["original_reply_complete"])
        self.assertTrue(terminal["stdout_eof_observed"])
        self.assertEqual(terminal["exit_code"], 0)

    def test_failed_stdin_close_does_not_replace_the_first_original_error(self):
        session, output = self.inert_terminal_session()
        class OriginalPipeCloseError:
            def close(self):
                raise BrokenPipeError("secondary pipe close denied")
        session.child.stdin = OriginalPipeCloseError()
        original = save.FirstSaveFailure("ORIGINAL_MUTATION_UNKNOWN", unknown=True)
        complete = {"raw": b"", "eof": True, "over_cap": False,
                    "reader_finished": True, "deadline_exceeded": False,
                    "read_error": False}
        with patch.object(save, "read_terminal_stdout", return_value=complete):
            # Context-manager __exit__ must not overwrite the original when
            # a secondary stdin-close error occurs after dispatch.
            self.assertFalse(session.__exit__(type(original), original, None))
        terminal = json.loads(
            (self.private / "graph-native-session.terminal.original.json").read_text())
        self.assertTrue(terminal["stdin_close_failed"])
        self.assertFalse(terminal["original_reply_complete"])
        self.assertEqual(original.code, "ORIGINAL_MUTATION_UNKNOWN")

    def test_stderr_custody_failure_denies_next_original_request_pre_dispatch(self):
        # An already-overrun stderr stream forbids a subsequent Native effect
        # even while the original process still reports a live PID.
        session, _ = self.inert_terminal_session()
        session.started = True
        session.child.poll = lambda: None
        writes = []
        session.child.stdin = SimpleNamespace(
            write=lambda body: writes.append(body),
            flush=lambda: None, close=lambda: None)
        session.stderr_capture.uncertain = lambda: True
        with patch.object(save, "read_live_stdout",
                          side_effect=AssertionError("never read Native stdout")):
            with self.assertRaisesRegex(
                    save.FirstSaveFailure,
                    "GN1_FIRST_SAVE_NATIVE_STDERR_CUSTODY_UNCERTAIN") as denied:
                session.request("second-native-effect", "apply_change",
                                {"not_dispatched": True})
        self.assertTrue(denied.exception.unknown)
        self.assertEqual(writes, [])
        self.assertFalse(
            (self.private / "second-native-effect.request.original.jsonl").exists())

    def test_stderr_overrun_after_original_reply_is_unknown_without_ack(self):
        # The exact original JSONL bytes must survive, but an already observed
        # stream overrun cannot turn a returned 'ok' into confirmed authority.
        session, _ = self.inert_terminal_session()
        session.started = True
        session.child.poll = lambda: None
        session.child.stdin = SimpleNamespace(
            write=lambda body: len(body),
            flush=lambda: None, close=lambda: None)
        state = {"uncertain": False}
        session.stderr_capture.uncertain = lambda: state["uncertain"]
        def original_reply(_pipe, **_kwargs):
            body = json.loads(
                (self.private / "returned-but-overrun.request.original.jsonl").read_text())
            state["uncertain"] = True
            return (json.dumps({
                "id": body["id"], "ok": True,
                "result": {"this_is_not_a_confirmed_save": True},
            }) + "\n").encode()
        with patch.object(save, "read_live_stdout", side_effect=original_reply):
            with self.assertRaisesRegex(
                    save.FirstSaveFailure,
                    "GN1_FIRST_SAVE_NATIVE_STDERR_CUSTODY_UNCERTAIN") as denied:
                session.request("returned-but-overrun", "apply_change",
                                {"not_dispatched": True})
        self.assertTrue(denied.exception.unknown)
        self.assertEqual(
            json.loads((self.private /
                "returned-but-overrun.response.original.jsonl").read_text())["ok"],
            True)
        self.assertEqual(state["uncertain"], True)

    def test_unknown_native_reply_timeout_blocks_another_request_and_successful_close(self):
        session, _ = self.inert_terminal_session()
        session.started = True
        session.child.poll = lambda: None
        writes = []
        session.child.stdin = SimpleNamespace(
            write=lambda raw: writes.append(raw),
            flush=lambda: None, close=lambda: None)
        with patch.object(save, "read_live_stdout", return_value=None):
            with self.assertRaisesRegex(
                    save.FirstSaveFailure,
                    "GN1_FIRST_SAVE_NATIVE_REPLY_TIMEOUT") as first:
                session.request("first-unknown-rpc", "apply_change",
                                {"effect": "never retried"})
            self.assertTrue(first.exception.unknown)
            with self.assertRaisesRegex(
                    save.FirstSaveFailure,
                    "GN1_FIRST_SAVE_NATIVE_SESSION_UNKNOWN_LATCHED") as replay:
                session.request("new-label-cannot-retry", "apply_change",
                                {"effect": "not authorized"})
            self.assertTrue(replay.exception.unknown)
        self.assertEqual(len(writes), 1)
        self.assertFalse(
            (self.private / "new-label-cannot-retry.request.original.jsonl").exists())
        complete = {"raw": b"", "eof": True, "over_cap": False,
                    "reader_finished": True, "deadline_exceeded": False,
                    "read_error": False}
        with patch.object(save, "read_terminal_stdout", return_value=complete):
            with self.assertRaisesRegex(save.FirstSaveFailure,
                                        "GN1_FIRST_SAVE_SESSION_CLOSE_UNCERTAIN"):
                session.__exit__(None, None, None)
        terminal = json.loads(
            (self.private / "graph-native-session.terminal.original.json").read_text())
        self.assertTrue(terminal["unknown_request_latched"])
        self.assertFalse(terminal["original_reply_complete"])

    def test_malformed_native_reply_latches_unknown_before_a_new_label(self):
        session, _ = self.inert_terminal_session()
        session.started = True
        session.child.poll = lambda: None
        writes = []
        session.child.stdin = SimpleNamespace(
            write=lambda raw: writes.append(raw),
            flush=lambda: None, close=lambda: None)
        original = b'{"original":"malformed",not-valid-json}\n'
        with patch.object(save, "read_live_stdout", return_value=original):
            with self.assertRaisesRegex(
                    save.FirstSaveFailure,
                    "GN1_FIRST_SAVE_NATIVE_REPLY_INVALID") as error:
                session.request("malformed-first-original", "apply_change",
                                {"original": "request"})
            self.assertTrue(error.exception.unknown)
            with self.assertRaisesRegex(
                    save.FirstSaveFailure,
                    "GN1_FIRST_SAVE_NATIVE_SESSION_UNKNOWN_LATCHED"):
                session.request("forbidden-second-original", "apply_change",
                                {"disallowed": True})
        self.assertEqual(len(writes), 1)
        self.assertEqual(
            (self.private / "malformed-first-original.response.original.jsonl").read_bytes(),
            original)
        self.assertFalse(
            (self.private / "forbidden-second-original.request.original.jsonl").exists())

    def test_unterminated_native_jsonl_reply_is_retained_as_partial_original(self):
        # The original JSONL read observed bytes, but no newline/terminal
        # envelope arrived. The read is UNKNOWN, not a replay or empty reply.
        session, output = self.inert_terminal_session()
        session.started = True
        session.child.poll = lambda: None
        session.child.stdin = SimpleNamespace(
            write=lambda body: len(body), flush=lambda: None, close=lambda: None)
        partial = b'{"id":"unfinished-original","ok":true'
        complete = {"raw": b"", "eof": True, "over_cap": False,
                    "reader_finished": True, "deadline_exceeded": False,
                    "read_error": False}
        with patch.object(save, "read_live_stdout", side_effect=[partial, None]):
            with self.assertRaisesRegex(
                    save.FirstSaveFailure, "GN1_FIRST_SAVE_NATIVE_REPLY_TIMEOUT") as unknown:
                session.request("incomplete-native-save", "apply_change",
                                {"test_only_not_dispatched": True})
        self.assertTrue(unknown.exception.unknown)
        self.assertEqual(bytes(session.pending), partial)
        with patch.object(save, "read_terminal_stdout", return_value=complete):
            self.assertFalse(session.__exit__(
                type(unknown.exception), unknown.exception, None))
        retained = self.private / "graph-native-session.pending-stdout.original"
        self.assertEqual(retained.read_bytes(), partial)
        receipt = json.loads(
            (self.private / "graph-native-session.terminal.original.json").read_text())
        self.assertFalse(receipt["original_reply_complete"])
        self.assertEqual(receipt["pending_stdout"]["bytes"], len(partial))
        self.assertEqual(receipt["pending_stdout"]["sha256"], save.digest(partial))
        self.assertFalse(receipt["automatic_retry"])
        self.assertFalse((self.private / "incomplete-native-save.response.original.jsonl").exists())


    def test_failed_partial_stdout_retention_keeps_first_native_timeout(self):
        session, output = self.inert_terminal_session()
        partial = b'{"partial":"uncommitted original"'
        session.pending.extend(partial)
        original = save.FirstSaveFailure("GN1_FIRST_SAVE_NATIVE_REPLY_TIMEOUT",
                                         unknown=True)
        complete = {"raw": b"", "eof": True, "over_cap": False,
                    "reader_finished": True, "deadline_exceeded": False,
                    "read_error": False}
        write_original = save.private_bytes
        def blocked_partial_write(path, data):
            if path.name.endswith(".pending-stdout.original"):
                raise OSError("original private retention unavailable")
            return write_original(path, data)
        with patch.object(save, "private_bytes", side_effect=blocked_partial_write), \
                patch.object(save, "read_terminal_stdout", return_value=complete):
            self.assertFalse(session.__exit__(type(original), original, None))
        self.assertEqual(original.code, "GN1_FIRST_SAVE_NATIVE_REPLY_TIMEOUT")
        self.assertTrue(original.unknown)
        terminal = json.loads(
            (self.private / "graph-native-session.terminal.original.json").read_text())
        self.assertTrue(terminal["pending_stdout_capture_failed"])
        self.assertIsNone(terminal["pending_stdout"])
        self.assertFalse(terminal["original_reply_complete"])
        self.assertFalse((self.private / "graph-native-session.pending-stdout.original").exists())

    def test_native_terminal_requires_actual_stdout_eof_even_after_last_reply(self):
        session, output = self.inert_terminal_session(read_only=True)
        complete = {"raw": b"", "eof": True, "over_cap": False,
                    "reader_finished": True, "deadline_exceeded": False,
                    "read_error": False}
        with patch.object(save, "read_terminal_stdout", return_value=complete):
            self.assertFalse(session.__exit__(None, None, None))
        terminal = json.loads(
            (self.private / "independent-readonly-session.terminal.original.json").read_text())
        self.assertEqual(terminal["exit_code"], 0)
        self.assertTrue(terminal["stdout_eof_observed"])
        self.assertTrue(terminal["original_reply_complete"])
        self.assertIsNone(terminal["trailing_stdout"])

    def test_native_terminal_retains_unexpected_stdout_and_refuses_success(self):
        session, output = self.inert_terminal_session()
        extra = {"raw": b"unexpected-private-jsonl\\n", "eof": True,
                 "over_cap": False, "reader_finished": True,
                 "deadline_exceeded": False, "read_error": False}
        with patch.object(save, "read_terminal_stdout", return_value=extra):
            with self.assertRaisesRegex(
                    save.FirstSaveFailure, "GN1_FIRST_SAVE_SESSION_CLOSE_UNCERTAIN") as result:
                session.__exit__(None, None, None)
        self.assertTrue(result.exception.unknown)
        trailing = self.private / "graph-native-session.trailing-stdout.original"
        self.assertEqual(trailing.read_bytes(), b"unexpected-private-jsonl\\n")
        terminal = json.loads(
            (self.private / "graph-native-session.terminal.original.json").read_text())
        self.assertFalse(terminal["original_reply_complete"])
        self.assertTrue(terminal["stdout_eof_observed"])
        self.assertEqual(terminal["trailing_stdout"]["bytes"], len(trailing.read_bytes()))

    def test_inherited_stdout_writer_cannot_fake_complete_terminal(self):
        session, output = self.inert_terminal_session()
        inherited = {"raw": b"", "eof": False, "over_cap": False,
                     "reader_finished": True, "deadline_exceeded": True,
                     "read_error": False}
        with patch.object(save, "read_terminal_stdout", return_value=inherited):
            with self.assertRaisesRegex(
                    save.FirstSaveFailure, "GN1_FIRST_SAVE_SESSION_CLOSE_UNCERTAIN") as result:
                session.__exit__(None, None, None)
        self.assertTrue(result.exception.unknown)
        terminal = json.loads(
            (self.private / "graph-native-session.terminal.original.json").read_text())
        self.assertFalse(terminal["stdout_eof_observed"])
        self.assertFalse(terminal["original_reply_complete"])

    @contextmanager
    def fake_session(self, replies, *args, **kwargs):
        owner = self
        class Fake:
            def request(self, label, command, payload):
                owner.trace.append(command)
                expected, response = replies.pop(0)
                owner.assertEqual(command, expected)
                return response
        yield Fake()

    def execute(self, *, context_ready=True, save_ok=True, release_ok=True,
                writer_witness="a" * 64, grant_token="ffffffff-ffff-4fff-8fff-ffffffffffff"):
        project = self.src["project"]
        saved = {**project, "revision": 3}
        replies = [
            ("get_project", {"ok": True, "result": project}),
            ("semantic_graph_authoring_context", {"ok": True, "result": {
                "availability": "ready" if context_ready else "blocked"}}),
            ("issue_effect_grant", {"ok": True, "result": {"token": grant_token}}),
            ("apply_change", {"ok": save_ok, "result": saved if save_ok else None}),
            ("opened_store_identity_witness", {"ok": True, "result": writer_witness}),
            ("reopen", {"ok": True, "result": saved}),
            ("prepare_read_only_handoff", {"ok": True, "result": {
                "read_only_handoff": True}}),
        ]
        @contextmanager
        def factory(*args, **kwargs):
            with self.fake_session(replies, *args, **kwargs) as instance:
                yield instance
        mounted = {"provider": "driver:motionwright",
                   "connection": "original-host-lease", "provider_generation": 7}
        def release_original():
            self.trace.append("release_bound")
            return {**mounted, "lease_shutdown_completed": release_ok,
                    "native_process_closure_inferred": False}
        with patch.object(save, "authenticated_connection", return_value={"test": "inert"}), \
             patch.object(save, "NativeSession", factory):
            return save.real_first_save(
                fixture=self.root / "never-dispatched",
                native_session=self.root / "never-started",
                source=self.src, store_path=self.database,
                client={"session_file": self.session}, mounted=mounted, binaries={},
                private=self.private, output=self.output, env={},
                release_provider=release_original)

    def test_native_connection_is_bound_to_exact_disposable_store_mount(self):
        mounted = {
            "provider": "driver:motionwright", "provider_generation": 1,
            "connection": "observed-actual", "binding_ref": "observed-binding",
            "mount": {"root": "motionwright-data",
                      "path": str(self.root), "read": True,
                      "write": True, "execute": False},
        }
        client = {"session_file": self.session, "socket": self.root / "broker.sock"}
        binaries = {"semwright": {"path": str(self.root / "semwright"),
                                  "sha256": "f" * 64}}
        with patch.object(Path, "is_socket", return_value=True):
            actual = save.authenticated_connection(
                client=client, mounted=mounted, resource=self.src["resource"],
                binaries=binaries, output=self.output,
                expected_store_parent=self.root)
            self.assertEqual(actual["session_file"], str(self.session))
            self.assertEqual(actual["resource"], self.src["resource"])
            foreign = {**mounted, "mount": {**mounted["mount"], "path": str(self.private)}}
            with self.assertRaisesRegex(save.FirstSaveFailure, "REAL_MOUNT_NOT_READY"):
                save.authenticated_connection(
                    client=client, mounted=foreign, resource=self.src["resource"],
                    binaries=binaries, output=self.output,
                    expected_store_parent=self.root)

    def test_one_save_after_grant_followed_by_reopen_and_close(self):
        result = self.execute()
        self.assertEqual(self.trace, [
            "get_project", "semantic_graph_authoring_context",
            "issue_effect_grant", "apply_change",
            "opened_store_identity_witness", "reopen",
            "release_bound", "prepare_read_only_handoff"])
        self.assertTrue(result["one_native_graph_save_and_reopen"])
        self.assertEqual(result["writer_opened_store_identity_witness"], "a" * 64)
        self.assertEqual(result["Graph6_accepted"], 0)
        self.assertFalse(result["formal_G2_granted"])
        intent = json.loads((self.private / "first-save-intent.original.json").read_text())
        self.assertTrue(intent["explicit_one_original_save_selected"])
        self.assertNotIn("first_save_authorized", intent)
        self.assertNotIn("token", intent)
        grant = json.loads((self.private / "first-save-grant-observed.original.json").read_text())
        self.assertTrue(grant["native_edit_grant_observed"])
        self.assertEqual(grant["request_id"], intent["request_id"])
        self.assertEqual(grant["project_id"], self.src["project"]["id"])
        self.assertFalse(grant["formal_G2_granted"])
        self.assertFalse(grant["automatic_retry"])
        self.assertNotIn("token", grant)

    def test_invalid_writer_opened_pager_identity_never_reopens_or_retries(self):
        with self.assertRaisesRegex(
                save.FirstSaveFailure, "OPENED_SQLITE_WITNESS_INVALID") as observed:
            self.execute(writer_witness="A" * 64)
        self.assertTrue(observed.exception.unknown)
        self.assertEqual(self.trace.count("apply_change"), 1)
        self.assertEqual(self.trace[-1], "opened_store_identity_witness")
        self.assertNotIn("reopen", self.trace)
        self.assertNotIn("release_bound", self.trace)

    def test_unconfirmed_provider_lease_blocks_wal_handoff_after_save(self):
        with self.assertRaisesRegex(save.FirstSaveFailure, "PROVIDER_LEASE_NOT_QUIESCENT") as failure:
            self.execute(release_ok=False)
        self.assertTrue(failure.exception.unknown)
        self.assertEqual(self.trace.count("apply_change"), 1)
        self.assertEqual(self.trace[-1], "release_bound")
        self.assertNotIn("prepare_read_only_handoff", self.trace)

    def test_readonly_subprocess_has_zero_native_callbacks_and_separate_custody(self):
        conn = self.private / "connection.json"
        conn.write_text("{}")
        reader = save.NativeSession(self.root / "never-dispatched",
                                    self.database, conn, self.private, {},
                                    read_only=True)
        self.assertEqual(reader.command[-1], "--read-only")
        self.assertEqual(reader.env["MOTIONWRIGHT_GRAPH_CALLBACK_CAP"], "0")
        self.assertEqual(reader.label, "independent-readonly-session")
        self.assertTrue((self.private / (reader.label + ".intent.original.json")).exists())

    def test_blocked_context_cannot_request_grant_or_save(self):
        with self.assertRaisesRegex(save.FirstSaveFailure, "CONTEXT_NOT_READY"):
            self.execute(context_ready=False)
        self.assertEqual(self.trace, ["get_project", "semantic_graph_authoring_context"])
        intent = json.loads((self.private / "first-save-intent.original.json").read_text())
        self.assertTrue(intent["explicit_one_original_save_selected"])
        self.assertNotIn("first_save_authorized", intent)
        self.assertFalse((self.private / "first-save-grant-observed.original.json").exists())

    def test_empty_grant_token_never_becomes_original_authority(self):
        with self.assertRaisesRegex(save.FirstSaveFailure, "REAL_EDIT_GRANT_UNAVAILABLE"):
            self.execute(grant_token="")
        self.assertEqual(self.trace[-1], "issue_effect_grant")
        self.assertNotIn("apply_change", self.trace)
        self.assertFalse((self.private / "first-save-grant-observed.original.json").exists())

    def test_nil_grant_token_is_never_a_real_native_edit_grant(self):
        with self.assertRaisesRegex(save.FirstSaveFailure, "REAL_EDIT_GRANT_UNAVAILABLE"):
            self.execute(grant_token="00000000-0000-0000-0000-000000000000")
        self.assertEqual(self.trace[-1], "issue_effect_grant")
        self.assertNotIn("apply_change", self.trace)
        self.assertFalse((self.private / "first-save-grant-observed.original.json").exists())

    def test_unknown_save_has_one_call_and_no_reopen_or_retry(self):
        with self.assertRaisesRegex(save.FirstSaveFailure, "SAVE_REJECTED_OR_UNKNOWN") as outcome:
            self.execute(save_ok=False)
        self.assertTrue(outcome.exception.unknown)
        self.assertEqual(self.trace.count("apply_change"), 1)
        self.assertNotIn("reopen", self.trace)


if __name__ == "__main__":
    unittest.main()
