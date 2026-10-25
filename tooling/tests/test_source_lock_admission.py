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
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from source_lock_admission import (
    SourceAdmissionError, distribution_scope, distribution_source_context,
    load_source_lock, verify_distribution_admission, verify_source_lock,
)
import source_lock_admission as admission
from test_public_surface_policy import write_source_evidence_fixture


def stat_view(info, **changes):
    fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size",
              "st_mtime_ns", "st_ctime_ns", "st_file_attributes")
    values = {name: getattr(info, name, 0) for name in fields}
    values.update(changes)
    return SimpleNamespace(**values)


class SourceLockAdmissionFixture:
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


class SourceLockAdmissionTests(SourceLockAdmissionFixture, unittest.TestCase):
    def test_windows_path_and_descriptor_clock_difference_preserves_stable_admission(self):
        before = self.snapshot()
        original_fstat = os.fstat

        def descriptor_clock(fd):
            info = original_fstat(fd)
            return stat_view(info, st_ctime_ns=info.st_ctime_ns + 100)

        with (patch.object(admission, "_WINDOWS_STAT", True, create=True),
              patch.object(admission.os, "fstat", side_effect=descriptor_clock)):
            self.assertIsNone(verify_source_lock(self.root, self.lock))
        self.assertEqual(self.snapshot(), before)

    def test_windows_descriptor_change_time_still_detects_mutation_during_read(self):
        original_fstat = os.fstat
        calls = 0

        def descriptor_clock(fd):
            nonlocal calls
            calls += 1
            info = original_fstat(fd)
            return stat_view(info, st_ctime_ns=info.st_ctime_ns + 100 + calls)

        with (patch.object(admission, "_WINDOWS_STAT", True, create=True),
              patch.object(admission.os, "fstat", side_effect=descriptor_clock)):
            with self.assertRaisesRegex(SourceAdmissionError, "evidence_changed"):
                admission._read(self.root, self.entry["verification"]["evidence"]["path"],
                                admission.MAX_EVIDENCE_BYTES)
        self.assertEqual(calls, 2)

    def test_windows_path_change_time_still_detects_mutation_during_read(self):
        target = self.evidence_path()
        original_lstat, original_fstat = Path.lstat, os.fstat
        path_calls = 0

        def path_clock(path):
            nonlocal path_calls
            info = original_lstat(path)
            if path == target:
                path_calls += 1
                return stat_view(info, st_ctime_ns=info.st_ctime_ns + path_calls)
            return info

        def descriptor_clock(fd):
            info = original_fstat(fd)
            return stat_view(info, st_ctime_ns=info.st_ctime_ns + 100)

        with (patch.object(admission, "_WINDOWS_STAT", True, create=True),
              patch.object(Path, "lstat", path_clock),
              patch.object(admission.os, "fstat", side_effect=descriptor_clock)):
            with self.assertRaisesRegex(SourceAdmissionError, "evidence_changed"):
                admission._read(self.root, self.entry["verification"]["evidence"]["path"],
                                admission.MAX_EVIDENCE_BYTES)
        self.assertEqual(path_calls, 2)

    def test_windows_other_path_descriptor_identity_fields_must_still_match(self):
        original_fstat = os.fstat
        for field in ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size",
                      "st_mtime_ns", "st_file_attributes"):
            with self.subTest(field=field):
                def changed(fd):
                    info = original_fstat(fd)
                    delta = stat.S_IXUSR if field == "st_mode" else 1
                    return stat_view(info, **{field: getattr(info, field, 0) + delta})

                with (patch.object(admission, "_WINDOWS_STAT", True, create=True),
                      patch.object(admission.os, "fstat", side_effect=changed)):
                    with self.assertRaises(SourceAdmissionError):
                        admission._read(self.root,
                            self.entry["verification"]["evidence"]["path"],
                            admission.MAX_EVIDENCE_BYTES)

    def test_posix_path_descriptor_change_time_mismatch_is_still_rejected(self):
        original_fstat = os.fstat

        def descriptor_clock(fd):
            info = original_fstat(fd)
            return stat_view(info, st_ctime_ns=info.st_ctime_ns + 100)

        with (patch.object(admission, "_WINDOWS_STAT", False, create=True),
              patch.object(admission.os, "fstat", side_effect=descriptor_clock)):
            with self.assertRaisesRegex(SourceAdmissionError, "evidence_changed"):
                admission._read(self.root, self.entry["verification"]["evidence"]["path"],
                                admission.MAX_EVIDENCE_BYTES)

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
        # Decoder depth limits vary by runtime; keep the real input bounded.
        depth = 1100
        encoded = b'{"nested":' + b"[" * depth + b"0" + b"]" * depth + b"}"
        self.assertLess(len(encoded), 64 * 1024)
        for target in ("lock", "evidence"):
            with self.subTest(target=target):
                if target == "lock":
                    (self.root / "SOURCE_LOCK.json").write_bytes(encoded)
                else:
                    self.replace_evidence_bytes(encoded)
                before = self.snapshot()
                lock_before = copy.deepcopy(self.lock)

                # Exercise actual admission whether decoding succeeds or raises.
                with self.assertRaises(SourceAdmissionError) as refusal:
                    if target == "lock":
                        verify_source_lock(self.root, load_source_lock(self.root))
                    else:
                        verify_source_lock(self.root, self.lock)
                self.assertRegex(str(refusal.exception), r"^source_admission_[a-z_]+$")
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.lock, lock_before)

                # Independently require translation of decoder exhaustion.
                decoder_error = RecursionError("synthetic decoder exhaustion")
                with patch(
                    "source_lock_admission.json.loads",
                    side_effect=decoder_error,
                ) as decoder:
                    with self.assertRaises(SourceAdmissionError) as translated:
                        if target == "lock":
                            load_source_lock(self.root)
                        else:
                            verify_source_lock(self.root, self.lock)
                decoder.assert_called_once()
                self.assertIs(translated.exception.__cause__, decoder_error)
                self.assertEqual(
                    str(translated.exception),
                    "source_admission_lock_json_invalid"
                    if target == "lock"
                    else "source_admission_evidence_json_invalid",
                )
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




