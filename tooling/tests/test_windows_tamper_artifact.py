"""Offline artifact transport regressions; ZIP payloads are never executable."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import windows_tamper_artifact as artifact


def zip_bytes(rows):
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, body in rows:
                archive.writestr(name, body)
    return output.getvalue()


class ArtifactFixtures(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mw-artifact-offline-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = "a" * 40
        self.run = {"repository": "example/motionwright", "run_id": 42, "run_attempt": 1}
        self.body = zip_bytes([
            ("admission.json", b'{"synthetic":true}\n'),
            ("inputs/current_setup/fixture.exe", b"not executable; fixture bytes"),
            ("upgrade-after/installed/uninstall.exe", b"not executable; captured fixture"),
        ])
        self.digest = hashlib.sha256(self.body).hexdigest()
        self.run_meta = {"id": 42, "head_sha": self.source, "run_attempt": 1,
                         "status": "in_progress", "conclusion": None,
                         "repository": {"id": 51, "full_name": self.run["repository"]},
                         "head_repository": {"id": 51, "full_name": self.run["repository"]}}
        self.meta = {"id": 73, "name": artifact.ARTIFACT_NAME, "expired": False,
                     "digest": "sha256:" + self.digest, "size_in_bytes": len(self.body),
                     "workflow_run": {"id": 42, "head_sha": self.source,
                                      "repository_id": 51, "head_repository_id": 51}}
        self.calls = []
        self.after_meta = None

    def fake_gh(self, transport, label, route, output_name, maximum, gh_executable, timeout):
        self.calls.append((label, route))
        if label == "run":
            value = self.run_meta
        elif label == "artifact-after" and self.after_meta is not None:
            value = self.after_meta
        else:
            value = self.meta
        body = self.body if label == "archive" else json.dumps(value).encode("utf-8")
        self.assertLessEqual(len(body), maximum)
        output = transport / output_name
        with output.open("xb") as stream:
            stream.write(body)
        with (transport / (label + ".stderr.original.log")).open("xb"):
            pass
        return output

    def receive(self, name="received"):
        with mock.patch.object(artifact, "_run_gh", side_effect=self.fake_gh):
            return artifact.receive_producer(self.base / name, artifact_id=73,
                artifact_sha256=self.digest, expected_source=self.source, expected_run=self.run,
                gh_executable="fixture-gh-never-executed")

    def extract(self, body, name="archive"):
        original = self.base / (name + ".original.zip")
        original.write_bytes(body)
        output = self.base / (name + "-producer")
        artifact.extract_archive(original, output, expected_bytes=len(body),
                                 expected_sha256=hashlib.sha256(body).hexdigest())
        return original, output

    def test_same_run_in_progress_receives_exact_binding_and_original_archive(self):
        binding = self.receive()
        expected = {"artifact_id": 73, "artifact_name": artifact.ARTIFACT_NAME,
            "artifact_bytes": len(self.body), "artifact_sha256": self.digest,
            "source_sha": self.source, "run": self.run}
        self.assertEqual(binding, expected)
        root = self.base / "received"
        self.assertEqual(json.loads((root / "binding.json").read_bytes()), expected)
        self.assertEqual((root / "transport/archive.original.zip").read_bytes(), self.body)
        self.assertEqual((root / "producer/inputs/current_setup/fixture.exe").read_bytes(),
                         b"not executable; fixture bytes")
        self.assertEqual([label for label, unused in self.calls],
                         ["run", "artifact-before", "archive", "artifact-after"])
        self.assertEqual(sum(route.endswith("/zip") for unused, route in self.calls), 1)

    def test_receiver_never_adopts_existing_root_or_repeats_download(self):
        self.receive()
        self.calls.clear()
        with self.assertRaisesRegex(artifact.ArtifactError, "output_already_exists"):
            self.receive()
        self.assertEqual(self.calls, [])
        self.assertEqual((self.base / "received/transport/archive.original.zip").read_bytes(), self.body)

    def test_external_authority_types_and_attempt_are_closed_before_any_effect(self):
        cases = [
            {"artifact_id": True}, {"artifact_id": 0}, {"artifact_sha256": "b" * 63},
            {"expected_source": "HEAD"}, {"expected_run": {**self.run, "run_attempt": 2}},
            {"expected_run": {**self.run, "run_attempt": True}},
            {"expected_run": {**self.run, "run_id": True}},
            {"expected_run": {**self.run, "repository": "../elsewhere"}},
            {"expected_run": {**self.run, "extra": 1}},
        ]
        for index, changed in enumerate(cases):
            with self.subTest(index=index), mock.patch.object(artifact, "_run_gh") as gh:
                values = dict(artifact_id=73, artifact_sha256=self.digest,
                              expected_source=self.source, expected_run=self.run)
                values.update(changed)
                root = self.base / ("bad-authority-" + str(index))
                with self.assertRaises(artifact.ArtifactError):
                    artifact.receive_producer(root, **values)
                self.assertFalse(root.exists())
                gh.assert_not_called()

    def test_artifact_metadata_does_not_supply_its_own_expected_identity(self):
        cases = [("id", 74), ("id", True), ("name", "another-artifact"), ("expired", True),
                 ("digest", "sha256:" + "f" * 64), ("size_in_bytes", 0),
                 ("size_in_bytes", True), ("size_in_bytes", artifact.MAX_ARCHIVE_BYTES + 1)]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                data = {**self.meta, field: value}
                with self.assertRaises(artifact.ArtifactError):
                    artifact._metadata(data, 73, self.digest, self.source, self.run, 51)
        for field, value in (("id", 43), ("head_sha", "f" * 40),
                             ("repository_id", 52), ("head_repository_id", 52)):
            with self.subTest(run_field=field):
                data = {**self.meta, "workflow_run": {**self.meta["workflow_run"], field: value}}
                with self.assertRaises(artifact.ArtifactError):
                    artifact._metadata(data, 73, self.digest, self.source, self.run, 51)

    def test_run_source_repository_and_attempt_are_independently_checked(self):
        for field, value in (("id", 43), ("head_sha", "f" * 40), ("run_attempt", 2),
                             ("repository", {"id": 51, "full_name": "elsewhere/repository"}),
                             ("head_repository", {"id": 52, "full_name": self.run["repository"]})):
            with self.subTest(field=field):
                with self.assertRaises(artifact.ArtifactError):
                    artifact._run_repository({**self.run_meta, field: value}, self.source, self.run)

    def test_metadata_change_after_download_retains_original_and_denies_binding(self):
        self.after_meta = {**self.meta, "expired": True}
        with self.assertRaisesRegex(artifact.ArtifactError, "metadata_identity"):
            self.receive()
        root = self.base / "received"
        self.assertEqual((root / "transport/archive.original.zip").read_bytes(), self.body)
        self.assertFalse((root / "binding.json").exists())
        self.assertFalse((root / "producer").exists())
        self.assertEqual(sum(label == "archive" for label, unused in self.calls), 1)

    def test_changed_short_appended_or_wrong_length_originals_are_denied(self):
        original = self.base / "expected.original.zip"
        for index, body in enumerate((self.body[:-1], self.body + b"x",
                                      bytes([self.body[0] ^ 1]) + self.body[1:])):
            original.write_bytes(body)
            with self.subTest(index=index), self.assertRaises(artifact.ArtifactError):
                artifact.extract_archive(original, self.base / ("rejected-" + str(index)),
                                         expected_bytes=len(self.body), expected_sha256=self.digest)
            self.assertEqual(original.read_bytes(), body)
        with self.assertRaisesRegex(artifact.ArtifactError, "zip_trailing"):
            self.extract(self.body + b"x", "append-with-own-new-digest")

    def test_unsafe_names_are_rejected_before_destination_creation(self):
        names = ["../escape", "/absolute", "C:/drive", "a\\b", "a//b", ".", "..",
                 "a/../b", "trailing. ", "a:b", "CON", "aux.txt", "COM1.log",
                 "a/\x01bad", "unicode-\u00e9"]
        for index, name in enumerate(names):
            # ZipInfo normalizes the host separator while writing on Windows;
            # mutate both raw header names to test the actual incoming bytes.
            body = zip_bytes([("a/b" if name == "a\\b" else name, b"x")])
            if name == "a\\b":
                body = body.replace(b"a/b", b"a\\b")
            with self.subTest(name=repr(name)), self.assertRaises(artifact.ArtifactError):
                self.extract(body, "name-" + str(index))
            self.assertFalse((self.base / ("name-" + str(index) + "-producer")).exists())
        body = zip_bytes([("normal", b"x")]).replace(b"normal", b"no\x00mal")
        with self.assertRaisesRegex(artifact.ArtifactError, "zip_name"):
            self.extract(body, "nul")

    def test_casefold_duplicates_and_file_directory_prefixes_are_rejected(self):
        rosters = [
            [("a", b"x"), ("a", b"y")], [("A", b"x"), ("a", b"y")],
            [("A/x", b"x"), ("a/y", b"y")], [("a", b"x"), ("a/b", b"y")],
            [("a/b", b"x"), ("a", b"y")], [("a/", b""), ("a", b"y")],
        ]
        for index, rows in enumerate(rosters):
            with self.subTest(index=index), self.assertRaises(artifact.ArtifactError):
                self.extract(zip_bytes(rows), "aliases-" + str(index))
            self.assertFalse((self.base / ("aliases-" + str(index) + "-producer")).exists())

    def test_explicit_directories_can_follow_implicit_parents(self):
        unused, output = self.extract(zip_bytes([("a/b/c", b"value"), ("a/", b""), ("a/b/", b"")]))
        self.assertEqual((output / "a/b/c").read_bytes(), b"value")

    def test_links_reparse_and_special_entries_are_never_materialized(self):
        for index, mode in enumerate((stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600,
                                      stat.S_IFCHR | 0o600, stat.S_IFDIR | 0o755)):
            entry = zipfile.ZipInfo("entry")
            entry.create_system = 3
            entry.external_attr = mode << 16
            with self.subTest(mode=mode), self.assertRaisesRegex(artifact.ArtifactError, "special_entry"):
                self.extract(zip_bytes([(entry, b"target")]), "special-" + str(index))
        for attribute in (0x400, 0x40, 0x10):
            entry = zipfile.ZipInfo("entry")
            entry.external_attr = (stat.S_IFREG | 0o600) << 16 | attribute
            with self.subTest(attribute=attribute), self.assertRaisesRegex(artifact.ArtifactError, "special_entry"):
                self.extract(zip_bytes([(entry, b"x")]), "attribute-" + str(attribute))

    def test_encrypted_flag_is_rejected_before_extraction(self):
        body = bytearray(zip_bytes([("entry", b"x")]))
        struct.pack_into("<H", body, 6, struct.unpack_from("<H", body, 6)[0] | 1)
        central = body.index(b"PK\x01\x02")
        struct.pack_into("<H", body, central + 8, struct.unpack_from("<H", body, central + 8)[0] | 1)
        with self.assertRaisesRegex(artifact.ArtifactError, "zip_encoding"):
            self.extract(bytes(body), "encrypted")

    def test_count_member_total_and_archive_bounds_refuse_without_relaxing_limits(self):
        cases = [
            ("MAX_MEMBERS", 1, [("one", b"a"), ("two", b"b")]),
            ("MAX_MEMBER_BYTES", 2, [("one", b"abc")]),
            ("MAX_UNPACKED_BYTES", 3, [("one", b"ab"), ("two", b"cd")]),
            ("MAX_ARCHIVE_BYTES", 10, [("one", b"a")]),
        ]
        for index, (constant, limit, rows) in enumerate(cases):
            with self.subTest(constant=constant), mock.patch.object(artifact, constant, limit):
                with self.assertRaises(artifact.ArtifactError):
                    self.extract(zip_bytes(rows), "bounds-" + str(index))
            self.assertFalse((self.base / ("bounds-" + str(index) + "-producer")).exists())

    def test_bad_crc_preserves_original_and_any_partial_extraction(self):
        body = bytearray(zip_bytes([("entry", b"payload")]))
        name_bytes, extra_bytes = struct.unpack_from("<HH", body, 26)
        body[30 + name_bytes + extra_bytes] ^= 1
        original = bytes(body)
        with self.assertRaisesRegex(artifact.ArtifactError, "zip_invalid"):
            self.extract(original, "crc")
        self.assertEqual((self.base / "crc.original.zip").read_bytes(), original)
        self.assertTrue((self.base / "crc-producer").is_dir())
        self.assertFalse((self.base / "binding.json").exists())

    def test_existing_extraction_root_and_sentinel_are_preserved(self):
        destination = self.base / "archive-producer"
        destination.mkdir()
        (destination / "sentinel").write_bytes(b"original")
        with self.assertRaisesRegex(artifact.ArtifactError, "output_already_exists"):
            self.extract(self.body)
        self.assertEqual((destination / "sentinel").read_bytes(), b"original")

    def test_duplicate_metadata_keys_are_not_silently_overwritten(self):
        path = self.base / "metadata.json"
        path.write_bytes(b'{"id":73,"id":74}')
        with self.assertRaisesRegex(artifact.ArtifactError, "duplicate_key"):
            artifact._json(path)


class FakeProcess:
    def __init__(self, stdout=b"{}", stderr=b"", terminal=0, never_exits=False):
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.terminal = terminal
        self.returncode = None
        self.never_exits = never_exits
        self.kill_count = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.never_exits and self.returncode is None:
            raise subprocess.TimeoutExpired("fixture-gh", timeout)
        if self.returncode is None:
            self.returncode = self.terminal
        return self.returncode

    def kill(self):
        self.kill_count += 1
        self.returncode = -9


class OwnedTransportControls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mw-artifact-gh-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def invoke(self, process, *, maximum=100, timeout=1):
        with mock.patch.object(artifact.subprocess, "Popen", return_value=process) as popen:
            try:
                result = artifact._run_gh(self.root, "request", "repos/example/motionwright/actions/runs/42",
                    "response.original.json", maximum, "synthetic-gh", timeout)
            finally:
                self.assertEqual(popen.call_count, 1)
                self.popen_arguments = popen.call_args
        return result

    def test_raw_streams_and_exit_survive_a_nonzero_without_token_or_trace_logging(self):
        process = FakeProcess(b'{"message":"denied"}', b"original stderr\n", terminal=1)
        with mock.patch.dict(os.environ, {"GH_TOKEN": "synthetic-secret",
                "GH_DEBUG": "api", "GIT_TRACE_CURL": "1", "GIT_CURL_VERBOSE": "0"}, clear=True):
            with self.assertRaisesRegex(artifact.ArtifactError, "gh_exit_nonzero"):
                self.invoke(process)
        self.assertEqual((self.root / "response.original.json").read_bytes(), b'{"message":"denied"}')
        self.assertEqual((self.root / "request.stderr.original.log").read_bytes(), b"original stderr\n")
        receipt = (self.root / "request.terminal.json").read_bytes()
        self.assertNotIn(b"synthetic-secret", receipt)
        self.assertEqual(json.loads(receipt)["exit_code"], 1)
        environment = self.popen_arguments.kwargs["env"]
        self.assertEqual(environment["GH_TOKEN"], "synthetic-secret")
        self.assertFalse(any(key in environment for key in ("GH_DEBUG", "GIT_TRACE_CURL", "GIT_CURL_VERBOSE")))
        self.assertFalse(self.popen_arguments.kwargs["shell"])

    def test_output_limit_retains_a_bounded_prefix_and_never_relaunches(self):
        with self.assertRaisesRegex(artifact.ArtifactError, "gh_capture_failed"):
            self.invoke(FakeProcess(b"123456789"), maximum=3)
        self.assertEqual((self.root / "response.original.json").read_bytes(), b"1234")
        receipt = json.loads((self.root / "request.terminal.json").read_bytes())
        self.assertFalse(receipt["stdout_complete"])
        self.assertEqual(receipt["capture_errors"]["stdout"], "stdout_limit")

    def test_timeout_marks_uncertainty_and_stops_only_the_owned_process_once(self):
        process = FakeProcess(never_exits=True)
        with self.assertRaisesRegex(artifact.ArtifactError, "gh_timeout"):
            self.invoke(process, timeout=0)
        receipt = json.loads((self.root / "request.terminal.json").read_bytes())
        self.assertTrue(receipt["timed_out"])
        self.assertFalse(receipt["stdout_complete"])
        self.assertEqual(process.kill_count, 1)


if __name__ == "__main__":
    unittest.main()
