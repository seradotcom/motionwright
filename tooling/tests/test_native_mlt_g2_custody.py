"""Offline cryptographic and closed-publication contracts; no renderer is launched."""
import base64
import copy
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import native_mlt_g2_custody as custody
import ci_private_evidence as sealed

CURRENT = {"source_sha": "a" * 40, "semwright_sha": custody.g2.SEMWRIGHT_PIN,
           "run_id": "123", "run_attempt": "1"}


def descriptor():
    return {"artifact_id": 1, "run_id": 2, "run_attempt": 1,
            "source_sha": "b" * 40, "zip_sha256": "c" * 64}


def final_receipt():
    return {"schema": "motionwright.native.mlt-g2-custody/1",
            "terminal_scope": "canonical_assemble_command", "native_terminal": "SUCCESS_REPORTED",
            "native_operation_retried": False, "source_content_and_live_module_verified": True,
            "secondary_failures": [], "packet_PTS_and_AAC_priming_verified": False,
            "physical_AV_master_acceptance": False, "human_acceptance": False,
            "native_result": {"private_owner_path": "/owner/private", "receipt": "SECRET-CANARY"}}


class G2CustodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        der = cls.private.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.recipient = {"spki_der_base64": base64.b64encode(der).decode(), "sha256": custody.sha(der)}

    def dispatch(self):
        return {"schema": custody.DISPATCH_SCHEMA, "motionwright_sha": CURRENT["source_sha"],
                "semwright_sha": CURRENT["semwright_sha"],
                "official_runtime": {"artifact": descriptor(), "receipt_sha256": "d" * 64,
                                     "known_original_module_sha256": "e" * 64,
                                     "known_melt_sha256": "f" * 64},
                "compiler": {"artifact": descriptor(),
                             "members": {key: key for key in (
                                 "original_c_source", "repaired_c_source", "compiled_module",
                                 "compiler_receipt_path")}},
                "recipient": self.recipient}

    def test_actual_RSA_OAEP_AESGCM_roundtrip_recovers_exact_private_bytes(self):
        raw = b"\x00original SQLite WAL SHM /owner/private\nsecret receipt\xff"
        encrypted = custody.encrypt_snapshot(raw, self.recipient, CURRENT)
        decoded, context = custody.decrypt_snapshot(encrypted, self.private)
        self.assertEqual(decoded, raw)
        self.assertEqual(context, CURRENT)
        self.assertNotIn(b"/owner/private", encrypted)

    def test_each_seal_uses_fresh_random_key_and_nonce(self):
        first = custody.encrypt_snapshot(b"same originals", self.recipient, CURRENT)
        second = custody.encrypt_snapshot(b"same originals", self.recipient, CURRENT)
        self.assertNotEqual(first, second)
        self.assertEqual(custody.decrypt_snapshot(first, self.private)[0],
                         custody.decrypt_snapshot(second, self.private)[0])

    def test_ciphertext_bit_change_fails_authenticated_decryption(self):
        raw = custody.encrypt_snapshot(b"original", self.recipient, CURRENT)
        damaged = raw[:-1] + bytes([raw[-1] ^ 1])
        with self.assertRaises(InvalidTag):
            custody.decrypt_snapshot(damaged, self.private)

    def test_context_header_change_cannot_rebind_the_encrypted_run(self):
        raw = custody.encrypt_snapshot(b"original", self.recipient, CURRENT)
        damaged = raw.replace(b'"run_id":"123"', b'"run_id":"124"', 1)
        self.assertNotEqual(damaged, raw)
        with self.assertRaises(InvalidTag):
            custody.decrypt_snapshot(damaged, self.private)

    def test_wrapped_key_tampering_never_yields_plaintext(self):
        raw = custody.encrypt_snapshot(b"original", self.recipient, CURRENT)
        off = len(custody.MAGIC)
        count = int.from_bytes(raw[off:off + 4], "big")
        header = custody.strict(raw[off + 4:off + 4 + count])
        wrapped = base64.b64decode(header["wrapped_key"])
        header["wrapped_key"] = base64.b64encode(bytes([wrapped[0] ^ 1]) + wrapped[1:]).decode()
        aad = custody.json_bytes(header)
        damaged = custody.MAGIC + len(aad).to_bytes(4, "big") + aad + raw[off + 4 + count:]
        with self.assertRaises(ValueError):
            custody.decrypt_snapshot(damaged, self.private)

    def test_recipient_hash_and_key_kind_are_mandatory(self):
        for change in ({"sha256": "0" * 64}, {"spki_der_base64": "not base64"}, {"extra": True}):
            value = {**self.recipient, **change}
            with self.subTest(change=change), self.assertRaises(ValueError):
                custody.public_key(value)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        der = key.public_key().public_bytes(serialization.Encoding.DER,
                                            serialization.PublicFormat.SubjectPublicKeyInfo)
        with self.assertRaises(custody.CustodyRejected):
            custody.public_key({"spki_der_base64": base64.b64encode(der).decode(),
                                "sha256": custody.sha(der)})

    def test_wrong_recovery_key_is_rejected_before_decryption(self):
        raw = custody.encrypt_snapshot(b"original", self.recipient, CURRENT)
        other = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        with self.assertRaisesRegex(custody.CustodyRejected, "recovery_key_differs"):
            custody.decrypt_snapshot(raw, other)

    def test_plaintext_budget_and_empty_envelopes_are_rejected(self):
        with self.assertRaises(custody.CustodyRejected):
            custody.encrypt_snapshot(b"", self.recipient, CURRENT)
        with mock.patch.object(sealed, "MAX_TOTAL", 1), mock.patch.object(sealed, "MAX_FILES", 0):
            with self.assertRaises(custody.CustodyRejected):
                custody.encrypt_snapshot(b"too large", self.recipient, CURRENT)
        with self.assertRaises(custody.CustodyRejected):
            custody.decrypt_snapshot(b"", self.private)

    def test_dispatch_is_closed_and_binds_current_source_and_full_pins(self):
        self.assertEqual(custody.validate_dispatch(custody.json_bytes(self.dispatch()), CURRENT),
                         self.dispatch())
        for key, value in (("motionwright_sha", "f" * 40), ("semwright_sha", "e" * 40),
                           ("extra", "execute arbitrary command")):
            changed = {**self.dispatch(), key: value}
            with self.subTest(key=key), self.assertRaises(custody.CustodyRejected):
                custody.validate_dispatch(custody.json_bytes(changed), CURRENT)
        bad = self.dispatch()
        bad["official_runtime"]["known_melt_sha256"] = "<NOT_ADMITTED>"
        with self.assertRaises(custody.CustodyRejected):
            custody.validate_dispatch(custody.json_bytes(bad), CURRENT)

    def test_dispatch_rejects_duplicate_keys_members_and_path_traversal(self):
        duplicate = custody.json_bytes(self.dispatch()).replace(
            b'"schema":', b'"schema":"duplicate","schema":', 1)
        with self.assertRaises(custody.CustodyRejected):
            custody.validate_dispatch(duplicate, CURRENT)
        for path in ("../outside", "/absolute", "a\\b", "a//b", "a\nb"):
            value = self.dispatch()
            value["compiler"]["members"]["compiled_module"] = path
            with self.subTest(path=path), self.assertRaises(custody.CustodyRejected):
                custody.validate_dispatch(custody.json_bytes(value), CURRENT)

    def test_only_original_manual_attempt_one_is_selected(self):
        env = {"GITHUB_ACTIONS": "true", "CI": "true", "RUNNER_OS": "Linux",
               "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
               "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "123", "MW_MLT_SCOPE": "repaired-g2"}
        self.assertEqual(custody.context(env), CURRENT)
        for key, value in (("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_EVENT_NAME", "pull_request"),
                           ("MW_MLT_SCOPE", "normal"), ("GITHUB_SHA", "unknown")):
            with self.subTest(key=key), self.assertRaises(custody.CustodyRejected):
                custody.context({**env, key: value})

    def test_projection_never_exports_owner_data_or_grants_AV_acceptance(self):
        projected = custody.projection(CURRENT, final_receipt(), 0, "success", b"cipher",
                                       self.recipient, producer_started=True, source_unchanged=True)
        self.assertEqual(set(projected), custody.PUBLIC_KEYS)
        encoded = custody.json_bytes(projected)
        self.assertNotIn(b"SECRET-CANARY", encoded)
        self.assertNotIn(b"/owner/private", encoded)
        self.assertTrue(projected["source_content_and_live_module_verified"])
        for key in ("packet_PTS_and_AAC_priming_verified", "physical_AV_master_acceptance",
                    "human_acceptance", "release_authorized", "native_operation_retried"):
            self.assertFalse(projected[key])

    def test_started_without_terminal_is_unknown_and_never_not_started(self):
        result = custody.projection(CURRENT, None, None, "failure", b"cipher", self.recipient,
                                    producer_started=True, source_unchanged=True)
        self.assertEqual(result["native_terminal"], "ERROR_OR_UNKNOWN")
        self.assertFalse(result["source_content_and_live_module_verified"])

    def test_source_change_or_failed_job_cannot_borrow_successful_native_terminal(self):
        for code, state, source in ((1, "success", True), (0, "failure", True),
                                    (0, "cancelled", True), (0, "success", False)):
            with self.subTest(code=code, state=state, source=source):
                result = custody.projection(CURRENT, final_receipt(), code, state, b"cipher",
                                            self.recipient, producer_started=True,
                                            source_unchanged=source)
                self.assertEqual(result["native_terminal"], "SUCCESS_REPORTED")
                self.assertFalse(result["source_content_and_live_module_verified"])

    def test_private_ambiguous_terminal_and_extra_public_contracts_are_rejected(self):
        final = final_receipt()
        final["native_terminal"] = "/owner/private"
        with self.assertRaises(custody.CustodyRejected):
            custody.projection(CURRENT, final, 0, "success", b"cipher", self.recipient)
        final = final_receipt()
        final["native_operation_retried"] = True
        with self.assertRaises(custody.CustodyRejected):
            custody.projection(CURRENT, final, 0, "success", b"cipher", self.recipient)

    def test_snapshot_keeps_original_database_and_journals_without_SQL_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            values = {"motionwright.sqlite3": b"original DB",
                      "motionwright.sqlite3-wal": b"original WAL",
                      "motionwright.sqlite3-shm": b"original SHM"}
            for name, raw in values.items():
                (root / name).write_bytes(raw)
            archive = custody.gather_snapshot({"owner": root})
            encrypted = custody.encrypt_snapshot(archive, self.recipient, CURRENT)
            recovered, _ = custody.decrypt_snapshot(encrypted, self.private)
            with tarfile.open(fileobj=io.BytesIO(recovered), mode="r:") as reader:
                for name, raw in values.items():
                    self.assertEqual(reader.extractfile("owner/" + name).read(), raw)
                    self.assertEqual((root / name).read_bytes(), raw)

    def test_snapshot_detects_namespace_growth_during_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "first").write_bytes(b"original")
            original = custody.regular
            def read(path, limit=custody.MAX_FILE):
                result = original(path, limit)
                (root / "new").write_bytes(b"concurrent original")
                return result
            with mock.patch.object(sealed, "regular", side_effect=read):
                with self.assertRaisesRegex(custody.CustodyRejected, "inventory_changed"):
                    custody.gather_snapshot({"owner": root})

    def test_exclusive_custody_output_preserves_existing_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "original"
            path.write_bytes(b"original bytes")
            with self.assertRaises(FileExistsError):
                custody.new_bytes(path, b"replacement")
            self.assertEqual(path.read_bytes(), b"original bytes")

    def test_zip_member_is_unique_regular_and_hash_bound(self):
        data = b"original source"
        def packed(kind=None, duplicate=False, name="file"):
            result = io.BytesIO()
            with zipfile.ZipFile(result, "w") as archive:
                entry = zipfile.ZipInfo(name)
                if kind is not None:
                    entry.create_system = 3
                    entry.external_attr = kind << 16
                archive.writestr(entry, data)
                if duplicate:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", UserWarning)
                        archive.writestr(entry, data)
            return result.getvalue()
        self.assertEqual(custody.zip_member(packed(), "file", custody.sha(data), 100), data)
        for raw in (packed(custody.stat.S_IFLNK | 0o777), packed(custody.stat.S_IFIFO | 0o600),
                    packed(duplicate=True), packed(name="../file")):
            with self.subTest(size=len(raw)), self.assertRaises(custody.CustodyRejected):
                custody.zip_member(raw, "file", custody.sha(data), 100)
        with self.assertRaises(custody.CustodyRejected):
            custody.zip_member(packed(), "file", "0" * 64, 100)


    def test_partial_capture_keeps_readable_originals_and_never_reports_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "db").write_bytes(b"original DB")
            (root / "wal").write_bytes(b"changing WAL")
            original = custody.regular
            def read(path, limit=custody.MAX_FILE):
                if path.name == "wal":
                    raise custody.CustodyRejected("same_handle_metadata_changed")
                return original(path, limit)
            with mock.patch.object(sealed, "regular", side_effect=read):
                raw, complete = custody.gather_snapshot({"owner": root}, preserve_partial=True)
            self.assertFalse(complete)
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
                self.assertEqual(archive.extractfile("owner/db").read(), b"original DB")
                manifest = json.load(archive.extractfile("snapshot-manifest.json"))
                self.assertFalse(manifest["selected_originals_complete"])
                rows = manifest["roots"]["owner"]["files"]
                self.assertEqual(rows[-1]["status"], "CAPTURE_FAILED")
                self.assertFalse(rows[-1]["automatic_retry"])
            self.assertEqual((root / "wal").read_bytes(), b"changing WAL")

    def test_namespace_growth_is_retained_as_partial_without_retrying_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "first").write_bytes(b"original")
            original = custody.regular
            calls = []
            def read(path, limit=custody.MAX_FILE):
                calls.append(path.name)
                result = original(path, limit)
                (root / "new").write_bytes(b"new original")
                return result
            with mock.patch.object(sealed, "regular", side_effect=read):
                raw, complete = custody.gather_snapshot({"owner": root}, preserve_partial=True)
            self.assertFalse(complete)
            self.assertEqual(calls, ["first"])
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
                self.assertEqual(archive.extractfile("owner/first").read(), b"original")
                manifest = json.load(archive.extractfile("snapshot-manifest.json"))
                self.assertEqual(manifest["roots"]["owner"]["status"], "PARTIAL")

    def test_partial_originals_cannot_borrow_scoped_native_success(self):
        result = custody.projection(CURRENT, final_receipt(), 0, "success", b"cipher",
                                    self.recipient, producer_started=True, source_unchanged=True,
                                    selected_originals_complete=False)
        self.assertFalse(result["source_content_and_live_module_verified"])
        self.assertFalse(result["selected_originals_complete"])
        self.assertEqual(result["native_terminal"], "SUCCESS_REPORTED")

    def test_directory_identity_excludes_access_time_but_not_change_time(self):
        from types import SimpleNamespace
        values = dict(st_dev=1, st_ino=2, st_mode=0o40700, st_uid=1001, st_gid=1001,
                      st_size=4, st_mtime_ns=5, st_ctime_ns=6, st_atime_ns=7)
        original = custody.metadata_identity(SimpleNamespace(**values))
        self.assertEqual(original, custody.metadata_identity(
            SimpleNamespace(**{**values, "st_atime_ns": 8})))
        self.assertNotEqual(original, custody.metadata_identity(
            SimpleNamespace(**{**values, "st_ctime_ns": 8})))

    def test_changed_source_prevents_the_original_producer_intent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            records = root / "records"
            records.mkdir()
            (records / "g2-inputs.json").write_bytes(b"{}")
            (records / "source-before.json").write_bytes(custody.json_bytes({"original": True}))
            env = {"GITHUB_WORKSPACE": str(root)}
            with mock.patch.object(custody.sys, "platform", "linux"), \
                 mock.patch.object(custody.os, "geteuid", return_value=1001, create=True), \
                 mock.patch.object(custody, "context", return_value=CURRENT), \
                 mock.patch.object(custody, "names", return_value=(root, root, root)), \
                 mock.patch.object(custody, "source_guard", return_value={"changed": True}):
                with self.assertRaisesRegex(custody.CustodyRejected, "source_changed_before_producer"):
                    custody.begin(env)
            self.assertFalse((records / "producer-started.json").exists())

    def test_second_producer_intent_cannot_replace_the_first(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            records = root / "records"
            records.mkdir()
            source = {"original": True}
            (records / "g2-inputs.json").write_bytes(b"{}")
            (records / "source-before.json").write_bytes(custody.json_bytes(source))
            env = {"GITHUB_WORKSPACE": str(root)}
            with mock.patch.object(custody.sys, "platform", "linux"), \
                 mock.patch.object(custody.os, "geteuid", return_value=1001, create=True), \
                 mock.patch.object(custody.os, "getuid", return_value=1001, create=True), \
                 mock.patch.object(custody, "context", return_value=CURRENT), \
                 mock.patch.object(custody, "names", return_value=(root, root, root)), \
                 mock.patch.object(custody, "source_guard", return_value=source):
                custody.begin(env)
                original = (records / "producer-started.json").read_bytes()
                with self.assertRaises(FileExistsError):
                    custody.begin(env)
            self.assertEqual((records / "producer-started.json").read_bytes(), original)



    def test_shared_context_rejects_malformed_values_in_both_directions(self):
        for key, value in (("source_sha", "unknown"), ("source_sha", True),
                           ("semwright_sha", ""), ("semwright_sha", "A" * 40),
                           ("run_id", 123), ("run_id", "0"), ("run_id", "01"),
                           ("run_attempt", False), ("run_attempt", "-1")):
            context = {**CURRENT, key: value}
            with self.subTest(key=key, value=value):
                with self.assertRaises(custody.CustodyRejected):
                    custody.encrypt_snapshot(b"original", self.recipient, context)
                raw = custody.encrypt_snapshot(b"original", self.recipient, CURRENT)
                offset = len(custody.MAGIC)
                count = int.from_bytes(raw[offset:offset + 4], "big")
                header = custody.strict(raw[offset + 4:offset + 4 + count])
                header["context"] = context
                aad = custody.json_bytes(header)
                changed = custody.MAGIC + len(aad).to_bytes(4, "big") + aad + raw[offset + 4 + count:]
                with self.assertRaises(custody.CustodyRejected):
                    custody.decrypt_snapshot(changed, self.private)

    def test_selected_absent_root_is_never_a_complete_original_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            absent = Path(temporary).resolve() / "absent"
            with self.assertRaisesRegex(custody.CustodyRejected, "snapshot_root_absent"):
                custody.gather_snapshot({"owner": absent})
            raw, complete = custody.gather_snapshot({"owner": absent}, preserve_partial=True)
            self.assertFalse(complete)
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
                manifest = json.load(archive.extractfile("snapshot-manifest.json"))
                self.assertEqual(manifest["roots"]["owner"]["status"], "ABSENT")
                self.assertFalse(manifest["selected_originals_complete"])



if __name__ == "__main__":
    unittest.main()
