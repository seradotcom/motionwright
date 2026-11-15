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


class InstalledCiCustodyWiringTests(PrivateScenarioCase):
    """Inert source/custody controls, never installed or Native acceptance."""

    def public_fixture(self):
        namespace = self.root / "scenario"
        extracted = namespace / "appimage/squashfs-root"
        (extracted / "usr/bin").mkdir(parents=True)
        (extracted / "usr/bin/fixture").write_bytes(b"INERT_PUBLIC_PAYLOAD")
        (extracted / "AppRun").symlink_to("usr/bin/fixture")
        artifact = self.root / "inert.AppImage"; artifact.write_bytes(b"INERT_NOT_EXECUTABLE")
        before = installed.installed_bundle_inventory(extracted)
        installed.publish(namespace / "appimage-extraction-terminal.json", {"status": "PASS", "extracted_tree": before})
        installed.publish(namespace / "appimage-tree-after.original.json", before)
        installed.publish(namespace / "appimage-extraction-intent.json", {
            "command": [str(artifact), "--appimage-extract"],
            "artifact": installed.source.regular_file(artifact)})
        return namespace, extracted, artifact

    def test_public_extraction_inventory_retains_all_bytes_identities_modes_and_unfollowed_links(self):
        namespace, extracted, _ = self.public_fixture()
        first = installed.installed_bundle_inventory(extracted)
        rows = {row["path"]: row for row in first["entries"]}
        self.assertEqual(set(rows), {".", "AppRun", "usr", "usr/bin", "usr/bin/fixture"})
        self.assertEqual(rows["AppRun"]["kind"], "symlink")
        self.assertEqual(rows["AppRun"]["link_target"], "usr/bin/fixture")
        self.assertEqual(rows["usr/bin/fixture"]["inode"], (extracted / "usr/bin/fixture").stat().st_ino)
        self.assertEqual(installed.ci_public_extraction_proof(namespace)["extraction"], first)
        (extracted / "usr/bin/fixture").chmod(0o700)
        self.assertNotEqual(first, installed.installed_bundle_inventory(extracted))
        with self.assertRaisesRegex(installed.ScenarioError, "UNARCHIVED_UNIQUE_BYTES"):
            installed.ci_public_extraction_proof(namespace)

    def test_changed_public_duplicate_is_not_omitted_and_unique_bytes_remain_private_originals(self):
        namespace, extracted, _ = self.public_fixture()
        changed = extracted / "usr/bin/fixture"; changed.write_bytes(b"UNIQUE_PRIVATE_FAILURE_BYTES")
        evidence = self.root / "evidence"; evidence.mkdir()
        stage = self.root / "stage"
        manifest = installed.ci_stage_private(evidence, namespace, stage)
        self.assertIsNone(manifest["public_duplicate_extraction"])
        self.assertFalse(manifest["complete"])
        row = next(row for row in manifest["original_entries"] if row["path"] == str(changed))
        self.assertEqual((stage / row["retained_file"]).read_bytes(), changed.read_bytes())
        self.assertEqual(row["inode"], changed.stat().st_ino)

    def test_whole_private_profile_host_hidden_empty_and_actual_short_runtime_are_captured(self):
        evidence = self.root / "evidence"; evidence.mkdir()
        namespace = self.root / "scenario"
        (namespace / "isolated-gui/data/cas").mkdir(parents=True)
        (namespace / "host/.control").mkdir(parents=True)
        (namespace / "host/empty-reservation").mkdir()
        originals = {"isolated-gui/data/motionwright.sqlite3": b"DB", "isolated-gui/data/motionwright.sqlite3-wal": b"WAL",
            "isolated-gui/data/cas/blob": b"CAS", "host/.control/intent": b"INTENT", "host/config.toml": b"CONFIG"}
        for name, raw in originals.items():
            (namespace / name).write_bytes(raw)
        with tempfile.TemporaryDirectory(prefix="mwi-", dir="/tmp") as directory:
            runtime = Path(directory); (runtime / "native.session").write_bytes(b"INERT_TICKET")
            (runtime / "socket-link").symlink_to("not-opened")
            installed.publish(namespace / "host/runtime-custody.json", {"path": str(runtime), "owner_uid": os.getuid(), "retained": True})
            stage = self.root / "stage"; manifest = installed.ci_stage_private(evidence, namespace, stage)
            self.assertTrue(manifest["complete"])
            rows = {row["path"]: row for row in manifest["original_entries"]}
            for name, raw in originals.items():
                row = rows[str(namespace / name)]
                self.assertEqual((stage / row["retained_file"]).read_bytes(), raw)
                self.assertEqual(row["inode"], (namespace / name).stat().st_ino)
            self.assertEqual(rows[str(namespace / "host/empty-reservation")]["kind"], "directory")
            self.assertEqual(rows[str(runtime / "socket-link")]["link_target"], "not-opened")
            self.assertFalse(rows[str(runtime / "socket-link")]["followed_or_opened"])
            self.assertEqual((stage / rows[str(runtime / "native.session")]["retained_file"]).read_bytes(), b"INERT_TICKET")

    def test_private_capacity_exhaustion_is_explicit_and_never_overwrites_or_removes_originals(self):
        evidence = self.root / "evidence"; evidence.mkdir()
        namespace = self.root / "scenario"; namespace.mkdir()
        original = namespace / "original"; original.write_bytes(b"1234")
        identity = original.stat().st_ino; stage = self.root / "stage"
        with patch.object(installed, "CI_PRIVATE_BYTES", 3):
            manifest = installed.ci_stage_private(evidence, namespace, stage)
        self.assertFalse(manifest["complete"])
        self.assertEqual(manifest["captured_bytes"], 0)
        self.assertIn("CAPACITY_EXHAUSTED", manifest["failures"][0]["detail"])
        self.assertEqual(original.read_bytes(), b"1234"); self.assertEqual(original.stat().st_ino, identity)
        with self.assertRaises(FileExistsError): installed.ci_stage_private(evidence, namespace, stage)

    def test_measured_provider_copy_and_private_tail_must_fit_before_any_scenario_effect(self):
        evidence = self.root / "evidence"; evidence.mkdir(); (evidence / "actual-build").write_bytes(b"1234")
        effects = []
        with patch.object(installed, "CI_PRIVATE_BYTES", 16), patch.object(installed, "CI_FUTURE_PRIVATE_BYTES", 8), \
             patch.object(installed, "CI_PRIVATE_FILES", 8), patch.object(installed, "CI_FUTURE_PRIVATE_FILES", 2):
            allocation = installed.ci_capacity_before_scenario(evidence, 4)
            self.assertEqual(allocation["observed_producer_bytes"], 4)
            with self.assertRaisesRegex(installed.ScenarioError, "CAPACITY_BEFORE_EFFECT"):
                installed.ci_capacity_before_scenario(evidence, 5)
                effects.append("must not launch")
        self.assertEqual(effects, [])
        namespace = self.root / "scenario"; namespace.mkdir()
        installed.publish(evidence / "private-capacity-before-scenario.original.json",
                          installed.ci_capacity_before_scenario(evidence, 4))
        with patch.object(installed, "ci_require_time") as clock:
            installed.ci_capacity_before_native(evidence, namespace, 4)
            clock.assert_called_once()
        with (namespace / "oversize-private-fixture").open("wb") as out:
            out.truncate(33 * 1024 * 1024)
        with patch.object(installed, "ci_require_time") as clock:
            with self.assertRaisesRegex(installed.ScenarioError, "CLOSURE_CAPACITY_BEFORE_EFFECT"):
                installed.ci_capacity_before_native(evidence, namespace, 4)
            clock.assert_not_called()

    def test_original_job_deadline_does_not_reset_for_another_phase_or_boot(self):
        workflow = (TOOLING.parent / ".github/workflows/package-candidate.yml").read_text()
        header = workflow.split("\n  linux-first-use-installed:\n", 1)[1].split("\n    steps:", 1)[0]
        budgets = [line for line in header.splitlines() if line.startswith("    timeout-minutes: ")]
        self.assertEqual(len(budgets), 1)
        self.assertEqual(int(budgets[0].partition(":")[2]) * 60, installed.CI_JOB_SECONDS)
        self.assertEqual((installed.CI_JOIN_SECONDS, installed.CI_TAIL_SECONDS), (1800, 360))
        evidence = self.root / "evidence"; evidence.mkdir()
        record = {"source_sha": "a" * 40, "run_id": "123", "run_attempt": "1", "job_seconds": installed.CI_JOB_SECONDS,
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(), "deadline_mono_ns": 1000000000000}
        installed.publish(evidence / "job-deadline.original.json", record)
        with patch.dict(os.environ, GITHUB_SHA="a" * 40, GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="1"), \
             patch.object(installed.time, "monotonic_ns", return_value=900000000000):
            self.assertEqual(installed.ci_require_time(evidence, 100), record)
            with self.assertRaisesRegex(installed.ScenarioError, "DO_NOT_FIT"):
                installed.ci_require_time(evidence, 101)
            with patch.dict(os.environ, GITHUB_RUN_ATTEMPT="2"), self.assertRaisesRegex(installed.ScenarioError, "DEADLINE_BINDING"):
                installed.ci_require_time(evidence, 1)
            for field, value in (("job_seconds", 2700), ("boot_id", "another-boot")):
                old_evidence = self.root / field; old_evidence.mkdir()
                old_record = {**record, field: value}
                installed.publish(old_evidence / "job-deadline.original.json", old_record)
                with self.subTest(field=field), self.assertRaisesRegex(installed.ScenarioError, "DEADLINE_BINDING"):
                    installed.ci_require_time(old_evidence, 1)
                self.assertEqual(installed.read_json(old_evidence / "job-deadline.original.json"), old_record)
        self.assertEqual(installed.read_json(evidence / "job-deadline.original.json"), record)

    def test_source_original_copies_bind_actual_bytes_and_refuse_changed_source(self):
        evidence = self.root / "evidence"; evidence.mkdir()
        source_root = self.root / "source"; source_root.mkdir(); original = source_root / "Cargo.lock"
        original.write_bytes(b"SOURCE_FIXTURE\n")
        observation = {"files": {"Cargo.lock": {"checkout": installed.source.regular_file(original)}}}
        installed.ci_capture_sources(evidence, "fixture", source_root, observation)
        self.assertEqual((evidence / "fixture-source/Cargo.lock").read_bytes(), original.read_bytes())
        original.write_bytes(b"CHANGED_FIXTURE\n")
        with self.assertRaisesRegex(installed.ScenarioError, "SOURCE_COPY_DIFFERS"):
            installed.ci_capture_sources(evidence, "changed", source_root, observation)

    def test_upload_header_reader_rejects_raw_private_bytes_and_structural_cipher_drift(self):
        import base64
        # This is an inert structural fixture, not an encryption/admission claim.
        header = {"schema": "motionwright.ci.private-evidence-seal/1", "algorithm": "RSA-OAEP-SHA256+AES-256-GCM",
            "recipient_spki_sha256": "a" * 64, "context": {"fixture": True}, "nonce": base64.b64encode(b"n" * 12).decode(),
            "wrapped_key": base64.b64encode(b"w" * 384).decode(), "plaintext_sha256": "b" * 64, "plaintext_bytes": 1}
        encoded = (json.dumps(header, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
        raw = b"MWCISEAL1\n" + len(encoded).to_bytes(4, "big") + encoded + b"x" * 17
        self.assertEqual(installed.ci_envelope_header(raw), header)
        for changed in (b"PRIVATE STORE", raw[:-1], raw + b"extra", b"MWCISEAL1\n" + (8193).to_bytes(4, "big") + encoded):
            with self.subTest(changed=changed[:16]), self.assertRaises(installed.ScenarioError):
                installed.ci_envelope_header(changed)


class InstalledCiPriorCapacityTests(PrivateScenarioCase):
    """Inert producer-byte controls; no SDK, GitHub, installed or Native vote."""

    def test_response_transfer_and_retention_cannot_extend_the_original_job_clock(self):
        evidence = self.root / "evidence"; evidence.mkdir()
        now = [1_000_000_000_000]
        calls, anchors = [], []
        class Response:
            status = 200
            # Twenty minutes already elapsed before this original metadata response.
            headers = {"Date": "Tue, 14 Nov 2023 22:33:20 GMT", "Age": "0"}
            def __init__(self, body, delayed=False):
                self.raw, self.delayed = json.dumps(body).encode(), delayed
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, count):
                self_test.assertEqual(count, 262145)
                if self.delayed: now[0] += 12_000_000_000
                return self.raw
        class Opener:
            def open(self, request, timeout):
                self_test.assertEqual(timeout, 12)
                calls.append(request.full_url)
                if len(calls) == 1:
                    return Response({"id": 123, "run_attempt": 1, "head_sha": "a" * 40,
                        "event": "workflow_dispatch", "status": "in_progress",
                        "repository": {"full_name": "seradotcom/motionwright"},
                        "head_repository": {"full_name": "seradotcom/motionwright"},
                        "path": ".github/workflows/package-candidate.yml"})
                anchors.append(now[0])
                return Response({"jobs": [{"name": "linux-first-use-installed", "status": "in_progress",
                    "run_id": 123, "head_sha": "a" * 40, "id": 456,
                    "started_at": "2023-11-14T22:13:20Z"}]}, True)
        self_test = self
        with patch.dict(os.environ, MW_INSTALLED_METADATA_TOKEN="INERT_NOT_A_CREDENTIAL",
                        GITHUB_RUN_ID="123", GITHUB_SHA="a" * 40), \
             patch("urllib.request.build_opener", return_value=Opener()), \
             patch.object(installed.time, "monotonic_ns", side_effect=lambda: now[0]):
            result = installed.ci_capture_job_deadline(evidence)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["job_seconds"], installed.CI_JOB_SECONDS)
        self.assertEqual(result["deadline_mono_ns"], anchors[0] + (installed.CI_JOB_SECONDS - 1200 - 2) * 1_000_000_000)
        self.assertEqual(result["server_anchor_mono_ns"], anchors[0])
        self.assertLess(result["deadline_mono_ns"], now[0] + (installed.CI_JOB_SECONDS - 1200) * 1_000_000_000)

    def test_measured_copies_are_charged_before_open_and_the_allocation_is_never_refunded(self):
        evidence = self.root / "evidence"; evidence.mkdir()
        namespace = self.root / "scenario"
        original = self.root / "binary.fixture"; original.write_bytes(b"x" * (1024 * 1024))
        baseline = installed.CI_CAPACITY_JOURNAL_BYTES + installed.CI_PRODUCER_CLOSE_BYTES + installed.CI_FUTURE_PRIVATE_BYTES
        with patch.object(installed, "CI_PRIVATE_BYTES", baseline + 2 * 1024 * 1024), \
             installed.ci_capacity_scope(evidence, namespace) as capacity:
            installed.ci_copy_original(original, evidence / "first.original", 2 * 1024 * 1024)
            first = capacity.snapshot()
            installed.ci_copy_original(original, evidence / "second.original", 2 * 1024 * 1024)
            second = capacity.snapshot()
            with self.assertRaisesRegex(installed.ScenarioError, "CAPACITY_BEFORE_WRITE"):
                installed.ci_copy_original(original, evidence / "denied.original", 2 * 1024 * 1024)
            self.assertFalse((evidence / "denied.original").exists())
            self.assertEqual(second["reserved_total_bytes"] - first["reserved_total_bytes"], original.stat().st_size)
            self.assertEqual(second, capacity.snapshot())
            with self.assertRaisesRegex(installed.ScenarioError, "RESERVATION"):
                installed.ci_copy_original(original, evidence / "first.original", 2 * 1024 * 1024)
        self.assertEqual(original.read_bytes(), b"x" * (1024 * 1024))
        events = [json.loads(line) for line in (evidence / "private-capacity.original.ndjson").read_text().splitlines()]
        self.assertTrue(all(row.get("no_refund", True) for row in events))
        self.assertEqual(events[0]["refunds"], False)
        # The original source built a 50,116,864-byte Semwright ELF with
        # 21,604,217 bytes of non-runtime symbols. Strip during the real
        # Cargo build rather than losing any byte of the retained executable.
        # Frozen byte, file, scenario and stream caps must never be raised
        # merely to make a source gate green.
        actual = Path(installed.__file__).read_text(encoding="utf8")
        self.assertEqual(actual.count('profile.dev.strip="symbols"'), 1)
        self.assertIn('"--config", "profile.dev.debug=0"', actual)
        self.assertIn('ci_copy_original(binary, copied, 256 * 1024 * 1024, expected=identity)', actual)
        self.assertEqual(installed.CI_PRIVATE_BYTES, 240 * 1024 * 1024)
        self.assertEqual(installed.CI_FUTURE_PRIVATE_BYTES, 64 * 1024 * 1024)
        # Original 43679808311: 14 command closure JSONs at most 1,068 B;
        # every closure retains >100x measured headroom and fails closed.
        self.assertEqual(installed.CI_COMMAND_CLOSURE_BYTES, 128 * 1024)
        self.assertGreater(installed.CI_COMMAND_CLOSURE_BYTES, 100 * 1068)
        self.assertEqual(installed.CI_COMMAND_RECORD_BYTES, 65536)
        self.assertEqual(installed.CI_PRODUCER_CLOSE_BYTES, 1024 * 1024)
        self.assertEqual(
            14 * (1024 * 1024 - installed.CI_COMMAND_CLOSURE_BYTES),
            12 * 1024 * 1024 + 256 * 1024)
        self.assertEqual(installed.CI_COMMAND_STREAM_BYTES["sdk-daemon-cli"], 768 * 1024)
        self.assertEqual(installed.CI_COMMAND_STREAM_BYTES["sdk-sandbox"], 256 * 1024)
        self.assertEqual(installed.CI_COMMAND_STREAM_BYTES["product-provider"], 512 * 1024)
        # Actual original Linux build logs retained in 43679808311, independently
        # authenticated by A1. All six streams have a >2x safety margin.
        originals = {"sdk-daemon-cli": (357392, 21479),
                     "sdk-sandbox": (77947, 3211),
                     "product-provider": (218228, 15444)}
        for name, (stdout, stderr) in originals.items():
            self.assertGreater(installed.CI_COMMAND_STREAM_BYTES[name], 2 * max(stdout, stderr))
        # Two streams per command, reserved BEFORE any child. 48 MiB in the
        # original => 3 MiB now; 13 MiB more than the 92319952f source.
        self.assertEqual(
            sum(2 * installed.CI_COMMAND_STREAM_BYTES[label] for label in originals),
            3 * 1024 * 1024)
        self.assertEqual(
            sum(2 * (8 * 1024 * 1024 - installed.CI_COMMAND_STREAM_BYTES[label])
                for label in originals),
            45 * 1024 * 1024)

    def test_file_slots_are_held_before_write_and_the_same_namespace_cannot_reset(self):
        evidence = self.root / "evidence"; evidence.mkdir()
        namespace = self.root / "scenario"
        baseline = 1 + installed.CI_PRODUCER_CLOSE_FILES + installed.CI_FUTURE_PRIVATE_FILES
        with patch.object(installed, "CI_PRIVATE_FILES", baseline + 1), \
             installed.ci_capacity_scope(evidence, namespace):
            installed.publish(evidence / "one.json", {"fixture": True})
            with self.assertRaisesRegex(installed.ScenarioError, "CAPACITY_BEFORE_WRITE"):
                installed.publish(evidence / "denied.json", {"fixture": True})
        self.assertFalse((evidence / "denied.json").exists())
        original = (evidence / "private-capacity.original.ndjson").read_bytes()
        with self.assertRaises(FileExistsError):
            with installed.ci_capacity_scope(evidence, namespace): pass
        self.assertEqual((evidence / "private-capacity.original.ndjson").read_bytes(), original)
        # Unused producer space cannot be borrowed to exceed the case's own
        # prior remainder, even when the aggregate 240 MiB ceiling would fit.
        selected = self.root / "selected"; selected.mkdir()
        case = self.root / "case"; case.mkdir(); (case / "actual.original").write_bytes(b"12345")
        installed.publish(selected / "private-capacity-before-scenario.original.json", {
            "observed_producer_bytes": 0, "observed_producer_files": 0,
            "reserved_provider_copy_bytes": 0, "reserved_private_bytes": 4, "reserved_private_files": 1,
            "reserved_total_bytes": installed.CI_PRIVATE_BYTES, "reserved_total_files": installed.CI_PRIVATE_FILES})
        manifest = installed.ci_stage_private(selected, case, self.root / "stage")
        self.assertFalse(manifest["complete"])
        self.assertTrue(any(row.get("scope") == "original-scenario-allocation" for row in manifest["failures"]))
        self.assertEqual((case / "actual.original").read_bytes(), b"12345")

    def test_real_inert_stream_overflow_keeps_the_observed_byte_and_never_claims_complete(self):
        @contextmanager
        def inert_scope(receipt, **_):
            # The original real-orphan test remains mandatory in actual CI.
            try: yield
            finally: installed.publish(receipt, {"status": "INERT_SINGLE_CHILD_ONLY", "native": False})
        for descriptor, stream in ((1, "stdout"), (2, "stderr")):
            with self.subTest(stream=stream):
                evidence = self.root / stream; evidence.mkdir()
                namespace = self.root / (stream + "-scenario")
                code = "import os; b=b'x'*65536; [os.write(%d,b) for _ in range(128)]; os.write(%d,b'x')" % (descriptor, descriptor)
                with installed.ci_capacity_scope(evidence, namespace) as capacity, \
                     patch.object(installed, "ci_require_time"), patch.object(installed, "owned_process_scope", inert_scope), \
                     patch.dict(os.environ, GITHUB_SHA="a" * 40, GITHUB_RUN_ID="123"):
                    with self.assertRaisesRegex(installed.ScenarioError, "STREAM_OR_CLOSURE_INCOMPLETE"):
                        installed.ci_command(evidence, "inert-overflow", [sys.executable, "-B", "-c", code],
                                             self.root, dict(os.environ), 10)
                    allocation = capacity.snapshot()
                self.assertEqual((evidence / ("inert-overflow." + stream + ".original")).stat().st_size, 8 * 1024 * 1024)
                self.assertEqual((evidence / ("inert-overflow." + stream + "-overflow.original")).read_bytes(), b"x")
                terminal = installed.read_json(evidence / "inert-overflow.terminal.original.json")
                self.assertEqual(terminal["status"], "UNKNOWN")
                self.assertTrue(terminal["overflow"])
                self.assertFalse(terminal["retention_exhausted"])
                self.assertEqual(terminal["observed_stream_bytes"][stream], 8 * 1024 * 1024 + 1)
                self.assertLessEqual(allocation["reserved_total_bytes"], installed.CI_PRIVATE_BYTES)

    def test_command_denies_before_spawn_and_pipes_do_not_limit_a_child_binary_file(self):
        evidence = self.root / "denied"; evidence.mkdir()
        baseline = installed.CI_CAPACITY_JOURNAL_BYTES + installed.CI_PRODUCER_CLOSE_BYTES + installed.CI_FUTURE_PRIVATE_BYTES
        with patch.object(installed, "CI_PRIVATE_BYTES", baseline + 1), \
             installed.ci_capacity_scope(evidence, self.root / "denied-scenario"), \
             patch.object(installed, "ci_require_time"), patch.object(installed.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(installed.ScenarioError, "CAPACITY_BEFORE_WRITE"):
                installed.ci_command(evidence, "inert-denied", [sys.executable, "-c", "raise AssertionError('must not run')"],
                                     self.root, dict(os.environ), 5)
            spawn.assert_not_called()
        evidence = self.root / "allowed"; evidence.mkdir()
        binary = self.root / "inert-public-build-output"
        @contextmanager
        def inert_scope(receipt, **_):
            try: yield
            finally: installed.publish(receipt, {"status": "INERT_SINGLE_CHILD_ONLY", "native": False})
        code = "from pathlib import Path; Path(%r).write_bytes(b'b'*(4*1024*1024)); print('inert-complete')" % str(binary)
        with installed.ci_capacity_scope(evidence, self.root / "allowed-scenario"), \
             patch.object(installed, "ci_require_time"), patch.object(installed, "owned_process_scope", inert_scope), \
             patch.dict(os.environ, GITHUB_SHA="a" * 40, GITHUB_RUN_ID="123"):
            result = installed.ci_command(evidence, "inert-allowed", [sys.executable, "-B", "-c", code],
                                          self.root, dict(os.environ), 10, stream_bytes=65536)
        self.assertEqual(result, b"inert-complete\n")
        self.assertEqual(binary.stat().st_size, 4 * 1024 * 1024)

    def test_copy_growth_is_denied_before_an_unreserved_chunk_and_prior_bytes_stay(self):
        evidence = self.root / "evidence"; evidence.mkdir()
        original = self.root / "source.fixture"; original.write_bytes(b"x" * 65536)
        reader = installed.source.regular_file
        grew = []
        def observe(path, maximum=installed.MAX_INPUT_BYTES):
            value = reader(path, maximum)
            if Path(path) == original and not grew:
                with original.open("ab") as output: output.write(b"y")
                grew.append(True)
            return value
        target = evidence / "copied.original"
        with installed.ci_capacity_scope(evidence, self.root / "scenario"), \
             patch.object(installed.source, "regular_file", side_effect=observe):
            with self.assertRaisesRegex(installed.ScenarioError, "GREW_BEFORE_COPY_WRITE"):
                installed.ci_copy_original(original, target, 1024 * 1024)
        self.assertEqual(target.read_bytes(), b"x" * 65536)
        self.assertEqual(original.read_bytes(), b"x" * 65536 + b"y")


class InstalledEarlyControlsJoinTests(PrivateScenarioCase):
    """Causal source/capacity controls, never real installed admission."""

    def test_same_original_allocation_resumes_once_without_refund_or_reopening_claimed_files(self):
        evidence = self.root / "evidence"; evidence.mkdir()
        namespace = self.root / "scenario"
        environment = {"GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"}
        with patch.dict(os.environ, environment), installed.ci_capacity_scope(evidence, namespace) as capacity:
            capacity.reserve(evidence / "future.json", 65536)
            installed.publish(evidence / "early.json", {"fixture": "original"})
            held = capacity.snapshot()
        journal = evidence / "private-capacity.original.ndjson"
        prefix = journal.read_bytes(); digest = hashlib.sha256(prefix).hexdigest()
        with patch.dict(os.environ, environment):
            with self.assertRaisesRegex(installed.ScenarioError, "ORIGINAL_CHANGED"):
                with installed.ci_capacity_scope(evidence, namespace, resume_sha256="0" * 64): pass
            self.assertEqual(journal.read_bytes(), prefix)
            with installed.ci_capacity_scope(evidence, namespace, resume_sha256=digest) as capacity:
                self.assertEqual(capacity.snapshot(), held)
                with self.assertRaisesRegex(installed.ScenarioError, "PRIOR_RESERVATION"):
                    installed.publish(evidence / "early.json", {"fixture": "replacement"})
                installed.publish(evidence / "future.json", {"fixture": "later"})
                self.assertEqual(capacity.snapshot(), held)
            self.assertTrue(journal.read_bytes().startswith(prefix))
            resumed = journal.read_bytes()
            with self.assertRaisesRegex(installed.ScenarioError, "FIRST_CLOSED_PHASE"):
                with installed.ci_capacity_scope(evidence, namespace,
                    resume_sha256=hashlib.sha256(resumed).hexdigest()): pass
            self.assertEqual(journal.read_bytes(), resumed)
        self.assertEqual(installed.read_json(evidence / "early.json"), {"fixture": "original"})

    def test_early_control_originals_bind_hashes_identity_source_and_complete_counts(self):
        import ci_test_runner as runner
        repo = self.root / "source"; evidence = self.root / "evidence"
        evidence.mkdir(); (repo / "tooling/tests").mkdir(parents=True); (repo / "apps/studio").mkdir(parents=True)
        for name in ("Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json",
                     "tooling/ci_test_runner.py", "tooling/tests/test_native_host_installed_shared_state.py"):
            (repo / name).write_bytes(b"EXPLICIT INERT SOURCE IDENTITY FIXTURE\n")
        environment = {"GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
                       "GITHUB_WORKFLOW_REF": "inert-fixture@" + "a" * 40, "GITHUB_JOB": "linux-first-use-installed"}
        paths = installed.ci_controls_paths(evidence)
        stderr = f"Ran {installed.CI_EXPECTED_CONTROLS} tests in 0.1s\n\nOK\n".encode()
        for name, path in paths.items():
            if name != "installed-controls.json":
                path.write_bytes(stderr if name == "installed-controls-00-stderr.log" else b"")
        capture = {"status": "COMPLETE", "stream_limit_bytes": 1024 * 1024,
                   "overflow_limit_per_stream_bytes": installed.CI_OVERFLOW_BYTES,
                   "eof": {"stdout": True, "stderr": True}, "overflow": False,
                   "retention_exhausted": False, "deadline_exceeded": False,
                   "child_file_size_limit_changed": False, "overflow_originals": {},
                   "observed_stream_bytes": {}, "retained_stream_bytes": {}}
        step = {"entry_point": "discovery:test_native_host_installed_shared_state.py", "status": "PASS", "exit_code": 0,
                "command": [sys.executable, "-B", "-E", "-s", "-m", "unittest", "discover", "-s", "tooling/tests",
                    "-p", "test_native_host_installed_shared_state.py", "-v"],
                "counts": runner.parse_unittest_summary(stderr.decode()), "bounded_capture": capture}
        for name in ("stdout", "stderr", "stdout-overflow", "stderr-overflow"):
            path = paths["installed-controls-00-" + name + ".log"]
            row = installed.source.regular_file(path)
            descriptor = {"file": path.name, "sha256": row["sha256"]}
            capture["retained_stream_bytes"][name] = row["bytes"]
            if name.endswith("-overflow"): capture["overflow_originals"][name] = descriptor
            else:
                step[name] = descriptor; capture["observed_stream_bytes"][name] = row["bytes"]
        report = {"schema": "motionwright.unittest-execution/1", "status": "PASS", "exit_code": 0,
                  "executed_source_sha": "a" * 40, "tree_sha": "b" * 40, "require_complete": True,
                  "github": environment, "steps": [step],
                  "test_definition_sha256": {"tooling/tests/test_native_host_installed_shared_state.py":
                      installed.source.regular_file(repo / "tooling/tests/test_native_host_installed_shared_state.py")["sha256"]},
                  "runner_sha256": installed.source.regular_file(repo / "tooling/ci_test_runner.py")["sha256"],
                  "python_executable_sha256": installed.source.regular_file(Path(sys.executable).resolve())["sha256"],
                  "source_locks": {name: installed.source.regular_file(repo / name)["sha256"] for name in
                                   ("Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json")}}
        installed.publish(paths["installed-controls.json"], report)
        binding = {"github": environment, "source_sha": "a" * 40, "tree_sha": "b" * 40,
                   "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                   "native_effects": 0, "automatic_retry": False,
                   "originals": {name: {**installed.source.regular_file(path),
                       "identity": list(installed.custody.stamp(path.lstat()))} for name, path in paths.items()}}
        binding_path = evidence / "early-controls-binding.original.json"
        installed.publish(binding_path, binding); digest = installed.source.regular_file(binding_path)["sha256"]
        with patch.dict(os.environ, environment), patch.object(runner, "__file__", str(repo / "tooling/ci_test_runner.py")), \
             patch.object(installed.source, "source_observation", return_value={"tree": "b" * 40}):
            self.assertEqual(installed.ci_verify_early_controls(repo, evidence, digest), binding)
            with self.assertRaisesRegex(installed.ScenarioError, "BINDING_CHANGED"):
                installed.ci_verify_early_controls(repo, evidence, "0" * 64)
            path = paths["installed-controls-00-stderr.log"]
            # Preserve the fixture original and replace only the test path with
            # identical bytes. A hash alone cannot hide its changed identity.
            path.rename(path.with_suffix(".retained-fixture")); path.write_bytes(stderr)
            with self.assertRaisesRegex(installed.ScenarioError, "ORIGINAL_CHANGED"):
                installed.ci_verify_early_controls(repo, evidence, digest)

    def test_sdk_profile_sidecar_has_a_child_only_kernel_bound_before_tee_writes(self):
        import resource
        before = resource.getrlimit(resource.RLIMIT_FSIZE)
        evidence = self.root / "evidence"; evidence.mkdir()
        script = self.root / "ci-driver-bwrap-profile.sh"
        # The REAL pinned SDK script copied /bin/sh (larger than 64 KiB)
        # and received SIGXFSZ before any scenario effect. This inert test
        # copies known 128 KiB bytes under the OS child limit, and then
        # writes ONLY a small evidence sidecar through tee. It has no SDK,
        # sudo, apparmor, Native provider, or product effect.
        original = self.root / "inert-os-driver.original"
        original.write_bytes(b"x" * (128 * 1024))
        copied = self.root / "inert-os-driver.copy"
        script.write_text(
            "set -euo pipefail\n"
            "cp \"$INERT_SOURCE\" \"$INERT_COPIED\"\n"
            "printf 'fixture-profile-ok\\n' | tee \"$1\" >/dev/null\n"
        )
        profile = evidence / "sandbox-profile.original.txt"
        @contextmanager
        def inert_scope(receipt, **_):
            try: yield
            finally: installed.publish(receipt, {"status": "INERT_PROFILE_FIXTURE_ONLY", "native": False})
        with installed.ci_capacity_scope(evidence, self.root / "scenario") as capacity, \
             patch.object(installed, "ci_require_time"), patch.object(installed, "owned_process_scope", inert_scope), \
             patch.dict(os.environ, GITHUB_SHA="a" * 40, GITHUB_RUN_ID="123"):
            capacity.reserve(profile, 65536); capacity.claim(profile, 0)
            installed.ci_command(
                evidence, "sdk-sandbox-profile", ["bash", str(script), str(profile)],
                self.root, dict(os.environ, INERT_SOURCE=str(original),
                                INERT_COPIED=str(copied)), 5, sidecar_file_limit=65536)
        self.assertEqual(copied.read_bytes(), original.read_bytes())
        self.assertEqual(profile.read_text(), "fixture-profile-ok\n")
        self.assertEqual(installed.ci_verify_sdk_profile_sidecar(profile)["bytes"],
                         len("fixture-profile-ok\n"))
        self.assertEqual(resource.getrlimit(resource.RLIMIT_FSIZE), before)
        terminal = installed.read_json(evidence / "sdk-sandbox-profile.terminal.original.json")
        self.assertEqual(terminal["status"], "EXIT_ZERO_AND_COMPLETE_COHORT_CLOSED")
        self.assertEqual(terminal["exit_code"], 0)
        self.assertFalse(terminal["overflow"])
        self.assertFalse(terminal["deadline_exceeded"])
        self.assertEqual(terminal["retained_stream_bytes"]["stdout"], 0)
        intent = installed.read_json(evidence / "sdk-sandbox-profile.intent.original.json")
        self.assertEqual(intent["sdk_profile_child_file_limit_bytes"],
                         installed.CI_SDK_PROFILE_TEMP_FILE_BYTES)
        self.assertEqual(intent["sdk_profile_sidecar_limit_bytes"], 65536)
        self.assertFalse(intent["cargo_file_size_limit_changed"])
        # The separate original evidence cap still denies an oversized
        # profile, even though sandbox setup may copy bounded OS binaries.
        profile.write_bytes(b"x" * 65537)
        with self.assertRaisesRegex(installed.ScenarioError,
                                    "CI_PROFILE_SIDECAR_LIMIT_REACHED"):
            installed.ci_verify_sdk_profile_sidecar(profile)



class EditorReadbackTests(unittest.TestCase):
    """Synthetic protocol controls only; these do not prove an installed GUI."""

    class Node:
        def __init__(self, role, name="", *, text=None, pid=700):
            self.role, self.name, self.pid_value = role, name, pid
            self.text = name if text is None else text
            self.states = {"VISIBLE", "SHOWING", "ENABLED", "SENSITIVE"}
            self.interfaces = {"Text"} if role in ("STATIC", "LABEL", "TEXT") else set()
            self.nodes, self.owner, self.cache_mask = [], self, None

        def get_role(self):
            return self.role

        def get_name(self):
            return self.name

        def get_state_set(self):
            return self

        def contains(self, state):
            return state in self.states

        def get_interfaces(self):
            return self.interfaces

        def get_child_count(self):
            return len(self.nodes)

        def get_child_at_index(self, index):
            return self.nodes[index]

        def get_process_id(self):
            return self.pid_value

        def get_application(self):
            return self.owner

        def set_cache_mask(self, mask):
            self.cache_mask = mask

    def fixture(self):
        desktop, app = self.Node("DESKTOP"), self.Node("APPLICATION")
        desktop.nodes = [app]
        nodes = [self.Node("STATIC", installed.TITLE), self.Node("LABEL", "r0"),
                 self.Node("PUSH_BUTTON", "Open project library"),
                 self.Node("PUSH_BUTTON", "Timeline")]
        app.nodes = nodes
        for node in nodes:
            node.owner = app
        context = SimpleNamespace(actions=[], iterations=0)

        def iterate(blocking):
            self.assertIs(blocking, False)
            context.iterations += 1
            context.actions.pop(0)()

        context.pending = lambda: bool(context.actions)
        context.iteration = iterate
        roles = ("DIALOG", "ENTRY", "TEXT", "PUSH_BUTTON", "STATIC", "LABEL")
        states = ("VISIBLE", "SHOWING", "ENABLED", "SENSITIVE", "EDITABLE", "DEFUNCT")
        api = SimpleNamespace(
            Role=SimpleNamespace(**{k: k for k in roles}),
            StateType=SimpleNamespace(**{k: k for k in states}),
            Cache=SimpleNamespace(ALL=7), get_desktop=lambda _: desktop,
            Text=SimpleNamespace(get_character_count=lambda n: len(n.text),
                                 get_text=lambda n, start, end: n.text[start:end]),
        )
        glib = SimpleNamespace(MainContext=SimpleNamespace(default=lambda: context))
        bridge = installed.EditorReadBridge(api, deadline=installed.time.monotonic() + 10,
            glib=glib, expected_texts=(installed.TITLE, "r0"))
        return bridge, desktop, app, nodes, context

    def test_static_and_label_text_remain_owned_readonly_witnesses(self):
        bridge, desktop, _, nodes, _ = self.fixture()
        witnesses = installed.select_reopened_view(bridge, 700, 0)
        self.assertEqual(set(witnesses), {"title", "revision", "library", "workspace"})
        self.assertEqual(witnesses["title"], {"name": installed.TITLE, "visible_matches": 1})
        self.assertEqual(witnesses["revision"]["name"], "r0")
        self.assertEqual(bridge.view(nodes[0]).interfaces, frozenset({"Text"}))
        self.assertEqual(desktop.cache_mask, 7)

    def test_hidden_defunct_editable_and_entry_nodes_cannot_supply_title(self):
        for condition in ("hidden", "defunct", "editable", "editable-interface", "entry"):
            with self.subTest(condition=condition):
                bridge, _, _, nodes, _ = self.fixture()
                title = nodes[0]
                if condition == "hidden":
                    title.states.remove("SHOWING")
                elif condition == "defunct":
                    title.states.add("DEFUNCT")
                elif condition == "editable":
                    title.states.add("EDITABLE")
                elif condition == "editable-interface":
                    title.interfaces.add("EditableText")
                else:
                    title.role = "ENTRY"
                with self.assertRaisesRegex(installed.accessible.NotReady, "MISSING_REOPENED_TITLE"):
                    installed.select_reopened_view(bridge, 700, 0)

    def test_identity_text_revision_library_and_ambiguity_fail_closed(self):
        cases = ("owner", "text", "revision", "duplicate-title", "library", "duplicate-app")
        for condition in cases:
            with self.subTest(condition=condition):
                bridge, desktop, app, nodes, _ = self.fixture()
                if condition == "owner":
                    nodes[0].owner = self.Node("APPLICATION", pid=701)
                    expected = "UI_WITNESS_OWNER_CHANGED"
                elif condition == "text":
                    nodes[0].text = "different original text"
                    expected = "UI_NAME_AND_TEXT_DISAGREE"
                elif condition == "revision":
                    nodes[1].name = nodes[1].text = "r1"
                    expected = "MISSING_REOPENED_REVISION"
                elif condition == "duplicate-title":
                    for _ in range(8):
                        extra = self.Node("STATIC", installed.TITLE)
                        extra.owner = app
                        app.nodes.append(extra)
                    expected = "REOPENED_TEXT_AMBIGUITY_LIMIT"
                elif condition == "library":
                    dialog = self.Node("DIALOG", "Project library")
                    dialog.owner = app
                    app.nodes.append(dialog)
                    expected = "EDITOR_STILL_IN_LIBRARY"
                else:
                    desktop.nodes.append(self.Node("APPLICATION"))
                    expected = "AMBIGUOUS_APPLICATION"
                with self.assertRaisesRegex((installed.ScenarioError, installed.accessible.ProbeError),
                                            expected):
                    installed.select_reopened_view(bridge, 700, 0)

    def test_unnamed_static_witness_uses_actual_bounded_text(self):
        bridge, _, _, nodes, _ = self.fixture()
        nodes[0].name = ""
        self.assertEqual(installed.select_reopened_view(bridge, 700, 0)["title"]["name"],
                         installed.TITLE)
        nodes[0].text = "some other text"
        with self.assertRaisesRegex(installed.accessible.NotReady, "MISSING_REOPENED_TITLE"):
            installed.select_reopened_view(bridge, 700, 0)
        for unrelated_text in ("x" * 257, installed.TITLE + " extra" * 64):
            nodes[0].text = unrelated_text
            with self.subTest(text_length=len(unrelated_text)), self.assertRaisesRegex(
                    installed.accessible.NotReady, "MISSING_REOPENED_TITLE"):
                installed.select_reopened_view(bridge, 700, 0)
        bridge, _, app, _, _ = self.fixture()
        unrelated = self.Node("STATIC", "", text="x" * 4096)
        unrelated.owner = app
        app.nodes.insert(0, unrelated)
        self.assertEqual(installed.select_reopened_view(bridge, 700, 0)["title"]["name"],
                         installed.TITLE)

    def test_cache_events_are_serviced_with_bounded_iterations_and_original_deadline(self):
        bridge, _, _, nodes, context = self.fixture()
        title = nodes[0]
        title.name = title.text = "pending label"

        def update():
            title.name = title.text = installed.TITLE

        context.actions.append(update)
        with self.assertRaisesRegex(installed.accessible.NotReady, "MISSING_REOPENED_TITLE"):
            installed.select_reopened_view(bridge, 700, 0)
        bridge.drain_events()
        self.assertEqual(installed.select_reopened_view(bridge, 700, 0)["title"]["name"],
                         installed.TITLE)
        context.actions = [lambda: None] * 70
        before = context.iterations
        bridge.drain_events()
        self.assertEqual(context.iterations - before, 64)
        self.assertEqual(len(context.actions), 6)
        before = context.iterations
        bridge.deadline = installed.time.monotonic() - 1
        with self.assertRaisesRegex(installed.accessible.ProbeError, "DEADLINE"):
            bridge.drain_events()
        self.assertEqual(context.iterations, before)

    def test_failure_codes_preserve_only_bounded_known_error_classes(self):
        self.assertEqual(installed.read_ui_failure_code(
            installed.accessible.ProbeError("DEADLINE")), "DEADLINE")
        self.assertEqual(installed.read_ui_failure_code(
            installed.accessible.NotReady("MISSING_REOPENED_TITLE")), "MISSING_REOPENED_TITLE")
        for error in (ValueError("DEADLINE"), installed.ScenarioError("private path / detail"),
                      installed.ScenarioError("x"), installed.ScenarioError("A" * 97)):
            with self.subTest(error_type=type(error).__name__):
                self.assertEqual(installed.read_ui_failure_code(error), "UNCLASSIFIED_READ_FAILURE")


class EditorReadDiagnosticTests(unittest.TestCase):
    """Synthetic reader observations only; no installed UI or Native execution."""

    Node = EditorReadbackTests.Node
    fixture = EditorReadbackTests.fixture
    binding = {"pid": 700, "start_time": "123", "window": "0x42"}

    def missing(self, bridge):
        with self.assertRaisesRegex(installed.accessible.NotReady, "MISSING_REOPENED_TITLE"):
            installed.select_reopened_view(bridge, 700, 0)

    @contextmanager
    def clock(self, bridge, deadline=0.3):
        now = [0.0]
        clock = SimpleNamespace(monotonic=lambda: now[0],
                                sleep=lambda seconds: now.__setitem__(0, now[0] + max(seconds, 0.001)))
        bridge.deadline = deadline
        with patch.object(installed, "time", clock), patch.object(
            installed.accessible, "time", clock
        ), patch.object(installed.accessible, "x11_identity", return_value=self.binding):
            yield now

    def exhausted(self, bridge):
        diagnostics = {}
        with self.clock(bridge), self.assertRaisesRegex(installed.ScenarioError, "READ_UI_DEADLINE"):
            installed.observe_editor_until(bridge, self.binding, 0, installed.TITLE, 0.3,
                                           diagnostics=diagnostics)
        return diagnostics

    def test_all_four_counts_do_not_verify_later_witnesses_before_missing_title(self):
        bridge, _, _, nodes, _ = self.fixture()
        nodes[0].name = nodes[0].text = "private unrelated name"
        with patch.object(nodes[1], "get_application", side_effect=AssertionError("later owner read")):
            self.missing(bridge)
        summary = bridge.read_diagnostics.export()
        self.assertEqual(summary["last"]["candidates"],
                         {"title": 0, "revision": 1, "library": 1, "workspace": 1})
        self.assertNotIn("private unrelated name", json.dumps(summary))
        self.assertEqual(summary["census"]["calls"], 0)

    def test_library_rejection_keeps_priority_and_does_not_spend_the_census(self):
        bridge, _, app, nodes, _ = self.fixture()
        nodes[0].name = nodes[0].text = "not the title"
        dialog = self.Node("DIALOG", "Project library")
        dialog.owner = app
        app.nodes.append(dialog)
        diagnostics = self.exhausted(bridge)
        self.assertEqual(diagnostics["last_condition"], "EDITOR_STILL_IN_LIBRARY")
        summary = diagnostics["read_diagnostics"]
        self.assertEqual(summary["last"]["markers"]["library_dialog"], 1)
        self.assertEqual(summary["last"]["candidates"]["title"], 0)
        self.assertEqual(summary["census"]["status"], "NOT_STARTED")
        self.assertEqual(summary["census"]["calls"], 0)

    def test_real_role_values_states_and_node_counts_never_disclose_unmatched_text(self):
        bridge, _, _, nodes, _ = self.fixture()
        nodes[0].role = 123
        nodes[0].states.remove("SHOWING")
        nodes[0].name = nodes[0].text = "PRIVATE ORIGINAL / not retained"
        self.missing(bridge)
        summary = bridge.read_diagnostics.export()
        self.assertEqual(summary["last"]["nodes"], 5)
        self.assertIn([123, 13, 1], summary["last"]["role_states"])
        self.assertNotIn("PRIVATE ORIGINAL", json.dumps(summary))
        self.assertNotIn(123, bridge.roles)

    def test_first_and_last_summaries_keep_distinct_complete_observations(self):
        bridge, _, app, nodes, _ = self.fixture()
        nodes[0].name = nodes[0].text = "pending"
        self.missing(bridge)
        original = bridge.read_diagnostics.export()["first"]
        extra = self.Node("PUSH_BUTTON", "Timeline")
        extra.owner = app
        app.nodes.append(extra)
        self.missing(bridge)
        summary = bridge.read_diagnostics.export()
        self.assertEqual(summary["first"], original)
        self.assertEqual(summary["first"]["candidates"]["workspace"], 1)
        self.assertEqual(summary["last"]["candidates"]["workspace"], 2)
        self.assertEqual(summary["last"]["ordinal"], 2)
        self.assertEqual(summary["last"]["nodes"], 6)

    def test_incomplete_traversal_preserves_the_previous_valid_summaries(self):
        bridge, _, app, nodes, _ = self.fixture()
        nodes[0].role = "UNACCEPTED_ROLE"
        scans = [0]
        original_child = app.get_child_at_index

        def child_count():
            scans[0] += 1
            return len(app.nodes)

        app.get_child_count = child_count
        app.get_child_at_index = lambda index: original_child(index) if scans[0] == 1 else None
        diagnostics = self.exhausted(bridge)
        summary = diagnostics["read_diagnostics"]
        self.assertEqual(diagnostics["last_condition"], "ACCESSIBLE_TREE_INCOMPLETE")
        self.assertEqual(summary["first"], summary["last"])
        self.assertEqual(summary["first"]["ordinal"], 1)
        self.assertGreater(summary["partial"]["ordinal"], 1)
        self.assertTrue(summary["incomplete"])

    def test_pass_makes_identical_api_reads_and_never_starts_diagnostic_census(self):
        traces = []
        for enabled in (False, True):
            bridge, _, _, _, _ = self.fixture()
            trace = []
            call = bridge.call

            def observed(function, *arguments):
                trace.append(function.__name__)
                return call(function, *arguments)

            with patch.object(bridge, "call", side_effect=observed):
                if enabled:
                    witnesses = installed.select_reopened_view(bridge, 700, 0)
                else:
                    with patch.object(installed, "editor_diagnostic"):
                        witnesses = installed.select_reopened_view(bridge, 700, 0)
            self.assertEqual(set(witnesses), {"title", "revision", "library", "workspace"})
            traces.append(trace)
        self.assertEqual(traces[0], traces[1])
        diagnostics = {}
        with self.clock(bridge), patch.object(bridge.read_diagnostics, "census_once") as census:
            installed.observe_editor_until(bridge, self.binding, 0, installed.TITLE, 0.3,
                                           diagnostics=diagnostics)
        census.assert_not_called()
        self.assertEqual(diagnostics["read_diagnostics"]["census"]["calls"], 0)

    def test_owner_and_x11_failures_keep_their_codes_and_do_not_run_a_census(self):
        for cause in ("owner", "x11"):
            with self.subTest(cause=cause):
                bridge, _, _, nodes, _ = self.fixture()
                if cause == "owner":
                    nodes[0].owner = self.Node("APPLICATION", pid=701)
                diagnostics = {}
                expected = "UI_WITNESS_OWNER_CHANGED" if cause == "owner" else "UI_PROCESS_CHANGED"
                with self.clock(bridge), patch.object(bridge.read_diagnostics, "census_once") as census:
                    with patch.object(installed.accessible, "x11_identity",
                                      return_value=self.binding if cause == "owner" else
                                      dict(self.binding, start_time="changed")):
                        with self.assertRaisesRegex(installed.ScenarioError, expected):
                            installed.observe_editor_until(bridge, self.binding, 0, installed.TITLE, 0.3,
                                                           diagnostics=diagnostics)
                census.assert_not_called()
                self.assertEqual(diagnostics["read_diagnostics"]["census"]["calls"], 0)

    def test_census_records_exact_excluded_role_matches_without_making_them_witnesses(self):
        bridge, _, _, nodes, _ = self.fixture()
        nodes[0].role = "UNACCEPTED_ROLE"
        diagnostics = self.exhausted(bridge)
        self.assertEqual(diagnostics["last_condition"], "MISSING_REOPENED_TITLE")
        summary = diagnostics["read_diagnostics"]
        self.assertEqual(summary["last"]["candidates"]["title"], 0)
        matches = summary["census"]["matches"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["role"], "UNACCEPTED_ROLE")
        self.assertEqual(matches[0]["name_matches"], ["title"])
        self.assertEqual(matches[0]["text_matches"], ["title"])
        self.assertIs(matches[0]["owner_matches"], True)
        self.assertLessEqual(summary["census"]["calls"], 16)
        self.assertNotIn("UNACCEPTED_ROLE", bridge.roles)
        with patch.object(bridge, "deadline", None):
            self.missing(bridge)

    def test_census_budget_is_sixteen_total_calls_including_failed_attempts_and_owner_reads(self):
        bridge, _, app, nodes, _ = self.fixture()
        discarded = [self.Node("UNACCEPTED_ROLE", "private unrelated name") for _ in range(25)]
        for node in discarded:
            node.owner = app
        app.nodes = discarded + nodes[1:]
        diagnostics = self.exhausted(bridge)
        self.assertGreater(diagnostics["read_attempts"], 1)
        census = diagnostics["read_diagnostics"]["census"]
        self.assertEqual(census["calls"], 16)
        self.assertEqual(census["status"], "INCOMPLETE_CALL_LIMIT")
        self.assertEqual(census["matches"], [])
        self.assertNotIn("private unrelated name", json.dumps(diagnostics))
        with self.clock(bridge):
            bridge.read_diagnostics.census_once(bridge, self.binding, 0.3, "MISSING_REOPENED_TITLE")
        self.assertEqual(bridge.read_diagnostics.census["calls"], 16)

    def test_expired_census_deadline_sends_no_additional_read_and_cannot_restart(self):
        bridge, _, app, nodes, _ = self.fixture()
        nodes[0].role = "UNACCEPTED_ROLE"
        self.missing(bridge)
        with self.clock(bridge) as now, patch.object(app, "get_name") as name:
            now[0] = 0.3
            bridge.read_diagnostics.census_once(bridge, self.binding, 0.3, "MISSING_REOPENED_TITLE")
            now[0] = 0
            bridge.read_diagnostics.census_once(bridge, self.binding, 0.3, "MISSING_REOPENED_TITLE")
        name.assert_not_called()
        self.assertEqual(bridge.read_diagnostics.census["calls"], 0)
        self.assertEqual(bridge.read_diagnostics.census["status"], "INCOMPLETE_DEADLINE")

    def test_census_read_error_keeps_original_notready_and_does_not_leak_exception_text(self):
        bridge, _, app, nodes, _ = self.fixture()
        nodes[0].role = "UNACCEPTED_ROLE"
        with patch.object(app, "get_name", side_effect=ValueError("PRIVATE EXCEPTION / input")):
            diagnostics = self.exhausted(bridge)
        self.assertEqual(diagnostics["last_condition"], "MISSING_REOPENED_TITLE")
        summary = diagnostics["read_diagnostics"]
        self.assertEqual(summary["census"]["status"], "INCOMPLETE_READ_ERROR")
        self.assertEqual(summary["census"]["calls"], 1)
        self.assertIsNotNone(summary["first"])
        self.assertIsNotNone(summary["last"])
        self.assertNotIn("PRIVATE EXCEPTION", json.dumps(summary))

    def test_event_drain_records_the_original_iteration_cap_and_incomplete_deadline(self):
        bridge, _, _, _, context = self.fixture()
        context.actions = [lambda: None] * 70
        bridge.drain_events()
        summary = bridge.read_diagnostics.export()
        self.assertEqual(summary["last_event_drain"],
                         {"iterations": 64, "complete": True, "iteration_limit_reached": True})
        self.assertEqual(len(context.actions), 6)
        before = context.iterations
        bridge.deadline = installed.time.monotonic() - 1
        with self.assertRaisesRegex(installed.accessible.ProbeError, "DEADLINE"):
            bridge.drain_events()
        self.assertEqual(context.iterations, before)
        self.assertEqual(bridge.read_diagnostics.export()["last_event_drain"],
                         {"iterations": 0, "complete": False, "iteration_limit_reached": False})

    def test_role_histogram_and_serialized_summary_are_bounded_and_keep_last_valid_data(self):
        bridge, _, app, nodes, _ = self.fixture()
        nodes[0].name = nodes[0].text = "pending"
        for index in range(64):
            extra = self.Node(1000 + index, "PRIVATE CONTENT")
            extra.owner = app
            app.nodes.append(extra)
        self.missing(bridge)
        summary = bridge.read_diagnostics.export()
        self.assertEqual(len(summary["last"]["role_states"]), 16)
        self.assertGreater(summary["last"]["role_state_overflow"], 0)
        raw = json.dumps(summary, sort_keys=True, separators=(",", ":")).encode()
        self.assertLessEqual(len(raw), 8 * 1024)
        self.assertNotIn(b"PRIVATE CONTENT", raw)
        bridge.read_diagnostics.last = {"invalid_large_summary": "PRIVATE" * 2000}
        retained = bridge.read_diagnostics.export()
        self.assertEqual(retained["first"], summary["first"])
        self.assertEqual(retained["last"], summary["last"])
        self.assertTrue(retained["incomplete"])
        self.assertEqual(retained["diagnostic_error"], "SUMMARY_UNAVAILABLE")
        self.assertLessEqual(len(json.dumps(retained).encode()), 8 * 1024)
        self.assertLess(len(json.dumps({"read_diagnostics": retained, "status": "FAIL"}).encode()), 16 * 1024)

    def test_diagnostic_faults_cannot_replace_the_rejection_or_erase_a_valid_summary(self):
        for operation in ("completed", "export"):
            with self.subTest(operation=operation):
                bridge, _, _, nodes, _ = self.fixture()
                nodes[0].name = nodes[0].text = "pending"
                self.missing(bridge)
                original = bridge.read_diagnostics.export()
                with patch.object(bridge.read_diagnostics, operation,
                                  side_effect=RuntimeError("PRIVATE DIAGNOSTIC FAILURE")):
                    diagnostics = self.exhausted(bridge)
                self.assertEqual(diagnostics["last_condition"], "MISSING_REOPENED_TITLE")
                retained = diagnostics["read_diagnostics"]
                self.assertEqual(retained["first"], original["first"])
                self.assertIsNotNone(retained["last"])
                self.assertTrue(retained["incomplete"])
                self.assertNotIn("PRIVATE DIAGNOSTIC FAILURE", json.dumps(retained))


if __name__ == "__main__":
    unittest.main()
