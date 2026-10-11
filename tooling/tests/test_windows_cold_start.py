"""Source-bound Windows first-launch regression tests, without starting the app.

All fixture bytes are created INSIDE the current Motionwright worktree.
The GitHub-only job supplies the installed executable and actual OS HWND.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "tooling" / "windows_cold_start.py"
spec = importlib.util.spec_from_file_location("windows_cold_start", MODULE)
assert spec and spec.loader
windows = importlib.util.module_from_spec(spec)
spec.loader.exec_module(windows)

TITLE = "Untitled Motion Project"


def project_db(target: Path, *, title: str = TITLE, duplicate: bool = False,
               forged_generation: bool = False) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    ident, generation = str(uuid.uuid4()), str(uuid.uuid4())
    source = {
        "id": ident,
        "generation": str(uuid.uuid4()) if forged_generation else generation,
        "revision": 1,
        "title": title,
    }
    with closing(sqlite3.connect(target)) as db:
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


if __name__ == "__main__":
    unittest.main()
