"""Inert protocol and actual AEAD/ZIP files; no HTTP, owner or Native calls."""
import base64
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import native_graph_build_reuse as reuse
import native_graph_build_transfer as transfer

CURRENT = {"source_sha": "a" * 40, "semwright_sha": reuse.ORIGIN["semwright_sha"],
           "run_id": "99999999999", "run_attempt": "1"}


def fixtures(instant):
    public = {"context": CURRENT, "recipient": {"sha256": "b" * 64}, "actor_id": 69337222,
        "input_contract_sha256": reuse.sha(reuse.encoded(transfer.input_contract(CURRENT))),
        "deadline_original_sha256": "c" * 64, "issued_at": instant.isoformat(),
        "expires_at": (instant + timedelta(seconds=1000)).isoformat()}
    release = {"id": 12345, "draft": True, "prerelease": True, "published_at": None,
        "tag_name": transfer.tag(CURRENT), "name": transfer.tag(CURRENT), "target_commitish": CURRENT["source_sha"],
        "body": transfer.release_body(public), "author": {"id": public["actor_id"]},
        "assets": [{"id": 55555, "name": transfer.ASSET_NAME, "state": "uploaded",
            "size": reuse.CAPSULE["bytes"] + 2000, "digest": "sha256:" + "d" * 64,
            "content_type": "application/octet-stream", "uploader": {"id": public["actor_id"]},
            "created_at": instant.isoformat(), "updated_at": instant.isoformat()}]}
    asset = transfer.validate_release(release, public)
    query = urllib.parse.urlencode({"sp": "r", "spr": "https", "sr": "b", "sig": "inert-not-a-capability",
                                   "se": (instant + timedelta(minutes=30)).isoformat()})
    url = "https://release-assets.githubusercontent.com/github-production-release-asset/12345/ab12-cd34?" + query
    return public, release, asset, url


class TransferContractTests(unittest.TestCase):
    def test_context_excludes_both_unknown_histories_and_nonexact_sdk(self):
        self.assertEqual(transfer.context(dict(CURRENT)), CURRENT)
        for delta in ({"run_id": "42356210001"}, {"run_id": "42372165893"},
                      {"run_attempt": "2"}, {"semwright_sha": "0" * 40}, {"extra": True}):
            with self.subTest(delta=delta), self.assertRaises(reuse.Rejected): transfer.context({**CURRENT, **delta})

    def test_read_ticket_binds_asset_recipient_read_only_origin_and_expiry(self):
        instant = datetime.now(timezone.utc)
        public, release, asset, url = fixtures(instant)
        ticket = transfer.read_ticket(public, asset, url, reuse.encoded(release), instant=instant)
        self.assertEqual(transfer.validate_ticket(ticket, public, instant=instant), asset)
        for bad in (url.replace("sp=r", "sp=rw"), url.replace("https://release-assets", "http://release-assets"),
                    url.replace("release-assets.githubusercontent.com", "release-assets.githubusercontent.com.example.invalid"),
                    url + "&sp=r", url + "#fragment", url.replace("/github-production-release-asset/", "/another/")):
            with self.subTest(url=bad), self.assertRaises(reuse.Rejected): transfer.signed_url(bad, instant=instant)
        for delta in ({"maximum_gets": 2}, {"maximum_gets": True}, {"native_calls": False},
                      {"github_token_forwarded": True}, {"context": {**CURRENT, "run_id": "999"}},
                      {"asset": {**asset, "asset_id": 1}}, {"recipient_sha256": "e" * 64}):
            with self.subTest(delta=delta), self.assertRaises(reuse.Rejected):
                transfer.validate_ticket({**ticket, **delta}, public, instant=instant)
        with self.assertRaises(reuse.Rejected):
            transfer.validate_ticket(ticket, public, instant=instant + timedelta(seconds=300))

    def test_courier_zip_rejects_extra_duplicate_alias_and_large_member(self):
        def zipped(rows):
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w") as archive:
                for name, mode, raw in rows:
                    info = zipfile.ZipInfo(name); info.external_attr = mode << 16
                    archive.writestr(info, raw)
            raw = out.getvalue()
            return raw, {"size_in_bytes": len(raw), "digest": "sha256:" + reuse.sha(raw)}
        row = ("read-ticket.mwseal", stat.S_IFREG | 0o600, b"inert")
        raw, meta = zipped([row]); self.assertEqual(transfer.read_courier_zip(raw, meta), b"inert")
        for rows in ([row, ("extra", stat.S_IFREG, b"x")],
                     [("../read-ticket.mwseal", stat.S_IFREG, b"x")],
                     [("read-ticket.mwseal", stat.S_IFLNK, b"target")],
                     [("read-ticket.mwseal", stat.S_IFREG, b"x" * (transfer.MAX_TICKET + 1))]):
            raw, meta = zipped(rows)
            with self.subTest(rows=len(rows)), self.assertRaises(reuse.Rejected): transfer.read_courier_zip(raw, meta)

    def test_courier_must_be_same_actor_workflow_source_and_separate_successful_run(self):
        instant = datetime.now(timezone.utc); public, _, _, _ = fixtures(instant)
        artifact = {"id": 1, "name": transfer.courier_name(CURRENT, public["recipient"]["sha256"]),
            "expired": False, "size_in_bytes": 100, "digest": "sha256:" + "f" * 64,
            "workflow_run": {"head_sha": CURRENT["source_sha"], "id": 99999999998},
            "created_at": instant.isoformat(), "updated_at": instant.isoformat()}
        run = {"id": 99999999998, "head_sha": CURRENT["source_sha"], "run_attempt": 1, "event": "workflow_dispatch",
            "status": "completed", "conclusion": "success", "repository": {"full_name": transfer.REPOSITORY},
            "head_repository": {"full_name": transfer.REPOSITORY}, "path": transfer.WORKFLOW,
            "workflow_id": 391230122, "actor": {"id": public["actor_id"], "type": "User"}}
        self.assertEqual(transfer.checked_courier_artifact(artifact, CURRENT, public, run), artifact)
        for delta in ({"conclusion": "failure"}, {"actor": {"id": 1, "type": "User"}},
                      {"workflow_id": 1}, {"head_sha": "f" * 40}, {"run_attempt": 2}):
            with self.subTest(delta=delta), self.assertRaises(reuse.Rejected):
                transfer.checked_courier_artifact(artifact, CURRENT, public, {**run, **delta})