# All disposition, payload and build records below are synthetic test fixtures.
# They test the contract only: no independent observation, human approval, source-use right
# or production distribution permission is created by these bytes or hashes.
DISTRIBUTION_FIXTURE_INPUTS = {
    "Cargo.toml": b'[workspace]\nmembers = []\n# synthetic consumer input\n',
    "Cargo.lock": b'version = 4\n# synthetic lock, not a resolved runtime closure\n',
    "apps/desktop/src-tauri/Cargo.toml":
        b'[package]\nname = "synthetic-desktop"\nversion = "0.0.0"\n',
    "crates/native/Cargo.toml":
        b'[package]\nname = "synthetic-native"\nversion = "0.0.0"\n',
}


def distribution_fixture_scope(arch="x64"):
    return {
        "action": "prepare-distribution",
        "component": "motionwright.desktop.main",
        "destination_class": "windows.nsis.capture-include",
        "arch": arch,
        "policy": "motionwright.nsis-main-distribution/1",
    }


def write_distribution_reference(root, relative, data):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {
        "path": relative, "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def distribution_json_bytes(document):
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def pin_distribution_fixture(root, fixture):
    encoded = distribution_json_bytes(fixture["document"])
    (root / fixture["record_path"]).write_bytes(encoded)
    fixture["record_sha256"] = hashlib.sha256(encoded).hexdigest()


def write_distribution_fixture(root, *, status="unresolved", arch="x64"):
    """Write synthetic bytes to an owned test root, never an authentic admission."""
    root.mkdir(parents=True, exist_ok=True)
    entry = write_source_evidence_fixture(root)
    lock = {"schema_version": 1, "dependencies": {"semwright": entry}}
    (root / "SOURCE_LOCK.json").write_bytes(distribution_json_bytes(lock))
    for relative, encoded in DISTRIBUTION_FIXTURE_INPUTS.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)
    source = {
        "source_lock_sha256":
            hashlib.sha256((root / "SOURCE_LOCK.json").read_bytes()).hexdigest(),
        "cargo_inputs": {
            relative: hashlib.sha256(encoded).hexdigest()
            for relative, encoded in DISTRIBUTION_FIXTURE_INPUTS.items()
        },
    }
    scope = distribution_fixture_scope(arch)
    payload_bytes = b"synthetic private fixture bytes; not a Motionwright executable\n"
    payload = {
        "bytes": len(payload_bytes),
        "sha256": hashlib.sha256(payload_bytes).hexdigest(),
    }
    evidence = write_distribution_reference(
        root, "evidence/distribution-review.txt",
        b"SYNTHETIC CONTRACT TEST ONLY. No actual issuer or distribution permission.\n",
    )
    document = {
        "schema": "motionwright.distribution-admission/1",
        "status": status,
        "scope": scope,
        "source": source,
        "review": {
            "issuer": "synthetic-test-fixture-not-an-authority",
            "reference": "synthetic-contract-control/no-actual-permission",
            "basis": "Synthetic parser/capture control only; not an admitted release.",
            "evidence": [evidence],
            "obligations": ["Fixture bytes must not be reported as production permission."],
        },
        "payload": None,
        "build_provenance": None,
    }
    if status == "permitted":
        document["payload"] = payload
        document["build_provenance"] = write_distribution_reference(
            root, "evidence/distribution-build.json",
            distribution_json_bytes({
                "schema": "motionwright.distribution-build/1",
                "source": source, "scope": scope, "payload": payload,
            }),
        )
    fixture = {
        "document": document,
        "record_path": "evidence/distribution-admission.json",
        "payload_bytes": payload_bytes,
    }
    pin_distribution_fixture(root, fixture)
    return fixture


