"""Offline cryptographic and closed-publication contracts; no renderer is launched."""
import base64
from contextlib import ExitStack, contextmanager
import copy
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tarfile
import tempfile
import types
import unittest
from unittest import mock
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import native_evidence_projection as selection_core
import native_mlt_evidence_selection as selection
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


    def test_film_source_report_is_sealed_exactly_and_absence_is_recorded(self):
        # Linux uses the production openat reader and real committed Git blobs.
        # The portable adapter keeps this crypto/selection unit contract usable
        # on authoring hosts; Linux filesystem security has its own full suite.
        class PortableFixtureHandles:
            def __init__(self, roots):
                self.roots = {name: Path(value) for name, value in roots.items()}
                self.initial = {name: selection_core.identity(path.stat())
                                for name, path in self.roots.items()}
            def close(self): pass
            def stable_roots(self):
                return all(selection_core.identity(path.stat()) == self.initial[name]
                           for name, path in self.roots.items())
            def stat_member(self, root, parts):
                path = self.roots[root].joinpath(*parts)
                info = path.lstat()
                selection_core.require(custody.stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                                       "member-regular-single-link")
                return selection_core.identity(info)
            def read(self, root, parts, maximum):
                path = self.roots[root].joinpath(*parts)
                raw, _ = custody.regular(path, maximum)
                return raw, self.stat_member(root, parts)
        handles = (selection_core.RootHandles if os.name == "posix" and hasattr(os, "O_NOFOLLOW")
                   else PortableFixtureHandles)
        for present in (True, False):
            with self.subTest(present=present), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                repo = root / "source"; repo.mkdir(mode=0o700)
                control = root / "control"; control.mkdir(mode=0o700)
                for name in ("records", "intake", "handoff", "logs", "public"):
                    (control / name).mkdir(mode=0o700)
                for name in selection.FILM_SOURCE_PATHS:
                    path = repo / name; path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(("Synthetic source identity fixture: " + name + "\n").encode())
                runner_source = Path(__file__).resolve().parents[1] / "ci_test_runner.py"
                (repo / "tooling/ci_test_runner.py").write_bytes(runner_source.read_bytes())
                test_dir = repo / "tooling/tests"; test_dir.mkdir()
                for name, passed in (("test_native_mlt_owner_workspace.py", True),
                                     ("test_native_film180_original_g2.py", False)):
                    # Explicit miniature fixtures exercise report serialization;
                    # they are never evidence for the real source-gate roster.
                    (test_dir / name).write_text("import unittest\n"
                        "class SourceGateFixture(unittest.TestCase):\n"
                        "    def test_original_source_gate(self):\n"
                        "        self.assertTrue(" + repr(passed) + ")\n", encoding="utf-8")
                git_env = {k:v for k,v in os.environ.items() if not k.upper().startswith("GIT_")}
                git_env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                               GIT_CONFIG_SYSTEM=os.devnull)
                def git(*args):
                    return subprocess.check_output(["git", *args], cwd=repo, env=git_env,
                                                   stderr=subprocess.PIPE)
                git("init", "--quiet")
                git("add", ".")
                git("-c", "user.name=Motionwright Test", "-c", "user.email=fixture@example.invalid",
                    "-c", "core.hooksPath=" + os.devnull, "commit", "--no-gpg-sign", "--quiet",
                    "-m", "Synthetic source identity fixture")
                current = {**CURRENT, "source_sha": git("rev-parse", "HEAD").decode().strip()}
                files = {name: custody.sha((repo / name).read_bytes()) for name in selection.FILM_SOURCE_PATHS}
                source = dict(context=current, tree=git("rev-parse", "HEAD^{tree}").decode().strip(), files=files)
                request = self.dispatch()
                request.update(schema=custody.FILM_DISPATCH_SCHEMA, motionwright_sha=current["source_sha"])
                request["compiler"]["source_mode"] = custody.SOURCE_MODE
                request["compiler"]["members"] = {
                    key: key for key in ("compiled_module", "compiler_receipt_path")}
                custody.new_json(control / "records/dispatch.json", request)
                custody.new_json(control / "records/source-before.json", source)
                common = root / "mw-mlt-g2-contracts-123-1"; common.mkdir(mode=0o700)
                film = root / "mw-film180-contracts-123-1"
                env = {
                    "GITHUB_ACTIONS": "true", "CI": "true", "RUNNER_OS": "Linux",
                    "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
                    "GITHUB_SHA": current["source_sha"], "GITHUB_RUN_ID": current["run_id"],
                    "GITHUB_WORKSPACE": str(repo), "MW_MLT_SCOPE": custody.FILM_SCOPE,
                }
                def run_contracts(directory, pattern, code):
                    process = subprocess.run([sys.executable, "-B", "-E", "-s",
                        str(repo / "tooling/ci_test_runner.py"), "--repo", str(repo),
                        "--mode", "dotted", "--require-complete", "--expected-source", current["source_sha"],
                        "--report", str(directory / "contracts.json"), "--pattern", pattern],
                        cwd=repo, env={**git_env, **env}, capture_output=True, timeout=30)
                    self.assertEqual(process.returncode, code, process.stderr.decode("utf-8", "replace"))
                    return (directory / "contracts.json").read_bytes()
                common_original = run_contracts(common, "test_native_mlt_owner_workspace.py", 0)
                self.assertEqual(json.loads(common_original)["status"], "PASS")
                secret = b"UNLISTED-CONTRACT-NEIGHBOR-NOT-READ"
                (common / "unlisted.json").write_bytes(secret)
                original = None
                if present:
                    film.mkdir(mode=0o700)
                    original = run_contracts(film, "test_native_film180_original_g2.py", 1)
                    self.assertEqual(json.loads(original)["status"], "FAIL")
                    (film / "unlisted.json").write_bytes(secret)
                # Limit the simulated non-root runner identity to custody's
                # platform guard; real reader ownership uses the actual uid.
                custody_os = types.SimpleNamespace(**vars(os)); custody_os.geteuid = lambda:1001
                reads = []; real_read = handles.read; real_iterdir = Path.iterdir
                def record_read(handle, label, parts, maximum):
                    reads.append((label, "/".join(parts)))
                    return real_read(handle, label, parts, maximum)
                def publication_only_iterdir(path):
                    self.assertEqual(path, control / "public")
                    return real_iterdir(path)
                with ExitStack() as stack:
                    stack.enter_context(mock.patch.object(custody.sys, "platform", "linux"))
                    stack.enter_context(mock.patch.object(custody, "os", custody_os))
                    stack.enter_context(mock.patch.object(custody, "names", return_value=(
                        control, root / "installation", root / "absent-native-session")))
                    stack.enter_context(mock.patch.object(custody, "runner_root", return_value=root))
                    stack.enter_context(mock.patch.object(custody, "source_guard", return_value=source))
                    stack.enter_context(mock.patch.object(selection_core, "RootHandles", handles))
                    stack.enter_context(mock.patch.object(handles, "read", new=record_read))
                    stack.enter_context(mock.patch.object(Path, "iterdir", new=publication_only_iterdir))
                    stack.enter_context(mock.patch.object(custody, "gather_snapshot", side_effect=AssertionError("recursive snapshot refused")))
                    result = custody.seal(env, "failure")
                ciphertext = (control / "public/sealed-originals.bin").read_bytes()
                raw, context = custody.decrypt_snapshot(ciphertext, self.private)
                self.assertEqual(context, current)
                self.assertNotIn(secret, raw)
                self.assertTrue(all(path == "contracts.json" for label, path in reads
                                    if label in ("contracts", "film-contracts")))
                with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
                    manifest = json.load(archive.extractfile("selected-evidence-manifest.json"))
                    row = next(r for r in manifest["members"] if r["role"] == "film-source-contracts")
                    if present:
                        self.assertEqual(row["state"], "INCLUDED")
                        self.assertEqual(row["kind"], "json-original")
                        self.assertTrue(row["original_bytes"])
                        self.assertEqual(archive.extractfile(row["member"]).read(), original)
                        self.assertEqual(row["retained_sha256"], custody.sha(original))
                    else:
                        self.assertEqual(row["state"], "REQUIRED_MISSING")
                        self.assertNotIn("member", row)
                        self.assertFalse(manifest["required_layers_complete"])
                        self.assertFalse(result["selected_layers_complete"])
                self.assertEqual(result["schema"], "motionwright.native.film180-g2-public/2")
                self.assertEqual(result["native_terminal"], "NOT_STARTED")
                self.assertFalse(result["selected_intake_verified"])
                self.assertFalse(result["source_content_and_live_module_verified"])
                self.assertFalse(result["physical_AV_master_acceptance"])
                self.assertFalse(result["human_acceptance"])
                self.assertFalse(result["release_authorized"])
                self.assertEqual({p.name for p in (control / "public").iterdir()},
                                 {"sealed-originals.bin", "public-evidence.json"})





