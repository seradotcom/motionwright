"""Offline canonical-executable contracts; no installed files or renderer are changed."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_mlt_g2_observation as g2
import native_mlt_g2_custody as custody
import native_mlt_runtime_admission as admission

MODULE_SHA = "1" * 64
MELT_SHA = "2" * 64


def official_receipt():
    module = {"installed_path": admission.INSTALLED.as_posix(), "sha256": MODULE_SHA}
    melt = {"installed_path": "/usr/bin/melt-7", "sha256": MELT_SHA}
    return {
        "schema": g2.OFFICIAL_SCHEMA,
        "runtime_bindings": {"original_module": module, "melt": melt},
        "members": {
            "libmlt7": {**module, "member_type": "regular", "archive_alias": None,
                       "architecture_header": "ELF64-LSB-x86_64"},
            "melt": {**melt, "member_type": "regular",
                     "architecture_header": "ELF64-LSB-x86_64",
                     "archive_alias": {"archive_path": "usr/bin/melt",
                                       "linkname": "melt-7", "target_path": "usr/bin/melt-7"}},
        },
    }


def alias_stat(**changes):
    return SimpleNamespace(**{
        **dict(st_dev=1, st_ino=2, st_mode=stat.S_IFLNK | 0o777, st_uid=0,
               st_gid=0, st_size=6, st_mtime_ns=3, st_ctime_ns=4, st_atime_ns=5),
        **changes,
    })


class OfficialExecutableContractTests(unittest.TestCase):
    def validate(self, receipt):
        g2.verify_official_runtime_bindings(receipt, MODULE_SHA, MELT_SHA)

    def test_canonical_regular_member_and_exact_archive_alias_are_accepted(self):
        self.validate(official_receipt())
        self.assertEqual(g2.MELT.as_posix(), "/usr/bin/melt-7")
        self.assertEqual(g2.MELT_ALIAS.as_posix(), "/usr/bin/melt")

    def test_old_alias_binary_binding_and_extra_runtime_authority_are_refused(self):
        for change in ("old-path", "extra-binding"):
            receipt = official_receipt()
            if change == "old-path":
                receipt["runtime_bindings"]["melt"]["installed_path"] = "/usr/bin/melt"
            else:
                receipt["runtime_bindings"]["verified"] = True
            with self.subTest(change=change), self.assertRaisesRegex(
                    g2.G2Unverified, "official_runtime_binary_bindings"):
                self.validate(receipt)

    def test_receipt_cannot_substitute_either_expected_binary_digest(self):
        for binding in ("original_module", "melt"):
            receipt = official_receipt()
            receipt["runtime_bindings"][binding]["sha256"] = "f" * 64
            with self.subTest(binding=binding), self.assertRaises(g2.G2Unverified):
                self.validate(receipt)

    def test_canonical_member_must_remain_regular_and_bound_to_the_same_bytes(self):
        for key, value in (("member_type", "symlink"), ("installed_path", "/usr/bin/melt"),
                           ("sha256", "f" * 64), ("architecture_header", "ELF32")):
            receipt = official_receipt()
            receipt["members"]["melt"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(
                    g2.G2Unverified, "official_runtime_member_bindings"):
                self.validate(receipt)
        for members in (None, {}, {"melt": {}}, {**official_receipt()["members"], "extra": {}}):
            receipt = official_receipt()
            receipt["members"] = members
            with self.subTest(members=members), self.assertRaises(g2.G2Unverified):
                self.validate(receipt)

    def test_archive_alias_contract_rejects_missing_extra_absolute_and_chained_targets(self):
        alias = official_receipt()["members"]["melt"]["archive_alias"]
        for value in (None, {}, {**alias, "verified": True}, {**alias, "linkname": "/usr/bin/melt-7"},
                      {**alias, "linkname": "../bin/melt-7"}, {**alias, "linkname": "another-link"},
                      {**alias, "target_path": "usr/bin/another"}, {**alias, "archive_path": "bin/melt"}):
            receipt = official_receipt()
            receipt["members"]["melt"]["archive_alias"] = value
            with self.subTest(alias=value), self.assertRaisesRegex(
                    g2.G2Unverified, "official_runtime_alias_binding"):
                self.validate(receipt)

    def test_module_member_does_not_gain_alias_or_alternate_path_permissions(self):
        for key, value in (("archive_alias", {"linkname": "module"}),
                           ("installed_path", "/tmp/libmltavformat.so"),
                           ("sha256", MELT_SHA)):
            receipt = official_receipt()
            receipt["members"]["libmlt7"][key] = value
            with self.subTest(key=key), self.assertRaises(g2.G2Unverified):
                self.validate(receipt)


class InstalledAliasObservationTests(unittest.TestCase):
    def observe(self, *, first=None, second=None, targets=None, reader=None):
        alias = mock.Mock(spec=Path)
        alias.as_posix.return_value = "/usr/bin/melt"
        alias.lstat.side_effect = [first or alias_stat(), second or alias_stat()]
        links = mock.Mock(side_effect=targets or ["melt-7", "melt-7"])
        binary = reader or mock.Mock(return_value=MELT_SHA)
        with mock.patch.object(g2, "MELT_ALIAS", alias), \
             mock.patch.object(g2.os, "readlink", links), \
             mock.patch.object(g2.loaded, "sha256", binary):
            result = g2.verified_installed_melt_alias(MELT_SHA)
        return result, alias, links, binary

    def test_alias_observation_hashes_only_the_canonical_regular_binary_once(self):
        result, alias, links, binary = self.observe()
        binary.assert_called_once_with(g2.MELT, MELT_SHA, g2.loaded.MAX_MELT)
        self.assertEqual(links.call_args_list, [mock.call(alias), mock.call(alias)])
        self.assertEqual(result["canonical_installed_path"], "/usr/bin/melt-7")
        self.assertEqual(result["installed_alias_path"], "/usr/bin/melt")
        self.assertEqual(result["linkname"], "melt-7")

    def test_regular_or_hardlink_alias_is_refused_before_readlink_or_binary_hashing(self):
        for mode in (stat.S_IFREG | 0o755, stat.S_IFDIR | 0o755, stat.S_IFIFO | 0o600):
            reader = mock.Mock()
            with self.subTest(mode=mode), self.assertRaisesRegex(
                    g2.G2Unverified, "alias_not_symlink"):
                self.observe(first=alias_stat(st_mode=mode), reader=reader)
            reader.assert_not_called()

    def test_absolute_traversal_or_second_link_target_is_refused_before_binary_read(self):
        for target in ("/usr/bin/melt-7", "../bin/melt-7", "./melt-7", "melt-70", "another-link"):
            reader = mock.Mock()
            with self.subTest(target=target), self.assertRaisesRegex(
                    g2.G2Unverified, "alias_target"):
                self.observe(targets=[target], reader=reader)
            reader.assert_not_called()

    def test_alias_inode_mode_owner_size_and_timestamps_must_remain_stable(self):
        for field in ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid",
                      "st_size", "st_mtime_ns", "st_ctime_ns"):
            changed = alias_stat(**{field: getattr(alias_stat(), field) + 1})
            with self.subTest(field=field), self.assertRaisesRegex(
                    g2.G2Unverified, "alias_changed"):
                self.observe(second=changed)

    def test_alias_target_changed_while_canonical_file_was_hashed_is_refused(self):
        with self.assertRaisesRegex(g2.G2Unverified, "alias_changed"):
            self.observe(targets=["melt-7", "another-link"])

    def test_unreadable_initial_alias_cannot_trigger_a_binary_read(self):
        reader = mock.Mock()
        with self.assertRaisesRegex(g2.G2Unverified, "alias_unreadable"):
            self.observe(first=FileNotFoundError("removed"), reader=reader)
        reader.assert_not_called()

    def test_unreadable_final_alias_preserves_failure_without_a_second_binary_read(self):
        reader = mock.Mock(return_value=MELT_SHA)
        with self.assertRaisesRegex(g2.G2Unverified, "alias_changed"):
            self.observe(second=PermissionError("unreadable"), reader=reader)
        reader.assert_called_once()

    def test_canonical_binary_verifier_failure_is_not_converted_to_an_alias_receipt(self):
        failure = g2.loaded.LoadedModuleUnverified("loaded_process_or_MLT_module_sha256_changed")
        reader = mock.Mock(side_effect=failure)
        with self.assertRaises(g2.loaded.LoadedModuleUnverified) as caught:
            self.observe(reader=reader)
        self.assertIs(caught.exception, failure)
        reader.assert_called_once()

    def test_read_access_time_alone_does_not_invalidate_unchanged_alias_identity(self):
        result, _, _, _ = self.observe(second=alias_stat(st_atime_ns=999))
        self.assertEqual(result["sha256"], MELT_SHA)

    def test_existing_regular_reader_is_used_on_real_private_fixture_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            canonical = Path(temporary).resolve() / "melt-7"
            raw = b"ordinary private test bytes; never executed"
            canonical.write_bytes(raw)
            pin = hashlib.sha256(raw).hexdigest()
            alias = mock.Mock(spec=Path)
            alias.as_posix.return_value = "/usr/bin/melt"
            alias.lstat.side_effect = [alias_stat(), alias_stat()]
            with mock.patch.object(g2, "MELT", canonical), \
                 mock.patch.object(g2, "MELT_ALIAS", alias), \
                 mock.patch.object(g2.os, "readlink", side_effect=["melt-7", "melt-7"]):
                result = g2.verified_installed_melt_alias(pin)
            self.assertEqual(result["sha256"], pin)
            self.assertEqual(canonical.read_bytes(), raw)


class AliasCompletionTests(unittest.TestCase):
    def session(self):
        session = g2.G2Session({"known_melt_sha256": MELT_SHA}, Path("unused"), b"", {})
        session.native_result = {"native_mlt_multisegment_e2e": "PASS"}
        session.live_proof = {"pid": 12, "process_start_ticks": 34}
        session.packet_observation = {"ffprobe_document_sha256": "3" * 64}
        session.pcm_proof = {"oracles": {"content": {"state": "PASS"}},
                             "decoded_pcm_sha256": "4" * 64}
        session.melt_alias_observation = {"alias_identity": [1, 2], "sha256": MELT_SHA}
        return session

    def complete(self, session, observed):
        with mock.patch.object(g2.admission, "checked_bytes", return_value=b"installed"), \
             mock.patch.object(g2, "sha", return_value=g2.REPAIRED_MODULE_SHA256), \
             mock.patch.object(g2, "verified_installed_melt_alias", return_value=observed):
            return session.complete()

    def test_unchanged_alias_may_complete_only_the_existing_scoped_source_evidence(self):
        session = self.session()
        result = self.complete(session, dict(session.melt_alias_observation))
        self.assertTrue(session.completed)
        self.assertEqual(result["g2_source_content_and_live_module"], "VERIFIED")
        self.assertFalse(result["g2_packet_PTS_and_AAC_priming_verified"])
        self.assertFalse(result["g2_physical_AV_master_acceptance"])

    def test_alias_changed_after_native_terminal_blocks_completion_and_retains_result(self):
        session = self.session()
        original = session.native_result
        with self.assertRaisesRegex(g2.G2Unverified, "alias_changed_after_native_effect"):
            self.complete(session, {"alias_identity": [1, 99], "sha256": MELT_SHA})
        self.assertIs(session.native_result, original)
        self.assertFalse(session.completed)

    def test_missing_initial_alias_observation_cannot_be_filled_by_later_success(self):
        session = self.session()
        session.melt_alias_observation = None
        with self.assertRaisesRegex(g2.G2Unverified, "alias_changed_after_native_effect"):
            self.complete(session, {"alias_identity": [1, 2], "sha256": MELT_SHA})
        self.assertFalse(session.completed)


class CustodyAliasContractTests(unittest.TestCase):
    def prepare(self, root, receipt):
        control = root / "control"
        dispatch = {
            "official_runtime": {"artifact": {}, "receipt_sha256": "3" * 64,
                                 "known_original_module_sha256": MODULE_SHA,
                                 "known_melt_sha256": MELT_SHA},
            "compiler": {"artifact": {}, "members": {key: key for key in (
                "original_c_source", "repaired_c_source", "compiled_module", "compiler_receipt_path")}},
        }
        raw = json.dumps(receipt).encode()
        download = mock.Mock(return_value=b"opaque authenticated-artifact test double")
        def member(_archive, name, *_args):
            return raw if name == "distribution/official-runtime-receipt.json" else b"compiler fixture"
        self.download, self.closure = download, mock.Mock()
        with mock.patch.object(custody.sys, "platform", "linux"), \
             mock.patch.object(custody.os, "geteuid", return_value=1001, create=True), \
             mock.patch.object(custody, "context", return_value={}), \
             mock.patch.object(custody, "validate_dispatch", return_value=dispatch), \
             mock.patch.object(custody, "names", return_value=(control, root / "installed", root / "session")), \
             mock.patch.object(custody, "source_guard", return_value={}), \
             mock.patch.object(custody, "download_artifact", download), \
             mock.patch.object(custody, "zip_member", side_effect=member), \
             mock.patch.object(g2, "reference_source", return_value=(b"", {})), \
             mock.patch.object(admission, "source_compiler_closure", self.closure):
            return custody.prepare({"GITHUB_WORKSPACE": str(root)})

    def test_artifact_intake_reuses_the_same_canonical_receipt_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with mock.patch.object(g2, "verify_official_runtime_bindings",
                                   wraps=g2.verify_official_runtime_bindings) as verify:
                control = self.prepare(root, official_receipt())
                verify.assert_called_once_with(official_receipt(), MODULE_SHA, MELT_SHA)
            self.assertEqual(self.download.call_count, 3)
            self.closure.assert_called_once()
            recorded = json.loads((control / "records/intake-complete.json").read_bytes())
            self.assertFalse(recorded["module_installed"])
            self.assertEqual(recorded["native_execution"], "NOT_RUN")

    def test_wrong_archive_alias_stops_before_compiler_intake_and_keeps_prior_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            receipt = official_receipt()
            receipt["members"]["melt"]["archive_alias"]["linkname"] = "another-link"
            with self.assertRaisesRegex(g2.G2Unverified, "official_runtime_alias_binding"):
                self.prepare(root, receipt)
            self.assertEqual(self.download.call_count, 2)
            self.closure.assert_not_called()
            self.assertTrue((root / "control/records/dispatch.json").is_file())
            self.assertFalse((root / "control/records/intake-complete.json").exists())


class MeltProcessInventoryTests(unittest.TestCase):
    def inventory(self, rows):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for pid, raw in rows.items():
                folder = root / pid
                folder.mkdir()
                if raw is not None:
                    (folder / "comm").write_bytes(raw)
            with mock.patch.object(admission, "Path", return_value=root) as factory:
                actual = admission.live_melt_pids()
            factory.assert_called_once_with("/proc")
        return actual

    def test_both_original_and_canonical_process_names_are_live_conflicts(self):
        self.assertEqual(sorted(self.inventory({"11": b"melt\n", "12": b"melt-7\n"})), [11, 12])

    def test_similar_names_nonpid_directories_and_missing_comm_do_not_match(self):
        self.assertEqual(self.inventory({
            "11": b"melt-70\n", "12": b"melt-helper\n", "13": b"Melt\n",
            "14": None, "self": b"melt\n", "15": b"melt-7 extra\n",
        }), [])

    def test_unreadable_comm_is_not_fabricated_as_a_process_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for pid, raw in (("11", b"melt\n"), ("12", b"\xff"), ("13", b"melt-7\n")):
                (root / pid).mkdir()
                (root / pid / "comm").write_bytes(raw)
            original = Path.read_text
            def read(path, *args, **kwargs):
                if path.parent.name == "13":
                    raise PermissionError("process exited or cannot be observed")
                return original(path, *args, **kwargs)
            with mock.patch.object(admission, "Path", return_value=root), \
                 mock.patch.object(Path, "read_text", autospec=True, side_effect=read):
                self.assertEqual(admission.live_melt_pids(), [11])


if __name__ == "__main__":
    unittest.main()
