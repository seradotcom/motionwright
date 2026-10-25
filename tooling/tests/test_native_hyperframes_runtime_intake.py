"""G0 setup intake contracts. Memory archives only; no download or native work."""
import io
import json
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import zipfile

import native_hyperframes_runtime_intake as intake
import native_hyperframes_actions as actions
from native_hyperframes_evidence import HyperframesEvidenceRejected
from native_evidence_projection import SelectionError, project


def archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as writer:
        for name, data, mode in entries:
            row = zipfile.ZipInfo(name)
            row.create_system = 3
            row.external_attr = mode << 16
            writer.writestr(row, data)
    output.seek(0)
    return zipfile.ZipFile(output)


class PublicIntakeTests(unittest.TestCase):
    def test_browser_url_is_derived_from_the_actual_resolved_revision(self):
        version = intake.chrome_version(b'{"chrome":"123.0.4567.89"}')
        self.assertEqual(intake.chrome_url(version),
            "https://storage.googleapis.com/chrome-for-testing-public/123.0.4567.89/linux64/chrome-linux64.zip")

    def test_revision_cannot_be_latest_an_override_url_or_an_untyped_value(self):
        for value in ("latest", "123", "123.0.1.2/../../private", "https://other.invalid", True, 123, None):
            with self.subTest(value=value), self.assertRaises(HyperframesEvidenceRejected):
                intake.chrome_version(json.dumps({"chrome": value}))
        with self.assertRaises(HyperframesEvidenceRejected):
            intake.chrome_version('{"chrome":"123.0.1.2","alternate":"124.0.1.2"}')

    def test_original_archive_root_and_regular_executable_are_preserved(self):
        with archive([
            ("chrome-linux64/", b"", stat.S_IFDIR | 0o755),
            ("chrome-linux64/chrome", b"G0 NOT AN EXECUTABLE", stat.S_IFREG | 0o755),
            ("chrome-linux64/resources.pak", b"G0 ORIGINAL RESOURCE", stat.S_IFREG | 0o644),
        ]) as value:
            rows = intake.archive_entries(value)
        self.assertEqual([row.filename for row in rows], [
            "chrome-linux64/", "chrome-linux64/chrome", "chrome-linux64/resources.pak"])

    def test_archive_traversal_and_unreviewed_namespace_are_rejected(self):
        for name in ("../private", "/private", "chrome-linux64/../private",
                     "chrome-linux64//private", "other/chrome", "chrome-linux64/.npmrc"):
            with archive([(name, b"G0", stat.S_IFREG | 0o600)]) as value:
                with self.subTest(name=name), self.assertRaises(HyperframesEvidenceRejected):
                    intake.archive_entries(value)

    def test_symlink_and_empty_regular_file_are_rejected_without_repair(self):
        for data, mode in ((b"target", stat.S_IFLNK | 0o777), (b"", stat.S_IFREG | 0o755)):
            with archive([("chrome-linux64/chrome", data, mode)]) as value:
                with self.subTest(mode=mode), self.assertRaises(HyperframesEvidenceRejected):
                    intake.archive_entries(value)

    def test_archive_must_contain_the_actual_chrome_executable_name(self):
        with archive([("chrome-linux64/renamed-chrome", b"G0", stat.S_IFREG | 0o755)]) as value:
            with self.assertRaises(HyperframesEvidenceRejected):
                intake.archive_entries(value)

    def test_duplicate_archive_member_is_rejected_before_extraction(self):
        row = zipfile.ZipInfo("chrome-linux64/chrome")
        row.file_size = 10
        row.external_attr = (stat.S_IFREG | 0o755) << 16
        value = SimpleNamespace(infolist=lambda: [row, row])
        with self.assertRaises(HyperframesEvidenceRejected):
            intake.archive_entries(value)

    def test_declared_archive_budget_is_checked_before_extracting_any_entry(self):
        row = zipfile.ZipInfo("chrome-linux64/chrome")
        row.file_size = 512 * 1024 * 1024 + 1
        row.external_attr = (stat.S_IFREG | 0o755) << 16
        value = SimpleNamespace(infolist=lambda: [row])
        with self.assertRaises(HyperframesEvidenceRejected):
            intake.archive_entries(value)

    def test_existing_setup_intent_blocks_any_process_launch(self):
        with patch.object(intake.custody, "new_json", side_effect=FileExistsError("existing intent")), \
             patch.object(intake.subprocess, "run") as run, \
             self.assertRaises(FileExistsError):
            intake.execute("npm-install", ["/public/npm"], Path("/owner"), {})
        run.assert_not_called()

    def test_missing_system_library_is_not_replaced_or_labeled_complete(self):
        with patch.object(intake.custody, "regular") as read, \
             self.assertRaises(HyperframesEvidenceRejected):
            intake.system_dependencies([b"libG0.so => not found\n"])
        read.assert_not_called()

    def test_intake_policy_preserves_observed_hashes_and_excludes_private_paths(self):
        policies, _ = actions.direct_contract()
        binary = {"path": "/private/original/tool", "sha256": "1" * 64,
                  "bytes": 10, "version": "G0 observed version"}
        tool = {**binary, "relative_path": "tools/original"}
        value = {
            "schema": intake.SCHEMA,
            "context": {"source_sha": "a" * 40, "semwright_sha": actions.stages.SEMWRIGHT,
                        "run_id": "123", "run_attempt": "1", "scenario": actions.SCENARIO},
            "status": "RUNTIME_FINALIZED", "source_bindings_sha256": "2" * 64,
            "hyperframes_version": "0.8.181",
            "hyperframes_commit": "ae02e5aceff4742e5d72c20f0859eb3e6b449035",
            "public_distribution_integrity": intake.INTEGRITY,
            "runtime_lock_sha256": "3" * 64, "npm_lock_sha256": "4" * 64,
            "node": binary, "tools": {role: tool for role in ("browser", "ffmpeg", "ffprobe")},
            "chrome_archive": {"url": intake.chrome_url("123.0.1.2"), "version": "123.0.1.2",
                               "sha256": "5" * 64, "bytes": 10},
            "system_dependency_observation": {
                "ldd_only": True, "immutable_system_closure_attested": False,
                "files": [{"name": "libG0.so", "path": "/private/not-selected/libG0.so",
                           "sha256": "6" * 64, "bytes": 10}]},
            "native_render_executed": False, "automatic_retry": False}
        observed = project(value, policies["hf-runtime-intake"])
        self.assertEqual(observed["node"]["sha256"], binary["sha256"])
        self.assertNotIn("path", observed["node"])
        self.assertNotIn("path", observed["tools"]["browser"])
        self.assertNotIn("path", observed["system_dependency_observation"]["files"][0])
        value["system_dependency_observation"]["immutable_system_closure_attested"] = True
        with self.assertRaises(SelectionError):
            project(value, policies["hf-runtime-intake"])


if __name__ == "__main__":
    unittest.main()
