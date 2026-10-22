"""Real temporary Git checkout admission and legacy receipt projection tests."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import source_checkout_observation as observer
import storage_schema_contract as storage
import windows_installer_lifecycle as lifecycle


class SourceCheckoutObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mw-source-observer-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "repo"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Motionwright source fixture")
        self.git("config", "user.email", "source-fixture@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "commit.gpgsign", "false")
        self.path = self.root / storage.SOURCE_PATH
        self.path.parent.mkdir(parents=True)
        self.raw = b"pub const STORAGE_SCHEMA_VERSION: u32 = 4;\n"
        self.path.write_bytes(self.raw)
        (self.root / "other.txt").write_bytes(b"unchanged source\n")
        self.git("add", "--", storage.SOURCE_PATH, "other.txt")
        self.git("commit", "-qm", "Create bounded source fixture")
        self.head = self.git("rev-parse", "HEAD").decode().strip()
        self.tree = self.git("rev-parse", "HEAD^{tree}").decode().strip()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=30).stdout

    def observe(self):
        return observer.source_observation(self.root, self.head, (storage.SOURCE_PATH,))

    def test_exact_git_bytes_have_independently_observed_source_and_checkout(self):
        result = self.observe()
        self.assertEqual((result["sha"], result["tree"]), (self.head, self.tree))
        item = result["files"][storage.SOURCE_PATH]
        expected = {"bytes": len(self.raw), "sha256": hashlib.sha256(self.raw).hexdigest()}
        self.assertEqual(item["checkout"], expected)
        self.assertEqual((item["git_bytes"], item["git_sha256"]),
                         (expected["bytes"], expected["sha256"]))
        self.assertEqual(item["checkout_encoding"], "EXACT_GIT_BYTES")

    def test_exact_crlf_conversion_retains_distinct_physical_and_git_identities(self):
        self.git("config", "core.autocrlf", "true")
        checkout = self.raw.replace(b"\n", b"\r\n")
        self.path.write_bytes(checkout)
        self.git("add", "--renormalize", "--", storage.SOURCE_PATH)
        self.assertEqual(self.git("diff", "--cached", "--name-only"), b"")
        self.assertEqual(self.git("status", "--porcelain=v1", "--untracked-files=no"), b"")
        item = self.observe()["files"][storage.SOURCE_PATH]
        self.assertEqual(item["checkout_encoding"], "EXACT_LF_TO_CRLF")
        self.assertEqual(item["git_sha256"], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(item["checkout"]["sha256"], hashlib.sha256(checkout).hexdigest())
        self.assertNotEqual(item["git_sha256"], item["checkout"]["sha256"])

    def test_crlf_checkout_from_global_configuration_survives_git_isolation(self):
        self.git("config", "--unset", "core.autocrlf")
        global_config = Path(self.temp.name) / "checkout.gitconfig"
        global_config.write_text("[core]\n    autocrlf = true\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(global_config)}):
            self.path.unlink()
            self.git("checkout", "HEAD", "--", storage.SOURCE_PATH)
            self.assertEqual(self.path.read_bytes(), self.raw.replace(b"\n", b"\r\n"))
            self.assertEqual(self.git("status", "--porcelain=v1", "--untracked-files=no"), b"")
            cohort = storage.admit_checkout(self.root, self.head)
        self.assertEqual(cohort["storage_schema"], 4)
        self.assertEqual(cohort["source_input"]["checkout_encoding"], "EXACT_LF_TO_CRLF")
        self.assertNotEqual(cohort["source_input"]["git"], cohort["source_input"]["checkout"])

    def test_explicit_binary_attributes_still_reject_line_ending_changes(self):
        (self.root / ".gitattributes").write_text(storage.SOURCE_PATH + " -text\n",
                                                encoding="utf-8")
        self.git("add", "--", ".gitattributes")
        self.git("commit", "-qm", "Preserve exact fixture source bytes")
        head = self.git("rev-parse", "HEAD").decode().strip()
        self.path.write_bytes(self.raw.replace(b"\n", b"\r\n"))
        with self.assertRaisesRegex(observer.ContractError, "tracked_source_dirty"):
            observer.source_observation(self.root, head, (storage.SOURCE_PATH,))

    def test_assume_unchanged_cannot_hide_non_git_source_bytes(self):
        self.git("update-index", "--assume-unchanged", "--", storage.SOURCE_PATH)
        self.path.write_bytes(self.raw.replace(b"4;", b"3;"))
        self.assertEqual(self.git("status", "--porcelain=v1", "--untracked-files=no"), b"")
        with self.assertRaisesRegex(observer.ContractError, "checkout_bytes_not_git"):
            self.observe()

    def test_dirty_tracked_source_outside_requested_path_still_blocks_admission(self):
        (self.root / "other.txt").write_bytes(b"changed\n")
        with self.assertRaisesRegex(observer.ContractError, "tracked_source_dirty"):
            self.observe()

    def test_wrong_commit_is_rejected_before_reading_a_source_file(self):
        with mock.patch.object(observer, "regular_file") as read:
            with self.assertRaisesRegex(observer.ContractError, "source_head_mismatch"):
                observer.source_observation(self.root, "a" * 40, (storage.SOURCE_PATH,))
            read.assert_not_called()

    def test_replace_ref_cannot_admit_schema4_under_original_schema3_commit(self):
        self.path.write_bytes(self.raw.replace(b"4;", b"3;"))
        self.git("add", "--", storage.SOURCE_PATH)
        self.git("commit", "-qm", "Record original schema3 source")
        original = self.git("rev-parse", "HEAD").decode().strip()
        self.path.write_bytes(self.raw)
        self.git("add", "--", storage.SOURCE_PATH)
        self.git("commit", "-qm", "Record replacement schema4 source")
        replacement = self.git("rev-parse", "HEAD").decode().strip()
        self.git("update-ref", "HEAD", original)
        self.git("replace", original, replacement)
        self.assertEqual(self.git("rev-parse", "HEAD").decode().strip(), original)
        self.assertEqual(self.git("show", original + ":" + storage.SOURCE_PATH), self.raw)
        self.assertEqual(self.git("status", "--porcelain=v1", "--untracked-files=no"), b"")
        with self.assertRaises(observer.ContractError):
            storage.admit_checkout(self.root, original)

    def test_inherited_git_repository_and_object_overrides_cannot_rebind_source(self):
        redirected = Path(self.temp.name) / "redirected"
        self.git("clone", "-q", str(self.root), str(redirected))
        def redirected_git(*args):
            return subprocess.run(["git", "-C", str(redirected), *args], check=True,
                capture_output=True, timeout=30).stdout
        redirected_git("config", "user.name", "Redirected source fixture")
        redirected_git("config", "user.email", "redirected-fixture@example.invalid")
        redirected_git("config", "commit.gpgsign", "false")
        (redirected / "other.txt").write_bytes(b"different repository source\n")
        redirected_git("add", "--", "other.txt")
        redirected_git("commit", "-qm", "Create distinct redirected source")
        redirected_head = redirected_git("rev-parse", "HEAD").decode().strip()
        self.assertNotEqual(redirected_head, self.head)
        overrides = {"GIT_DIR": str(redirected / ".git"),
            "GIT_WORK_TREE": str(redirected),
            "GIT_INDEX_FILE": str(redirected / ".git" / "index"),
            "GIT_OBJECT_DIRECTORY": str(redirected / ".git" / "objects")}
        with mock.patch.dict(os.environ, overrides):
            with self.assertRaisesRegex(observer.ContractError, "source_head_mismatch"):
                observer.source_observation(self.root, redirected_head, (storage.SOURCE_PATH,))
            self.assertEqual(self.observe()["sha"], self.head)

    def test_inherited_git_config_cannot_change_checkout_semantics(self):
        foreign_worktree = Path(self.temp.name) / "foreign-worktree"
        foreign_worktree.mkdir()
        foreign_path = foreign_worktree / storage.SOURCE_PATH
        foreign_path.parent.mkdir(parents=True)
        foreign_path.write_bytes(self.raw.replace(b"4;", b"3;"))
        (foreign_worktree / "other.txt").write_bytes(b"unchanged source\n")
        global_config = Path(self.temp.name) / "injected.gitconfig"
        global_config.write_text("[core]\n    worktree = " + str(foreign_worktree) + "\n",
                                 encoding="utf-8")
        variants = ({"GIT_CONFIG_GLOBAL": str(global_config)},
                    {"GIT_CONFIG_SYSTEM": str(global_config), "GIT_CONFIG_NOSYSTEM": "0"},
                    {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.worktree",
                     "GIT_CONFIG_VALUE_0": str(foreign_worktree)})
        for overrides in variants:
            with self.subTest(overrides=tuple(overrides)), mock.patch.dict(os.environ, overrides):
                self.assertEqual(self.observe()["sha"], self.head)

    def test_requested_root_must_be_the_git_worktree_root(self):
        nested = self.root / "nested"
        nested.mkdir()
        path = nested / storage.SOURCE_PATH
        path.parent.mkdir(parents=True)
        path.write_bytes(self.raw)
        self.git("add", "--", "nested")
        self.git("commit", "-qm", "Record nested source fixture")
        head = self.git("rev-parse", "HEAD").decode().strip()
        with self.assertRaisesRegex(observer.ContractError, "source_root_mismatch"):
            observer.source_observation(nested, head, (storage.SOURCE_PATH,))

    def test_untracked_requested_source_is_not_admitted(self):
        (self.root / "untracked.rs").write_bytes(self.raw)
        with self.assertRaises(subprocess.CalledProcessError):
            observer.source_observation(self.root, self.head, ("untracked.rs",))

    def test_symlink_or_reparse_metadata_is_rejected_before_payload_read(self):
        for metadata in (SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0),
                         SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)):
            with self.subTest(metadata=metadata), mock.patch.object(Path, "lstat", return_value=metadata):
                with self.assertRaisesRegex(observer.ContractError, "link_or_reparse"):
                    observer.regular_file(self.path)

    def test_changed_second_read_does_not_reuse_the_initial_file_digest(self):
        original_read = Path.read_bytes
        def changed(path):
            return self.raw + b"changed" if path == self.path else original_read(path)
        with mock.patch.object(Path, "read_bytes", changed):
            with self.assertRaisesRegex(observer.ContractError, "checkout_changed_during_source_read"):
                self.observe()

    def test_regular_file_rejects_truncated_and_growing_reads(self):
        import io
        for body, reason in ((b"", "file_truncated"), (self.raw + b"x", "file_grew")):
            with self.subTest(reason=reason), mock.patch.object(Path, "open", return_value=io.BytesIO(body)):
                with self.assertRaisesRegex(observer.ContractError, reason):
                    observer.regular_file(self.path)

    def test_real_source_admission_requires_no_installer_or_producer_module(self):
        script = '''import importlib, importlib.abc, json, sys
from pathlib import Path
class NoInstallerImport(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.rsplit(".", 1)[-1] in ("windows_nsis_interruption", "windows_installer_producer"):
            raise AssertionError("installer import attempted")
sys.meta_path.insert(0, NoInstallerImport())
sys.path[:0] = [sys.argv[1], str(Path(sys.argv[1]) / "tooling")]
module = importlib.import_module(sys.argv[2])
print(json.dumps(module.admit_checkout(Path(sys.argv[3]), sys.argv[4])))
'''
        source_root = Path(__file__).resolve().parents[2]
        for name in ("storage_schema_contract", "tooling.storage_schema_contract"):
            with self.subTest(entrypoint=name):
                completed = subprocess.run([sys.executable, "-B", "-I", "-c", script,
                    str(source_root), name, str(self.root), self.head],
                    capture_output=True, check=True, timeout=30)
                result = json.loads(completed.stdout)
                self.assertEqual(result["storage_schema"], 4)
                self.assertEqual(result["document_schema_after_save"], 2)
                self.assertEqual((result["source_sha"], result["source_tree"]), (self.head, self.tree))
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_lifecycle_projection_calls_the_shared_observer_once_and_preserves_shape(self):
        observed = self.observe()
        with mock.patch.object(lifecycle, "_checkout") as admitted, \
                mock.patch.object(observer, "source_observation", return_value=observed) as read:
            result = lifecycle.source_observation(self.root, self.head)
        self.assertEqual(read.call_args_list, [mock.call(self.root, self.head, lifecycle.SOURCE_PATHS)])
        self.assertEqual(admitted.call_args_list, [mock.call(self.root, self.head)] * 2)
        self.assertEqual(result, {"checkout": self.head, "tree": self.tree,
            "files": {storage.SOURCE_PATH: observed["files"][storage.SOURCE_PATH]["checkout"]}})

    def test_legacy_lifecycle_does_not_claim_current_application_storage_execution(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_windows_installer_lifecycle import Fixture
        evidence = Path(self.temp.name) / "evidence"
        evidence.mkdir()
        result = Fixture(evidence).verify()
        self.assertEqual(result["project_observation_scope"], "HISTORICAL_PREDECESSOR_PRESERVATION")
        self.assertEqual(result["current_application_storage_open"], "NOT_RUN")
        self.assertEqual(result["current_application_storage_migration"], "NOT_RUN")
        self.assertEqual(result["published_product_upgrade"], "NOT_CLAIMED")


if __name__ == "__main__":
    unittest.main()
