"""Exercise bounded, read-only admission of retained source evidence.

The fixed-byte fixtures are synthetic. Passing these tests grants no source-use
rights and does not establish complete SDK availability or past authorization.
"""
from pathlib import Path
import copy
import errno
import hashlib
import json
import os
import stat
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from source_lock_admission import SourceAdmissionError, load_source_lock, verify_source_lock
from test_public_surface_policy import write_source_evidence_fixture


class SourceLockAdmissionTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="motionwright-source-admission-")
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.root = self.scratch / "consumer"
        self.root.mkdir()
        self.entry = write_source_evidence_fixture(self.root)
        self.lock = {
            "schema_version": 1,
            "dependencies": {"semwright": self.entry},
        }

    def snapshot(self):
        observed = {}
        for directory, dirs, files in os.walk(self.scratch, followlinks=False):
            parent = Path(directory)
            descend = []
            for name in sorted(dirs + files):
                path = parent / name
                info = path.lstat()
                relative = path.relative_to(self.scratch).as_posix()
                redirected = stat.S_ISLNK(info.st_mode) or bool(
                    getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                )
                if redirected:
                    observed[relative] = ("link", os.readlink(path))
                elif stat.S_ISDIR(info.st_mode):
                    observed[relative] = ("directory",)
                    descend.append(name)
                elif stat.S_ISREG(info.st_mode):
                    data = path.read_bytes()
                    observed[relative] = (
                        "file", len(data), hashlib.sha256(data).hexdigest(),
                        info.st_nlink,
                    )
                else:
                    observed[relative] = ("special", info.st_mode)
            dirs[:] = [name for name in dirs if name in descend]
        return observed

    def assert_rejected_without_changes(self):
        before = self.snapshot()
        lock_before = copy.deepcopy(self.lock)
        with self.assertRaises(SourceAdmissionError):
            verify_source_lock(self.root, self.lock)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.lock, lock_before)

    def evidence_path(self):
        return self.root / self.entry["verification"]["evidence"]["path"]

    def evidence(self):
        return json.loads(self.evidence_path().read_text(encoding="utf-8"))

    def replace_evidence_bytes(self, data):
        self.evidence_path().write_bytes(data)
        self.entry["verification"]["evidence"]["sha256"] = hashlib.sha256(data).hexdigest()

    def replace_evidence(self, evidence):
        self.replace_evidence_bytes(
            (json.dumps(evidence, indent=2) + "\n").encode("utf-8"))

    def replace_source(self, upstream_path, data):
        evidence = self.evidence()
        record = next(
            item for item in evidence["files"]
            if item["upstream_path"] == upstream_path
        )
        (self.root / record["path"]).write_bytes(data)
        record["bytes"] = len(data)
        record["sha256"] = hashlib.sha256(data).hexdigest()
        record["git_blob_sha1"] = hashlib.sha1(
            b"blob " + str(len(data)).encode("ascii") + b"\0" + data,
            usedforsecurity=False,
        ).hexdigest()
        self.replace_evidence(evidence)

    def create_symlink(self, target, link, *, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError as error:
            if error.errno in {
                errno.EACCES, errno.EPERM, errno.ENOSYS, errno.ENOTSUP,
            } or getattr(error, "winerror", None) == 1314:
                self.skipTest(
                    "symlink creation unavailable: "
                    f"errno={error.errno}, winerror={getattr(error, 'winerror', None)}"
                )
            raise

    def test_verified_fixed_bytes_are_read_only_and_do_not_launch_external_effects(self):
        before = self.snapshot()
        lock_before = copy.deepcopy(self.lock)
        with (
            patch("subprocess.Popen", side_effect=AssertionError("unexpected process")),
            patch("os.system", side_effect=AssertionError("unexpected shell")),
            patch("socket.socket", side_effect=AssertionError("unexpected network")),
        ):
            self.assertIsNone(verify_source_lock(self.root, self.lock))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.lock, lock_before)

    def test_unknown_or_boolean_outer_schema_is_not_admitted(self):
        for version in (None, 2, True, "1"):
            with self.subTest(version=version):
                self.lock["schema_version"] = version
                self.assert_rejected_without_changes()
        del self.lock["schema_version"]
        self.assert_rejected_without_changes()

    def test_other_repository_is_refused_even_when_all_record_bindings_match(self):
        repository = "https://example.invalid/different/source"
        self.entry["repository"] = repository
        self.entry["verification"]["repository"] = repository
        evidence = self.evidence()
        evidence["repository"] = repository
        self.replace_evidence(evidence)
        self.assert_rejected_without_changes()

    def test_missing_or_legacy_verification_is_not_admission(self):
        for verification in (
            None, {}, "unverified",
            "git fetch origin " + "a" * 40 + " && git cat-file -e HEAD",
        ):
            with self.subTest(verification=verification):
                self.entry["verification"] = verification
                self.assert_rejected_without_changes()
        del self.entry["verification"]
        self.assert_rejected_without_changes()

    def test_unverified_or_unavailable_states_fail_closed(self):
        for status in ("unverified", "unavailable", "UNKNOWN"):
            with self.subTest(status=status):
                self.entry["verification"]["status"] = status
                self.assert_rejected_without_changes()

    def test_verification_cannot_move_to_another_revision_or_repository(self):
        original = copy.deepcopy(self.entry["verification"])
        for field, value in (
            ("revision", "b" * 40),
            ("repository", "https://example.invalid/other"),
        ):
            with self.subTest(field=field):
                self.entry["verification"] = copy.deepcopy(original)
                self.entry["verification"][field] = value
                self.assert_rejected_without_changes()

    def test_rehashed_evidence_cannot_change_source_identity_or_license(self):
        original = self.evidence()
        for field, value in (
            ("schema", "motionwright.source-evidence/2"),
            ("repository", "https://example.invalid/other"),
            ("revision", "b" * 40),
            ("version", "2.0.0"),
            ("license_expression", "unresolved"),
        ):
            with self.subTest(field=field):
                evidence = copy.deepcopy(original)
                evidence[field] = value
                self.replace_evidence(evidence)
                self.assert_rejected_without_changes()

    def test_missing_or_changed_evidence_record_is_rejected(self):
        path = self.evidence_path()
        original = path.read_bytes()
        path.unlink()
        self.assert_rejected_without_changes()
        path.write_bytes(original + b" ")
        self.assert_rejected_without_changes()

    def test_missing_or_changed_captured_source_bytes_are_rejected(self):
        for record in self.evidence()["files"]:
            path = self.root / record["path"]
            original = path.read_bytes()
            for mutation in ("missing", "changed"):
                with self.subTest(source=record["upstream_path"], mutation=mutation):
                    if mutation == "missing":
                        path.unlink()
                    else:
                        path.write_bytes(original + b"\n")
                    self.assert_rejected_without_changes()
                    path.write_bytes(original)

    def test_wrong_git_blob_hash_or_declared_size_is_rejected(self):
        original = self.evidence()
        for field, value in (
            ("git_blob_sha1", "0" * 40),
            ("bytes", original["files"][0]["bytes"] + 1),
        ):
            with self.subTest(field=field):
                evidence = copy.deepcopy(original)
                evidence["files"][0][field] = value
                self.replace_evidence(evidence)
                self.assert_rejected_without_changes()

    def test_boolean_length_is_not_an_evidence_byte_count(self):
        self.replace_source("LICENSE-MIT", b"M")
        evidence = self.evidence()
        record = next(
            item for item in evidence["files"] if item["upstream_path"] == "LICENSE-MIT")
        record["bytes"] = True
        self.replace_evidence(evidence)
        self.assert_rejected_without_changes()

    def test_evidence_requires_each_distinct_upstream_file(self):
        original = self.evidence()
        for mutation in ("missing", "duplicate"):
            with self.subTest(mutation=mutation):
                evidence = copy.deepcopy(original)
                if mutation == "missing":
                    evidence["files"].pop()
                else:
                    evidence["files"][2] = copy.deepcopy(evidence["files"][1])
                self.replace_evidence(evidence)
                self.assert_rejected_without_changes()

    def test_distinct_sources_cannot_share_one_local_evidence_path(self):
        evidence = self.evidence()
        first, second = evidence["files"][:2]
        for field in ("path", "bytes", "sha256", "git_blob_sha1"):
            second[field] = first[field]
        self.replace_evidence(evidence)
        self.assert_rejected_without_changes()

    def test_rehashed_cargo_bytes_must_keep_declared_version_and_license(self):
        for data in (
            b'[workspace.package]\nversion="2.0.0"\nlicense="MIT OR Apache-2.0"\n',
            b'[workspace.package]\nversion="1.0.0"\nlicense="unresolved"\n',
            b"not valid TOML [",
        ):
            with self.subTest(data=data):
                self.replace_source("Cargo.toml", data)
                self.assert_rejected_without_changes()

    def test_duplicate_json_keys_are_rejected_even_with_a_matching_record_hash(self):
        evidence = self.evidence()
        ordinary = json.dumps(evidence)
        duplicated = (
            '{"revision":' + json.dumps(evidence["revision"]) + "," + ordinary[1:]
        ).encode("utf-8")
        self.replace_evidence_bytes(duplicated)
        self.assert_rejected_without_changes()

    def test_evidence_reference_cannot_escape_or_use_an_absolute_path(self):
        reference = self.entry["verification"]["evidence"]
        original = reference["path"]
        absolute = str(self.root / original)
        protected = self.scratch / "protected-evidence.json"
        protected.write_bytes(self.evidence_path().read_bytes())
        for path in ("../protected-evidence.json", absolute):
            with self.subTest(path=path):
                reference["path"] = path
                self.assert_rejected_without_changes()

    def test_captured_source_cannot_escape_the_evidence_root(self):
        original = self.evidence()
        record = original["files"][0]
        protected = self.scratch / "protected-source.toml"
        protected.write_bytes((self.root / record["path"]).read_bytes())
        for path in (
            "../protected-source.toml",
            str(self.root / record["path"]),
            "..\\protected-source.toml",
        ):
            with self.subTest(path=path):
                evidence = copy.deepcopy(original)
                evidence["files"][0]["path"] = path
                self.replace_evidence(evidence)
                self.assert_rejected_without_changes()

    def test_device_names_and_alternate_streams_are_refused_before_open(self):
        reference = self.entry["verification"]["evidence"]
        for path in ("CON", "NUL.json", "LICENSE-MIT:stream", "folder./record.json"):
            with self.subTest(path=path):
                reference["path"] = path
                with patch("source_lock_admission.os.open") as opening:
                    self.assert_rejected_without_changes()
                    opening.assert_not_called()

    def test_oversized_evidence_record_is_rejected_with_correct_hash(self):
        data = self.evidence_path().read_bytes() + b" " * (64 * 1024 + 1)
        self.replace_evidence_bytes(data)
        self.assert_rejected_without_changes()

    def test_oversized_source_is_rejected_even_with_updated_proof(self):
        data = (
            b'[workspace.package]\nversion="1.0.0"\nlicense="MIT OR Apache-2.0"\n#'
            + b"x" * (256 * 1024 + 1)
        )
        self.replace_source("Cargo.toml", data)
        self.assert_rejected_without_changes()

    def test_hardlinked_source_is_rejected_without_modifying_either_name(self):
        record = self.evidence()["files"][1]
        source = self.root / record["path"]
        alias = self.scratch / "retained-license-alias"
        try:
            os.link(source, alias)
        except OSError as error:
            if error.errno in {
                errno.EACCES, errno.EPERM, errno.ENOSYS, errno.ENOTSUP,
            }:
                self.skipTest(f"hardlink creation unavailable: errno={error.errno}")
            raise
        self.assert_rejected_without_changes()

    def test_symlinked_source_is_rejected_without_modifying_target(self):
        record = self.evidence()["files"][1]
        source = self.root / record["path"]
        retained = self.scratch / "retained-license"
        source.rename(retained)
        self.create_symlink(retained, source)
        self.assert_rejected_without_changes()

    def test_symlinked_evidence_parent_is_rejected(self):
        directory = self.evidence_path().parent
        retained = self.scratch / "retained-evidence"
        directory.rename(retained)
        self.create_symlink(retained, directory, directory=True)
        self.assert_rejected_without_changes()


    @unittest.skipUnless(os.name == "nt", "Windows junction semantics required")
    def test_junctioned_evidence_parent_is_rejected(self):
        import _winapi

        directory = self.evidence_path().parent
        retained = self.scratch / "junction-retained-evidence"
        directory.rename(retained)
        try:
            _winapi.CreateJunction(str(retained), str(directory))
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EPERM} or (
                getattr(error, "winerror", None) == 1314
            ):
                self.skipTest(
                    "junction creation denied: "
                    f"errno={error.errno}, winerror={getattr(error, 'winerror', None)}"
                )
            raise
        self.assert_rejected_without_changes()


    def test_source_lock_loader_is_bounded_read_only_and_has_no_external_effects(self):
        encoded = (json.dumps(self.lock) + "\n").encode("utf-8")
        self.assertLess(len(encoded), 64 * 1024)
        (self.root / "SOURCE_LOCK.json").write_bytes(encoded)
        before = self.snapshot()
        lock_before = copy.deepcopy(self.lock)
        with (
            patch("subprocess.Popen", side_effect=AssertionError("unexpected process")),
            patch("os.system", side_effect=AssertionError("unexpected shell")),
            patch("socket.socket", side_effect=AssertionError("unexpected network")),
        ):
            loaded = load_source_lock(self.root)
            self.assertEqual(loaded, self.lock)
            self.assertIsNone(verify_source_lock(self.root, loaded))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.lock, lock_before)

    def test_source_lock_loader_rejects_duplicate_status_before_last_value_wins(self):
        ordinary = json.dumps(self.lock)
        field = '"status": "verified"'
        self.assertEqual(ordinary.count(field), 1)
        encoded = ordinary.replace(
            field, '"status": "unverified", "status": "verified"'
        ).encode("utf-8")
        self.assertEqual(
            json.loads(encoded)["dependencies"]["semwright"]["verification"]["status"],
            "verified",
        )
        (self.root / "SOURCE_LOCK.json").write_bytes(encoded)
        before = self.snapshot()
        with self.assertRaises(SourceAdmissionError) as refusal:
            load_source_lock(self.root)
        self.assertRegex(str(refusal.exception), r"^source_admission_[a-z_]+$")
        self.assertEqual(self.snapshot(), before)

    def test_small_deep_json_returns_a_static_source_admission_refusal(self):
        depth = sys.getrecursionlimit() + 100
        encoded = b'{"nested":' + b"[" * depth + b"0" + b"]" * depth + b"}"
        self.assertLess(len(encoded), 64 * 1024)
        with self.assertRaises(RecursionError):
            json.loads(encoded)
        for target in ("lock", "evidence"):
            with self.subTest(target=target):
                if target == "lock":
                    (self.root / "SOURCE_LOCK.json").write_bytes(encoded)
                else:
                    self.replace_evidence_bytes(encoded)
                before = self.snapshot()
                lock_before = copy.deepcopy(self.lock)
                with self.assertRaises(SourceAdmissionError) as refusal:
                    if target == "lock":
                        load_source_lock(self.root)
                    else:
                        verify_source_lock(self.root, self.lock)
                self.assertIsInstance(refusal.exception.__cause__, RecursionError)
                self.assertRegex(str(refusal.exception), r"^source_admission_[a-z_]+$")
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.lock, lock_before)

    def test_rehashed_deep_cargo_metadata_returns_a_static_admission_refusal(self):
        depth = sys.getrecursionlimit() + 100
        encoded = (
            b'[workspace.package]\nversion="1.0.0"\nlicense="MIT OR Apache-2.0"\nextra='
            + b"[" * depth + b"0" + b"]" * depth + b"\n"
        )
        self.assertLess(len(encoded), 256 * 1024)
        with self.assertRaises(RecursionError):
            tomllib.loads(encoded.decode("utf-8"))
        self.replace_source("Cargo.toml", encoded)
        before = self.snapshot()
        lock_before = copy.deepcopy(self.lock)
        with self.assertRaises(SourceAdmissionError) as refusal:
            verify_source_lock(self.root, self.lock)
        self.assertIsInstance(refusal.exception.__cause__, RecursionError)
        self.assertRegex(str(refusal.exception), r"^source_admission_[a-z_]+$")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.lock, lock_before)

    def test_source_lock_loader_rejects_bytes_above_its_bound(self):
        encoded = json.dumps(self.lock).encode("utf-8")
        encoded += b" " * (64 * 1024 + 1 - len(encoded))
        self.assertEqual(len(encoded), 64 * 1024 + 1)
        (self.root / "SOURCE_LOCK.json").write_bytes(encoded)
        before = self.snapshot()
        with self.assertRaises(SourceAdmissionError) as refusal:
            load_source_lock(self.root)
        self.assertRegex(str(refusal.exception), r"^source_admission_[a-z_]+$")
        self.assertEqual(self.snapshot(), before)



if __name__ == "__main__":
    unittest.main()