class TransferCryptoTests(unittest.TestCase):
    """Runs in the hash-locked custody environment; no skip as Native proof."""
    @classmethod
    def setUpClass(cls):
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives import serialization
        except ImportError as error:
            # Generic G0 installs no custody package. The closed producer
            # imports ci_private_evidence before this module, after its exact
            # hash-locked installation, so it cannot reach R74 by this skip.
            raise unittest.SkipTest("real AEAD controls require the closed custody environment") from error
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        der = cls.key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.recipient = {"spki_der_base64": base64.b64encode(der).decode(), "sha256": reuse.sha(der)}

    def test_actual_streaming_aead_matches_existing_format_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory(prefix="graph-crypto-") as folder:
            root = Path(folder).resolve(); original = b"inert capsule bytes\0" * 110000
            source = root / "original.tar"; source.write_bytes(original)
            fixture = {"bytes": len(original), "sha256": reuse.sha(original)}
            with patch.object(reuse, "CAPSULE", fixture):
                sealed = root / "sealed.mwseal"
                identity = transfer.encrypt_capsule(source, sealed, self.recipient, CURRENT, lambda: None)
                self.assertEqual(transfer.seal_module().decrypt_snapshot(sealed.read_bytes(), self.key), (original, CURRENT))
                public = {"recipient": self.recipient, "context": CURRENT}
                target = root / "opened.tar"
                transfer.decrypt_capsule(sealed, target, self.key, public, identity, lambda: None)
                self.assertEqual(target.read_bytes(), original)
                with self.assertRaises(FileExistsError):
                    transfer.decrypt_capsule(sealed, target, self.key, public, identity, lambda: None)
                corrupt = root / "corrupt.mwseal"; data = bytearray(sealed.read_bytes()); data[-1] ^= 1
                corrupt.write_bytes(data)
                with self.assertRaises(Exception):
                    transfer.decrypt_capsule(corrupt, root / "rejected.tar", self.key, public,
                        reuse.identity(corrupt), lambda: None)
                self.assertEqual(source.read_bytes(), original)

    def test_opaque_courier_roundtrip_keeps_url_encrypted_and_context_exact(self):
        instant = datetime.now(timezone.utc).replace(microsecond=0)
        public = transfer.recipient_record(self.recipient, CURRENT, 69337222, instant.isoformat(),
            (instant + timedelta(seconds=1000)).isoformat(), "c" * 64)
        self.assertEqual(transfer.validate_recipient(public, CURRENT, 69337222, "c" * 64, instant=instant), self.recipient)
        raw = b'{"inert_secret_url":"https://example.invalid/not-a-capability"}\n'
        encrypted = transfer.seal_module().encrypt_snapshot(raw, self.recipient, CURRENT)
        with tempfile.TemporaryDirectory(prefix="graph-courier-") as folder:
            output = Path(folder).resolve() / "delivery"
            env = {"GITHUB_SHA": CURRENT["source_sha"], "GRAPH_BUILD_TARGET_RUN": CURRENT["run_id"],
                "GITHUB_REPOSITORY": transfer.REPOSITORY, "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "graph-input-delivery", "GITHUB_ACTOR_ID": "69337222",
                "GRAPH_BUILD_TICKET": base64.b64encode(encrypted).decode(),
                "GRAPH_BUILD_TICKET_SHA256": reuse.sha(encrypted), "GRAPH_BUILD_RECIPIENT_SHA256": self.recipient["sha256"]}
            name = transfer.create_courier_file(output, env)
            self.assertEqual(name, transfer.courier_name(CURRENT, self.recipient["sha256"]))
            kept = (output / "read-ticket.mwseal").read_bytes()
            self.assertNotIn(b"example.invalid", kept)
            self.assertEqual(transfer.seal_module().decrypt_snapshot(kept, self.key), (raw, CURRENT))
            with self.assertRaises(reuse.Rejected):
                transfer.create_courier_file(output.parent / "wrong", {**env, "GRAPH_BUILD_TARGET_RUN": "888"})


if __name__ == "__main__":
    unittest.main()