class Ffv1SeedAndPublicV3Tests(unittest.TestCase):
    """Existing producer/guard integration with synthetic process boundaries only."""
    def seed(self):
        identity="01234567-89ab-cdef-0123-456789abcdef"
        return dict(project_id=identity,resource="project:"+identity,generation=identity,
            revision=102,master_profile_id=identity,width=1280,height=720,fps_num=30,
            fps_den=1,scene_count=33,deliverable_count=3)

    def test_existing_seed_callback_retains_exact_bytes_before_json_parse(self):
        import native_mlt_sequence_e2e as producer
        import native_av_failure_guard as guard
        stdout=json.dumps(self.seed(),indent=1).encode()+b"\r\n"
        stderr=b"\xffseed-original-stderr\r\n"
        argv=["/synthetic/native-mlt-sequence-e2e","seed","/synthetic/motionwright.sqlite3"]
        completed=subprocess.CompletedProcess(argv,0,stdout,stderr)
        with tempfile.TemporaryDirectory() as directory:
            evidence=Path(directory)
            real_loads=json.loads
            def parse_after_custody(*args,**kwargs):
                self.assertEqual((evidence / Path(selection.FFV1_SEED_STDOUT_PATH).name).read_bytes(),stdout)
                self.assertEqual((evidence / Path(selection.FFV1_SEED_STDERR_PATH).name).read_bytes(),stderr)
                self.assertTrue((evidence / Path(selection.FFV1_SEED_TERMINAL_PATH).name).is_file())
                return real_loads(*args,**kwargs)
            with mock.patch.object(guard.subprocess,"run",return_value=completed) as run, \
                 mock.patch.object(guard.json,"loads",side_effect=parse_after_custody):
                result=producer.run_original_seed(argv,env={},evidence=evidence,current=CURRENT)
            self.assertEqual(result,self.seed())
            self.assertEqual(run.call_count,1)
            self.assertEqual(run.call_args.args[0],argv)
            self.assertEqual(run.call_args.kwargs["timeout"],30)
            self.assertFalse(run.call_args.kwargs["check"])
            terminal=real_loads((evidence / Path(selection.FFV1_SEED_TERMINAL_PATH).name).read_bytes())
            self.assertEqual(terminal["context"],{**CURRENT,"scenario":"repaired-g2"})
            self.assertEqual(terminal["stdout"]["sha256"],selection_core.sha(stdout))
            self.assertTrue(terminal["before_assemble"])
            self.assertFalse(terminal["stdout"]["stream_eof_attested"])
            self.assertFalse(terminal["native_outcome_inferred"])
            self.assertFalse(terminal["automatic_retry"])

    def test_seed_native_failure_wins_over_failed_original_writer_without_retry(self):
        import native_mlt_sequence_e2e as producer
        import native_av_failure_guard as guard
        import native_mlt_g2_observation as observation
        stdout=b'{"ok":false,"error":{"code":"BackendFailed","outcome_known":false}}\n'
        stderr=b"original seed failure"
        argv=["/synthetic/native-mlt-sequence-e2e","seed","/synthetic/motionwright.sqlite3"]
        original_diagnostic=guard.classify_native_command_failure(
            stdout=stdout,stderr=stderr,exit_code=17)
        original_write=observation.new_bytes
        writes=[]
        def failing_writer(path,body):
            writes.append(path.name)
            if path.name==Path(selection.FFV1_SEED_STDOUT_PATH).name:
                raise OSError("synthetic competing custody failure")
            return original_write(path,body)
        with tempfile.TemporaryDirectory() as directory:
            evidence=Path(directory)
            with mock.patch.object(guard.subprocess,"run",
                    return_value=subprocess.CompletedProcess(argv,17,stdout,stderr)) as run, \
                 mock.patch.object(observation,"new_bytes",side_effect=failing_writer):
                with self.assertRaises(guard.NativeCommandFailure) as observed:
                    producer.run_original_seed(argv,env={},evidence=evidence,current=CURRENT)
            self.assertEqual(observed.exception.diagnostic,original_diagnostic)
            self.assertTrue(observed.exception.private_capture_failed)
            self.assertEqual(run.call_count,1)
            self.assertEqual(writes,[Path(path).name for path in (
                selection.FFV1_SEED_STDOUT_PATH,selection.FFV1_SEED_STDERR_PATH,selection.FFV1_SEED_TERMINAL_PATH)])
            self.assertEqual((evidence / Path(selection.FFV1_SEED_STDERR_PATH).name).read_bytes(),stderr)
            terminal=json.loads((evidence / Path(selection.FFV1_SEED_TERMINAL_PATH).name).read_bytes())
            self.assertEqual(terminal["exit_code"],17)
            self.assertFalse(terminal["stdout"]["retained"])
            self.assertTrue(terminal["stderr"]["retained"])

    def test_g2_seed_custody_failure_stops_main_before_driver_setup_or_assemble(self):
        from contextlib import nullcontext
        import native_mlt_sequence_e2e as producer
        import native_av_failure_guard as guard
        import native_mlt_g2_observation as observation
        stdout=selection_core.canonical(self.seed())
        original_write=observation.new_bytes
        def failing_writer(path,body):
            if path.name==Path(selection.FFV1_SEED_STDOUT_PATH).name:
                raise OSError("synthetic seed custody failure")
            return original_write(path,body)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            owner=root/"owner";owner.mkdir()
            session=types.SimpleNamespace(evidence_dir=root/"session",
                inputs={"run_id":CURRENT["run_id"],"run_attempt":"1"},seed_environment=lambda env:env)
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(producer,"exact_inputs"))
                stack.enter_context(mock.patch.object(producer,"GITHUB_SHA",CURRENT["source_sha"]))
                stack.enter_context(mock.patch.object(producer,"EVIDENCE",root/"unused"))
                stack.enter_context(mock.patch.object(producer,"native_owner_workspace",return_value=nullcontext(str(owner))))
                stack.enter_context(mock.patch.object(producer.subprocess,"check_output",return_value=CURRENT["source_sha"]+"\n"))
                run=stack.enter_context(mock.patch.object(guard.subprocess,"run",
                    return_value=subprocess.CompletedProcess(["synthetic-seed"],0,stdout,b"")))
                copy=stack.enter_context(mock.patch.object(producer.shutil,"copyfile"))
                stack.enter_context(mock.patch.object(observation,"new_bytes",side_effect=failing_writer))
                with self.assertRaises(guard.NativeCommandFailure) as observed:
                    producer.main(session)
                self.assertTrue(observed.exception.private_capture_failed)
                self.assertEqual(run.call_count,1)
                self.assertEqual(run.call_args.args[0][1],"seed")
                copy.assert_not_called()
            self.assertTrue((root/"session"/"producer"/Path(selection.FFV1_SEED_TERMINAL_PATH).name).is_file())

    def test_seed_oversize_original_retains_bounded_prefix_and_refuses_fixture_admission(self):
        import native_mlt_sequence_e2e as producer
        import native_av_failure_guard as guard
        stdout=b"x"*65537
        argv=["/synthetic/native-mlt-sequence-e2e","seed","/synthetic/motionwright.sqlite3"]
        with tempfile.TemporaryDirectory() as directory:
            evidence=Path(directory)
            with mock.patch.object(guard.subprocess,"run",
                    return_value=subprocess.CompletedProcess(argv,0,stdout,b"")) as run:
                with self.assertRaises(guard.NativeCommandFailure):
                    producer.run_original_seed(argv,env={},evidence=evidence,current=CURRENT)
            self.assertEqual(run.call_count,1)
            self.assertEqual((evidence / Path(selection.FFV1_SEED_STDOUT_PATH).name).read_bytes(),stdout[:65536])
            terminal=json.loads((evidence / Path(selection.FFV1_SEED_TERMINAL_PATH).name).read_bytes())
            self.assertTrue(terminal["stdout"]["prefix_truncated"])
            self.assertEqual(terminal["stdout"]["returned_buffer_bytes"],len(stdout))
            self.assertEqual(terminal["stdout"]["returned_buffer_sha256"],selection_core.sha(stdout))

    def test_seed_duplicate_json_key_is_retained_but_never_becomes_fixture_authority(self):
        import native_mlt_sequence_e2e as producer
        import native_av_failure_guard as guard
        base=selection_core.canonical(self.seed()).strip()
        stdout=base[:-1]+b',"revision":102}\n'
        argv=["/synthetic/native-mlt-sequence-e2e","seed","/synthetic/motionwright.sqlite3"]
        with tempfile.TemporaryDirectory() as directory:
            evidence=Path(directory)
            with mock.patch.object(guard.subprocess,"run",
                    return_value=subprocess.CompletedProcess(argv,0,stdout,b"")) as run:
                with self.assertRaises(selection_core.SelectionError):
                    producer.run_original_seed(argv,env={},evidence=evidence,current=CURRENT)
            self.assertEqual(run.call_count,1)
            self.assertEqual((evidence / Path(selection.FFV1_SEED_STDOUT_PATH).name).read_bytes(),stdout)

    def test_public_v3_preserves_unknown_and_does_not_promote_partial_native_layers(self):
        from test_native_mlt_evidence_selection import Ffv1DiagnosticWorld,Ffv1OriginalSelectionTests
        world=Ffv1DiagnosticWorld(Ffv1OriginalSelectionTests().plan())
        del world.files[("observation","producer/native-multisegment-timeline-evidence.json")]
        entry=world.admission["diagnostic_contract"]["entries"][2]
        world.put("owner",entry["relative_path"],b"\xfforiginal FFV1 failure")
        with world.patched():
            _raw,manifest=world.capture()
        current={key:world.admission["context"][key] for key in CURRENT}
        public=custody.selected_projection(current,None,1,"failure",b"synthetic-ciphertext",
            {"sha256":"e"*64},manifest,producer_started=True,source_unchanged=True,
            intake_verified=False,diagnostic_admission=world.admission)
        self.assertEqual(public["schema"],"motionwright.native.mlt-g2-public/3")
        self.assertEqual(public["custody_scope"],"explicit-source-bound-selected-layers/3")
        self.assertEqual(public["native_terminal"],"ERROR_OR_UNKNOWN")
        self.assertFalse(public["selected_layers_complete"])
        self.assertFalse(public["selected_intake_verified"])
        self.assertFalse(public["source_content_and_live_module_verified"])
        self.assertTrue(public["raw_diagnostics_included"])
        self.assertEqual(public["diagnostic_admission_sha256"],
                         selection_core.sha(selection_core.canonical(world.admission)))
        for name in ("diagnostic_eof_claimed","diagnostic_history_complete_claimed",
                     "diagnostic_payloads_redacted","physical_AV_master_acceptance",
                     "native_operation_retried","human_acceptance","release_authorized"):
            self.assertFalse(public[name])
            changed=copy.deepcopy(public);changed[name]=True
            with self.subTest(field=name),self.assertRaises(selection_core.SelectionError):
                selection.validate_public_v3(changed)
        with self.assertRaises(custody.CustodyRejected):
            custody.selected_projection(current,None,1,"failure",b"synthetic-ciphertext",
                {"sha256":"e"*64},manifest,producer_started=True,source_unchanged=True)



