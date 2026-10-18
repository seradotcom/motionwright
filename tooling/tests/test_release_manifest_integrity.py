"""Adversarial source-bound v0.1 delivery manifest tests.

Synthetic, fresh local Git repositories only. No native runtime, real owner
connections, private acceptance data, signing key or publisher required.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_manifest as manifest


def _git(root: Path, *args: str) -> str:
    run = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True,
        text=True, timeout=12, check=True,
    )
    return run.stdout.strip()


class DeliveryIntegrityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mw-v01-delivery-tests-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "source"
        self.root.mkdir()
        contents = {
            "Cargo.toml": '[workspace.package]\nrepository = "https://example.invalid/motionwright"\nversion = "0.1.0"\nlicense = "AGPL-3.0-or-later"\n',
            "Cargo.lock": "# synthetic fixed lock\n",
            "SOURCE_LOCK.json": json.dumps({"dependencies": {"semwright": {
                "repository": "https://example.invalid/semwright",
                "revision": "b" * 40, "native_sdk_package": "semwright-client",
                "license_observed": "AGPL-3.0",
                "features_planned": ["native-sdk"],
            }}}),
            "LICENSE": "fixture agpl license\n",
            "THIRD_PARTY_NOTICES.md": "fixture notice\n",
            "apps/studio/package-lock.json": '{"lockfileVersion":3}',
            "apps/desktop/src-tauri/tauri.conf.json": "{}",
            "docs/acceptance/requirements-status.json": "{}",
            "docs/acceptance/acceptance-status.json": "{}",
            "docs/security/known-rustsec-advisories.json": "{}",
        }
        for relative, content in contents.items():
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        _git(self.root, "init", "-q")
        _git(self.root, "add", ".")
        _git(self.root, "-c", "user.name=fixture", "-c",
             "user.email=fixture@example.invalid", "commit", "-qm", "baseline")
        self.sha = _git(self.root, "rev-parse", "HEAD")
        self.output_dir = self.root / "ci-evidence"
        self.output_dir.mkdir()
        self.output = self.output_dir / "delivery-manifest.json"

    def invoke(self, sha=None, output=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = manifest.main(
                ["--git-sha", sha if sha is not None else self.sha,
                 "--output", str(output or self.output)],
                root=self.root,
            )
        return result, stdout.getvalue(), stderr.getvalue()

    def test_clean_exact_checkout_is_bound_and_unreleased(self):
        code, stdout, stderr = self.invoke()
        self.assertEqual((code, stderr), (0, ""))
        doc = json.loads(self.output.read_text())
        self.assertEqual(doc["schema_version"], 1)
        self.assertEqual(doc["motionwright"]["source_revision"], self.sha)
        self.assertEqual(doc["semwright"]["revision"], "b" * 40)
        self.assertFalse(doc["delivery_boundary"]["automatic_publish"])
        self.assertFalse(doc["delivery_boundary"]["project_receipts_exported"])
        self.assertEqual(set(doc["locked_inputs"]), set(manifest.LOCKED_INPUTS))
        for name, field in doc["locked_inputs"].items():
            self.assertEqual(field["sha256"],
                             hashlib.sha256((self.root / name).read_bytes()).hexdigest())
        self.assertNotIn(str(self.root), stdout)

    def test_relative_output_under_checkout_still_works_for_ci(self):
        destination = self.root / "ci-evidence" / "portable-source-proof.json"
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = manifest.main(
                ["--git-sha", self.sha,
                 "--output", "ci-evidence/portable-source-proof.json"],
                root=self.root,
            )
        self.assertEqual((code, stderr.getvalue()), (0, ""))
        self.assertTrue(destination.is_file())
        self.assertEqual(json.loads(destination.read_text())["motionwright"]["source_revision"],
                         self.sha)

    def test_ntfs_reparse_output_parent_refused_without_writing(self):
        original = Path.lstat
        parent = self.output_dir

        def forged(path, *args, **kwargs):
            st = original(path, *args, **kwargs)
            if path == parent:
                from types import SimpleNamespace
                return SimpleNamespace(st_mode=st.st_mode,
                                       st_file_attributes=0x400)
            return st

        doc = manifest.build_manifest(self.root, self.sha)
        with patch.object(Path, "lstat", autospec=True, side_effect=forged):
            with self.assertRaisesRegex(
                manifest.ManifestError, "manifest_output_link_refused"
            ):
                manifest.write_new_manifest(self.root, self.output, doc)
        self.assertFalse(self.output.exists())

    def test_old_forged_source_sha_is_rejected_without_a_manifest(self):
        code, stdout, stderr = self.invoke("a" * 40)
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("source_checkout_sha_mismatch", stderr)
        self.assertFalse(self.output.exists())
        self.assertNotIn(str(self.root), stderr)

    def test_non_sha_input_fails_closed(self):
        code, stdout, stderr = self.invoke("main")
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("manifest_sha_invalid", stderr)
        self.assertFalse(self.output.exists())

    def test_tracked_lockfile_edit_cannot_be_reported_as_clean(self):
        (self.root / "Cargo.lock").write_text("# modified after source commit")
        code, stdout, stderr = self.invoke()
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("source_metadata_dirty", stderr)
        self.assertFalse(self.output.exists())

    def test_tracked_source_manifest_path_must_be_regular(self):
        license_path = self.root / "LICENSE"
        license_path.unlink()
        license_path.mkdir()
        with self.assertRaises(manifest.ManifestError):
            manifest._stable_read(self.root, "LICENSE")

    def test_unexpected_input_name_not_accepted(self):
        with self.assertRaisesRegex(manifest.ManifestError, "manifest_input_unexpected"):
            manifest._stable_read(self.root, "owner/private-session")

    def test_a_reparse_point_in_input_ancestor_is_refused(self):
        original = Path.lstat
        ancestor = self.root / "apps" / "studio"

        def simulated(path, *args, **kwargs):
            st = original(path, *args, **kwargs)
            if path == ancestor:
                return type("FakeStat", (), {
                    "st_mode": st.st_mode,
                    "st_file_attributes": 0x400,
                })()
            return st

        with patch.object(Path, "lstat", autospec=True, side_effect=simulated):
            with self.assertRaisesRegex(manifest.ManifestError, "manifest_input_link_refused"):
                manifest._stable_read(self.root, "apps/studio/package-lock.json")

    def test_input_file_size_limit_is_enforced_before_read(self):
        with patch.object(manifest, "MAX_SOURCE_BYTES", 3):
            with self.assertRaisesRegex(manifest.ManifestError, "manifest_input_bounds"):
                manifest._stable_read(self.root, "Cargo.lock")

    def test_duplicate_source_lock_authority_rejected(self):
        original = (self.root / "SOURCE_LOCK.json").read_text()
        doubled = original.replace('"revision":', '"revision": "0", "revision":', 1)
        (self.root / "SOURCE_LOCK.json").write_text(doubled)
        # Commit the malformed but clean fixture to exercise parser independently.
        _git(self.root, "add", "SOURCE_LOCK.json")
        _git(self.root, "-c", "user.name=fixture", "-c",
             "user.email=fixture@example.invalid", "commit", "-qm", "malformed")
        newer = _git(self.root, "rev-parse", "HEAD")
        code, stdout, stderr = self.invoke(newer)
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("manifest_input_duplicate_json_key", stderr)

    def test_output_exists_cannot_be_overwritten(self):
        self.output.write_bytes(b"previous-private-or-review-evidence")
        code, stdout, stderr = self.invoke()
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("manifest_output_refused", stderr)
        self.assertEqual(self.output.read_bytes(), b"previous-private-or-review-evidence")

    def test_linked_output_parent_is_refused_without_output(self):
        outsider = self.root / "outside"
        outsider.mkdir()
        try:
            (self.root / "linked").symlink_to(outsider, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation disallowed by OS policy")
        code, stdout, stderr = self.invoke(output=self.root / "linked" / "manifest.json")
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("manifest_output_link_refused", stderr)
        self.assertFalse((outsider / "manifest.json").exists())

    @unittest.skipUnless(os.name == "nt", "NTFS junction requires Windows")
    def test_actual_temporary_junction_output_parent_is_refused(self):
        external = self.root / "safe-target"
        external.mkdir()
        alias = self.root / "actual-junction"
        try:
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(external)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=8, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            self.skipTest("Junction creation unavailable")
        if result.returncode != 0:
            self.skipTest("Unprivileged NTFS junction creation unavailable")
        try:
            code, stdout, stderr = self.invoke(output=alias / "receipt.json")
            self.assertEqual((code, stdout), (2, ""))
            self.assertIn("manifest_output_link_refused", stderr)
            self.assertFalse((external / "receipt.json").exists())
        finally:
            alias.rmdir()

    def test_missing_output_parent_refused(self):
        code, stdout, stderr = self.invoke(output=self.root / "no-directory" / "receipt.json")
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("manifest_output_parent_unavailable", stderr)

    def test_an_untracked_replaced_input_must_fail_checkout_gate(self):
        original = self.root / "LICENSE"
        original.unlink()
        _git(self.root, "rm", "-q", "--cached", "LICENSE")
        code, stdout, stderr = self.invoke()
        self.assertEqual((code, stdout), (2, ""))
        self.assertFalse(self.output.exists())

    def test_output_schema_stays_bounded_without_private_refs(self):
        doc = manifest.build_manifest(self.root, self.sha)
        encoded = json.dumps(doc)
        self.assertNotIn("owner", encoded.lower())
        self.assertNotIn("session_file", encoded)
        self.assertNotIn(str(self.root), encoded)
        self.assertFalse(doc["delivery_boundary"]["automatic_publish"])

    def test_script_cannot_claim_success_if_checkout_changes_after_build(self):
        document = manifest.build_manifest(self.root, self.sha)
        expected = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
        real_checkout = manifest._checkout
        real_writer = manifest.write_new_manifest
        real_link = manifest.os.link
        real_unlink = manifest.os.unlink
        real_remove = manifest.os.remove
        license_path = self.root / "LICENSE"
        changed_license = license_path.read_bytes() + b"fixture tracked change after publication\n"
        checkout_calls = []
        checkout_failures = []
        link_calls = []
        committed = []
        removed_final = []
        observed_before_source_change = []

        def is_final(path, directory_fd):
            if isinstance(path, int):
                return False
            value = os.fsdecode(os.fspath(path))
            if directory_fd is None and value == str(self.output):
                return True
            return bool(committed and directory_fd == committed[0][1]
                        and value == os.fsdecode(os.fspath(committed[0][0])))

        def link_once(source, target, *args, **kwargs):
            link_calls.append((source, target))
            if len(link_calls) != 1:
                raise AssertionError("Manifest publication must not retry os.link")
            result = real_link(source, target, *args, **kwargs)
            committed.append((target, kwargs.get("dst_dir_fd")))
            return result

        def changed_after_output(root, sha):
            checkout_calls.append((root, sha))
            if len(checkout_calls) == 3:
                observed_before_source_change.append(self.output.read_bytes())
                license_path.write_bytes(changed_license)
            try:
                return real_checkout(root, sha)
            except manifest.ManifestError as exc:
                checkout_failures.append((len(checkout_calls), exc.code))
                raise

        def track_unlink(path, *args, **kwargs):
            if is_final(path, kwargs.get("dir_fd")):
                removed_final.append("unlink")
            return real_unlink(path, *args, **kwargs)

        def track_remove(path, *args, **kwargs):
            if is_final(path, kwargs.get("dir_fd")):
                removed_final.append("remove")
            return real_remove(path, *args, **kwargs)

        with contextlib.ExitStack() as patches:
            writer = patches.enter_context(
                patch.object(manifest, "write_new_manifest", wraps=real_writer)
            )
            patches.enter_context(
                patch.object(manifest, "_checkout", side_effect=changed_after_output)
            )
            patches.enter_context(patch.object(manifest.os, "link", side_effect=link_once))
            patches.enter_context(patch.object(manifest.os, "unlink", side_effect=track_unlink))
            patches.enter_context(patch.object(manifest.os, "remove", side_effect=track_remove))
            code, stdout, stderr = self.invoke()

        self.assertEqual(len(checkout_calls), 3)
        self.assertEqual(checkout_failures, [(3, "source_metadata_dirty")])
        self.assertEqual(observed_before_source_change, [expected])
        self.assertEqual(license_path.read_bytes(), changed_license)
        self.assertEqual(writer.call_count, 1, "Do not retry a published manifest")
        self.assertEqual((len(link_calls), len(committed)), (1, 1))
        self.assertEqual(
            removed_final, [], "A failed post-publication checkout must not unlink the final path"
        )
        self.assertEqual((code, stdout), (2, ""))
        self.assertEqual(
            stderr, "delivery-manifest=REFUSED code=manifest_output_commit_uncertain\n"
        )
        self.assertEqual(self.output.read_bytes(), expected)
        self.assertEqual(list(self.output_dir.iterdir()), [self.output])


    def test_file_fsync_failure_does_not_publish_manifest(self):
        observed = []

        def fail_file_sync(descriptor):
            info = os.fstat(descriptor)
            observed.append({
                "regular": stat.S_ISREG(info.st_mode),
                "size": info.st_size,
                "final_visible": os.path.lexists(self.output),
            })
            raise OSError("fixture_file_fsync_failure")

        with patch.object(manifest.os, "fsync", side_effect=fail_file_sync):
            code, stdout, stderr = self.invoke()

        self.assertEqual((code, stdout), (2, ""))
        self.assertEqual(
            stderr, "delivery-manifest=REFUSED code=manifest_output_refused\n"
        )
        self.assertEqual(len(observed), 1, "The real file-sync boundary must run")
        self.assertTrue(observed[0]["regular"])
        self.assertGreater(observed[0]["size"], 0, "Bytes must reach the real file")
        self.assertFalse(
            os.path.lexists(self.output),
            "A known file-sync failure must leave no final manifest",
        )
        self.assertFalse(
            observed[0]["final_visible"],
            "The final name must not be visible before file synchronization",
        )
        self.assertEqual(list(self.output_dir.iterdir()), [])
        self.assertEqual(_git(self.root, "rev-parse", "HEAD"), self.sha)
        self.assertEqual(
            _git(self.root, "status", "--porcelain", "--untracked-files=no"), ""
        )

    def test_partial_write_failure_does_not_publish_manifest(self):
        real_fdopen = manifest.os.fdopen
        output_streams = []
        observed = []
        final_output = self.output

        class PartialWriteThenFail:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *exception):
                return self.stream.__exit__(*exception)

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def write(self, content):
                prefix = content[:17]
                written = self.stream.write(prefix)
                self.stream.flush()
                observed.append({
                    "content_size": len(content),
                    "written": written,
                    "file_size": os.fstat(self.stream.fileno()).st_size,
                    "final_visible": os.path.lexists(final_output),
                })
                raise OSError("fixture_partial_write_failure")

        def fail_only_output_stream(descriptor, mode="r", *args, **kwargs):
            stream = real_fdopen(descriptor, mode, *args, **kwargs)
            if mode != "wb":
                return stream
            wrapped = PartialWriteThenFail(stream)
            output_streams.append(wrapped)
            return wrapped

        with patch.object(manifest.os, "fdopen", side_effect=fail_only_output_stream):
            code, stdout, stderr = self.invoke()

        self.assertEqual(len(output_streams), 1, "Only the real output is wrapped")
        self.assertTrue(output_streams[0].stream.closed, "Close the owned handle")
        self.assertEqual((code, stdout), (2, ""))
        self.assertEqual(
            stderr, "delivery-manifest=REFUSED code=manifest_output_refused\n"
        )
        self.assertEqual(len(observed), 1, "Inject exactly one failed write")
        self.assertGreater(observed[0]["content_size"], 17)
        self.assertEqual(observed[0]["written"], 17)
        self.assertEqual(observed[0]["file_size"], 17)
        self.assertFalse(
            os.path.lexists(self.output),
            "A known partial-write failure must leave no final manifest",
        )
        self.assertFalse(
            observed[0]["final_visible"],
            "Partial bytes must not be visible at the final name",
        )
        self.assertEqual(list(self.output_dir.iterdir()), [])
        self.assertEqual(_git(self.root, "rev-parse", "HEAD"), self.sha)
        self.assertEqual(
            _git(self.root, "status", "--porcelain", "--untracked-files=no"), ""
        )



if __name__ == "__main__":
    unittest.main()
