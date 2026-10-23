"""Control fixtures for a retained-artifact observation; no Apple commands run.

Real symbolic-link cases require a host able to create those filesystem objects.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
import plistlib
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import warnings
import zipfile

try:
    from tooling import ci_macos_package_metadata as gate
except ModuleNotFoundError as error:
    if error.name != "tooling":
        raise
    import ci_macos_package_metadata as gate


def environment():
    return {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "MW_MAC_METADATA_SCOPE": gate.SCOPE, "RUNNER_OS": "macOS",
            "GITHUB_REPOSITORY": gate.REPOSITORY, "GITHUB_JOB": "macos-package-metadata",
            "GITHUB_SHA": "a" * 40, "GITHUB_WORKFLOW_SHA": "a" * 40,
            "GITHUB_WORKFLOW_REF": gate.REPOSITORY + "/" + gate.WORKFLOW + "@refs/heads/main",
            "GITHUB_RUN_ID": "900", "GITHUB_RUN_ATTEMPT": "1"}


def api_fixture():
    artifact = {"id": gate.ARTIFACT, "name": "motionwright-macos-candidate",
                "size_in_bytes": gate.ZIP_BYTES, "digest": "sha256:" + gate.ZIP_SHA, "expired": False,
                "workflow_run": {"id": gate.RUN, "repository_id": gate.REPOSITORY_ID,
                                 "head_repository_id": gate.REPOSITORY_ID,
                                 "head_sha": gate.SOURCE, "head_branch": "main"}}
    run = {"id": gate.RUN, "run_attempt": 1, "head_sha": gate.SOURCE, "head_branch": "main",
           "event": "push", "status": "completed", "conclusion": "success",
           "path": ".github/workflows/package-candidate.yml",
           "repository": {"id": gate.REPOSITORY_ID, "full_name": gate.REPOSITORY}}
    job = {"id": gate.JOB, "run_id": gate.RUN, "run_attempt": 1, "head_sha": gate.SOURCE,
           "name": "macos", "status": "completed", "conclusion": "success"}
    return artifact, run, job


def receipt_fixture():
    source = {"application": "Motionwright", "arch": "arm64", "platform": "macos",
              "semwright_revision": gate.SDK, "source_revision": gate.SOURCE, "version": "0.1.0"}
    size, digest = gate.MEMBERS[gate.DMG_MEMBER]
    record = {"bundle_kind": "dmg", "bytes": size, "filename": gate.DMG_NAME, "sha256": digest}
    candidate = {"schema_version": 1, "candidate": source, "artifacts": [record],
                 "claims": {"published": False, "human_install_acceptance": "NOT_RUN",
                            "distribution_signature": "NOT_CLAIMED", "notarization": "NOT_CLAIMED"}}
    smoke = {"schema_version": 1, "candidate": dict(source), "bundle_kind": "dmg",
             "candidate_archive": {key: record[key] for key in ("bytes", "filename", "sha256")},
             "automated_inspection": {
                 "status": "PASS", "method": "dmg_readonly_mount_copy", "gui_window_probe": "NOT_RUN",
                 "bundle_executable": "motionwright-desktop", "bundle_identifier": "com.seradotcom.motionwright",
                 "binary": {"binary_bytes": gate.BINARY[0], "binary_sha256": gate.BINARY[1],
                            "binary_filename": "motionwright-desktop", "binary_format": "MACHO64_arm64"}},
             "claims": {"release_published": False, "human_install_acceptance": "NOT_RUN",
                        "signing": "NOT_CLAIMED", "notarization": "NOT_CLAIMED"}}
    return candidate, smoke


def archive_fixture(names=("safe.bin",), mode=stat.S_IFREG | 0o600):
    stream = io.BytesIO()
    records = {}
    with zipfile.ZipFile(stream, "w") as archive, warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        for name in names:
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            info.create_system = 3
            data = b"original candidate bytes"
            archive.writestr(info, data)
            records[name] = (len(data), hashlib.sha256(data).hexdigest())
    raw = stream.getvalue()
    return raw, hashlib.sha256(raw).hexdigest(), records


class ExecutionBoundaryTests(unittest.TestCase):
    def test_real_selected_context_keeps_product_and_observer_source_separate(self):
        result = gate.execution_context(environment(), "darwin")
        self.assertEqual(result["product_source_sha"], gate.SOURCE)
        self.assertEqual(result["observer_source_sha"], "a" * 40)
        self.assertNotEqual(result["product_source_sha"], result["observer_source_sha"])

    def test_non_macos_implicit_or_foreign_workflow_context_is_refused(self):
        changes = {"RUNNER_OS": "Windows", "GITHUB_EVENT_NAME": "push",
                   "MW_MAC_METADATA_SCOPE": "all", "GITHUB_ACTIONS": "false",
                   "GITHUB_REPOSITORY": "foreign/repository", "GITHUB_JOB": "macos",
                   "GITHUB_SHA": "b" * 40, "GITHUB_WORKFLOW_REF": "foreign/workflow@main"}
        for key, value in changes.items():
            with self.subTest(key=key), self.assertRaises(gate.custody.VerificationError):
                gate.execution_context({**environment(), key: value}, "darwin")
        with self.assertRaises(gate.custody.VerificationError):
            gate.execution_context(environment(), "win32")

    def test_missing_malformed_and_retried_run_identities_are_refused(self):
        for key, value in (("GITHUB_RUN_ID", ""), ("GITHUB_RUN_ID", "0"),
                           ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_SHA", "main"),
                           ("GITHUB_WORKFLOW_SHA", "a" * 39)):
            with self.subTest(key=key, value=value), self.assertRaises(gate.custody.VerificationError):
                gate.execution_context({**environment(), key: value}, "darwin")

    def test_unknown_metadata_stops_remaining_observations_without_replay(self):
        calls = []
        def command(label, argv, timeout):
            calls.append(label)
            if len(calls) == 3:
                raise gate.observer.UnknownExecution("uncertain", {"observation": "UNKNOWN_TIMEOUT_NO_RETRY"})
            return {"exit_code": 0}
        with self.assertRaises(gate.observer.UnknownExecution):
            gate.observe_commands(command, Path("app"), Path("dmg"))
        self.assertEqual(calls, ["host", "architecture", "xcrun-version"])

    def test_nonzero_metadata_exit_is_retained_without_unsigned_or_acceptance_inference(self):
        values = gate.observe_commands(lambda label, argv, timeout: {"exit_code": 1}, Path("app"), Path("dmg"))
        self.assertEqual(len(values), 12)
        self.assertTrue(all(row["exit_code"] == 1 for row in values.values()))
        self.assertTrue(all(row["interpretation"] == "ORIGINAL_TOOL_OUTPUT_REQUIRES_REVIEW" for row in values.values()))

    def test_missing_and_boolean_exit_never_completes_metadata(self):
        for row in ({}, {"exit_code": None}, {"exit_code": False}):
            with self.subTest(row=row), self.assertRaises(gate.custody.VerificationError):
                gate.observe_commands(lambda *args: row, Path("app"), Path("dmg"))

    def test_selected_commands_cannot_launch_sign_submit_or_alter_security_policy(self):
        rows = gate.observation_commands(Path("owned/app"), Path("owned/file.dmg"))
        self.assertEqual(len({name for name, _ in rows}), 12)
        allowed = {"/usr/bin/sw_vers", "/usr/bin/uname", "/usr/bin/xcrun",
                   "/usr/bin/xcodebuild", "/usr/bin/codesign", "/usr/sbin/spctl"}
        for _, argv in rows:
            self.assertIn(argv[0], allowed)
            self.assertTrue(set(argv).isdisjoint({"--sign", "-s", "staple",
                                                 "notarytool", "submit", "--master-disable",
                                                 "--global-disable", "--add", "--remove"}))


class OriginalAuthorityTests(unittest.TestCase):
    def test_exact_original_api_identities_are_admitted(self):
        gate.verify_api(*api_fixture())

    def test_other_source_run_attempt_job_and_artifact_are_refused(self):
        changes = ((0, "id", gate.ARTIFACT + 1), (0, "digest", "sha256:" + "0" * 64),
                   (0, "expired", True), (0, "size_in_bytes", gate.ZIP_BYTES - 1),
                   (1, "id", gate.RUN + 1), (1, "head_sha", "a" * 40),
                   (1, "run_attempt", 2), (1, "event", "pull_request"),
                   (2, "id", gate.JOB + 1), (2, "head_sha", "a" * 40),
                   (2, "run_id", gate.RUN + 1), (2, "run_attempt", 2))
        for index, key, value in changes:
            rows = api_fixture()
            rows[index][key] = value
            with self.subTest(index=index, key=key), self.assertRaises(gate.custody.VerificationError):
                gate.verify_api(*rows)

    def test_skipped_cancelled_or_incomplete_original_jobs_cannot_be_promoted(self):
        for index in (1, 2):
            for state in ("skipped", "cancelled", "failure", None):
                rows = api_fixture()
                rows[index]["conclusion"] = state
                with self.subTest(index=index, state=state), self.assertRaises(gate.custody.VerificationError):
                    gate.verify_api(*rows)
            rows = api_fixture()
            rows[index]["status"] = "in_progress"
            with self.assertRaises(gate.custody.VerificationError):
                gate.verify_api(*rows)

    def test_numeric_aliases_and_foreign_repositories_cannot_authorize_an_original(self):
        for index, key in ((0, "id"), (0, "size_in_bytes"), (1, "run_attempt"), (2, "run_attempt")):
            for value in (True, float(api_fixture()[index][key])):
                rows = api_fixture()
                rows[index][key] = value
                with self.subTest(index=index, key=key, value=value), self.assertRaises(gate.custody.VerificationError):
                    gate.verify_api(*rows)
        rows = api_fixture()
        rows[0]["workflow_run"]["head_repository_id"] += 1
        with self.assertRaises(gate.custody.VerificationError):
            gate.verify_api(*rows)

    def test_exact_receipts_keep_bounded_prior_inspection_distinct_from_metadata(self):
        gate.verify_receipts(*receipt_fixture())

    def test_receipt_source_sdk_binary_or_archive_substitution_is_refused(self):
        for change in ("source", "sdk", "binary", "archive"):
            candidate, smoke = receipt_fixture()
            if change == "source":
                candidate["candidate"]["source_revision"] = "a" * 40
            elif change == "sdk":
                smoke["candidate"]["semwright_revision"] = "b" * 40
            elif change == "binary":
                smoke["automated_inspection"]["binary"]["binary_sha256"] = "0" * 64
            else:
                smoke["candidate_archive"]["sha256"] = "0" * 64
            with self.subTest(change=change), self.assertRaises(gate.custody.VerificationError):
                gate.verify_receipts(candidate, smoke)

    def test_prior_receipts_cannot_gain_human_or_signing_claims(self):
        for index, key, value in ((0, "human_install_acceptance", "PASS"),
                                  (0, "published", True), (0, "distribution_signature", "SIGNED"),
                                  (1, "notarization", "PASS"), (1, "release_published", True)):
            rows = receipt_fixture()
            rows[index]["claims"][key] = value
            with self.subTest(index=index, key=key), self.assertRaises(gate.custody.VerificationError):
                gate.verify_receipts(*rows)

    def test_original_json_rejects_duplicate_fields_and_nonobject_roots(self):
        for raw in (b'{"a":1,"a":2}', b"[]", b"null", b'{"a":NaN}'):
            with self.subTest(raw=raw), self.assertRaises((ValueError, gate.custody.VerificationError)):
                gate.strict_object(raw)


class ArchiveAndMountTests(unittest.TestCase):
    def test_exact_complete_archive_is_read_without_extracting_paths(self):
        raw, digest, members = archive_fixture()
        self.assertEqual(set(gate.read_zip(raw, digest, members)), {"safe.bin"})

    def test_rebound_zip_digest_does_not_hide_wrong_member_hash_or_size(self):
        raw, digest, members = archive_fixture()
        for expectation in ((1, members["safe.bin"][1]), (members["safe.bin"][0], "0" * 64)):
            with self.subTest(expectation=expectation), self.assertRaises(gate.custody.VerificationError):
                gate.read_zip(raw, digest, {"safe.bin": expectation})
        with self.assertRaises(gate.custody.VerificationError):
            gate.read_zip(raw + b"changed", digest, members)

    def test_unexpected_missing_duplicate_and_link_members_are_refused(self):
        raw, digest, members = archive_fixture(("one", "two"))
        with self.assertRaises(gate.custody.VerificationError):
            gate.read_zip(raw, digest, {"one": members["one"]})
        for names, mode in ((("same", "same"), stat.S_IFREG | 0o600),
                            (("link",), stat.S_IFLNK | 0o777)):
            raw, digest, members = archive_fixture(names, mode)
            with self.subTest(names=names), self.assertRaises(gate.custody.VerificationError):
                gate.read_zip(raw, digest, members)

    def test_paths_cannot_escape_even_when_external_digest_is_rebound(self):
        for name in ("../escape", "/absolute", "dir/../../escape", "dir\\escape"):
            raw, digest, members = archive_fixture((name,))
            with self.subTest(name=name), self.assertRaises(gate.custody.VerificationError):
                gate.read_zip(raw, digest, members)

    def test_attach_requires_one_owned_mount_and_its_device(self):
        mount = Path("owned-mount")
        raw = plistlib.dumps({"system-entities": [{"dev-entry": "/dev/disk4s1", "mount-point": str(mount)}]})
        self.assertEqual(gate.mounted_device(raw, mount), "/dev/disk4s1")
        for items in ([], [{"dev-entry": "/dev/disk4s1", "mount-point": "other"}],
                      [{"dev-entry": "/dev/disk4s1", "mount-point": str(mount)}] * 2,
                      [{"dev-entry": "/dev/disk4s1", "mount-point": str(mount)},
                       {"dev-entry": "/dev/disk5s1", "mount-point": "foreign"}],
                      [{"dev-entry": "foreign", "mount-point": str(mount)}]):
            with self.subTest(items=items), self.assertRaises(gate.custody.VerificationError):
                gate.mounted_device(plistlib.dumps({"system-entities": items}), mount)

    def test_bundle_executable_escape_and_changed_binary_are_refused(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            app = root / "Motionwright.app"
            contents = app / "Contents"
            binary_dir = contents / "MacOS"
            binary_dir.mkdir(parents=True)
            binary = binary_dir / "motionwright-desktop"
            binary.write_bytes(b"bounded synthetic Mach-O")
            info = {"CFBundleExecutable": "motionwright-desktop", "CFBundleIdentifier": "com.seradotcom.motionwright",
                    "CFBundleShortVersionString": "0.1.0"}
            plist = contents / "Info.plist"
            plist.write_bytes(plistlib.dumps(info))
            expected = (binary.stat().st_size, hashlib.sha256(binary.read_bytes()).hexdigest())
            with mock.patch.object(gate, "BINARY", expected):
                gate.verify_bundle(app, root)
                info["CFBundleExecutable"] = "../../foreign"
                plist.write_bytes(plistlib.dumps(info))
                with self.assertRaises(gate.custody.VerificationError):
                    gate.verify_bundle(app, root)
                info["CFBundleExecutable"] = "motionwright-desktop"
                plist.write_bytes(plistlib.dumps(info))
                binary.write_bytes(b"substituted binary")
                with self.assertRaises(gate.custody.VerificationError):
                    gate.verify_bundle(app, root)

    def test_readonly_mount_requires_the_observed_block_device_and_immutable_filesystem(self):
        mount = mock.Mock()
        mount.is_dir.return_value = True
        mount.is_symlink.return_value = False
        mount.lstat.return_value = SimpleNamespace(st_dev=7, st_ino=9, st_mode=stat.S_IFDIR | 0o555)
        block = SimpleNamespace(st_dev=1, st_ino=3, st_rdev=7, st_mode=stat.S_IFBLK | 0o600)
        with mock.patch.object(gate.os.path, "ismount", return_value=True), \
             mock.patch.object(gate.os, "stat", return_value=block), \
             mock.patch.object(gate.os, "statvfs", return_value=SimpleNamespace(f_flag=1), create=True), \
             mock.patch.object(gate.os, "ST_RDONLY", 1, create=True):
            self.assertTrue(gate.readonly_mount_identity(mount, "/dev/disk4s1")["readonly"])
            with mock.patch.object(gate.os, "statvfs", return_value=SimpleNamespace(f_flag=0)):
                with self.assertRaises(gate.custody.VerificationError):
                    gate.readonly_mount_identity(mount, "/dev/disk4s1")
            block.st_rdev = 8
            with self.assertRaises(gate.custody.VerificationError):
                gate.readonly_mount_identity(mount, "/dev/disk4s1")
            block.st_rdev = 7
            block.st_mode = stat.S_IFREG | 0o600
            with self.assertRaises(gate.custody.VerificationError):
                gate.readonly_mount_identity(mount, "/dev/disk4s1")
            block.st_mode = stat.S_IFBLK | 0o600
            mount.is_symlink.return_value = True
            with self.assertRaises(gate.custody.VerificationError):
                gate.readonly_mount_identity(mount, "/dev/disk4s1")

    def test_writable_copy_outside_original_mount_never_reaches_metadata_tools(self):
        command = mock.Mock()
        with self.assertRaises(gate.custody.VerificationError):
            gate.observe_mounted_commands(command, Path("copy/Motionwright.app"), Path("original.dmg"),
                                          Path("mount"), "/dev/disk4s1", {})
        command.assert_not_called()
        source = (Path(gate.__file__).resolve()).read_text(encoding="utf-8")
        self.assertNotIn("copy-owned-app", source)
        self.assertNotIn("/usr/bin/ditto", source)

    def test_mount_change_stops_metadata_sequence_after_first_observed_command(self):
        mount = Path("original-mount")
        original = {"device": "/dev/disk4s1", "readonly": True, "identity": [7, 9, 1]}
        changed = {**original, "identity": [8, 10, 1]}
        command = mock.Mock(return_value={"exit_code": 0})
        with mock.patch.object(gate, "verify_bundle", return_value={"synthetic": True}), \
             mock.patch.object(gate, "bundle_resource_inventory", return_value={"synthetic": True}), \
             mock.patch.object(gate, "readonly_mount_identity", side_effect=[original, original, changed]):
            with self.assertRaises(gate.custody.VerificationError):
                gate.observe_mounted_commands(command, mount / "Motionwright.app", Path("original.dmg"),
                                              mount, "/dev/disk4s1", original)
        self.assertEqual(command.call_count, 1)
        self.assertEqual(command.call_args[0][0], "host")
        command.reset_mock()
        with mock.patch.object(gate, "readonly_mount_identity", return_value=changed):
            with self.assertRaises(gate.custody.VerificationError):
                gate.observe_mounted_commands(command, mount / "Motionwright.app", Path("original.dmg"),
                                              mount, "/dev/disk4s1", original)
        command.assert_not_called()

    def bundle_boundary_fixture(self, name):
        mount = Path(name).absolute() / "mount"
        app = mount / "Motionwright.app"
        (app / "Contents").mkdir(parents=True)
        info = mount.lstat()
        return mount, app, {"identity": [info.st_dev, info.st_ino, info.st_mode]}

    def test_bundle_boundary_keeps_real_internal_framework_links(self):
        with tempfile.TemporaryDirectory() as name:
            mount, app, expected = self.bundle_boundary_fixture(name)
            framework = app / "Contents/Frameworks/Example.framework"
            version = framework / "Versions/A"
            (version / "Resources").mkdir(parents=True)
            (version / "Example").write_bytes(b"bounded framework fixture")
            (version / "Resources/info.txt").write_text("resource", encoding="utf-8")
            (framework / "Versions/Current").symlink_to("A", target_is_directory=True)
            (framework / "Example").symlink_to("Versions/Current/Example")
            (framework / "Resources").symlink_to("Versions/Current/Resources", target_is_directory=True)
            result = gate.bundle_resource_inventory(app, mount, expected)
            links = [row for row in result["entries"] if row["kind"] == "symlink"]
            self.assertEqual(len(links), 3)
            self.assertTrue(all(row["resolved"].startswith("Motionwright.app/") for row in links))
            self.assertEqual(result, gate.bundle_resource_inventory(app, mount, expected))
            self.assertFalse(result["atomic_snapshot"])

    def test_bundle_boundary_rejects_real_external_dangling_and_cyclic_links(self):
        for kind in ("external", "dangling", "cycle", "directory-cycle"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as name:
                mount, app, expected = self.bundle_boundary_fixture(name)
                contents = app / "Contents"
                if kind == "external":
                    foreign = Path(name).absolute() / "foreign.txt"
                    foreign.write_bytes(b"outside original mounted volume")
                    (contents / "_CodeSignature").mkdir()
                    (contents / "_CodeSignature/CodeResources").symlink_to(foreign)
                elif kind == "dangling":
                    (contents / "resource").symlink_to("absent")
                elif kind == "cycle":
                    (contents / "one").symlink_to("two")
                    (contents / "two").symlink_to("one")
                else:
                    (contents / "loop").symlink_to(".", target_is_directory=True)
                with self.assertRaises((gate.custody.VerificationError, FileNotFoundError)):
                    gate.bundle_resource_inventory(app, mount, expected)

    def test_bundle_boundary_rejects_injected_nested_device_change(self):
        with tempfile.TemporaryDirectory() as name:
            mount, app, expected = self.bundle_boundary_fixture(name)
            nested = app / "Contents/Nested"
            nested.mkdir()
            original_lstat = Path.lstat
            def observed(path, *args, **kwargs):
                info = original_lstat(path, *args, **kwargs)
                if path == nested:
                    return SimpleNamespace(st_dev=info.st_dev + 1)
                return info
            with mock.patch.object(Path, "lstat", autospec=True, side_effect=observed):
                with self.assertRaisesRegex(gate.custody.VerificationError, "device boundary"):
                    gate.bundle_resource_inventory(app, mount, expected)

    def test_bundle_boundary_budget_stops_before_any_metadata_command(self):
        with tempfile.TemporaryDirectory() as name:
            mount, app, expected = self.bundle_boundary_fixture(name)
            (app / "Contents/resource.txt").write_bytes(b"resource")
            command = mock.Mock()
            with mock.patch.object(gate, "BUNDLE_ENTRY_LIMIT", 2), \
                 mock.patch.object(gate, "readonly_mount_identity", return_value=expected):
                with self.assertRaisesRegex(gate.custody.VerificationError, "entry budget"):
                    gate.observe_mounted_commands(command, app, Path(name) / "original.dmg",
                                                  mount, "/dev/disk4s1", expected)
            command.assert_not_called()

    def test_real_nonlinked_bundle_and_external_signature_parent_boundary(self):
        with tempfile.TemporaryDirectory() as name:
            mount, app, expected = self.bundle_boundary_fixture(name)
            signature = app / "Contents/_CodeSignature"
            signature.mkdir()
            (signature / "CodeResources").write_bytes(b"original signing resources")
            positive = gate.bundle_resource_inventory(app, mount, expected)
            self.assertTrue(any(row["path"].endswith("_CodeSignature/CodeResources")
                                and row["kind"] == "file" for row in positive["entries"]))
            foreign = Path(name).absolute() / "foreign-signature"
            foreign.mkdir()
            (foreign / "CodeResources").write_bytes(b"external signing resources")
            signature.rename(app / "Contents/RetainedOriginalSignature")
            if gate.os.name == "nt":
                made = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(signature),
                                       str(foreign)], capture_output=True, timeout=10, check=False)
                self.assertEqual(made.returncode, 0, repr((made.stdout, made.stderr)))
            else:
                signature.symlink_to(foreign, target_is_directory=True)
            try:
                with self.assertRaises(gate.custody.VerificationError):
                    gate.bundle_resource_inventory(app, mount, expected)
            finally:
                if gate.os.name == "nt":
                    signature.rmdir()  # Removes only the junction, never its target.
                else:
                    signature.unlink()
            self.assertEqual((foreign / "CodeResources").read_bytes(), b"external signing resources")

    def test_launch_intent_is_exclusive_and_does_not_replace_an_existing_attempt(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "intent.json"
            gate.write_intent(path, {"no_retry": True})
            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                gate.write_intent(path, {"no_retry": False})
            self.assertEqual(path.read_bytes(), original)

    def test_workflow_only_manual_inspection_and_both_g0_entrypoints(self):
        source = (Path(gate.__file__).resolve().parents[1] / gate.WORKFLOW).read_text(encoding="utf-8")
        self.assertIn("github.event_name == 'workflow_dispatch'", source)
        self.assertNotIn("&& github.run_attempt", source)  # A retry fails preflight instead of skipping to green.
        self.assertIn("--mode both --require-complete", source)
        self.assertIn("cancel-in-progress: false", source)
        self.assertIn("if-no-files-found: error", source)
        self.assertIn("actions: read", source)
        for forbidden in ("tauri build", "cargo build", "npm ci", "codesign --sign", "notarytool submit",
                          "xattr ", "stapler staple", "sudo ", "permissions: write-all"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