def distribution_fixture_snapshot(root):
    observed = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            observed[relative] = ("link", os.readlink(path))
        elif path.is_dir():
            observed[relative] = ("directory",)
        else:
            observed[relative] = (
                "file", hashlib.sha256(path.read_bytes()).hexdigest(),
                info.st_size, info.st_dev, info.st_ino, info.st_nlink,
                info.st_mtime_ns, info.st_ctime_ns,
            )
    return observed


class DistributionAdmissionTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="motionwright-distribution-admission-")
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name).resolve(strict=True)
        self.root = self.scratch / "consumer"
        self.fixture = write_distribution_fixture(self.root, status="permitted")

    def admission(self, **overrides):
        arguments = {
            "arch": "x64",
            "record_path": self.fixture["record_path"],
            "record_sha256": self.fixture["record_sha256"],
        }
        arguments.update(overrides)
        return verify_distribution_admission(self.root, **arguments)

    def assert_refused_read_only(self, code=None, **overrides):
        before = distribution_fixture_snapshot(self.scratch)
        with self.assertRaises(SourceAdmissionError) as failure:
            self.admission(**overrides)
        if code is not None:
            self.assertEqual(str(failure.exception), code)
        self.assertEqual(distribution_fixture_snapshot(self.scratch), before)

    def repin(self):
        pin_distribution_fixture(self.root, self.fixture)

    def test_synthetic_permitted_control_is_read_only_not_a_real_permission(self):
        before = distribution_fixture_snapshot(self.scratch)
        with (
            patch("subprocess.Popen", side_effect=AssertionError("unexpected process")),
            patch("os.system", side_effect=AssertionError("unexpected shell")),
            patch("socket.socket", side_effect=AssertionError("unexpected network")),
        ):
            self.assertEqual(
                distribution_source_context(self.root),
                self.fixture["document"]["source"],
            )
            self.assertEqual(self.admission(), self.fixture["document"]["payload"])
        self.assertEqual(distribution_fixture_snapshot(self.scratch), before)
        for arch in ("x64", "arm64", "x86"):
            self.assertEqual(distribution_scope(arch), distribution_fixture_scope(arch))

    def test_exact_negative_records_refuse_after_valid_source_and_leave_it_valid(self):
        for status in ("unresolved", "prohibited"):
            with self.subTest(status=status):
                document = self.fixture["document"]
                document["status"] = status
                document["payload"] = None
                document["build_provenance"] = None
                self.repin()
                self.assertEqual(distribution_source_context(self.root), document["source"])
                self.assert_refused_read_only("distribution_admission_" + status)
                self.assertIsNone(verify_source_lock(self.root, load_source_lock(self.root)))

    def test_missing_or_unpinned_record_cannot_supply_disposition(self):
        for overrides in (
            {"record_path": None}, {"record_path": ""},
            {"record_sha256": None}, {"record_sha256": ""},
            {"record_sha256": "0" * 64},
        ):
            with self.subTest(overrides=overrides):
                code = ("distribution_admission_record_hash_mismatch"
                        if overrides.get("record_sha256") == "0" * 64
                        else "distribution_admission_missing")
                self.assert_refused_read_only(code, **overrides)
        path = self.root / self.fixture["record_path"]
        path.write_bytes(path.read_bytes() + b" ")
        self.assert_refused_read_only("distribution_admission_record_hash_mismatch")

    def test_source_input_drift_is_distinct_from_source_consistency_failure(self):
        for relative in ("SOURCE_LOCK.json", *DISTRIBUTION_FIXTURE_INPUTS):
            with self.subTest(relative=relative):
                path = self.root / relative
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b" ")
                    # The source record remains valid, but its exact input bytes drift.
                    self.assertIsNone(verify_source_lock(self.root, load_source_lock(self.root)))
                    self.assertNotEqual(
                        distribution_source_context(self.root),
                        self.fixture["document"]["source"],
                    )
                    self.assert_refused_read_only("distribution_admission_source_mismatch")
                finally:
                    path.write_bytes(original)

    def test_unverified_source_cannot_be_masked_by_a_negative_disposition(self):
        lock = load_source_lock(self.root)
        lock["dependencies"]["semwright"]["verification"]["status"] = "unverified"
        (self.root / "SOURCE_LOCK.json").write_bytes(distribution_json_bytes(lock))
        self.fixture["document"].update(
            status="unresolved", payload=None, build_provenance=None,
        )
        self.repin()
        self.assert_refused_read_only("source_admission_unverified")

    def test_scope_binding_rejects_each_mismatched_caller_dimension(self):
        for field in ("action", "component", "destination_class", "arch", "policy"):
            with self.subTest(field=field):
                original = self.fixture["document"]["scope"][field]
                self.fixture["document"]["scope"][field] = "another-context"
                self.repin()
                self.assert_refused_read_only("distribution_admission_scope_mismatch")
                self.fixture["document"]["scope"][field] = original
        self.repin()
        self.assert_refused_read_only("distribution_admission_scope_mismatch", arch="arm64")
        for arch in (None, True, "AMD64", ""):
            with self.subTest(invalid_arch=arch):
                self.assert_refused_read_only("distribution_admission_arch_invalid", arch=arch)

    def test_record_json_rejects_unknown_missing_duplicate_and_oversized_inputs(self):
        original = copy.deepcopy(self.fixture["document"])
        mutations = [
            {**original, "unexpected": True},
            {key: value for key, value in original.items() if key != "review"},
            {**original, "schema": "motionwright.distribution-admission/2"},
            {**original, "status": "PERMITTED"},
            {**original, "status": True},
        ]
        for document in mutations:
            with self.subTest(document=document):
                self.fixture["document"] = document
                self.repin()
                self.assert_refused_read_only()
        self.fixture["document"] = original
        ordinary = distribution_json_bytes(original)
        duplicate = ordinary.replace(
            b'"status": "permitted"',
            b'"status": "unresolved", "status": "permitted"', 1,
        )
        self.assertNotEqual(duplicate, ordinary)
        for encoded in (duplicate, ordinary + b" " * (64 * 1024)):
            with self.subTest(bytes=len(encoded)):
                (self.root / self.fixture["record_path"]).write_bytes(encoded)
                self.fixture["record_sha256"] = hashlib.sha256(encoded).hexdigest()
                self.assert_refused_read_only()
        self.repin()

    def test_review_is_explicit_bounded_and_does_not_execute_its_text(self):
        original = copy.deepcopy(self.fixture["document"]["review"])
        variants = []
        for field in ("issuer", "reference", "basis"):
            variants.append({**original, field: ""})
        variants.extend([
            {**original, "evidence": []},
            {**original, "evidence": original["evidence"] * 17},
            {**original, "obligations": [True]},
            {**original, "obligations": ["x"] * 17},
            {**original, "obligations": ["x" * 2049]},
            {key: value for key, value in original.items() if key != "obligations"},
            {**original, "approved": True},
        ])
        for review in variants:
            with self.subTest(review=review):
                self.fixture["document"]["review"] = review
                self.repin()
                self.assert_refused_read_only("distribution_admission_review_invalid")
        self.fixture["document"]["review"] = original
        self.repin()
        reference = original["evidence"][0]
        path = self.root / reference["path"]
        encoded = path.read_bytes()
        path.write_bytes(bytes([encoded[0] ^ 1]) + encoded[1:])
        self.assert_refused_read_only("distribution_admission_reference_mismatch")

    def test_evidence_identity_and_portable_record_path_cannot_be_substituted(self):
        review = self.fixture["document"]["review"]
        original = copy.deepcopy(review["evidence"][0])
        for reference in (
            {**original, "bytes": True},
            {**original, "bytes": 64 * 1024 + 1},
            {**original, "sha256": "0" * 64},
        ):
            with self.subTest(reference=reference):
                review["evidence"] = [reference]
                self.repin()
                self.assert_refused_read_only()
        review["evidence"] = [original, original]
        self.repin()
        self.assert_refused_read_only("distribution_admission_reference_reused")
        review["evidence"] = [original]
        self.repin()
        record = self.root / self.fixture["record_path"]
        (self.scratch / "outside.json").write_bytes(record.read_bytes())
        for relative in (str(record), "../outside.json", "evidence/../evidence/distribution-admission.json"):
            with self.subTest(record_path=relative):
                self.assert_refused_read_only(record_path=relative)

    def test_permitted_requires_a_bounded_payload_and_actual_pinned_build_record(self):
        original = copy.deepcopy(self.fixture["document"])
        payload = original["payload"]
        for invalid in (
            None, {**payload, "bytes": 0}, {**payload, "bytes": True},
            {**payload, "bytes": 2_147_483_648},
            {**payload, "sha256": "G" * 64},
            {**payload, "unexpected": "field"},
        ):
            with self.subTest(payload=invalid):
                self.fixture["document"] = {**original, "payload": invalid}
                self.repin()
                self.assert_refused_read_only("distribution_admission_payload_invalid")
        self.fixture["document"] = {**original, "build_provenance": None}
        self.repin()
        self.assert_refused_read_only("distribution_admission_reference_invalid")

    def test_rehashed_build_evidence_must_bind_the_same_source_scope_and_payload(self):
        original = copy.deepcopy(self.fixture["document"])
        relative = original["build_provenance"]["path"]
        build = json.loads((self.root / relative).read_bytes())
        for field in ("schema", "source", "scope", "payload"):
            with self.subTest(field=field):
                changed = copy.deepcopy(build)
                if field == "schema":
                    changed[field] = "motionwright.distribution-build/2"
                elif field == "source":
                    changed[field]["source_lock_sha256"] = "0" * 64
                elif field == "scope":
                    changed[field]["arch"] = "arm64"
                else:
                    changed[field]["sha256"] = "0" * 64
                reference = write_distribution_reference(
                    self.root, relative, distribution_json_bytes(changed),
                )
                self.fixture["document"] = {**original, "build_provenance": reference}
                self.repin()
                self.assert_refused_read_only("distribution_admission_build_mismatch")

    def test_negative_record_cannot_carry_positive_payload_or_build_authority(self):
        original = copy.deepcopy(self.fixture["document"])
        for field in ("payload", "build_provenance"):
            with self.subTest(field=field):
                self.fixture["document"] = {
                    **original, "status": "unresolved",
                    "payload": None, "build_provenance": None,
                }
                self.fixture["document"][field] = original[field]
                self.repin()
                self.assert_refused_read_only("distribution_admission_negative_record_invalid")


if __name__ == "__main__":
    unittest.main()