class Ffv1EarlySealTests(unittest.TestCase):
    """Original seed/collector/crypto calls, with synthetic process and FS adapters."""
    @classmethod
    def setUpClass(cls):
        cls.private=rsa.generate_private_key(public_exponent=65537,key_size=3072)
        der=cls.private.public_key().public_bytes(serialization.Encoding.DER,
                                                 serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.recipient=dict(spki_der_base64=base64.b64encode(der).decode(),sha256=custody.sha(der))

    def fixture(self, root):
        from native_selection_test_fixtures import World,CURRENT as fixture_current
        world=World("repaired-g2")
        current=dict(fixture_current)
        repo=root/"source";repo.mkdir(mode=0o700)
        control=root/"control";control.mkdir(mode=0o700)
        for name in ("records","intake","handoff","public"):
            (control/name).mkdir(mode=0o700)
        session=root/"session";session.mkdir(mode=0o700)
        (session/"producer").mkdir(mode=0o700)
        owner=root/"motionwright-native-av-g2-fixture";owner.mkdir(mode=0o700)
        (owner/"motionwright-data").mkdir(mode=0o700)
        info=owner.lstat()
        # This portable unit fixture supplies the owner's existing guarded stat
        # record; it is not Linux openat or owner-authority evidence.
        values=list(info);values[0]=custody.stat.S_IFDIR|0o700
        guarded_info=os.stat_result(values)
        source=world.document("control","records/source-before.json")
        preflight=root/("mw-mlt-preflight-custody-"+current["run_id"]+"-1")
        preflight.mkdir(mode=0o700)
        (preflight/"records").mkdir(mode=0o700);(preflight/"public").mkdir(mode=0o700)
        request=dict(schema=custody.DISPATCH_SCHEMA,motionwright_sha=current["source_sha"],
            semwright_sha=current["semwright_sha"],
            official_runtime=dict(artifact=descriptor(),receipt_sha256="d"*64,
                known_original_module_sha256="e"*64,known_melt_sha256="f"*64),
            compiler=dict(artifact=descriptor(),members={name:name for name in (
                "original_c_source","repaired_c_source","compiled_module","compiler_receipt_path")}),
            recipient=self.recipient)
        exit_receipt=dict(context=current,exit_code=1,native_operation_retried=False)
        custody.new_json(control/"records/dispatch.json",request)
        custody.new_json(control/"records/source-before.json",source)
        custody.new_json(control/"records/producer-exit.json",exit_receipt)
        dispatch_raw=custody.json_bytes(request)
        custody.new_bytes(preflight/"records/dispatch.original.json",dispatch_raw)
        custody.new_json(preflight/"records/source-before.json",dict(
            git=dict(synthetic_fixture=True),physical=source))
        custody.new_json(preflight/"records/initial.json",dict(
            schema="motionwright.native.mlt-preflight-initial/1",context=current,
            github_job="native-mlt-sequence",scope="repaired-g2",
            effect_namespaces_initially_absent=True,dispatch_input_sha256=custody.sha(dispatch_raw),
            admission_granted=False,native_effect_started=False,automatic_retry=False))
        marker=dict(schema="motionwright.native.mlt-g2-owner-workspace/1",
            motionwright_sha=current["source_sha"],semwright_sha=current["semwright_sha"],
            run_id=current["run_id"],run_attempt="1",path=str(owner),
            device=info.st_dev,inode=info.st_ino,uid=info.st_uid,gid=info.st_gid,
            custody_policy="retain_originals_in_place_for_sealing",native_effect_started=False)
        custody.new_json(session/"owner-workspace.json",marker)
        # Only pre-plan observation originals exist in this synthetic attempt.
        world.files={key:value for key,value in world.files.items() if key[0]!="observation"}
        world.put("observation","owner-workspace.json",marker)
        world.put("control","records/producer-exit.json",exit_receipt)
        world.roots.update(source=str(repo),control=str(control),observation=str(session),owner=str(owner))
        env=dict(GITHUB_ACTIONS="true",CI="true",RUNNER_OS="Linux",
            GITHUB_EVENT_NAME="workflow_dispatch",GITHUB_RUN_ATTEMPT="1",
            GITHUB_SHA=current["source_sha"],GITHUB_RUN_ID=current["run_id"],
            GITHUB_WORKSPACE=str(repo),GITHUB_JOB="native-mlt-sequence",MW_MLT_SCOPE="repaired-g2")
        return types.SimpleNamespace(root=root,world=world,current=current,repo=repo,control=control,
            session=session,owner=owner,source=source,env=env,guarded_info=guarded_info,
            preflight=preflight)

    @contextmanager
    def seal_context(self, fixture):
        actual_lstat=Path.lstat
        def guarded_lstat(path,*args,**kwargs):
            if path==fixture.owner:
                return fixture.guarded_info
            return actual_lstat(path,*args,**kwargs)
        custody_os=types.SimpleNamespace(**vars(os))
        custody_os.geteuid=lambda:1001
        custody_os.getuid=lambda:fixture.guarded_info.st_uid
        with ExitStack() as stack:
            stack.enter_context(fixture.world.patched())
            stack.enter_context(mock.patch.object(custody.sys,"platform","linux"))
            stack.enter_context(mock.patch.object(custody,"os",custody_os))
            stack.enter_context(mock.patch.object(Path,"lstat",new=guarded_lstat))
            stack.enter_context(mock.patch.object(custody,"names",return_value=(
                fixture.control,fixture.root/"installation",fixture.session)))
            stack.enter_context(mock.patch.object(custody,"runner_root",return_value=fixture.root))
            # The portable fixture does not claim to exercise POSIX ACL guards.
            stack.enter_context(mock.patch.object(custody,"_ffv1_preflight_root",return_value=fixture.preflight))
            stack.enter_context(mock.patch.object(custody,"source_guard",return_value=fixture.source))
            stack.enter_context(mock.patch.object(custody,"gather_snapshot",
                side_effect=AssertionError("second snapshot/runtime forbidden")))
            capture=stack.enter_context(mock.patch.object(selection_core,"capture_selected_originals",
                wraps=selection_core.capture_selected_originals))
            encrypt=stack.enter_context(mock.patch.object(custody,"encrypt_snapshot",
                wraps=custody.encrypt_snapshot))
            yield types.SimpleNamespace(capture=capture,encrypt=encrypt)

    def test_failed_original_seed_reaches_one_capture_and_the_existing_encryption(self):
        import native_mlt_sequence_e2e as producer
        import native_av_failure_guard as guard
        stdout=b'{"ok":false,"error":{"code":"BackendFailed","outcome_known":false}}\n'
        stderr=b"synthetic original seed failure\xff\r\n"
        with tempfile.TemporaryDirectory() as directory:
            fixture=self.fixture(Path(directory).resolve())
            argv=[str(fixture.repo/"target"/"debug"/"examples"/"native-mlt-sequence-e2e"),
                  "seed",str(fixture.owner/"motionwright-data"/"motionwright.sqlite3")]
            with mock.patch.object(guard.subprocess,"run",
                    return_value=subprocess.CompletedProcess(argv,17,stdout,stderr)) as run:
                with self.assertRaises(guard.NativeCommandFailure):
                    producer.run_original_seed(argv,env={},evidence=fixture.session/"producer",
                                               current=fixture.current)
            self.assertEqual(run.call_count,1)
            for path in (selection.FFV1_SEED_STDOUT_PATH,selection.FFV1_SEED_STDERR_PATH,
                         selection.FFV1_SEED_TERMINAL_PATH):
                fixture.world.put("observation",path,(fixture.session/path).read_bytes())
            self.assertFalse((fixture.session/selection.FFV1_PLAN_PATH).exists())
            with self.seal_context(fixture) as observed:
                frozen=custody.freeze_ffv1_preflight_expectation(fixture.env,fixture.session)
                self.assertEqual(frozen["state"],"PLAN_ABSENT")
                public=custody.seal(fixture.env,"failure")
            self.assertEqual(observed.capture.call_count,1)
            self.assertEqual(observed.encrypt.call_count,1)
            self.assertEqual(public["schema"],"motionwright.native.mlt-g2-public/3")
            self.assertEqual(public["diagnostic_status"],"FFV1_DIAGNOSTIC_PLAN_ABSENT")
            self.assertEqual(public["selected_evidence_schema"],selection_core.MANIFEST_SCHEMA)
            self.assertEqual(public["native_terminal"],"ERROR_OR_UNKNOWN")
            self.assertFalse(public["diagnostic_admitted"])
            self.assertFalse(public["selected_intake_verified"])
            self.assertFalse(public["source_content_and_live_module_verified"])
            self.assertFalse(public["native_operation_retried"])
            self.assertFalse((fixture.control/"records/ffv1-diagnostic-admission.json").exists())
            encrypted=(fixture.control/"public/sealed-originals.bin").read_bytes()
            raw,current=custody.decrypt_snapshot(encrypted,self.private)
            self.assertEqual(current,fixture.current)
            self.assertNotIn(stdout,raw)
            self.assertNotIn(stderr,raw)
            with tarfile.open(fileobj=io.BytesIO(raw),mode="r:") as archive:
                manifest=selection_core.strict(archive.extractfile("selected-evidence-manifest.json").read())
            selection.checked_ffv1_plan_absence(manifest,current=fixture.current,
                                                  source_bindings=fixture.world.bindings)
            self.assertEqual(manifest["lost_scope"],list(selection_core.LOST_SCOPE))
            with self.assertRaisesRegex(selection_core.SelectionError,
                    "ffv1-plan-absence-is-not-complete-native-intake"):
                selection.verify_sealed_selection(encrypted,self.private,public)

    def test_late_plan_at_either_capture_boundary_never_gets_the_absent_public_variant(self):
        for appearance in ("before","after"):
            with self.subTest(appearance=appearance),tempfile.TemporaryDirectory() as directory:
                fixture=self.fixture(Path(directory).resolve())
                with self.seal_context(fixture):
                    custody.freeze_ffv1_preflight_expectation(fixture.env,fixture.session)
                fixture.world.reads.clear();fixture.world.stats.clear()
                observations=[]
                def appear(key):
                    if key==("observation",selection.FFV1_PLAN_PATH):
                        observations.append(key)
                        # Cotejo observes two initial ENOENTs; Core then checks
                        # both sides of its sole capture, without reading plan bytes.
                        if len(observations)==(3 if appearance=="before" else 4):
                            fixture.world.put("observation",selection.FFV1_PLAN_PATH,
                                              b"late-plan-original-not-admitted")
                fixture.world.on_stat=appear
                with self.seal_context(fixture) as observed:
                    with self.assertRaises(selection_core.SelectionError):
                        custody.seal(fixture.env,"failure")
                self.assertEqual(observed.capture.call_count,1)
                self.assertEqual(observed.encrypt.call_count,0)
                self.assertEqual(len(observations),4)
                self.assertFalse(any((fixture.control/"public").iterdir()))
                self.assertEqual(fixture.world.reads.count(
                    ("observation",selection.FFV1_PLAN_PATH)),0)

    def test_seal_preserves_non_absence_admission_error_without_capture_or_downgrade(self):
        for error in (PermissionError("synthetic original access failure"),
                      ValueError("synthetic invalid original JSON"),
                      selection_core.SelectionError("ffv1-seed-plan-identity")):
            with self.subTest(error=type(error).__name__),tempfile.TemporaryDirectory() as directory:
                fixture=self.fixture(Path(directory).resolve())
                with self.seal_context(fixture):
                    custody.freeze_ffv1_preflight_expectation(fixture.env,fixture.session)
                with self.seal_context(fixture) as observed, \
                     mock.patch.object(selection,"read_ffv1_admission",side_effect=error):
                    with self.assertRaises(type(error)) as caught:
                        custody.seal(fixture.env,"failure")
                self.assertIs(caught.exception,error)
                self.assertEqual(observed.capture.call_count,0)
                self.assertEqual(observed.encrypt.call_count,0)
                self.assertFalse(any((fixture.control/"public").iterdir()))

    def test_absent_public_union_refuses_diagnostic_or_native_scope_escalation(self):
        from native_selection_test_fixtures import World,CURRENT as fixture_current
        world=World("repaired-g2")
        del world.files[("observation","producer/native-multisegment-timeline-evidence.json")]
        with world.patched():
            collector=selection.Selection(roots=world.roots,current=fixture_current,
                scenario="repaired-g2",source_bindings=world.bindings,
                role_contract=selection.mlt_contract(),json_policies=selection.policies("repaired-g2"),
                ffv1_stage_plan_absent=True)
            _raw,manifest=collector.capture()
        public=custody.selected_projection(fixture_current,None,1,"failure",b"synthetic-ciphertext",
            self.recipient,manifest,producer_started=True,source_unchanged=True,
            ffv1_stage_plan_absent=True)
        self.assertIs(selection.validate_public_v3(public),public)
        changes=(
            ("diagnostic_status","unknown"),
            ("selected_evidence_schema",selection_core.MANIFEST_SCHEMA_V3),
            ("custody_scope","explicit-source-bound-selected-layers/3"),
            ("selected_lost_scope",list(selection_core.LOST_SCOPE_V3)),
            ("diagnostic_admitted",True),("diagnostic_plan_absence_verified",False),
            ("diagnostic_contract_sha256","a"*64),("diagnostic_admission_sha256","b"*64),
            ("diagnostic_intake_verified",True),("diagnostic_roster_complete",True),
            ("raw_diagnostics_included",True),("selected_intake_verified",True),
            ("source_content_and_live_module_verified",True),
        )
        for name,value in changes:
            with self.subTest(field=name),self.assertRaises(selection_core.SelectionError):
                selection.validate_public_v3({**public,name:value})



class Ffv1PreflightBridgeTests(unittest.TestCase):
    """Actual existing preflight composition and crypto; synthetic owner/process adapters."""
    @classmethod
    def setUpClass(cls):
        cls.private=rsa.generate_private_key(public_exponent=65537,key_size=3072)
        der=cls.private.public_key().public_bytes(serialization.Encoding.DER,
                                                 serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.recipient=dict(spki_der_base64=base64.b64encode(der).decode(),sha256=custody.sha(der))

    seal_context=Ffv1EarlySealTests.seal_context

    def fixture(self, root, *, incomplete=True):
        from test_native_mlt_evidence_selection import Ffv1DiagnosticWorld,Ffv1OriginalSelectionTests
        fixture=Ffv1EarlySealTests.fixture(self,root)
        previous=fixture.world
        world=Ffv1DiagnosticWorld(Ffv1OriginalSelectionTests().plan())
        world.roots.update(previous.roots)
        world.put("observation","owner-workspace.json",
                  (fixture.session/"owner-workspace.json").read_bytes())
        world.put("control","records/producer-exit.json",
                  (fixture.control/"records/producer-exit.json").read_bytes())
        terminal=world.document("observation",selection.FFV1_SEED_TERMINAL_PATH)
        terminal["argv"]=[str(fixture.repo/"target"/"debug"/"examples"/"native-mlt-sequence-e2e"),
                          "seed",str(fixture.owner/"motionwright-data"/"motionwright.sqlite3")]
        world.put("observation",selection.FFV1_SEED_TERMINAL_PATH,terminal)
        if incomplete:
            del world.files[("observation","producer/native-multisegment-timeline-evidence.json")]
        with world.patched():
            world.admission=selection.read_ffv1_admission(roots=world.roots,current=fixture.current,
                                                          source_bindings=world.bindings)
        fixture.world=world
        return fixture

    def source_capsule(self, fixture, *, incomplete_reports=False):
        # Reuse the actual two equal workflow fragments and existing gather/
        # encryption functions. Only filesystem permission checking is adapted
        # for this portable fixture; no source/native gate PASS is manufactured.
        from test_native_mlt_workflow_preflight import workflow_namespace
        code=workflow_namespace()
        def private(path,directory):
            self.assertTrue(path.is_absolute() and path.resolve(strict=True)==path)
            self.assertTrue(path.is_dir() if directory else path.is_file())
        code["p1_private"]=private
        suffix=fixture.current["run_id"]+"-1"
        for label,prefix in (("common","mw-mlt-g2-contracts-"),
                             ("film","mw-film180-contracts-"),
                             ("workflow","mw-mlt-workflow-contracts-")):
            root=fixture.root/(prefix+suffix);root.mkdir(mode=0o700)
            for name,body in (
                ("contracts.json",b'{"status":"FAIL","synthetic_source_fixture":true}\n'),
                ("runner.stdout",b"original source stdout\xff\r\n"),
                ("runner.stderr",b"original source stderr\n"),
                ("runner-exit.json",b'{"exit_code":1,"automatic_retry":false}\n'),
            ):
                if incomplete_reports and label=="common" and name=="runner.stderr":
                    continue
                sealed.new_bytes(root/name,body)
        env={**fixture.env,"P1_COMMON_OUTCOME":"failure","P1_FILM_OUTCOME":"skipped",
             "P1_PUBLICATION_OUTCOME":"failure","P1_PREPARE_OUTCOME":"success",
             "P1_ADMISSION_OUTCOME":"success","P1_PRODUCER_OUTCOME":"failure"}
        source_state=mock.Mock(side_effect=AssertionError("no new source/runtime process"))
        public,code_value=code["p1_finish"](env,sealed,custody,source_state)
        source_state.assert_not_called()
        self.assertEqual(code_value,2 if incomplete_reports else 0)
        self.assertFalse(public["native_originals_claimed"])
        self.assertFalse(public["source_test_pass_claimed"])
        self.assertFalse(public["admission_granted"])
        envelope=(fixture.preflight/"public/sealed-source-originals.bin").read_bytes()
        plaintext,authenticated=sealed.decrypt_snapshot(envelope,self.private)
        self.assertEqual(authenticated,fixture.current)
        return public,envelope,plaintext

    def two_capsules(self, fixture, *, incomplete_reports=False):
        with self.seal_context(fixture) as observed:
            status=custody.freeze_ffv1_preflight_expectation(fixture.env,fixture.session)
            self.assertEqual(status["state"],"FROZEN")
            source_public,source_envelope,source_plaintext=self.source_capsule(
                fixture,incomplete_reports=incomplete_reports)
            native_public=custody.seal(fixture.env,"failure")
        self.assertEqual(observed.capture.call_count,1)
        self.assertEqual(observed.encrypt.call_count,1)
        native_envelope=(fixture.control/"public/sealed-originals.bin").read_bytes()
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_STORED) as output:
            output.writestr("sealed-source-originals.bin",source_envelope)
            output.writestr("source-public-evidence.json",sealed.json_bytes(source_public))
        fixture.source_artifact_zip=archive.getvalue()
        # Synthetic already-admitted descriptor; this fixture performs no GitHub
        # request and cannot establish real artifact provenance.
        fixture.source_artifact=dict(artifact_id=123456,run_id=int(fixture.current["run_id"]),
            run_attempt=1,source_sha=fixture.current["source_sha"],
            zip_sha256=sealed.sha(fixture.source_artifact_zip))
        return source_public,source_envelope,source_plaintext,native_public,native_envelope

    @staticmethod
    def rewrite_source(raw, change):
        entries=[]
        with tarfile.open(fileobj=io.BytesIO(raw),mode="r:") as archive:
            for info in archive.getmembers():
                entries.append((info.name,archive.extractfile(info).read()))
        change(entries)
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode="w",format=tarfile.PAX_FORMAT) as archive:
            for name,body in entries:
                info=tarfile.TarInfo(name);info.size=len(body);info.mode=0o600
                info.uid=info.gid=0;info.mtime=0
                archive.addfile(info,io.BytesIO(body))
        return stream.getvalue()

    def test_freeze_retains_metadata_once_before_existing_preflight_seal(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture=self.fixture(Path(directory).resolve())
            originals=dict(fixture.world.files)
            with self.seal_context(fixture) as observed, \
                 mock.patch.object(custody,"new_bytes",wraps=custody.new_bytes) as write:
                status=custody.freeze_ffv1_preflight_expectation(fixture.env,fixture.session)
                original_reads=len(fixture.world.reads)
                with self.assertRaisesRegex(custody.CustodyRejected,"must-precede"):
                    custody.freeze_ffv1_preflight_expectation(fixture.env,fixture.session)
            self.assertEqual([call.args[0] for call in write.call_args_list],[
                fixture.preflight/custody.FFV1_PREFLIGHT_ADMISSION,
                fixture.preflight/custody.FFV1_PREFLIGHT_FREEZE])
            self.assertEqual(len(fixture.world.reads),original_reads)
            self.assertEqual(observed.capture.call_count,0)
            self.assertEqual(observed.encrypt.call_count,0)
            raw=(fixture.preflight/custody.FFV1_PREFLIGHT_ADMISSION).read_bytes()
            admitted=selection_core.strict(raw)
            self.assertEqual(admitted,fixture.world.admission)
            self.assertEqual((status["admission_sha256"],status["admission_bytes"]),
                             (selection_core.sha(raw),len(raw)))
            self.assertTrue(status["before_producer_return"])
            self.assertTrue(status["before_preflight_seal"])
            self.assertFalse(status["native_originals_claimed"])
            self.assertFalse(status["native_effects_absent_claimed"])
            self.assertEqual(fixture.world.files,originals)
            self.assertNotIn(b'"argv"',raw)
            self.assertNotIn(b'"slots"',raw)
            self.assertNotIn(fixture.world.plan_original,raw)

    def test_freeze_rejects_preexisting_intent_or_expectation_before_any_new_write(self):
        for relative in ("records/seal-intent.json",custody.FFV1_PREFLIGHT_ADMISSION,
                         custody.FFV1_PREFLIGHT_FREEZE):
            with self.subTest(preexisting=relative),tempfile.TemporaryDirectory() as directory:
                fixture=self.fixture(Path(directory).resolve())
                sealed.new_bytes(fixture.preflight/relative,b"existing original")
                with self.seal_context(fixture), \
                     mock.patch.object(custody,"new_bytes",wraps=custody.new_bytes) as write, \
                     mock.patch.object(selection,"read_ffv1_admission",
                                       wraps=selection.read_ffv1_admission) as admission_read:
                    with self.assertRaisesRegex(custody.CustodyRejected,"must-precede"):
                        custody.freeze_ffv1_preflight_expectation(fixture.env,fixture.session)
                write.assert_not_called();admission_read.assert_not_called()
                self.assertEqual((fixture.preflight/relative).read_bytes(),b"existing original")

    def test_existing_producer_returns_after_freeze_and_preserves_original_unknown_on_custody_failure(self):
        import native_mlt_sequence_e2e as producer
        session=types.SimpleNamespace(evidence_dir=Path("/synthetic/native-session"),
                                      secondary=mock.Mock())
        order=[];result={"synthetic":True}
        def body(value):
            self.assertIs(value,session);order.append("producer");return result
        def freeze(env,evidence):
            self.assertIs(env,producer.os.environ)
            self.assertEqual(evidence,session.evidence_dir);order.append("freeze")
        with mock.patch.object(producer,"main",side_effect=body) as main, \
             mock.patch.object(custody,"freeze_ffv1_preflight_expectation",side_effect=freeze):
            self.assertIs(producer.run_g2_with_frozen_expectation(session),result)
        self.assertEqual(order,["producer","freeze"]);main.assert_called_once_with(session)
        for primary in (RuntimeError("synthetic original UNKNOWN"),KeyboardInterrupt("synthetic interruption")):
            with self.subTest(primary=type(primary).__name__):
                primary.outcome_known=False
                session.secondary=mock.Mock(side_effect=OSError("secondary custody failed"))
                freeze_error=OSError("original freeze failed")
                with mock.patch.object(producer,"main",side_effect=primary) as main, \
                     mock.patch.object(custody,"freeze_ffv1_preflight_expectation",
                                       side_effect=freeze_error) as freeze_call:
                    with self.assertRaises(type(primary)) as caught:
                        producer.run_g2_with_frozen_expectation(session)
                self.assertIs(caught.exception,primary)
                self.assertFalse(caught.exception.outcome_known)
                self.assertEqual(main.call_count,1);self.assertEqual(freeze_call.call_count,1)
                session.secondary.assert_called_once_with("ffv1_preflight_expectation_custody")
        freeze_error=OSError("success cannot conceal missing freeze")
        with mock.patch.object(producer,"main",return_value=result), \
             mock.patch.object(custody,"freeze_ffv1_preflight_expectation",side_effect=freeze_error):
            with self.assertRaises(OSError) as caught:
                producer.run_g2_with_frozen_expectation(session)
        self.assertIs(caught.exception,freeze_error)

    def test_native_seal_rejects_missing_changed_or_foreign_frozen_expectation_before_capture(self):
        for case in ("missing-contract","changed-contract-bytes","foreign-context",
                     "changed-source-before","changed-original-stderr","changed-plan"):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as directory:
                fixture=self.fixture(Path(directory).resolve())
                with self.seal_context(fixture):
                    custody.freeze_ffv1_preflight_expectation(fixture.env,fixture.session)
                path=fixture.preflight/custody.FFV1_PREFLIGHT_ADMISSION
                if case=="missing-contract":
                    path.unlink()
                elif case=="changed-contract-bytes":
                    path.write_bytes(path.read_bytes()+b"\n")
                elif case=="foreign-context":
                    value=selection_core.strict(path.read_bytes());value["context"]["run_id"]="999"
                    body=selection_core.canonical(value);path.write_bytes(body)
                    status_path=fixture.preflight/custody.FFV1_PREFLIGHT_FREEZE
                    status=sealed.strict(status_path.read_bytes())
                    status.update(admission_sha256=sealed.sha(body),admission_bytes=len(body))
                    status_path.write_bytes(sealed.json_bytes(status))
                elif case=="changed-source-before":
                    source=fixture.preflight/"records/source-before.json"
                    source.write_bytes(source.read_bytes()+b"\n")
                elif case=="changed-original-stderr":
                    fixture.world.put("observation",selection.FFV1_SEED_STDERR_PATH,b"changed stderr\xff")
                else:
                    fixture.world.edit("observation",selection.FFV1_PLAN_PATH,
                                       lambda value:value.update(revision=value["revision"]+1))
                with self.seal_context(fixture) as observed:
                    with self.assertRaises((custody.CustodyRejected,selection_core.SelectionError,OSError)):
                        custody.seal(fixture.env,"failure")
                self.assertEqual(observed.capture.call_count,0)
                self.assertEqual(observed.encrypt.call_count,0)
                self.assertFalse(any((fixture.control/"public").iterdir()))

    def test_existing_workflow_capsule_recovers_independent_contract_for_partial_native_intake(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture=self.fixture(Path(directory).resolve())
            source_public,source_envelope,source_raw,native_public,native_envelope=self.two_capsules(
                fixture,incomplete_reports=True)
            self.assertEqual(source_public["status"],"PARTIAL_SOURCE_ORIGINALS_SEALED")
            self.assertFalse(source_public["captured_source_originals_complete"])
            self.assertTrue(native_public["diagnostic_intake_verified"])
            self.assertFalse(native_public["selected_layers_complete"])
            self.assertFalse(native_public["selected_intake_verified"])
            with fixture.world.patched():
                admitted=custody.recover_ffv1_preflight_admission(
                    source_envelope,self.private,source_public,current=fixture.current)
                report=custody.verify_ffv1_sealed_custody(fixture.source_artifact_zip,native_envelope,self.private,
                    native_public,current=fixture.current,expected_source_artifact=fixture.source_artifact,
                    require_complete=False)
                with self.assertRaises(selection_core.SelectionError):
                    custody.verify_ffv1_sealed_custody(fixture.source_artifact_zip,native_envelope,self.private,
                        native_public,current=fixture.current,expected_source_artifact=fixture.source_artifact)
            self.assertEqual(admitted,fixture.world.admission)
            self.assertTrue(report["expected_contract_recovered_from_independent_source_custody"])
            self.assertTrue(report["diagnostic_intake_verified"])
            self.assertEqual(report["native_terminal"],"ERROR_OR_UNKNOWN")
            self.assertFalse(report["selected_layers_complete"])
            self.assertFalse(report["source_content_and_live_module_verified"])
            self.assertFalse(report["native_operation_retried"])
            native_raw,authenticated=sealed.decrypt_snapshot(native_envelope,self.private)
            self.assertEqual(authenticated,fixture.current)
            self.assertNotIn(fixture.world.plan_original,source_raw)
            self.assertIn(fixture.world.plan_original,native_raw)
            self.assertNotIn(fixture.world.files[("observation",selection.FFV1_SEED_STDOUT_PATH)],source_raw)
            for path in (selection.FFV1_SEED_STDOUT_PATH,selection.FFV1_SEED_TERMINAL_PATH):
                self.assertIn(fixture.world.files[("observation",path)],native_raw)

    def test_offline_recovery_rejects_missing_duplicate_or_aliased_source_members(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture=self.fixture(Path(directory).resolve())
            source_public,source_envelope,source_raw,_public,_envelope=self.two_capsules(fixture)
            for target in (custody.FFV1_PREFLIGHT_ADMISSION,custody.FFV1_PREFLIGHT_FREEZE,
                           "records/source-before.json"):
                for mode in ("missing","duplicate","alias"):
                    with self.subTest(target=target,mode=mode):
                        def change(entries):
                            original=next(item for item in entries if item[0]==target)
                            if mode=="duplicate":
                                entries.append(original)
                            else:
                                entries[:]=[item for item in entries if item[0]!=target]
                                if mode=="alias":
                                    entries.append((target.replace("records/","records/./"),original[1]))
                        altered=self.rewrite_source(source_raw,change)
                        encrypted=sealed.encrypt_snapshot(altered,self.recipient,fixture.current)
                        public={**source_public,"sealed_originals_sha256":sealed.sha(encrypted),
                                "sealed_originals_bytes":len(encrypted)}
                        with self.assertRaises(custody.CustodyRejected):
                            custody.recover_ffv1_preflight_admission(
                                encrypted,self.private,public,current=fixture.current)

    def test_native_archive_cannot_replace_independent_source_contract_or_context(self):
        from test_native_mlt_evidence_selection import rewrite_selected
        with tempfile.TemporaryDirectory() as directory:
            fixture=self.fixture(Path(directory).resolve())
            source_public,source_envelope,_raw,native_public,native_envelope=self.two_capsules(fixture)
            raw,_current=sealed.decrypt_snapshot(native_envelope,self.private)
            def forge(manifest,entries):
                manifest["diagnostic_contract"]["entries"][0]["maximum"]=1
                manifest["diagnostic_contract_sha256"]=selection_core.sha(
                    selection_core.canonical(manifest["diagnostic_contract"]))
            changed=rewrite_selected(raw,forge)
            with tarfile.open(fileobj=io.BytesIO(changed),mode="r:") as archive:
                manifest_raw=archive.extractfile("selected-evidence-manifest.json").read()
            manifest=selection_core.strict(manifest_raw)
            encrypted=sealed.encrypt_snapshot(changed,self.recipient,fixture.current)
            public={**native_public,"sealed_originals_sha256":sealed.sha(encrypted),
                "sealed_originals_bytes":len(encrypted),"selected_manifest_sha256":sealed.sha(manifest_raw),
                "diagnostic_contract_sha256":manifest["diagnostic_contract_sha256"]}
            with fixture.world.patched():
                with self.assertRaisesRegex(selection_core.SelectionError,"public-external-admission"):
                    custody.verify_ffv1_sealed_custody(fixture.source_artifact_zip,encrypted,self.private,
                        public,current=fixture.current,expected_source_artifact=fixture.source_artifact,
                        require_complete=False)
                foreign={**fixture.current,"run_id":"999"}
                with self.assertRaises(custody.CustodyRejected):
                    custody.verify_ffv1_sealed_custody(fixture.source_artifact_zip,native_envelope,self.private,
                        native_public,current=foreign,expected_source_artifact=fixture.source_artifact,
                        require_complete=False)

    def test_offline_source_artifact_requires_independent_run_source_zip_and_closed_members(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture=self.fixture(Path(directory).resolve())
            _public,_envelope,_raw,native_public,native_envelope=self.two_capsules(fixture)
            changes=(
                ("source",lambda d:d.update(source_sha="f"*40)),
                ("run",lambda d:d.update(run_id=d["run_id"]+1)),
                ("attempt",lambda d:d.update(run_attempt=2)),
                ("zip",lambda d:d.update(zip_sha256="f"*64)),
                ("extra",lambda d:d.update(admitted=True)),
                ("boolean-id",lambda d:d.update(artifact_id=True)),
            )
            for label,change in changes:
                with self.subTest(case=label):
                    descriptor=copy.deepcopy(fixture.source_artifact);change(descriptor)
                    with mock.patch.object(custody,"decrypt_snapshot",
                            side_effect=AssertionError("must bind source artifact first")) as decrypt:
                        with self.assertRaises(custody.CustodyRejected):
                            custody.verify_ffv1_sealed_custody(fixture.source_artifact_zip,
                                native_envelope,self.private,native_public,current=fixture.current,
                                expected_source_artifact=descriptor,require_complete=False)
                    decrypt.assert_not_called()
            for mode in ("extra-member","duplicate-public"):
                with self.subTest(case=mode):
                    entries=[]
                    with zipfile.ZipFile(io.BytesIO(fixture.source_artifact_zip)) as archive:
                        entries=[(entry.filename,archive.read(entry)) for entry in archive.infolist()]
                    if mode=="extra-member":
                        entries.append(("adjacent-private.bin",b"not admitted"))
                    else:
                        entries.append(next(entry for entry in entries
                                            if entry[0]=="source-public-evidence.json"))
                    output=io.BytesIO()
                    with warnings.catch_warnings(),zipfile.ZipFile(output,"w") as archive:
                        warnings.simplefilter("ignore",UserWarning)
                        for name,body in entries:
                            archive.writestr(name,body)
                    raw=output.getvalue()
                    expected={**fixture.source_artifact,"zip_sha256":sealed.sha(raw)}
                    with self.assertRaisesRegex(custody.CustodyRejected,"member-roster"):
                        custody.verify_ffv1_sealed_custody(raw,native_envelope,self.private,
                            native_public,current=fixture.current,expected_source_artifact=expected,
                            require_complete=False)

    def test_source_snapshot_incomplete_or_appended_physical_records_are_not_admitted(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture=self.fixture(Path(directory).resolve())
            public,envelope,raw,_native_public,_native_envelope=self.two_capsules(fixture)
            # A complete authenticated ciphertext does not make an incomplete
            # or concatenated physical TAR acceptable.
            data_end=0
            while raw[data_end:data_end+512]!=b"\0"*512:
                info=tarfile.TarInfo.frombuf(raw[data_end:data_end+512],"utf-8","strict")
                data_end+=512+((info.size+511)//512)*512
            malformed=(
                ("truncated-footer",raw[:data_end+512]),
                ("appended-tar",raw+raw),
                ("nonzero-after-footer",raw+b"x"*512),
            )
            for label,changed in malformed:
                with self.subTest(case=label):
                    encrypted=sealed.encrypt_snapshot(changed,self.recipient,fixture.current)
                    altered_public={**public,"sealed_originals_sha256":sealed.sha(encrypted),
                                    "sealed_originals_bytes":len(encrypted)}
                    with self.assertRaises(custody.CustodyRejected):
                        custody.recover_ffv1_preflight_admission(
                            encrypted,self.private,altered_public,current=fixture.current)

    def test_original_authentication_error_stays_primary_and_no_recovery_writer_or_native_intake_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture=self.fixture(Path(directory).resolve())
            source_public,source_envelope,_raw,native_public,native_envelope=self.two_capsules(fixture)
            primary=InvalidTag("synthetic authenticated ciphertext failure")
            with mock.patch.object(custody,"decrypt_snapshot",side_effect=primary), \
                 mock.patch.object(custody,"new_bytes",side_effect=OSError("must not write")) as write, \
                 mock.patch.object(selection,"verify_sealed_selection",
                                   side_effect=AssertionError("must not admit native")) as intake:
                with self.assertRaises(InvalidTag) as caught:
                    custody.verify_ffv1_sealed_custody(fixture.source_artifact_zip,native_envelope,self.private,
                        native_public,current=fixture.current,expected_source_artifact=fixture.source_artifact,
                        require_complete=False)
            self.assertIs(caught.exception,primary)
            write.assert_not_called();intake.assert_not_called()



class AudioSourceCustodyTests(unittest.TestCase):
    def test_audio_environment_rejects_loader_compiler_and_physical_overrides(self):
        root=Path(tempfile.gettempdir()).absolute()/"owned-audio"
        for key in ("LD_PRELOAD","LD_LIBRARY_PATH","LD_AUDIT","RUSTC_WRAPPER",
                    "CARGO_ALIAS_CLIPPY","SEMWRIGHT_TEST_MLT_VIDEO","SEMWRIGHT_AUDIO_CAPTURE_FIXTURE"):
            with self.subTest(key=key),self.assertRaises(custody.CustodyRejected):
                custody.audio_environment({"PATH":"tools",key:"untrusted"},root)
        result=custody.audio_environment({"PATH":"tools","GITHUB_TOKEN":"secret","RANDOM_SECRET":"secret","GIT_NO_REPLACE_OBJECTS":"0"},root)
        self.assertNotIn("GITHUB_TOKEN",result);self.assertNotIn("RANDOM_SECRET",result)
        self.assertEqual(result["CARGO_TARGET_DIR"],str(root/"target"))
        self.assertEqual(result["TMPDIR"],str(root/"tmp"))
        self.assertEqual(result["GIT_NO_REPLACE_OBJECTS"],"1")

    def test_audio_original_clock_has_fixed_aggregate_and_retention_reserve(self):
        value=custody.audio_clock(10,20)
        self.assertEqual(value["deadline_ns"],10+10200*10**9)
        self.assertEqual(value["work_deadline_ns"],10+9600*10**9)
        for start,now in ((True,20),(30,20),(0,20),(10,10+10200*10**9)):
            with self.subTest(start=start,now=now),self.assertRaises(custody.CustodyRejected):
                custody.audio_clock(start,now)

    def producer_fixture(self, *, wrong_normal=False, failed_label=None):
        work=Path(tempfile.gettempdir()).absolute()/"audio-sdk"
        target=Path(tempfile.gettempdir()).absolute()/"audio-target"
        calls=[];phases=[]
        def call(argv,cwd,env,logs,label,timeout,context):
            calls.append((label,argv))
            return {"exit_code":1 if label==failed_label else 0,"label":label}
        def paths(raw,exit_code,repo,root,specs,*,testing):
            self.assertEqual(repo,work);self.assertEqual(root,target)
            if testing:return {s["name"]:str(target/"debug/deps"/(s["name"]+"-hash")) for s in specs}
            return {s["name"]:str(target/("foreign" if wrong_normal else "debug")/s["name"]) for s in specs}
        adapter=types.SimpleNamespace(observer=types.SimpleNamespace(original_stream=lambda logs,row,key:b"Cargo JSON"),
                                      select_binary_paths=paths)
        def run():return custody.audio_prepare_producers(adapter,call,"/owned/cargo",work,{},work,{"id":1},target,phases.append)
        return run,calls,phases

    def test_audio_same_target_fake_producers_precede_final_test_sealing(self):
        run,calls,phases=self.producer_fixture()
        value=run()
        self.assertEqual(phases,["strict-clippy","normal-tools-build","affected-test-build"])
        self.assertEqual([x[0] for x in calls],phases)
        self.assertEqual(set(value["collections"]),{"tests","normal"})
        self.assertEqual(value["collections"]["normal"]["label"],"affected-test-build")
        self.assertEqual(value["clippy"],"PASS")

    def test_audio_foreign_target_stops_before_final_test_producer(self):
        run,calls,phases=self.producer_fixture(wrong_normal=True)
        with self.assertRaisesRegex(custody.CustodyRejected,"same-target"):run()
        self.assertEqual([x[0] for x in calls],["strict-clippy","normal-tools-build"])

    def test_audio_failed_producer_never_reaches_a_later_producer(self):
        run,calls,phases=self.producer_fixture(failed_label="strict-clippy")
        with self.assertRaisesRegex(custody.CustodyRejected,"producer-failed"):run()
        self.assertEqual(len(calls),1)

    def test_audio_unknown_producer_is_not_retried_or_relabelled(self):
        failure=RuntimeError("UNKNOWN_ORIGINAL_NO_RETRY")
        calls=[]
        def unknown(*args):calls.append(args);raise failure
        with self.assertRaisesRegex(RuntimeError,"UNKNOWN_ORIGINAL_NO_RETRY"):
            custody.audio_prepare_producers(types.SimpleNamespace(),unknown,"cargo",Path("sdk"),{},
                                           Path("logs"),{},Path("target"),lambda name:None)
        self.assertEqual(len(calls),1)

    def test_audio_workflow_source_profiles_cannot_enter_physical_matrix(self):
        workflow=(Path(__file__).resolve().parents[2]/".github/workflows/native-mlt-sequence-e2e.yml").read_text()
        self.assertIn("options: [normal, source, repaired-g2, film180-g2, audio-source-g0, audio-source-g1]",workflow)
        guard="inputs.verification_scope == 'normal' || inputs.verification_scope == 'repaired-g2' || inputs.verification_scope == 'film180-g2'"
        physical=workflow.split("  native-mlt-sequence:\n",1)[1].split("    runs-on:",1)[0]
        self.assertIn(guard,physical)
        audio=workflow.split("  audio-source:\n",1)[1]
        self.assertIn("--mode both --require-complete --expected-source",audio)
        self.assertIn("--pattern test_native_mlt_g2_custody.py --pattern test_native_mlt_evidence_selection.py",audio)
        retention = audio.split("      - name: Retain source originals for this exact attempt\n", 1)[1]
        # Failed source controls still retain their originals, but an admission
        # failure cannot upload an uninitialized path or an unrelated directory.
        self.assertIn(
            "if: ${{ always() && env.MW_AUDIO_CONTROLS != '' && env.MW_AUDIO_ORIGINALS != '' }}",
            retention,
        )
        self.assertNotIn("success()", retention)
        self.assertIn("${{ env.MW_AUDIO_CONTROLS }}/*.json", retention)
        self.assertIn("${{ env.MW_AUDIO_ORIGINALS }}", retention)
        self.assertIn("if-no-files-found: error", retention)
        self.assertIn("ref: "+selection.AUDIO_SOURCE_SHA,audio)
        self.assertNotIn("--ignored --nocapture",audio)
        import re
        caps=[int(value) for value in re.findall(r"^        timeout-minutes: (\d+)$",audio,re.M)]
        self.assertEqual(caps,[2,2,6,5,16,3,3,172,5])
        self.assertEqual(sum(caps),214)
        self.assertIn("    timeout-minutes: 220\n",audio)
        self.assertGreater(16*60,3*2*150)
        self.assertGreater(172*60,sum(custody.AUDIO_PHASE_SECONDS.values()))

if __name__ == "__main__":
    unittest.main()
