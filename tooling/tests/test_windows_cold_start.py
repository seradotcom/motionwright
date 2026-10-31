"""Source-bound Windows first-launch regression tests, without starting the app.

All fixture bytes are created INSIDE the current Motionwright worktree.
The GitHub-only job supplies the installed executable and actual OS HWND.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import importlib.util
import itertools
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
sys.path.insert(0, str(ROOT / "tooling/tests"))
from test_storage_schema_contract import add_schema_five_cas, cohort as storage_cohort_fixture
MODULE = ROOT / "tooling" / "windows_cold_start.py"
spec = importlib.util.spec_from_file_location("windows_cold_start", MODULE)
assert spec and spec.loader
windows = importlib.util.module_from_spec(spec)
spec.loader.exec_module(windows)

TITLE = "motionwright ci first use"



def schema_four_project(target):
    source = project_db(target, schema_version=4)
    source["schema_version"] = 2
    with closing(sqlite3.connect(target)) as db, db:
        db.execute("UPDATE projects SET document_json=?", (json.dumps(source),))
        db.execute("""CREATE TABLE receipts(project_id TEXT,request_id TEXT,request_sha256 TEXT,
          result_json TEXT,created_at TEXT,
          apply_binding_version INTEGER NOT NULL DEFAULT 0 CHECK(apply_binding_version IN (0,1)),
          apply_binding_json TEXT)""")
    return source

def schema_five_project(target):
    source = schema_four_project(target)
    with closing(sqlite3.connect(target)) as connection, connection:
        add_schema_five_cas(connection)
    return source

def project_db(target: Path, *, title: str = TITLE, duplicate: bool = False,
               forged_generation: bool = False, schema_version: int = 3) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    ident, generation = str(uuid.uuid4()), str(uuid.uuid4())
    source = {
        "id": ident,
        "generation": str(uuid.uuid4()) if forged_generation else generation,
        "revision": 1,
        "title": title,
    }
    with closing(sqlite3.connect(target)) as db:
        db.execute("PRAGMA user_version=" + str(schema_version))
        db.execute("""
            CREATE TABLE projects (
                id TEXT PRIMARY KEY, generation TEXT, revision INTEGER,
                title TEXT, document_json TEXT
            )
        """)
        db.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            (ident, generation, 1, title, json.dumps(source)),
        )
        if duplicate:
            other = str(uuid.uuid4())
            db.execute(
                "INSERT INTO projects VALUES (?,?,?,?,?)",
                (other, generation, 1, title, json.dumps({
                    "id": other, "generation": generation, "revision": 1,
                    "title": title,
                })),
            )
        db.commit()
    return source


def candidate_fixture(root: Path) -> tuple[Path, Path, str]:
    executable = root / "installed" / "motionwright-desktop.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"MZ" + b"regular-PE-fixture" * 22)
    sha = windows.sha256_file(executable)
    source_sha = "a" * 40
    receipt = {
        "schema_version": 1,
        "bundle_kind": "nsis",
        "candidate": {
            "application": "Motionwright",
            "platform": "windows",
            "source_revision": source_sha,
        },
        "automated_inspection": {
            "method": "nsis_silent_current_user",
            "status": "PASS",
            "binary": {
                "binary_filename": executable.name,
                "binary_bytes": executable.stat().st_size,
                "binary_sha256": sha,
                "binary_format": "PE64_x86_64",
            },
        },
        "claims": {
            "human_install_acceptance": "NOT_RUN",
            "runtime_renderer_acceptance": "SEPARATE_GATE",
            "release_published": False,
        },
    }
    path = root / "installed-smoke.json"
    path.write_text(json.dumps(receipt))
    return executable, path, source_sha


class _BootProcess:
    pid = 4321

    def __init__(self):
        self.returncode = None
        self.kill_calls = 0
        self.cleanup_failure = None

    def poll(self):
        return self.returncode

    def kill(self):
        self.kill_calls += 1
        if self.cleanup_failure is not None:
            raise self.cleanup_failure
        self.returncode = -9

    def wait(self, timeout):
        return self.returncode


@contextmanager
def mocked_boot(*, hwnd=123456, process=None, create_effect=None):
    """Synthetic transport only; SQLite files/readers remain real."""
    process = process or _BootProcess()

    def close(unused_hwnd, child):
        child.returncode = 0

    with patch.object(windows.subprocess, "Popen", return_value=process) as launch, \
            patch.object(windows, "windows_motionwright_window", return_value=hwnd), \
            patch.object(windows, "_create_first_project_in_native_window",
                         side_effect=create_effect) as create, \
            patch.object(windows, "graceful_close", side_effect=close), \
            patch.object(windows.time, "monotonic", side_effect=itertools.count(0, 0.25)), \
            patch.object(windows.time, "sleep"):
        yield process, launch, create


class WindowsCandidateUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_valid_installed_source_receipt_uses_binary_bytes_not_filename_only(self):
        exe, receipt, sha = candidate_fixture(self.root)
        result = windows.check_installed_candidate(receipt, exe, sha)
        self.assertEqual(result["source_sha"], sha)
        self.assertEqual(result["installed_binary_sha256"], windows.sha256_file(exe))
        receipt_path, synthetic_source = receipt, sha
        receipt = json.loads(receipt_path.read_text())
        binary_sha = windows.sha256_file(exe)
        receipt["automated_inspection"].update({
            "status": "HISTORICAL_PREDECESSOR_BINARY_ONLY",
            "scope": "IMMUTABLE_0_1_0_PREDECESSOR",
            "license_notices": {
                "status": "NOT_VERIFIED_HISTORICAL_UNDECLARED",
                "distribution_compliance": "NOT_CLAIMED"}})
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(windows.ColdStartError, "no completed technical smoke"):
            windows.check_installed_candidate(receipt_path, exe, synthetic_source)
        with patch.object(windows, "LEGACY_FIRST_USE_SOURCE", synthetic_source), patch.object(
            windows, "LEGACY_FIRST_USE_BINARY", binary_sha):
            admitted = windows.check_installed_candidate(receipt_path, exe, synthetic_source)
            self.assertEqual(admitted["installed_binary_sha256"], binary_sha)
            for field, wrong in (
                ("scope", "UNREVIEWED"),
                ("status", "PASSING_LEGACY"),
                ("license_notices", {"status": "PASS", "distribution_compliance": "VERIFIED"}),
            ):
                changed = json.loads(json.dumps(receipt))
                changed["automated_inspection"][field] = wrong
                receipt_path.write_text(json.dumps(changed))
                with self.assertRaisesRegex(windows.ColdStartError, "no completed technical smoke"):
                    windows.check_installed_candidate(receipt_path, exe, synthetic_source)
                receipt_path.write_text(json.dumps(receipt))
            exe.write_bytes(exe.read_bytes() + b"one-byte-change")
            with self.assertRaises(windows.ColdStartError):
                windows.check_installed_candidate(receipt_path, exe, synthetic_source)


    def test_tampered_installed_executable_refused(self):
        exe, receipt, sha = candidate_fixture(self.root)
        exe.write_bytes(exe.read_bytes() + b"altered")
        with self.assertRaises(windows.ColdStartError):
            windows.check_installed_candidate(receipt, exe, sha)

    def test_forged_source_revision_and_human_acceptance_refused(self):
        exe, receipt, sha = candidate_fixture(self.root)
        with self.assertRaises(windows.ColdStartError):
            windows.check_installed_candidate(receipt, exe, "b" * 40)
        record = json.loads(receipt.read_text())
        record["claims"]["human_install_acceptance"] = "PASS"
        receipt.write_text(json.dumps(record))
        with self.assertRaisesRegex(windows.ColdStartError, "human approval"):
            windows.check_installed_candidate(receipt, exe, sha)

    def test_first_ui_action_requires_actual_empty_sqlite_and_never_seeds(self):
        database = self.root / "first-use.sqlite3"
        project_db(database)
        self.assertFalse(windows._empty_library_ready(database))
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("PRAGMA user_version=3")
            connection.execute("DELETE FROM projects")
        original = database.read_bytes()
        self.assertTrue(windows._empty_library_ready(database))
        self.assertEqual(database.read_bytes(), original)
        for version in (2, 4, 99):
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("PRAGMA user_version=" + str(version))
            with self.subTest(schema=version):
                self.assertFalse(windows._empty_library_ready(database))

    def test_accessible_native_action_must_be_one_confirmed_invocation(self):
        from unittest.mock import Mock
        if os.name != "nt":
            # A Linux CI job must prove the Windows native UI transport is
            # refused before subprocess. Do not forge os.name or weaken the
            # real disposable-Windows guard to obtain a green test.
            with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), patch.object(
                windows.subprocess, "run",
            ) as call:
                with self.assertRaisesRegex(windows.ColdStartError, "restricted"):
                    windows._create_first_project_in_native_window(12345)
            call.assert_not_called()
            return
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), patch.object(
            windows.subprocess, "run",
            return_value=Mock(returncode=0, stdout="VISIBLE_PROJECT_CREATE_ACTIVATED\n"),
        ) as call:
            windows._create_first_project_in_native_window(12345)
        self.assertEqual(call.call_count, 1)
        args = call.call_args.args[0]
        self.assertEqual(args[-2:], ["-ProjectTitle", windows.EXPECTED_FIRST_UI_TITLE])
        self.assertTrue(args[0].endswith("powershell.exe"))
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), patch.object(
            windows.subprocess, "run",
            return_value=Mock(returncode=1, stdout=""),
        ):
            with self.assertRaisesRegex(windows.ColdStartError, "not confirmed"):
                windows._create_first_project_in_native_window(12345)

    def test_native_accessibility_error_stage_is_closed_and_never_leaks_raw_text(self):
        from unittest.mock import Mock
        if os.name != "nt":
            return
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), patch.object(
            windows.subprocess, "run",
            return_value=Mock(
                returncode=75, stdout="",
                stderr="MOTIONWRIGHT_UI_STAGE:FORM_FIND\nprivate-must-not-leak",
            ),
        ):
            with self.assertRaisesRegex(windows.ColdStartError, "FORM_FIND") as ctx:
                windows._create_first_project_in_native_window(12345)
        self.assertNotIn("private-must-not-leak", str(ctx.exception))

    def test_native_form_selector_requires_edit_type_and_unique_accessible_button(self):
        script = Path(__file__).parents[1] / "windows_first_project_ui.ps1"
        text = script.read_text(encoding="utf-8")
        self.assertIn("ControlType]::Edit", text)
        self.assertIn("ControlType]::Button", text)
        self.assertIn("$edits.Item($index)", text)
        self.assertIn("$buttons.Item($index)", text)
        self.assertIn("$inputMatches -gt 1", text)
        self.assertIn("$buttonMatches -gt 1", text)
        self.assertIn("IsKeyboardFocusable", text)
        self.assertIn("$Phase = 'INVOKE_UNKNOWN'", text)
        self.assertEqual(text.count("$invoke.Invoke()"), 1)
        self.assertEqual(text.count("SendKeys]::SendWait($ProjectTitle)"), 1)
        self.assertIn("AppActivate([int]$window.Current.ProcessId)", text)
        self.assertIn("$inputControl.Current.HasKeyboardFocus", text)
        self.assertIn("$valuePattern.Current.Value -ceq $ProjectTitle", text)
        self.assertIn("$inputMatches -gt 1", text)
        self.assertIn("$buttonMatches -gt 1", text)
        self.assertNotIn("$buttonCondition", text)
        self.assertNotIn("$valuePattern.SetValue($ProjectTitle)", text)
        self.assertNotIn("$window.FindFirst($scope, $inputCondition)", text)

    def test_single_real_sqlite_project_is_read_only_and_stable(self):
        path = self.root / "roaming" / "com.seradotcom.motionwright" / "motionwright.sqlite3"
        source = project_db(path)
        first = windows.read_project_snapshot(path)
        second = windows.read_project_snapshot(path)
        self.assertEqual(first, second)
        self.assertEqual(first["project_id"], source["id"])
        self.assertEqual(first["revision"], 1)
        self.assertEqual(first["title"], TITLE)
        self.assertEqual(len(first["document_sha256"]), 64)

    def test_replacing_generation_inside_document_must_not_pass(self):
        path = self.root / "motionwright.sqlite3"
        project_db(path, forged_generation=True)
        with self.assertRaisesRegex(windows.ColdStartError, "conflicts"):
            windows.read_project_snapshot(path)

    def test_two_projects_fail_fresh_start_instead_of_hiding_one(self):
        path = self.root / "motionwright.sqlite3"
        project_db(path, duplicate=True)
        with self.assertRaisesRegex(windows.ColdStartError, "exactly one"):
            windows.read_project_snapshot(path)

    def test_wrong_initial_project_title_is_not_mistaken_for_fresh_project(self):
        path = self.root / "motionwright.sqlite3"
        project_db(path, title="Previously edited project")
        with self.assertRaisesRegex(windows.ColdStartError, "initialized"):
            windows.read_project_snapshot(path)

    def test_corrupt_sqlite_database_does_not_become_evidence(self):
        path = self.root / "motionwright.sqlite3"
        path.write_bytes(b"not-a-real-sqlite-file")
        with self.assertRaises(windows.ColdStartError):
            windows.read_project_snapshot(path)

    def test_database_discovery_does_not_traverse_unrelated_user_directories(self):
        root = self.root / "roaming"
        project_db(root / "com.seradotcom.motionwright" / "motionwright.sqlite3")
        project_db(root / "other-app" / "motionwright.sqlite3")
        results = windows.known_databases([root])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].parent.name, "com.seradotcom.motionwright")

    def test_relative_evidence_output_is_refused_before_any_runtime_launch(self):
        with self.assertRaisesRegex(windows.ColdStartError, "bounded absolute"):
            windows.validate_evidence_paths(
                self.root / "scratch",
                Path("ci-evidence/windows-nsis-cold-relaunch.json"),
            )
        with self.assertRaisesRegex(windows.ColdStartError, "bounded absolute"):
            windows.validate_evidence_paths(
                Path("relative-scratch"),
                self.root / "proof.json",
            )

    def test_webview_launch_preserves_runner_windows_profile_without_provider_authority(self):
        original = {
            "APPDATA": "C:\\Users\\runneradmin\\AppData\\Roaming",
            "LOCALAPPDATA": "C:\\Users\\runneradmin\\AppData\\Local",
            "USERPROFILE": "C:\\Users\\runneradmin",
            "MOTIONWRIGHT_SEMWRIGHT_CONNECTION": "private-owner-connection.json",
        }
        observed = windows.native_windows_launch_environment(original)
        for name in ("APPDATA", "LOCALAPPDATA", "USERPROFILE"):
            self.assertEqual(observed[name], original[name])
        self.assertNotIn("MOTIONWRIGHT_SEMWRIGHT_CONNECTION", observed)
        self.assertIn("MOTIONWRIGHT_SEMWRIGHT_CONNECTION", original)

    @unittest.skipUnless(os.name == "nt", "Windows-only failure artifact IO")
    def test_failure_artifact_is_sanitized_and_keeps_human_acceptance_not_run(self):
        scratch = self.root / "runner-scratch"
        scratch.mkdir()
        (scratch / "first-launch.log").write_bytes(
            b"WebView2 native startup failed at C:\\Users\\private\\session.json"
        )
        output = self.root / "ci-evidence" / "proof.json"
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            windows.write_sanitized_failure_record(
                output=output, scratch=scratch, expected_sha="a" * 40,
                error=windows.ColdStartError("Installed desktop exited (exit_code=2)"),
            )
        report = output.with_name("windows-nsis-cold-relaunch-diagnostic.json")
        decoded = json.loads(report.read_text())
        self.assertEqual(decoded["result"], "FAIL")
        self.assertEqual(decoded["human_install_acceptance"], "NOT_RUN")
        self.assertTrue(decoded["logs"]["first-launch"]["known_error_signals"]["webview2"])
        self.assertNotIn("C:\\Users\\private", report.read_text())
        self.assertFalse(decoded["raw_application_logs_uploaded"])

    def test_no_local_machine_or_browser_can_start_installed_smoke(self):
        exe, receipt, sha = candidate_fixture(self.root)
        scratch = self.root / "no-runtime-start"
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}):
            with self.assertRaisesRegex(windows.ColdStartError, "GitHub Windows runner"):
                windows.run_installed_relaunch(
                    executable=exe,
                    smoke_receipt=receipt,
                    expected_sha=sha,
                    scratch=scratch,
                    output=self.root / "not-created.json",
                )
        self.assertFalse(scratch.exists())

    def test_forged_nsis_bundle_scope_rejected(self):
        exe, receipt, sha = candidate_fixture(self.root)
        item = json.loads(receipt.read_text())
        item["bundle_kind"] = "appimage"
        receipt.write_text(json.dumps(item))
        with self.assertRaisesRegex(windows.ColdStartError, "bundle"):
            windows.check_installed_candidate(receipt, exe, sha)


    def test_predecessor_contract_requires_the_exact_source_and_installed_binary_pair(self):
        self.assertEqual(windows.select_first_use_contract(
            windows.LEGACY_FIRST_USE_SOURCE, windows.LEGACY_FIRST_USE_BINARY,
        ), windows.LEGACY_FIRST_USE_CONTRACT)
        self.assertEqual(windows.select_first_use_contract("a" * 40, "b" * 64),
                         windows.CURRENT_FIRST_USE_CONTRACT)
        for source, binary in (
            (windows.LEGACY_FIRST_USE_SOURCE, "b" * 64),
            ("a" * 40, windows.LEGACY_FIRST_USE_BINARY),
            (True, windows.LEGACY_FIRST_USE_BINARY),
            (windows.LEGACY_FIRST_USE_SOURCE, "invalid"),
        ):
            with self.subTest(source=source, binary=binary), self.assertRaises(windows.ColdStartError):
                windows.select_first_use_contract(source, binary)

    def test_legacy_real_sqlite_reopen_preserves_identity_revision_schema_and_exact_content(self):
        database = self.root / "legacy.sqlite3"
        document = project_db(database, title=windows.LEGACY_FIRST_USE_TITLE, schema_version=2)
        original = database.read_bytes()
        first = windows.read_project_snapshot(
            database, first_use_contract=windows.LEGACY_FIRST_USE_CONTRACT,
        )
        # Every call opens and closes its own genuine read-only SQLite connection.
        second = windows.read_project_snapshot(
            database, first_use_contract=windows.LEGACY_FIRST_USE_CONTRACT,
        )
        self.assertEqual(first, second)
        self.assertEqual(first["project_id"], document["id"])
        self.assertEqual(first["generation"], document["generation"])
        self.assertEqual(first["revision"], document["revision"])
        self.assertEqual(first["document_sha256"],
                         hashlib.sha256(json.dumps(document).encode()).hexdigest())
        self.assertEqual(database.read_bytes(), original)
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone(), (2,))

    def test_current_and_legacy_contracts_never_accept_each_others_title(self):
        for index, (title, contract, schema) in enumerate((
            (windows.LEGACY_FIRST_USE_TITLE, windows.CURRENT_FIRST_USE_CONTRACT, 3),
            (TITLE, windows.LEGACY_FIRST_USE_CONTRACT, 2),
            ("Previously edited project", windows.LEGACY_FIRST_USE_CONTRACT, 2),
        )):
            database = self.root / (str(index) + ".sqlite3")
            project_db(database, title=title, schema_version=schema)
            with self.subTest(contract=contract, title=title), self.assertRaisesRegex(
                windows.ColdStartError, "initialized",
            ):
                windows.read_project_snapshot(database, first_use_contract=contract)
        with self.assertRaisesRegex(windows.ColdStartError, "unsupported"):
            windows.read_project_snapshot(database, first_use_contract=True)

    def test_bootstrap_contracts_require_their_exact_source_schema(self):
        for index, (title, contract, expected, rejected) in enumerate((
            (TITLE, windows.CURRENT_FIRST_USE_CONTRACT, 3, (0, 1, 2, 4)),
            (windows.LEGACY_FIRST_USE_TITLE, windows.LEGACY_FIRST_USE_CONTRACT, 2, (0, 1, 3, 4)),
        )):
            database = self.root / (str(index) + ".sqlite3")
            project = project_db(database, title=title, schema_version=expected)
            original = database.read_bytes()
            snapshot = windows.read_project_snapshot(database, first_use_contract=contract)
            self.assertEqual(snapshot["project_id"], project["id"])
            self.assertEqual(database.read_bytes(), original)
            for version in rejected:
                with closing(sqlite3.connect(database)) as connection, connection:
                    connection.execute("PRAGMA user_version=" + str(version))
                original = database.read_bytes()
                with self.subTest(contract=contract, schema=version), self.assertRaisesRegex(
                    windows.ColdStartError, "schema",
                ):
                    windows.read_project_snapshot(database, first_use_contract=contract)
                self.assertEqual(database.read_bytes(), original)

    def test_legacy_contract_keeps_row_identity_and_unique_project_requirements(self):
        for index, options in enumerate(({"forged_generation": True}, {"duplicate": True})):
            database = self.root / (str(index) + ".sqlite3")
            project_db(database, title=windows.LEGACY_FIRST_USE_TITLE, schema_version=2, **options)
            with self.assertRaises(windows.ColdStartError):
                windows.read_project_snapshot(
                    database, first_use_contract=windows.LEGACY_FIRST_USE_CONTRACT,
                )

    def test_legacy_boot_and_reopen_never_activate_the_new_library_create_control(self):
        database = self.root / "com.seradotcom.motionwright" / "motionwright.sqlite3"
        project_db(database, title=windows.LEGACY_FIRST_USE_TITLE, schema_version=2)
        original = database.read_bytes()
        with mocked_boot() as (_, launch, create):
            first, actual, _ = windows.boot_once(
                self.root / "fixture.exe", {}, [self.root], self.root / "first.log",
                first_use_contract=windows.LEGACY_FIRST_USE_CONTRACT,
            )
            launch.assert_called_once()
            create.assert_not_called()
        with mocked_boot() as (_, launch, create):
            second, reopened, _ = windows.boot_once(
                self.root / "fixture.exe", {}, [self.root], self.root / "second.log",
                expected_db=actual, first_use_contract=windows.LEGACY_FIRST_USE_CONTRACT,
            )
            launch.assert_called_once()
            create.assert_not_called()
        self.assertEqual(first, second)
        self.assertEqual(actual, reopened)
        self.assertEqual(database.read_bytes(), original)

    def test_empty_legacy_store_never_falls_back_to_current_ui_creation(self):
        database = self.root / "motionwright.sqlite3"
        project_db(database, title=windows.LEGACY_FIRST_USE_TITLE, schema_version=2)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("DELETE FROM projects")
        with mocked_boot() as (_, launch, create), self.assertRaises(windows.ColdStartError) as ctx:
            windows.boot_once(
                self.root / "fixture.exe", {}, [self.root], self.root / "empty.log",
                seconds=3, first_use_contract=windows.LEGACY_FIRST_USE_CONTRACT,
            )
        launch.assert_called_once()
        create.assert_not_called()
        observed = ctx.exception.boot_observations.snapshot()
        self.assertFalse(observed["ui_creation_reserved"])
        self.assertEqual(observed["last_reader_error"]["reason"],
                         "Installed fresh-run project count is not exactly one")

    def test_current_boot_reserves_one_ui_create_and_reads_the_real_committed_fixture(self):
        database = self.root / "motionwright.sqlite3"
        project_db(database)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("DELETE FROM projects")

        def commit_fixture(unused_hwnd):
            ident, generation = str(uuid.uuid4()), str(uuid.uuid4())
            document = {"id": ident, "generation": generation, "revision": 1, "title": TITLE}
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("INSERT INTO projects VALUES (?,?,?,?,?)",
                                   (ident, generation, 1, TITLE, json.dumps(document)))

        with mocked_boot(create_effect=commit_fixture) as (_, launch, create):
            result, actual, _ = windows.boot_once(
                self.root / "fixture.exe", {}, [self.root], self.root / "current.log",
            )
        launch.assert_called_once()
        create.assert_called_once_with(123456)
        self.assertEqual(actual, database)
        self.assertEqual(result, windows.read_project_snapshot(database))

    def test_uncertain_ui_create_keeps_original_error_and_never_replays(self):
        database = self.root / "motionwright.sqlite3"
        project_db(database)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("DELETE FROM projects")
        original = windows.ColdStartError("Native accessible project-creation action was not confirmed")
        with mocked_boot(create_effect=original) as (_, launch, create):
            with self.assertRaises(windows.ColdStartError) as ctx:
                windows.boot_once(
                    self.root / "fixture.exe", {}, [self.root], self.root / "unknown.log",
                )
        self.assertIs(ctx.exception, original)
        launch.assert_called_once()
        create.assert_called_once()
        observed = original.boot_observations.snapshot()
        self.assertTrue(observed["ui_creation_reserved"])
        self.assertFalse(observed["project_snapshot_seen"])

    def test_timeout_distinguishes_visible_hwnd_from_rejected_real_sqlite_snapshot(self):
        database = self.root / "motionwright.sqlite3"
        project_db(database, title=windows.LEGACY_FIRST_USE_TITLE, schema_version=3)
        with mocked_boot() as (_, launch, create), self.assertRaises(windows.ColdStartError) as ctx:
            windows.boot_once(
                self.root / "fixture.exe", {}, [self.root], self.root / "wrong-title.log",
                seconds=3,
            )
        launch.assert_called_once()
        create.assert_not_called()
        observed = ctx.exception.boot_observations.snapshot()
        self.assertTrue(observed["visible_hwnd_seen"])
        self.assertTrue(observed["database_seen"])
        self.assertFalse(observed["project_snapshot_seen"])
        self.assertEqual(observed["last_reader_error"]["reason"],
                         "Installed first-use project was not initialized as expected")
        self.assertTrue(any(row["project_reader"] == "ERROR" for row in observed["samples"]))
        self.assertNotIn("123456", json.dumps(observed))

    def test_timeout_without_hwnd_or_database_does_not_invent_reader_failure(self):
        with mocked_boot(hwnd=None) as (_, launch, create), self.assertRaises(windows.ColdStartError) as ctx:
            windows.boot_once(
                self.root / "fixture.exe", {}, [self.root], self.root / "absent.log",
                seconds=3,
            )
        launch.assert_called_once()
        create.assert_not_called()
        observed = ctx.exception.boot_observations.snapshot()
        self.assertFalse(observed["visible_hwnd_seen"])
        self.assertFalse(observed["database_seen"])
        self.assertIsNone(observed["last_reader_error"])
        self.assertTrue(all(row["project_reader"] == "NOT_ATTEMPTED" for row in observed["samples"]))

    def test_boot_observations_are_bounded_and_redact_unclassified_reader_errors(self):
        observed = windows._BootObservations(windows.CURRENT_FIRST_USE_CONTRACT)
        private = windows.ColdStartError("C:\\private\\project.json must not be public")
        for index in range(100):
            observed.sample(index / 10, index == 0, 1, "ERROR", private)
        snapshot = observed.snapshot()
        self.assertEqual(len(snapshot["samples"]), 16)
        self.assertEqual(snapshot["sample_count"], 100)
        self.assertTrue(snapshot["visible_hwnd_seen"])
        self.assertEqual(snapshot["last_reader_error"]["reason"], "UNCLASSIFIED_PROJECT_READ_ERROR")
        self.assertNotIn("private", json.dumps(snapshot))
        self.assertEqual(snapshot["samples"][0]["elapsed_ms"], 8400)

    def test_original_timeout_survives_process_cleanup_failure(self):
        process = _BootProcess()
        process.cleanup_failure = OSError("private cleanup failure")
        with mocked_boot(hwnd=None, process=process) as (_, launch, create):
            with self.assertRaisesRegex(windows.ColdStartError, "deadline") as ctx:
                windows.boot_once(
                    self.root / "fixture.exe", {}, [self.root], self.root / "cleanup.log",
                    seconds=3,
                )
        launch.assert_called_once()
        create.assert_not_called()
        self.assertEqual(process.kill_calls, 1)
        projected = ctx.exception.boot_observations.snapshot()
        self.assertEqual(projected["cleanup_error"], "PROCESS_CLEANUP_UNCONFIRMED")
        self.assertNotIn("private", json.dumps(projected))

    def test_multiple_database_roots_fail_before_any_create_or_project_read(self):
        project_db(self.root / "roaming" / "motionwright.sqlite3")
        project_db(self.root / "local" / "motionwright.sqlite3")
        with mocked_boot() as (_, launch, create), self.assertRaisesRegex(
            windows.ColdStartError, "multiple",
        ) as ctx:
            windows.boot_once(
                self.root / "fixture.exe", {}, [self.root / "roaming", self.root / "local"],
                self.root / "multiple.log",
            )
        launch.assert_called_once()
        create.assert_not_called()
        sample = ctx.exception.boot_observations.snapshot()["samples"][-1]
        self.assertEqual(sample["database_count_capped_at_two"], 2)
        self.assertIsNone(sample["visible_hwnd"])
        self.assertEqual(sample["project_reader"], "NOT_ATTEMPTED")

    def test_early_exit_remains_distinct_from_window_or_reader_observations(self):
        process = _BootProcess()
        process.returncode = 7
        with mocked_boot(process=process) as (_, launch, create), self.assertRaisesRegex(
            windows.ColdStartError, "exit_code=7",
        ) as ctx:
            windows.boot_once(
                self.root / "fixture.exe", {}, [self.root], self.root / "exited.log",
            )
        launch.assert_called_once()
        create.assert_not_called()
        sample = ctx.exception.boot_observations.snapshot()["samples"][-1]
        self.assertIsNone(sample["visible_hwnd"])
        self.assertIsNone(sample["database_count_capped_at_two"])
        self.assertEqual(process.kill_calls, 0)

    def test_failure_record_keeps_bounded_observations_without_raw_window_or_paths(self):
        observations = windows._BootObservations(windows.LEGACY_FIRST_USE_CONTRACT)
        original = windows.ColdStartError("Installed first-use project was not initialized as expected")
        observations.sample(1.2, True, 1, "ERROR", original)
        observations.attach(original)
        output = self.root / "diagnostic" / "proof.json"
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            windows.write_sanitized_failure_record(
                output=output, scratch=self.root / "no-raw-logs",
                expected_sha=windows.LEGACY_FIRST_USE_SOURCE, error=original,
            )
        diagnostic = output.with_name("windows-nsis-cold-relaunch-diagnostic.json")
        if os.name != "nt":
            self.assertFalse(diagnostic.exists())
            return
        result = json.loads(diagnostic.read_bytes())
        self.assertEqual(result["result"], "FAIL")
        self.assertEqual(result["observations"], observations.snapshot())
        self.assertFalse(result["raw_application_logs_uploaded"])
        self.assertNotIn(str(self.root), diagnostic.read_text())
        self.assertEqual(result["human_install_acceptance"], "NOT_RUN")


    def test_explicit_four_reads_real_sqlite_without_changing_the_document(self):
        database = self.root / "motionwright.sqlite3"
        source = schema_four_project(database)
        original = database.read_bytes()
        observed = windows.read_project_snapshot(database, storage_cohort=storage_cohort_fixture(4))
        self.assertEqual(observed["project_id"], source["id"])
        self.assertEqual(database.read_bytes(), original)
        self.assertEqual(source["schema_version"], 2)

    def test_four_is_not_selected_by_current_library_ux_or_database_version(self):
        database = self.root / "motionwright.sqlite3"
        schema_four_project(database)
        with self.assertRaises(windows.ColdStartError):
            windows.read_project_snapshot(database)
        with self.assertRaises(windows.ColdStartError):
            windows.read_project_snapshot(database, storage_cohort=storage_cohort_fixture(3))
        self.assertEqual(windows.expected_storage_schema(None, windows.CURRENT_FIRST_USE_CONTRACT), 3)

    def test_four_empty_store_requires_matching_columns_before_one_create(self):
        database = self.root / "motionwright.sqlite3"
        schema_four_project(database)
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("DELETE FROM projects")
        selected = storage_cohort_fixture(4)
        self.assertTrue(windows._empty_library_ready(database, storage_cohort=selected))
        self.assertFalse(windows._empty_library_ready(database))
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("ALTER TABLE receipts DROP COLUMN apply_binding_json")
        self.assertFalse(windows._empty_library_ready(database, storage_cohort=selected))

    def test_four_document_or_layout_mismatch_does_not_become_a_snapshot(self):
        database = self.root / "motionwright.sqlite3"
        source = schema_four_project(database)
        source["schema_version"] = 4
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("UPDATE projects SET document_json=?", (json.dumps(source),))
        with self.assertRaises(windows.ColdStartError):
            windows.read_project_snapshot(database, storage_cohort=storage_cohort_fixture(4))

    def test_four_cannot_be_relabelled_as_the_automatic_predecessor(self):
        with self.assertRaises(windows.ColdStartError):
            windows.expected_storage_schema(storage_cohort_fixture(4), windows.LEGACY_FIRST_USE_CONTRACT)
        with self.assertRaises(windows.ColdStartError):
            windows.expected_storage_schema(storage_cohort_fixture(2), windows.CURRENT_FIRST_USE_CONTRACT)
        self.assertEqual(windows.expected_storage_schema(None, windows.LEGACY_FIRST_USE_CONTRACT), 2)

    def test_foreign_four_source_fails_before_process_creation(self):
        executable, receipt, source = candidate_fixture(self.root)
        with patch.object(windows.os, "name", "nt"), patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), \
             patch.object(windows, "validate_evidence_paths"), patch.object(windows.subprocess, "Popen") as launch:
            with self.assertRaises(windows.storage.StorageContractError):
                windows.run_installed_relaunch(
                    executable=executable, smoke_receipt=receipt, expected_sha=source,
                    scratch=self.root / "scratch", output=self.root / "output.json",
                    storage_cohort=storage_cohort_fixture(4, source="b" * 40))
            launch.assert_not_called()

    def test_four_boot_threads_the_cohort_to_the_actual_snapshot_reader(self):
        database = self.root / "motionwright.sqlite3"
        source = schema_four_project(database)
        with mocked_boot() as (process, launch, create), \
             patch.object(windows, "known_databases", return_value=[database]):
            observed, repeated, elapsed = windows.boot_once(
                self.root / "unused.exe", {}, [self.root], self.root / "boot-four.log",
                storage_cohort=storage_cohort_fixture(4))
        self.assertEqual(observed["project_id"], source["id"])
        self.assertEqual(repeated, database)
        create.assert_not_called()
        self.assertEqual(launch.call_count, 1)

    def test_uncertain_create_under_four_preserves_the_original_and_never_replays(self):
        database = self.root / "motionwright.sqlite3"
        schema_four_project(database)
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("DELETE FROM projects")
        error = windows.ColdStartError("synthetic original create UNKNOWN")
        with mocked_boot(create_effect=error) as (process, launch, create), \
             patch.object(windows, "known_databases", return_value=[database]):
            with self.assertRaises(windows.ColdStartError) as caught:
                windows.boot_once(self.root / "unused.exe", {}, [self.root], self.root / "unknown-four.log",
                                  storage_cohort=storage_cohort_fixture(4))
            self.assertIs(caught.exception, error)
            self.assertEqual(create.call_count, 1)
            self.assertEqual(launch.call_count, 1)



    def test_explicit_five_reads_real_sqlite_and_keeps_both_historical_contracts(self):
        database = self.root / "motionwright.sqlite3"
        source = schema_five_project(database)
        original = database.read_bytes()
        selected = storage_cohort_fixture(5)
        observed = windows.read_project_snapshot(database, storage_cohort=selected)
        self.assertEqual(observed["project_id"], source["id"])
        self.assertEqual(source["schema_version"], 2)
        self.assertEqual(windows.expected_storage_schema(selected, windows.CURRENT_FIRST_USE_CONTRACT), 5)
        with self.assertRaises(windows.ColdStartError):
            windows.expected_storage_schema(selected, windows.LEGACY_FIRST_USE_CONTRACT)
        with self.assertRaises(windows.ColdStartError):
            windows.read_project_snapshot(database)
        self.assertEqual(windows.expected_storage_schema(None, windows.CURRENT_FIRST_USE_CONTRACT), 3)
        self.assertEqual(windows.expected_storage_schema(None, windows.LEGACY_FIRST_USE_CONTRACT), 2)
        self.assertEqual(database.read_bytes(), original)

    def test_five_cas_layout_is_required_by_snapshot_and_preaction_readiness(self):
        database = self.root / "motionwright.sqlite3"
        schema_five_project(database)
        selected = storage_cohort_fixture(5)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("DELETE FROM projects")
        self.assertTrue(windows._empty_library_ready(database, storage_cohort=selected))
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("DROP TABLE cas_import_commits")
        original = database.read_bytes()
        self.assertFalse(windows._empty_library_ready(database, storage_cohort=selected))
        with self.assertRaisesRegex(windows.ColdStartError, "storage_cas_objects_differ"):
            windows.read_project_snapshot(database, storage_cohort=selected)
        self.assertEqual(database.read_bytes(), original)

    def test_five_boot_threads_the_cohort_to_the_actual_snapshot_reader(self):
        database = self.root / "motionwright.sqlite3"
        source = schema_five_project(database)
        with mocked_boot() as (process, launch, create), \
             patch.object(windows, "known_databases", return_value=[database]):
            observed, repeated, elapsed = windows.boot_once(
                self.root / "unused.exe", {}, [self.root], self.root / "boot-five.log",
                storage_cohort=storage_cohort_fixture(5))
        self.assertEqual(observed["project_id"], source["id"])
        self.assertEqual(repeated, database)
        create.assert_not_called()
        self.assertEqual(launch.call_count, 1)

    def test_five_missing_cas_prevents_create_during_bounded_boot(self):
        database = self.root / "motionwright.sqlite3"
        schema_five_project(database)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("DELETE FROM projects")
            connection.execute("DROP TABLE cas_import_commits")
        with mocked_boot() as (process, launch, create), \
             patch.object(windows, "known_databases", return_value=[database]):
            with self.assertRaisesRegex(windows.ColdStartError, "deadline"):
                windows.boot_once(
                    self.root / "unused.exe", {}, [self.root], self.root / "missing-five-cas.log",
                    seconds=3, storage_cohort=storage_cohort_fixture(5))
        create.assert_not_called()
        self.assertEqual(launch.call_count, 1)

    def test_uncertain_create_under_five_keeps_the_original_and_never_replays(self):
        database = self.root / "motionwright.sqlite3"
        schema_five_project(database)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("DELETE FROM projects")
        original = windows.ColdStartError("synthetic original create UNKNOWN")
        with mocked_boot(create_effect=original) as (process, launch, create), \
             patch.object(windows, "known_databases", return_value=[database]):
            with self.assertRaises(windows.ColdStartError) as caught:
                windows.boot_once(
                    self.root / "unused.exe", {}, [self.root], self.root / "unknown-five.log",
                    storage_cohort=storage_cohort_fixture(5))
        self.assertIs(caught.exception, original)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(launch.call_count, 1)

    def test_postlaunch_source_change_blocks_positive_publication(self):
        executable, receipt, source = candidate_fixture(self.root)
        selected = storage_cohort_fixture(4, source=source)
        database = self.root / "profile/motionwright.sqlite3"
        output = self.root / "result.json"
        observation = ({"project_id": "synthetic"}, database, 0.25)
        with patch.object(windows.os, "name", "nt"), \
             patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "APPDATA": str(self.root),
                                    "LOCALAPPDATA": str(self.root)}), \
             patch.object(windows, "validate_evidence_paths"), \
             patch.object(windows, "known_databases", return_value=[]), \
             patch.object(windows, "boot_once", return_value=observation) as boot, \
             patch.object(windows.storage, "admit_checkout",
                          side_effect=[selected, storage_cohort_fixture(3, source=source)]):
            with self.assertRaisesRegex(windows.ColdStartError, "Storage source changed"):
                windows.run_installed_relaunch(
                    executable=executable, smoke_receipt=receipt, expected_sha=source,
                    scratch=self.root / "scratch", output=output, storage_cohort=selected,
                    storage_source_repo=self.root)
        self.assertEqual(boot.call_count, 2)
        self.assertFalse(output.exists())



def schema_six_project(target, *, empty=False):
    from test_storage_schema_contract import SCHEMA_SIX_V5_SQL, SCHEMA_SIX_MIGRATION_SQL
    target.parent.mkdir(parents=True, exist_ok=True)
    source = {"id": str(uuid.uuid4()), "generation": str(uuid.uuid4()),
              "revision": 1, "title": TITLE, "schema_version": 2}
    with closing(sqlite3.connect(target)) as db, db:
        db.executescript(SCHEMA_SIX_V5_SQL)
        if not empty:
            db.execute("INSERT INTO projects(id,generation,revision,title,document_json,updated_at) "
                       "VALUES(?,?,?,?,?,?)", (source["id"], source["generation"], source["revision"],
                       source["title"], json.dumps(source), "synthetic"))
        db.execute("INSERT INTO cas_store_identity VALUES(1,?)", (str(uuid.uuid4()),))
        db.executescript(SCHEMA_SIX_MIGRATION_SQL)
        db.execute("PRAGMA user_version=6")
    return source


class StorageSixColdStartControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mw-cold-six-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_explicit_six_snapshot_reads_original_and_preserves_historical_defaults(self):
        path = self.root / "motionwright.sqlite3"
        source = schema_six_project(path)
        original = path.read_bytes()
        selected = storage_cohort_fixture(6)
        self.assertEqual(windows.read_project_snapshot(path, storage_cohort=selected)["project_id"],
                         source["id"])
        self.assertEqual(windows.expected_storage_schema(selected, windows.CURRENT_FIRST_USE_CONTRACT), 6)
        with self.assertRaises(windows.ColdStartError):
            windows.expected_storage_schema(selected, windows.LEGACY_FIRST_USE_CONTRACT)
        with self.assertRaises(windows.ColdStartError):
            windows.read_project_snapshot(path)
        self.assertEqual(windows.expected_storage_schema(None, windows.CURRENT_FIRST_USE_CONTRACT), 3)
        self.assertEqual(windows.expected_storage_schema(None, windows.LEGACY_FIRST_USE_CONTRACT), 2)
        self.assertEqual(path.read_bytes(), original)

    def test_six_empty_library_requires_budget_and_cas_writer_fences_before_create(self):
        path = self.root / "motionwright.sqlite3"
        schema_six_project(path, empty=True)
        selected = storage_cohort_fixture(6)
        self.assertTrue(windows._empty_library_ready(path, storage_cohort=selected))
        self.assertFalse(windows._empty_library_ready(path))
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("DROP TRIGGER mw_writer_v6_cas_store_identity_insert")
        original = path.read_bytes()
        self.assertFalse(windows._empty_library_ready(path, storage_cohort=selected))
        with self.assertRaisesRegex(windows.ColdStartError, "storage_schema6_writer_objects_differ"):
            windows.read_project_snapshot(path, storage_cohort=selected)
        self.assertEqual(path.read_bytes(), original)

    def test_six_boot_passes_selected_cohort_to_actual_sqlite_reader(self):
        path = self.root / "motionwright.sqlite3"
        source = schema_six_project(path)
        with mocked_boot() as (process, launch, create), \
             patch.object(windows, "known_databases", return_value=[path]):
            observed, repeated, _ = windows.boot_once(
                self.root / "unused.exe", {}, [self.root], self.root / "six.log",
                storage_cohort=storage_cohort_fixture(6))
        self.assertEqual(observed["project_id"], source["id"])
        self.assertEqual(repeated, path)
        create.assert_not_called()
        self.assertEqual(launch.call_count, 1)

    def test_six_unknown_create_is_not_replayed(self):
        path = self.root / "motionwright.sqlite3"
        schema_six_project(path, empty=True)
        original = windows.ColdStartError("synthetic original create UNKNOWN")
        with mocked_boot(create_effect=original) as (process, launch, create), \
             patch.object(windows, "known_databases", return_value=[path]):
            with self.assertRaises(windows.ColdStartError) as caught:
                windows.boot_once(self.root / "unused.exe", {}, [self.root], self.root / "unknown-six.log",
                                  storage_cohort=storage_cohort_fixture(6))
        self.assertIs(caught.exception, original)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(launch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
