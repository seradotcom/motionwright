"""v0.1 independent package candidate receipt provenance and file integrity.

Every package and Git repository is synthetic; no owner installer, secret,
native provider or private product scenario is run.
"""
from __future__ import annotations

import contextlib
from hashlib import sha256
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import package_receipt as candidate


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args],
                            capture_output=True, text=True,
                            check=True, timeout=12)
    return result.stdout.strip()


class CandidateReceiptSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mw-v01-candidate-source-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "checkout"
        self.root.mkdir()
        inputs = {
            "Cargo.toml": '[workspace.package]\nversion = "0.1.0"\nrepository="https://example.invalid/motionwright"\nlicense="AGPL-3.0-or-later"\n',
            "Cargo.lock": "# fixture\n",
            "SOURCE_LOCK.json": json.dumps({"dependencies": {"semwright": {
                "repository": "https://example.invalid/semwright",
                "revision": "b"*40, "native_sdk_package": "semwright-client",
                "license_observed": "AGPL-3.0", "features_planned": ["native-sdk"]
            }}}),
            "LICENSE": "not a production license",
            "THIRD_PARTY_NOTICES.md": "synthetic",
            "apps/studio/package-lock.json": '{"lockfileVersion":3}',
            "apps/desktop/src-tauri/tauri.conf.json": "{}",
            "docs/acceptance/requirements-status.json": "{}",
            "docs/acceptance/acceptance-status.json": "{}",
            "docs/security/known-rustsec-advisories.json": "{}",
        }
        for name, value in inputs.items():
            p=self.root/name
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text(value,encoding="utf8")
        git(self.root,"init","-q")
        git(self.root,"add",".")
        git(self.root,"-c","user.name=fixture","-c",
            "user.email=fixture@example.invalid","commit","-qm","fixture")
        self.sha=git(self.root,"rev-parse","HEAD")
        self.bundle=self.root/"target"/"release"/"bundle"/"nsis"/"Motionwright_0.1.0_x64-setup.exe"
        self.bundle.parent.mkdir(parents=True)
        self.bundle.write_bytes(b"synthetic package bytes - NOT an installer")
        self.receipt=self.root/"ci-evidence"/"windows-candidate.json"

    def invoke(self, sha=None, artifacts=None, receipt=None, platform="windows"):
        stdout,stderr=io.StringIO(),io.StringIO()
        args=["--git-sha",sha or self.sha,"--platform",platform,
              "--arch","x86_64"]
        for p in artifacts or [self.bundle]:
            args+=["--artifact",str(p)]
        args+=["--output",str(receipt or self.receipt)]
        with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
            code=candidate.main(args,root=self.root)
        return code,stdout.getvalue(),stderr.getvalue()

    def test_exact_source_receipt_preserves_legacy_schema_and_release_block(self):
        code,output,errors=self.invoke()
        self.assertEqual((code,errors),(0,""))
        data=json.loads(self.receipt.read_text())
        self.assertEqual(data["schema_version"],1)
        self.assertEqual(data["candidate"]["source_revision"],self.sha)
        self.assertEqual(data["candidate"]["semwright_revision"],"b"*40)
        self.assertEqual(data["artifacts"][0]["sha256"],sha256(self.bundle.read_bytes()).hexdigest())
        self.assertEqual(data["artifacts"][0]["bytes"],self.bundle.stat().st_size)
        self.assertEqual(data["artifacts"][0]["bundle_kind"],"nsis")
        self.assertFalse(data["claims"]["published"])
        self.assertEqual(data["claims"]["distribution_signature"],"NOT_CLAIMED")
        self.assertEqual(data["claims"]["human_install_acceptance"],"NOT_RUN")
        self.assertNotIn(str(self.root),output)

    def test_forged_40hex_checkout_source_refused(self):
        code,output,errors=self.invoke(sha="a"*40)
        self.assertEqual((code,output),(2,""))
        self.assertIn("source_checkout_sha_mismatch",errors)
        self.assertFalse(self.receipt.exists())
        self.assertNotIn(str(self.root),errors)

    def test_modified_tracked_checkout_refused(self):
        (self.root/"Cargo.lock").write_text("locally modified")
        code,output,errors=self.invoke()
        self.assertEqual((code,output),(2,""))
        self.assertIn("source_metadata_dirty",errors)
        self.assertFalse(self.receipt.exists())

    def test_overwrite_previous_candidate_receipt_refused(self):
        self.receipt.parent.mkdir()
        self.receipt.write_bytes(b"previous genuine evidence")
        code,output,errors=self.invoke()
        self.assertEqual((code,output),(2,""))
        self.assertEqual(self.receipt.read_bytes(),b"previous genuine evidence")

    def test_relative_output_path_created_under_source_for_ci(self):
        args=["--git-sha",self.sha,"--platform","windows","--arch","x86_64",
              "--artifact",str(self.bundle),
              "--output","ci-evidence/windows-candidate.json"]
        self.assertEqual(candidate.main(args,root=self.root),0)
        self.assertTrue(self.receipt.is_file())

    def test_file_identity_changed_after_hashing_is_rejected(self):
        real_fstat=os.fstat
        count=0
        def forged(fd):
            nonlocal count
            count+=1
            st=real_fstat(fd)
            if count == 6:
                return SimpleNamespace(st_dev=st.st_dev,st_ino=st.st_ino,
                    st_size=st.st_size,st_mtime_ns=st.st_mtime_ns+1,
                    st_nlink=st.st_nlink)
            return st
        with patch.object(candidate.os,"fstat",side_effect=forged):
            code,output,errors=self.invoke()
        self.assertEqual((code,output),(2,""))
        self.assertIn("candidate_artifact_changed",errors)
        self.assertFalse(self.receipt.exists())

    def test_large_artifact_is_rejected_before_hashing(self):
        with patch.object(candidate,"MAX_ARTIFACT_BYTES",10):
            code,output,errors=self.invoke()
        self.assertEqual((code,output),(2,""))
        self.assertIn("candidate_artifact_size_invalid",errors)
        self.assertFalse(self.receipt.exists())

    def test_a_symlink_to_package_refused_before_any_source_read(self):
        alias=self.bundle.parent/"alias-setup.exe"
        try:
            alias.symlink_to(self.bundle)
        except (OSError,NotImplementedError):
            self.skipTest("Symlink creation disallowed")
        code,output,errors=self.invoke(sha="a"*40,artifacts=[alias])
        self.assertEqual((code,output),(2,""))
        self.assertIn("non-symlink",errors)
        self.assertFalse(self.receipt.exists())

    def test_reparse_point_in_artifact_parent_is_rejected(self):
        original=Path.lstat
        parent=self.bundle.parent
        def forged(path,*args,**kwargs):
            st=original(path,*args,**kwargs)
            if path==parent:
                return SimpleNamespace(st_mode=st.st_mode,st_file_attributes=0x400)
            return st
        with patch.object(Path,"lstat",autospec=True,side_effect=forged):
            with self.assertRaisesRegex(candidate.CandidateError,"non-symlink"):
                candidate.verified_artifact(self.root,self.bundle)

    @unittest.skipUnless(os.name == "nt", "NTFS junction requires Windows")
    def test_real_ntfs_junction_under_temporary_candidate_is_refused(self):
        alias = self.root / "untrusted-artifact-folder"
        target = self.bundle.parent
        try:
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(target)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=8, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            self.skipTest("Junction creation unavailable")
        if result.returncode != 0:
            self.skipTest("Unprivileged NTFS junction creation unavailable")
        try:
            linked = alias / self.bundle.name
            self.assertTrue(linked.is_file())
            with self.assertRaisesRegex(candidate.CandidateError, "non-symlink"):
                candidate.verified_artifact(self.root, linked)
        finally:
            # Removing this newly made junction never touches its target.
            alias.rmdir()

    def test_windows_bundle_filename_rules_remain_strict(self):
        wrong=self.bundle.parent/"not-a-bundle.exe"
        wrong.write_bytes(self.bundle.read_bytes())
        code,output,errors=self.invoke(artifacts=[wrong])
        self.assertEqual((code,output),(2,""))
        self.assertIn("candidate_artifact_type_invalid",errors)
        self.assertFalse(self.receipt.exists())

    def test_bundle_cannot_be_reused_twice(self):
        with self.assertRaisesRegex(candidate.CandidateError,"candidate_artifact_name_reused"):
            candidate.make_receipt(self.root,self.sha,"windows","x86_64",
                                   [self.bundle,self.bundle])

    def test_changed_source_after_receipt_written_does_not_leave_fake_receipt(self):
        from release_manifest import ManifestError
        original=candidate._checkout
        calls=0
        def change_after_write(root,sha):
            nonlocal calls
            calls+=1
            if calls==3:
                raise ManifestError("source_checkout_changed_after_write")
            return original(root,sha)
        with patch.object(candidate,"_checkout",side_effect=change_after_write):
            code,output,errors=self.invoke()
        self.assertEqual((code,output),(2,""))
        self.assertEqual(calls,3)
        self.assertIn("source_checkout_changed_after_write",errors)
        self.assertFalse(self.receipt.exists())

    def test_source_lock_pin_not_mutable_without_new_checkout(self):
        lock=self.root/"SOURCE_LOCK.json"
        old=json.loads(lock.read_text())
        old["dependencies"]["semwright"]["revision"]="c"*40
        lock.write_text(json.dumps(old))
        code,output,errors=self.invoke()
        self.assertEqual((code,output),(2,""))
        self.assertFalse(self.receipt.exists())


if __name__=="__main__":
    unittest.main()
