"""Inert offline-only originals for A5 independent Graph R74 pre-effect review."""
import base64
import io
import json
import os
import stat
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

TOOLING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLING))
import ci_private_evidence as seal
import graph_r74_external_reviewer as reviewer


class IndependentR74ReviewerTests(unittest.TestCase):
    PRODUCT = (
        "SOURCE_LOCK.json", "Cargo.lock", "tooling/ci_canvas_service.py",
        "tooling/ci_private_evidence.py", "tooling/native_graph_browser_owner.py",
        "crates/native/examples/native-graph-review-session.rs",
        "apps/studio/tests/semantic-graph-service.spec.ts",
    )
    SDK = ("Cargo.lock", "Cargo.toml", "scripts/dev/ci-driver-bwrap-profile.sh")

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="a5-r74-offline-")
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.product, self.p_sha, self.p_tree, self.p_rows = self.git(
            self.root / "trusted-product", self.PRODUCT)
        self.sdk, self.s_sha, self.s_tree, self.s_rows = self.git(
            self.root / "trusted-sem", self.SDK)
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        pem = self.key.private_bytes(serialization.Encoding.PEM,
                  serialization.PrivateFormat.PKCS8,
                  serialization.NoEncryption())
        self.keyfile = self.root / "reviewer-only.pem"
        with self.keyfile.open("xb") as out: out.write(pem)
        self.keyfile.chmod(0o600)
        der = self.key.public_key().public_bytes(serialization.Encoding.DER,
                       serialization.PublicFormat.SubjectPublicKeyInfo)
        self.recipient = {"spki_der_base64": base64.b64encode(der).decode(),
                          "sha256": reviewer.sha(der)}
        self.run_id = "42400011223"

    def git(self, root, names):
        root.mkdir()
        def call(*args):
            r = subprocess.run(["git", "-C", str(root), *args],capture_output=True)
            if r.returncode: raise AssertionError(r.stderr.decode(errors="replace"))
            return r.stdout.decode().strip()
        call("init", "-q")
        call("config", "user.name", "A5 independent fixture")
        call("config", "user.email", "a5-fixture@example.invalid")
        rows = {}
        for name in names:
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            data = ("A5 original source fixture " + name + "\n").encode()
            p.write_bytes(data)
            rows[name] = {"bytes": len(data), "sha256": reviewer.sha(data)}
        call("add", ".")
        call("commit", "-q", "-m", "A5 independent inert original")
        sha = call("rev-parse", "HEAD")
        tree = call("rev-parse", "HEAD^{tree}")
        return root, sha, tree, rows

    def make_artifact(self, name="original", *, fake_source_digest=False,
                      missing_manifest=False):
        producer = self.root / ("producer-" + name)
        producer.mkdir()
        request_path = producer / "review-request.original.json"
        index_path = producer / "producer-index.original.json"
        copies = producer / "review-originals"
        copies.mkdir()
        source = {"sha": self.p_sha, "sdk_sha": self.s_sha,
                  "tree": self.p_tree, "run_id": self.run_id,
                  "run_attempt": 1}
        execution = {"scope": "phase1-native-graph", "job": "real-service",
                     "selected_case": "graph-flow"}
        product_rows = dict(self.p_rows)
        if fake_source_digest:
            first = "SOURCE_LOCK.json"
            product_rows[first] = {**product_rows[first], "sha256": "0"*64}
        index = {
          "schema": reviewer.INDEX_SCHEMA, "source": source,
          "contract": {"source_files": product_rows, "sdk_files": self.s_rows,
                       "sdk_tree": self.s_tree, "execution": execution},
        }
        index_raw = seal.json_bytes(index)
        index_path.write_bytes(index_raw)
        copy_refs = []
        for i, content in enumerate((b"original-python-ELF", b"original-ffmpeg-ELF")):
            retained = copies / (str(i)+".original")
            retained.write_bytes(content)
            original = {"path": "/usr/bin/" + ("python3" if i == 0 else "ffmpeg"),
                        "sha256": reviewer.sha(content), "size_bytes": len(content)}
            ref = {**original, "path": str(retained)}
            copy_refs.append({"original": original, "retained": ref})
        request = {
          "source": source, "execution": execution,
          "index": {"path": str(index_path), "sha256": reviewer.sha(index_raw),
                    "size_bytes": len(index_raw)},
          "contexts": ["mw-owner/phase1-native-graph/"+self.run_id+"/1/graph-flow"],
          "outside_original_copies": copy_refs,
        }
        request_path.write_bytes(seal.json_bytes(request))
        plaintext = seal.gather_snapshot({"producer": producer})
        if missing_manifest:
            # A trusted-but-partial source must not be relabeled as complete.
            with tarfile.open(fileobj=io.BytesIO(plaintext), mode="r:") as tar:
                rows = [(row.name, tar.extractfile(row).read()) for row in tar]
            replacement = io.BytesIO()
            with tarfile.open(fileobj=replacement, mode="w") as tar:
                for member, raw in rows:
                    if member == "snapshot-manifest.json":
                        report = reviewer.json_original(raw)
                        report["selected_originals_complete"] = False
                        raw = seal.json_bytes(report)
                    info = tarfile.TarInfo(member); info.size = len(raw)
                    tar.addfile(info, io.BytesIO(raw))
            plaintext = replacement.getvalue()
        context = {"source_sha": self.p_sha, "semwright_sha": self.s_sha,
                   "run_id": self.run_id, "run_attempt": "1"}
        envelope = seal.encrypt_snapshot(plaintext, self.recipient, context)
        carrier = {"source_sha": self.p_sha, "semwright_sha": self.s_sha,
                   "run_id": self.run_id, "run_attempt": 1,
                   "index_sha256": reviewer.sha(index_raw),
                   "recipient_spki_sha256": self.recipient["sha256"],
                   "envelope": {"filename": "producer-originals.mwseal",
                                "sha256": reviewer.sha(envelope),
                                "size_bytes": len(envelope)}}
        path = self.root / (name + ".zip")
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("producer-originals.mwseal", envelope)
            z.writestr("carrier.original.json", seal.json_bytes(carrier))
        return path

    def check(self, name="original", **kwargs):
        path = self.make_artifact(name, **kwargs)
        return reviewer.review(path,self.keyfile,self.product,self.sdk,
                        self.p_sha,self.s_sha,self.run_id)

    def test_same_trusted_source_and_recipient_accept_pre_effect_only(self):
        result = self.check()
        self.assertEqual(result["decision"],
                         "SOURCE_CARRIER_AUTHENTICATED_NO_OWNER_ADMISSION_NO_G2")
        self.assertEqual(result["independent_product_sources"], 7)
        self.assertEqual(result["independent_semwright_sources"], 3)
        self.assertFalse(result["github_status_written"])
        self.assertEqual(result["G2"], "NOT_GRANTED")

    def test_gh_download_extracted_directory_preserves_identity_and_rejects_injection(self):
        original_zip = self.make_artifact("extracted")
        extracted = self.root / "gh-run-download"
        extracted.mkdir()
        with zipfile.ZipFile(original_zip) as archive:
            for name in ("producer-originals.mwseal", "carrier.original.json"):
                (extracted / name).write_bytes(archive.read(name))
        expected = reviewer.review(original_zip, self.keyfile, self.product, self.sdk,
                                   self.p_sha, self.s_sha, self.run_id)
        actual = reviewer.review(extracted, self.keyfile, self.product, self.sdk,
                                 self.p_sha, self.s_sha, self.run_id)
        self.assertEqual(actual, expected)
        unexpected = extracted / ".hidden-extra"
        unexpected.write_bytes(b"foreign")
        with self.assertRaisesRegex(reviewer.ReviewRejected,
                                    "ARTIFACT_TWO_ORIGINAL_MEMBERS"):
            reviewer.review(extracted, self.keyfile, self.product, self.sdk,
                            self.p_sha, self.s_sha, self.run_id)
        unexpected.unlink()
        carrier = extracted / "carrier.original.json"
        carrier.unlink()
        carrier.symlink_to(original_zip)
        with self.assertRaisesRegex(reviewer.ReviewRejected, "ARTIFACT_REGULAR_FILE"):
            reviewer.review(extracted, self.keyfile, self.product, self.sdk,
                            self.p_sha, self.s_sha, self.run_id)
        alias = self.root / "foreign-alias"
        alias.symlink_to(extracted, target_is_directory=True)
        with self.assertRaisesRegex(reviewer.ReviewRejected, "ARTIFACT_CANONICAL_PATH"):
            reviewer.review(alias, self.keyfile, self.product, self.sdk,
                            self.p_sha, self.s_sha, self.run_id)

    def test_authenticated_carrier_rejects_forged_original_manifest_digest(self):
        """A public-key holder can reencrypt a snapshot: its own manifest must agree."""
        artifact = self.make_artifact("forged-manifest-attestation")
        with zipfile.ZipFile(artifact) as archive:
            carrier = reviewer.json_original(archive.read("carrier.original.json"))
            envelope = archive.read("producer-originals.mwseal")
        plaintext, context = seal.decrypt_snapshot(envelope, self.key)
        rows = []
        with tarfile.open(fileobj=io.BytesIO(plaintext), mode="r:") as archive:
            for member in archive:
                raw = archive.extractfile(member).read()
                if member.name == "snapshot-manifest.json":
                    manifest = reviewer.json_original(raw)
                    captured = next(row for row in manifest["roots"]["producer"]["files"]
                                    if row["archive_path"] == "producer/producer-index.original.json")
                    captured["observation"]["sha256"] = "f" * 64
                    raw = seal.json_bytes(manifest)
                rows.append((member.name, raw))
        new_tar = io.BytesIO()
        with tarfile.open(fileobj=new_tar, mode="w") as archive:
            for name, raw in rows:
                info = tarfile.TarInfo(name)
                info.mode, info.mtime, info.size = 0o600, 0, len(raw)
                archive.addfile(info, io.BytesIO(raw))
        encrypted = seal.encrypt_snapshot(new_tar.getvalue(), self.recipient, context)
        carrier["envelope"]["sha256"] = reviewer.sha(encrypted)
        carrier["envelope"]["size_bytes"] = len(encrypted)
        with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("producer-originals.mwseal", encrypted)
            archive.writestr("carrier.original.json", seal.json_bytes(carrier))
        with self.assertRaisesRegex(reviewer.ReviewRejected,
                                    "SNAPSHOT_ORIGINAL_OBSERVATION"):
            reviewer.review(artifact, self.keyfile, self.product, self.sdk,
                            self.p_sha, self.s_sha, self.run_id)

    def test_raw_zip_rejects_nonregular_symlink_member_even_with_valid_bytes(self):
        """ZIP member mode is original transport metadata, not an optional hint."""
        artifact = self.make_artifact("symlink-member")
        with zipfile.ZipFile(artifact) as archive:
            rows = [(name, archive.read(name)) for name in
                    ("producer-originals.mwseal", "carrier.original.json")]
        with zipfile.ZipFile(artifact, "w") as archive:
            for name, raw in rows:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                mode = stat.S_IFLNK | 0o777 if name == "carrier.original.json" else stat.S_IFREG | 0o600
                info.external_attr = mode << 16
                archive.writestr(info, raw)
        with self.assertRaisesRegex(reviewer.ReviewRejected, "ARTIFACT_REGULAR_FILE"):
            reviewer.review(artifact, self.keyfile, self.product, self.sdk,
                            self.p_sha, self.s_sha, self.run_id)

    def test_wrong_source_run_recipient_and_insecure_key_are_denied(self):
        path = self.make_artifact()
        for psha, rid in (("1"*40,self.run_id),(self.p_sha,"13")):
            with self.assertRaises(reviewer.ReviewRejected):
                reviewer.review(path,self.keyfile,self.product,self.sdk,psha,self.s_sha,rid)
        key2 = self.root / "other.pem"
        key2.write_bytes(rsa.generate_private_key(65537,3072).private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption()))
        key2.chmod(0o600)
        with self.assertRaises(reviewer.ReviewRejected):
            reviewer.review(path,key2,self.product,self.sdk,
                            self.p_sha,self.s_sha,self.run_id)
        self.keyfile.chmod(0o644)
        with self.assertRaises(reviewer.ReviewRejected):
            reviewer.review(path,self.keyfile,self.product,self.sdk,
                            self.p_sha,self.s_sha,self.run_id)

    def test_runner_and_owner_checkout_private_keys_are_forbidden(self):
        path = self.make_artifact()
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            with self.assertRaisesRegex(reviewer.ReviewRejected,
                                        "INDEPENDENT_ONLY_NEVER_RUNNER"):
                reviewer.review(path, self.keyfile, self.product, self.sdk,
                                self.p_sha, self.s_sha, self.run_id)
        owner_key = self.product / "owner-key.pem"
        owner_key.write_bytes(self.keyfile.read_bytes())
        owner_key.chmod(0o600)
        with self.assertRaisesRegex(reviewer.ReviewRejected,
                                    "PRIVATE_KEY_INSIDE_OWNER_CHECKOUT"):
            reviewer.review(path, owner_key, self.product, self.sdk,
                            self.p_sha, self.s_sha, self.run_id)

    def test_same_recipient_encrypted_git_digest_forgery_is_denied(self):
        with self.assertRaisesRegex(reviewer.ReviewRejected,
                                    "SOURCE_GIT_BYTES_SOURCE_LOCK.json"):
            self.check("forged-source",fake_source_digest=True)

    def test_valid_encryption_with_partial_original_manifest_is_denied(self):
        with self.assertRaisesRegex(reviewer.ReviewRejected,
                                    "ORIGINAL_MANIFEST_COMPLETE"):
            self.check("missing-original",missing_manifest=True)

    def test_zip_unknown_or_duplicate_member_is_denied(self):
        path = self.make_artifact()
        with zipfile.ZipFile(path, "a") as z:
            z.writestr("other-source/unknown", b"unauthorized")
        with self.assertRaisesRegex(reviewer.ReviewRejected,
                                    "ARTIFACT_TWO_ORIGINAL_MEMBERS"):
            reviewer.review(path,self.keyfile,self.product,self.sdk,
                            self.p_sha,self.s_sha,self.run_id)

    def test_source_path_traversal_and_duplicate_snapshot_are_denied(self):
        for name in ("../foreign", "tooling/../source", "/etc/passwd",
                     "tooling\\foreign", "tooling//blank"):
            with self.assertRaises(reviewer.ReviewRejected):
                reviewer.clean_repo_path(name)
        reviewer.clean_repo_path("Cargo.lock")
        data=io.BytesIO()
        with tarfile.open(fileobj=data,mode="w") as tar:
            for member in ("producer/replay", "producer/replay"):
                raw=b"duplicated";info=tarfile.TarInfo(member);info.size=len(raw)
                tar.addfile(info,io.BytesIO(raw))
        with self.assertRaises(reviewer.ReviewRejected):
            reviewer.tar_originals(data.getvalue())


if __name__ == "__main__":
    unittest.main()
