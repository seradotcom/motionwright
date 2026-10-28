"""Focused controls for installed authority evidence; no installed acceptance credit.

This is a newly authored test source. The historical 14,610-byte source with
SHA256 b3ab46b7ee08513fb311fa783bb7b1d657eb3994ebe0451a930d143a911ac254
could not be recovered and is not claimed as this file's preimage or result.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

TOOLING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLING))
import native_host_installed_shared_state as installed
import native_provider_lifecycle as lifecycle


class PrivateScenarioCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)


class OneAttemptMutationTests(PrivateScenarioCase):
    def test_lost_reply_retains_unknown_and_blocks_a_second_dispatch(self):
        calls = []

        def lost_reply():
            calls.append("dispatched")
            raise subprocess.TimeoutExpired("private-command", 1)

        with self.assertRaises(subprocess.TimeoutExpired):
            installed.dispatch_rename_once(self.root, lost_reply, {"request": "retained"})
        intent = self.root / "native-rename-intent.json"
        outcome = self.root / "native-rename-outcome.json"
        originals = (intent.read_bytes(), outcome.read_bytes())
        self.assertEqual(json.loads(originals[1])["status"], "UNKNOWN")
        self.assertIs(json.loads(originals[1])["automatic_retry"], False)
        with self.assertRaises(FileExistsError):
            installed.dispatch_rename_once(self.root, lost_reply, {"request": "new-key"})
        self.assertEqual(calls, ["dispatched"])
        self.assertEqual((intent.read_bytes(), outcome.read_bytes()), originals)

    def test_error_keeps_original_child_and_only_literal_known_true_is_definitive(self):
        for label, known, expected in (
            ("absent", None, "UNKNOWN"), ("false", False, "UNKNOWN"),
            ("integer", 1, "UNKNOWN"), ("true", True, "KNOWN_FAILURE"),
        ):
            with self.subTest(case=label):
                directory = self.root / label
                directory.mkdir()
                error = {"code": "PrivateOriginal", "message": "original child"}
                if known is not None:
                    error["outcome_known"] = known
                original = {"ok": False, "error": error, "extra": {"retained": True}}
                result = installed.dispatch_rename_once(directory, lambda: original, {})
                self.assertIs(result, original)
                retained = installed.read_json(directory / "native-rename-outcome.json")
                self.assertEqual(retained["original_child"], original)
                self.assertEqual(retained["status"], expected)


class ClosedGuiLogTests(PrivateScenarioCase):
    def prepare_profile(self):
        profile = self.root / "profile"
        profile.mkdir()
        (profile / "app-run.log").write_bytes(b"leader output\n")
        return profile

    def closure(self, profile, status):
        @contextmanager
        def scope(receipt):
            try:
                yield
            finally:
                with (profile / "app-run.log").open("ab") as stream:
                    stream.write(b"descendant final output\n")
                installed.publish(receipt, {"status": status})
                if status != "PASS":
                    raise installed.ScenarioError("DESCENDANTS_NOT_CLOSED_NO_FINAL_SNAPSHOT")
        return scope

    def test_log_is_copied_after_descendant_final_write_and_pass_receipt(self):
        profile = self.prepare_profile()
        with patch.object(installed, "owned_process_scope", self.closure(profile, "PASS")), patch.object(
            installed, "gui_launch", return_value={"observed": True}
        ):
            result = installed.closed_gui_launch(SimpleNamespace(), profile, None, phase="created")
        self.assertEqual(result, {"observed": True})
        retained = self.root / "app-run-created.log"
        self.assertEqual(retained.read_bytes(), b"leader output\ndescendant final output\n")
        self.assertEqual(retained.stat().st_mode & 0o777, 0o600)

    def test_ui_failure_keeps_final_log_after_successful_closure(self):
        profile = self.prepare_profile()
        with patch.object(installed, "owned_process_scope", self.closure(profile, "PASS")), patch.object(
            installed, "gui_launch", side_effect=installed.ScenarioError("UI_READBACK_FAILED")
        ), self.assertRaisesRegex(installed.ScenarioError, "UI_READBACK_FAILED"):
            installed.closed_gui_launch(SimpleNamespace(), profile, None, phase="reopened")
        self.assertEqual((self.root / "app-run-reopened.log").read_bytes(),
                         b"leader output\ndescendant final output\n")

    def test_incomplete_closure_cannot_publish_an_immutable_phase_log(self):
        profile = self.prepare_profile()
        with patch.object(installed, "owned_process_scope", self.closure(profile, "FAIL")), patch.object(
            installed, "gui_launch", return_value={}
        ), self.assertRaisesRegex(installed.ScenarioError, "DESCENDANTS_NOT_CLOSED"):
            installed.closed_gui_launch(SimpleNamespace(), profile, None, phase="created")
        self.assertFalse((self.root / "app-run-created.log").exists())
        self.assertTrue((profile / "app-run.log").exists())


class ProcessCustodyTests(PrivateScenarioCase):
    @staticmethod
    def clock():
        value = [0.0]

        def monotonic():
            value[0] += 0.1
            return value[0]
        return SimpleNamespace(monotonic=monotonic, sleep=lambda _: None)

    @staticmethod
    def empty_tree(*, truncated=False):
        return {"root_observed": True, "truncated": truncated,
                "processes": [{"pid": os.getpid(), "ppid": os.getppid()}]}

    def test_incomplete_proc_tree_never_proves_closed_descendants(self):
        for observed in (
            self.empty_tree(truncated=True),
            {"root_observed": False, "truncated": False, "processes": []},
        ):
            with self.subTest(observation=observed), patch.object(
                installed, "subreaper", return_value=1
            ), patch.object(installed, "tree_snapshot", return_value=observed), patch.object(
                installed, "time", self.clock()
            ):
                result = installed.close_own_children(seconds=0.5, grace=0)
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["reason"], "CHILD_CLOSURE_DEADLINE")
            self.assertEqual(result["authority"], "process_custody_only_not_revocation")

    def test_missing_children_file_is_a_truncated_observation(self):
        process = self.root / "101"
        (process / "task" / "101").mkdir(parents=True)
        (process / "status").write_text("Name:\tpython3\nPPid:\t1\nState:\tR\n")
        result = lifecycle.tree_snapshot(101, proc_root=self.root)
        self.assertTrue(result["root_observed"])
        self.assertTrue(result["truncated"])

    def test_pid_namespace_mismatch_is_rejected_before_enabling_subreaper(self):
        with patch.object(Path, "read_text", return_value=f"{os.getpid() + 1} (other) R"), patch.object(
            installed, "subreaper"
        ) as subreaper, self.assertRaisesRegex(installed.ScenarioError, "PROC_PID_NAMESPACE_MISMATCH"):
            with installed.supervise_own_processes():
                self.fail("mismatched procfs was admitted")
        subreaper.assert_not_called()

    def test_echild_is_not_death_or_permission_to_signal_a_pid(self):
        observed = self.empty_tree()
        observed["processes"].append({"pid": os.getpid() + 1000, "ppid": os.getpid()})
        with patch.object(installed, "subreaper", return_value=1), patch.object(
            installed, "tree_snapshot", return_value=observed
        ), patch.object(installed, "time", self.clock()), patch.object(
            installed.os, "pidfd_open", return_value=73
        ), patch.object(installed.os, "waitid", side_effect=ChildProcessError) as waitid, patch.object(
            installed.os, "waitpid"
        ) as waitpid, patch.object(installed.os, "close") as close, patch.object(
            installed.signal, "pidfd_send_signal"
        ) as send:
            result = installed.close_own_children(seconds=0.5, grace=0)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(waitid.called)
        self.assertEqual(close.call_count, waitid.call_count)
        send.assert_not_called()
        waitpid.assert_not_called()

    def test_failed_scope_publishes_failure_before_forbidding_final_snapshot(self):
        receipt = self.root / "closure.json"
        with patch.object(installed, "subreaper", return_value=1), patch.object(
            installed, "tree_snapshot", return_value=self.empty_tree()
        ), patch.object(installed, "close_own_children", return_value={"status": "FAIL"}), self.assertRaisesRegex(
            installed.ScenarioError, "DESCENDANTS_NOT_CLOSED_NO_FINAL_SNAPSHOT"
        ):
            with installed.owned_process_scope(receipt):
                pass
        self.assertEqual(installed.read_json(receipt)["status"], "FAIL")

    def test_real_adopted_orphan_is_closed_without_stealing_leader_exit(self):
        if sys.platform != "linux" or not all(hasattr(os, name) for name in (
            "pidfd_open", "waitid", "P_PIDFD", "WNOWAIT"
        )) or not hasattr(signal, "pidfd_send_signal"):
            self.skipTest("NOT_RUN: Linux pidfd/subreaper prerequisites unavailable")
        if Path("/proc/self/stat").read_text().split(" ", 1)[0] != str(os.getpid()):
            self.skipTest("NOT_RUN: procfs PID namespace differs from this process")
        observed = lifecycle.tree_snapshot(os.getpid())
        if not observed["root_observed"] or observed["truncated"]:
            self.skipTest("NOT_RUN: this environment cannot completely observe process descendants")

        # The probe is a separate session; timeout cleanup signals only its own
        # process group. Neither mocks nor a fake /proc tree enter this path.
        child = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('READY',flush=True); time.sleep(30)"
        leader = textwrap.dedent(f"""
            import json,subprocess,sys
            process = subprocess.Popen([sys.executable, '-c', {child!r}], stdout=subprocess.PIPE)
            assert process.stdout.readline().strip() == b'READY'
            print(json.dumps({{'child': process.pid}}), flush=True)
            raise SystemExit(7)
        """)
        program = textwrap.dedent(f"""
            import json,os,subprocess,sys,time,traceback
            from pathlib import Path
            sys.path.insert(0, {str(TOOLING)!r})
            import native_host_installed_shared_state as installed
            try:
                with installed.supervise_own_processes():
                    with installed.owned_process_scope(Path({str(self.root / 'real-closure.json')!r}), seconds=4, grace=0.1):
                        leader = subprocess.Popen([sys.executable, '-c', {leader!r}], stdout=subprocess.PIPE)
                        child = json.loads(leader.stdout.readline())['child']
                        assert child != leader.pid, 'fixture did not create a distinct descendant'
                        assert leader.wait(timeout=3) == 7, 'leader exit status was stolen'
                        leader.stdout.close()
                        deadline = time.monotonic() + 2
                        adopted = False
                        while time.monotonic() < deadline:
                            tree = installed.tree_snapshot(os.getpid())
                            if (tree['root_observed'] and not tree['truncated']
                                    and any(item['pid'] == child and item['ppid'] == os.getpid()
                                            for item in tree['processes'])):
                                adopted = True
                                break
                            time.sleep(0.02)
                        assert adopted, 'grandchild adoption was not observed'
                    receipt = installed.read_json(Path({str(self.root / 'real-closure.json')!r}))
                    assert receipt['status'] == 'PASS'
                    assert any(item['pid'] == child for item in receipt['reaped']), 'adopted orphan not reaped'
                    assert all(item['pid'] != leader.pid for item in receipt['reaped']), 'already-waited leader reaped twice'
                    assert not Path('/proc/' + str(child)).exists(), 'orphan still exists'
                    print(json.dumps({{'status': 'PASS', 'leader_exit': 7, 'orphan_reaped': True}}), flush=True)
            except BaseException:
                traceback.print_exc()
                sys.stderr.flush()
                # Keep the session leader alive until the bounded outer probe
                # kills its process group, even if cleanup itself was faulty.
                time.sleep(30)
                raise
        """)
        compile(leader, "leader", "exec")
        compile(program, "kernel-probe", "exec")
        process = subprocess.Popen([sys.executable, "-c", program], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=12)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate(timeout=3)
            self.fail("kernel cohort probe timed out: " + stderr[-4000:])
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(json.loads(stdout), {"status": "PASS", "leader_exit": 7, "orphan_reaped": True})


class ExtractionCustodyTests(PrivateScenarioCase):
    """Exercise extraction orchestration with a small fake archive process.

    Candidate hash/version inspection is real. The produced binary is a fixture,
    and smoke inspection plus cohort closure are explicitly mocked. These tests
    provide no AppImage, Desktop, Native Host, or installed acceptance evidence.
    """

    def fixture(self, *, mutation=False, escape=False):
        artifact = self.root / "fixture.AppImage"
        outside = self.root / "outside-run"
        outside.write_text("#!/bin/sh\nexit 0\n")
        outside.chmod(0o700)
        script = textwrap.dedent("""
            import sys
            from pathlib import Path
            assert sys.argv[1:] == ['--appimage-extract']
            root = Path('squashfs-root')
            (root / 'usr' / 'bin').mkdir(parents=True)
            binary = root / 'usr' / 'bin' / 'motionwright'
            binary.write_text('#!/bin/sh\\nexit 0\\n')
            binary.chmod(0o700)
            (root / 'motionwright.desktop').write_text('[Desktop Entry]\\nName=Motionwright\\n')
        """)
        if escape:
            script += f"(root / 'AppRun').symlink_to({str(outside)!r})\n"
        else:
            script += "(root / 'AppRun').symlink_to('usr/bin/motionwright')\n"
        if mutation:
            script += "archive = Path(__file__); archive.write_bytes(archive.read_bytes() + b'# changed\\n')\n"
        artifact.write_text("#!" + sys.executable + "\n" + script)
        artifact.chmod(0o700)
        lock = json.loads((installed.ROOT / "SOURCE_LOCK.json").read_text())
        desktop = json.loads((installed.ROOT / "apps/desktop/src-tauri/tauri.conf.json").read_text())
        sdk = lock["dependencies"]["semwright"]["revision"]
        receipt = self.root / "candidate.json"
        installed.publish(receipt, {
            "schema_version": 1,
            "candidate": {"application": "Motionwright", "platform": "linux",
                          "source_revision": "a" * 40, "semwright_revision": sdk,
                          "version": desktop["version"], "arch": "x86_64"},
            "claims": {"published": False, "human_install_acceptance": "NOT_RUN",
                       "distribution_signature": "NOT_CLAIMED", "notarization": "NOT_CLAIMED"},
            "artifacts": [{"filename": artifact.name, "bundle_kind": "appimage",
                           **installed.source.regular_file(artifact, installed.MAX_INPUT_BYTES)}],
        })
        namespace = self.root / "namespace"
        namespace.mkdir()
        return SimpleNamespace(artifact=artifact, candidate_receipt=receipt, namespace=namespace,
                               source_sha="a" * 40, semwright_sha=sdk)

    @staticmethod
    @contextmanager
    def closed_fixture_scope(receipt):
        try:
            yield
        finally:
            installed.publish(receipt, {"status": "PASS", "evidence": "UNIT_FIXTURE_ONLY"})

    def test_selected_archive_runs_once_and_extracted_inputs_are_derived(self):
        args = self.fixture()
        actual_run = subprocess.run
        smoke = {"automated_inspection": {"binary": {"fixture": True}}}
        with patch.object(installed, "owned_process_scope", self.closed_fixture_scope), patch.object(
            installed.candidate, "build_receipt", return_value=smoke
        ) as inspection, patch.object(installed.subprocess, "run", wraps=actual_run) as run:
            result = installed.extract_installed_bundle(args)
            with self.assertRaises(FileExistsError):
                installed.extract_installed_bundle(args)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], [str(args.artifact), "--appimage-extract"])
        self.assertEqual(run.call_args.kwargs["cwd"], args.namespace / "appimage")
        self.assertEqual(args.app_run, args.namespace / "appimage/squashfs-root/AppRun")
        self.assertEqual(args.installed_binary,
                         args.namespace / "appimage/squashfs-root/usr/bin/motionwright")
        self.assertEqual(inspection.call_args.kwargs["installed_binary"], args.installed_binary)
        self.assertEqual(inspection.call_args.kwargs["method"], "appimage_extract")
        self.assertIs(installed.read_json(args.namespace / "appimage-extraction-intent.json")["automatic_retry"], False)

    def test_archive_changed_during_extraction_is_rejected_before_smoke(self):
        args = self.fixture(mutation=True)
        with patch.object(installed, "owned_process_scope", self.closed_fixture_scope), patch.object(
            installed.candidate, "build_receipt"
        ) as inspection, self.assertRaisesRegex(installed.ScenarioError, "CANDIDATE_CHANGED_DURING_EXTRACTION"):
            installed.extract_installed_bundle(args)
        inspection.assert_not_called()
        terminal = installed.read_json(args.namespace / "appimage-extraction-terminal.json")
        self.assertEqual(terminal["status"], "FAIL")
        self.assertEqual(terminal["exit_code"], 0)
        self.assertIs(terminal["automatic_retry"], False)

    def test_extracted_apprun_cannot_escape_bundle_by_symlink(self):
        args = self.fixture(escape=True)
        with patch.object(installed, "owned_process_scope", self.closed_fixture_scope), patch.object(
            installed.candidate, "build_receipt"
        ) as inspection, self.assertRaisesRegex(installed.ScenarioError, "EXTRACTED_APP_RUN_INVALID"):
            installed.extract_installed_bundle(args)
        inspection.assert_not_called()
        self.assertFalse(hasattr(args, "app_run"))


class ProfileCustodyTests(PrivateScenarioCase):
    def fixture(self):
        data = self.root / "data"
        (data / "cas" / "aa").mkdir(parents=True)
        contents = {"motionwright.sqlite3": b"db bytes", "motionwright.sqlite3-wal": b"wal bytes",
                    "motionwright.sqlite3-shm": b"shm bytes", "cas/aa/blob": b"CAS original"}
        for name, raw in contents.items():
            (data / name).write_bytes(raw)
        return data, contents

    def test_whole_profile_retains_hashes_for_cas_and_all_sqlite_sidecars(self):
        data, contents = self.fixture()
        original_inodes = {name: (data / name).stat().st_ino for name in contents}
        result = installed.closed_profile_identity(data)
        self.assertIs(result["originals_retained_in_place"], True)
        self.assertEqual(result["members"], {
            name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            for name, raw in contents.items()
        })
        self.assertEqual({name: (data / name).stat().st_ino for name in contents}, original_inodes)

    def test_profile_links_cannot_be_hashed_as_independent_originals(self):
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind):
                data = self.root / kind
                data.mkdir()
                original = data / "original"
                original.write_bytes(b"source")
                if kind == "symlink":
                    (data / "alias").symlink_to(original)
                else:
                    os.link(original, data / "alias")
                with self.assertRaises(installed.custody.CustodyError):
                    installed.closed_profile_identity(data)

    def test_late_profile_write_invalidates_previously_hashed_tree(self):
        data, _ = self.fixture()
        original_copy = installed.custody.copy_original
        changed = []

        def mutate_after_read(path, *args, **kwargs):
            result = original_copy(path, *args, **kwargs)
            if not changed:
                path.write_bytes(path.read_bytes() + b"late write")
                changed.append(path)
            return result

        with patch.object(installed.custody, "copy_original", side_effect=mutate_after_read), self.assertRaisesRegex(
            installed.ScenarioError, "CLOSED_PROFILE_CHANGED_DURING_CUSTODY"
        ):
            installed.closed_profile_identity(data)
        self.assertEqual(len(changed), 1)


class SnapshotContractTests(PrivateScenarioCase):
    def seed(self, name, *, title):
        data = self.root / name
        data.mkdir()
        database = data / "motionwright.sqlite3"
        identity, generation = str(uuid.uuid4()), str(uuid.uuid4())
        document = {"id": identity, "generation": generation, "revision": 0, "title": title}
        # sqlite3's connection context manager commits; closing is additionally
        # required to release the actual connection before profile observation.
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.executescript("""
                PRAGMA user_version=3;
                CREATE TABLE projects(id TEXT, generation TEXT, revision INTEGER, title TEXT, document_json TEXT);
                CREATE TABLE events(event_id INTEGER);
                CREATE TABLE production_receipts(receipt_id TEXT);
            """)
            connection.execute("INSERT INTO projects VALUES (?,?,?,?,?)",
                               (identity, generation, 0, title, json.dumps(document)))
        return data, database

    def test_expected_title_is_explicit_and_original_default_stays_strict(self):
        initial, _ = self.seed("initial", title=installed.cold.EXPECTED_INITIAL_TITLE)
        renamed, _ = self.seed("renamed", title=installed.TITLE)
        self.assertEqual(installed.cold.read_snapshot(initial).title, installed.cold.EXPECTED_INITIAL_TITLE)
        self.assertEqual(installed.cold.read_snapshot(renamed, expected_title=installed.TITLE).title,
                         installed.TITLE)
        with self.assertRaisesRegex(installed.cold.ColdStartError, "unexpected default project"):
            installed.cold.read_snapshot(renamed)

    def test_title_override_preserves_document_and_no_receipt_oracles(self):
        for kind in ("document", "receipt"):
            with self.subTest(kind=kind):
                data, database = self.seed(kind, title=installed.TITLE)
                with closing(sqlite3.connect(database)) as connection, connection:
                    if kind == "document":
                        document = json.loads(connection.execute("SELECT document_json FROM projects").fetchone()[0])
                        document["title"] = "different title"
                        connection.execute("UPDATE projects SET document_json=?", (json.dumps(document),))
                    else:
                        connection.execute("INSERT INTO production_receipts VALUES ('unexpected')")
                with self.assertRaises(installed.cold.ColdStartError):
                    installed.cold.read_snapshot(data, expected_title=installed.TITLE)

    def test_snapshot_connection_is_really_closed_and_read_does_not_change_database(self):
        data, database = self.seed("closed", title=installed.TITLE)
        raw_before = database.read_bytes()
        connect, connections = sqlite3.connect, []

        def capture(*args, **kwargs):
            connection = connect(*args, **kwargs)
            connections.append(connection)
            return connection

        with patch.object(installed.cold.sqlite3, "connect", side_effect=capture):
            installed.cold.read_snapshot(data, expected_title=installed.TITLE)
        self.assertEqual(len(connections), 1)
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
            connections[0].execute("SELECT 1")
        self.assertEqual(database.read_bytes(), raw_before)


if __name__ == "__main__":
    unittest.main()
