"""Real filesystem and authenticated inert-fixture controls for inventory v1.

No compiler, package extractor, installer, renderer or Native host executes.
The one subprocess test runs only this Python interpreter on inert text.
These tests are not physical package observations or G2/G3 acceptance.
"""
from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import candidate_package_inventory as inventory
import nsis_static_uninstaller as nsis
from test_nsis_static_uninstaller import listing


def inputs(kind="appimage", origins=(), notices=()):
    return {
        "context": {
            "source": "1" * 40, "source_tree": "2" * 40, "kind": kind,
            "platform": inventory.PROFILES[kind][0], "arch": "x86_64",
            "run_id": "123", "run_attempt": 1,
            "candidate_receipt": inventory.digest(b"inert candidate receipt fixture"),
            "package": {"filename": "inert-package.fixture", **inventory.digest(b"inert package")},
            "cargo_index": inventory.digest(b"inert cargo fixture"),
            "artifact_identities_sha256": "3" * 64, "generated_metadata_sha256": "4" * 64,
        },
        "origins": sorted(origins, key=lambda r: r["id"]),
        "components": [{"component_id": "cargo:fixture", "notice_sha256": sorted(notices)}],
    }


def git_origin(path, raw):
    return {"id": "git:" + path, "kind": "TRACKED_SOURCE_BYTES", "path": path,
            "git_blob": "5" * 40, "checkout_transform": "EXACT", **inventory.digest(raw)}


class CandidateArtifactRosterTests(unittest.TestCase):
    def receipt(self, platform, kinds):
        return {"candidate": {"platform": platform}, "artifacts": [
            {"bundle_kind": kind, "filename": "candidate-" + str(number)}
            for number, kind in enumerate(kinds)]}

    def test_existing_receipt_cardinality_is_preserved(self):
        for platform, kinds, requested in [
                ("linux", ["deb", "appimage"], "deb"),
                ("linux", ["appimage", "deb"], "appimage"),
                ("macos", ["dmg"], "dmg"), ("windows", ["nsis"], "nsis")]:
            with self.subTest(platform=platform, requested=requested):
                candidate = self.receipt(platform, kinds)
                self.assertIs(inventory.candidate_artifact_roster(candidate, requested),
                              candidate["artifacts"])

    def test_absolute_traversal_and_foreign_kinds_fail_before_path_construction(self):
        for bad in ["/outside", r"E:\outside", "../outside", "deb/..",
                    "nsis", "", None, ["deb"]]:
            with self.subTest(kind=bad):
                candidate = self.receipt("linux", ["appimage", bad])
                with self.assertRaisesRegex(inventory.InventoryError, "bundle_roster"):
                    inventory.candidate_artifact_roster(candidate, "appimage")

    def test_missing_duplicate_extra_and_wrong_platform_rosters_are_refused(self):
        for kinds in [[], ["deb"], ["deb", "deb"], ["deb", "appimage", "dmg"]]:
            with self.subTest(kinds=kinds):
                with self.assertRaises(inventory.InventoryError):
                    inventory.candidate_artifact_roster(self.receipt("linux", kinds), "deb")
        for platform in ["windows", "macos", None]:
            with self.subTest(platform=platform):
                with self.assertRaisesRegex(inventory.InventoryError, "platform"):
                    inventory.candidate_artifact_roster(
                        self.receipt(platform, ["deb", "appimage"]), "deb")

    def test_path_bearing_filenames_are_rejected_in_every_row(self):
        for bad in ["/outside", "../outside", r"E:\outside", "sub/file", "a.", "", None]:
            with self.subTest(filename=bad):
                candidate = self.receipt("linux", ["appimage", "deb"])
                candidate["artifacts"][1]["filename"] = bad
                with self.assertRaises(inventory.InventoryError):
                    inventory.candidate_artifact_roster(candidate, "appimage")


class InventoryFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mw-inventory-unit-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.payload = self.base / "payload"
        self.payload.mkdir()

    def write(self, name, raw):
        path = self.payload / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def report(self, kind="appimage", original=None):
        return inventory.compose(original or inputs(kind),
                                 [inventory.observe_tree(self.payload, "payload")])

    def dmg_roots(self):
        # Actual temporary directories, not a mounted DMG or process receipt.
        app = self.payload / "Motionwright.app"
        (app / "empty").mkdir(parents=True)
        (app / "file").write_bytes(b"abcd")
        copied = self.base / "copied.app"
        shutil.copytree(app, copied)
        return [inventory.observe_tree(self.payload, "volume"),
                inventory.observe_tree(copied, "application")]

    def test_actual_member_set_includes_empty_directories_empty_files_and_native_header(self):
        (self.payload / "empty-directory").mkdir()
        self.write("empty-file", b"")
        self.write("lib/native.so", b"\x7fELF inert bytes, never executed")
        root = inventory.observe_tree(self.payload, "payload")
        self.assertEqual([r["path"] for r in root["members"]],
                         ["empty-directory", "empty-file", "lib", "lib/native.so"])
        self.assertEqual(root["members"][-1]["native_format"], "ELF")
        self.assertEqual(root["members"][1]["bytes"], 0)
        self.assertEqual(root["members"][1]["sha256"], inventory.digest(b"")["sha256"])

    def test_retained_file_read_compares_windows_timestamp_domains_legitimately(self):
        path = self.write("new-file", b"real retained bytes")
        identity, classification = inventory.stable_file(path)
        self.assertEqual(identity, inventory.digest(b"real retained bytes"))
        self.assertEqual(classification, "NOT_IDENTIFIED")

    def test_namespace_addition_during_capture_is_rejected(self):
        self.write("file", b"original")
        real = inventory.stable_file
        def capture_then_add(path, limit):
            observed = real(path, limit)
            (self.payload / "new-empty-directory").mkdir()
            return observed
        with mock.patch.object(inventory, "stable_file", side_effect=capture_then_add):
            with self.assertRaisesRegex(inventory.InventoryError, "namespace_changed"):
                inventory.observe_tree(self.payload, "payload")

    def test_same_size_change_after_hash_is_rejected(self):
        path = self.write("file", b"aaaa")
        real = inventory.stable_file
        def capture_then_change(member, limit):
            observed = real(member, limit)
            path.write_bytes(b"bbbb")
            return observed
        with mock.patch.object(inventory, "stable_file", side_effect=capture_then_change):
            with self.assertRaisesRegex(inventory.InventoryError, "namespace_changed"):
                inventory.observe_tree(self.payload, "payload")

    def test_file_and_aggregate_limits_fail_without_truncation(self):
        self.write("file", b"123456789")
        with self.assertRaisesRegex(inventory.InventoryError, "budget"):
            inventory.stable_file(self.payload / "file", limit=8)
        with mock.patch.object(inventory, "MAX_TOTAL", 8):
            with self.assertRaisesRegex(inventory.InventoryError, "budget"):
                inventory.observe_tree(self.payload, "payload")

    def test_appimage_aggregate_allowance_stays_scoped_and_bounded(self):
        self.write("file", b"123456789")
        with mock.patch.object(inventory, "MAX_TOTAL", 8), \
                mock.patch.object(inventory, "APPIMAGE_MAX_TOTAL", 12):
            for kind in (None, "deb", "dmg", "nsis"):
                with self.subTest(kind=kind), self.assertRaisesRegex(
                        inventory.InventoryError, "inventory_total_budget"):
                    inventory.observe_tree(self.payload, "payload", kind=kind)
            observed = inventory.observe_tree(self.payload, "payload", kind="appimage")
            self.assertEqual(observed["file_bytes"], 9)
            report = inventory.compose(inputs("appimage"), [observed])
            with mock.patch.object(inventory, "APPIMAGE_MAX_TOTAL", 8):
                with self.assertRaisesRegex(inventory.InventoryError, "inventory_aggregate_budget"):
                    inventory.validate(report)
                with self.assertRaisesRegex(inventory.InventoryError, "inventory_total_budget"):
                    inventory.observe_tree(self.payload, "payload", kind="appimage")
            with mock.patch.object(inventory, "MAX_FILE", 8):
                with self.assertRaisesRegex(inventory.InventoryError, "inventory_file_budget_or_hardlink"):
                    inventory.observe_tree(self.payload, "payload", kind="appimage")

    def test_hardlinked_payload_is_refused(self):
        path = self.write("file", b"original")
        try:
            os.link(path, self.payload / "alias")
        except OSError as error:
            self.skipTest("filesystem cannot create hardlink: " + type(error).__name__)
        with self.assertRaisesRegex(inventory.InventoryError, "hardlink") as caught:
            inventory.observe_tree(self.payload, "payload", kind="appimage")
        self.assertEqual(str(caught.exception), "inventory_file_budget_or_hardlink")
        details = caught.exception.member_details
        self.assertEqual(details["links"], 2)
        self.assertEqual(details["bytes"], len(b"original"))
        self.assertEqual(details["max_bytes"], inventory.MAX_FILE)
        self.assertEqual(details["path"], "alias")

    def test_member_refusal_cli_preserves_code_and_bounded_details(self):
        details = {"path": "usr/lib/source.so", "bytes": 7, "links": 2,
                   "max_bytes": inventory.MAX_FILE}
        failure = inventory.InventoryError("inventory_file_budget_or_hardlink",
                                           member_details=details)
        with mock.patch.object(inventory, "_profile") as profile, \
                mock.patch.object(inventory, "extract_and_observe", side_effect=failure), \
                mock.patch.dict(os.environ, {"GITHUB_WORKSPACE": str(ROOT)}), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as output:
            profile.return_value.linux_source.return_value = "1" * 40
            result = inventory.main(["extract", "--kind", "appimage",
                "--artifact", str(self.base / "source.AppImage"),
                "--candidate-receipt", str(self.base / "candidate.json"),
                "--directory", str(self.base / "originals"),
                "--work", str(self.base / "fresh")])
        self.assertEqual(result, 2)
        lines = output.getvalue().splitlines()
        self.assertEqual(lines[-1], "candidate-package-inventory-failed:inventory_file_budget_or_hardlink")
        self.assertEqual(json.loads(lines[0].split(":", 1)[1]), details)

    def test_symlinks_are_literals_and_external_target_is_never_read(self):
        self.write("file", b"original")
        try:
            os.symlink("file", self.payload / "inside")
            os.symlink(str(self.base / "absent-external"), self.payload / "outside")
        except OSError as error:
            self.skipTest("symlink creation unavailable: " + type(error).__name__)
        report = self.report()
        self.assertEqual(report["symlink_relations"], [
            {"member": "payload:inside", "target_member": "payload:file",
             "status": "INTERNAL_MEMBER_LITERAL"},
            {"member": "payload:outside", "target_member": None,
             "status": "EXTERNAL_LITERAL_NOT_FOLLOWED"}])
        self.assertIn("LINK_RESOLUTION_NOT_CLOSED", [g["code"] for g in report["gaps"]])

    def test_complete_member_set_does_not_grant_package_or_legal_closure(self):
        raw = b"exact tracked fixture"
        self.write("LICENSE", raw)
        source = inputs(origins=[git_origin("LICENSE", raw)], notices=[inventory.digest(raw)["sha256"]])
        report = self.report(original=source)
        self.assertEqual(report["closure"]["all_regular_member_origins"], "CLOSED")
        self.assertEqual(report["closure"]["cargo_notice_presence"], "CLOSED")
        self.assertEqual(report["claims"]["full_package_inventory"], "NOT_VERIFIED")
        self.assertEqual(report["claims"]["redistribution_permission"], "NOT_INFERRED")
        self.assertEqual(report["claims"]["formal_g3"], "NOT_GRANTED")
        self.assertEqual(report["component_notice_relations"][0]["notice_members"],
                         {inventory.digest(raw)["sha256"]: ["payload:LICENSE"]})

    def test_unresolved_native_origin_and_missing_notice_remain_explicit_gaps(self):
        self.write("library.so", b"\x7fELF inert")
        report = self.report(original=inputs(notices=["6" * 64]))
        codes = {g["code"] for g in report["gaps"]}
        self.assertTrue({"MEMBER_ORIGIN_UNRESOLVED", "NATIVE_TRANSITIVE_ORIGIN_AND_LICENSE_UNCLOSED",
                         "CARGO_NOTICE_BYTES_NOT_IN_OBSERVED_MEMBERS"} <= codes)

    def test_consumer_recomputes_closed_relations_and_refuses_forged_claims(self):
        self.write("file", b"original")
        report = self.report()
        changes = [
            lambda r: r["claims"].update(full_package_inventory="VERIFIED"),
            lambda r: r["claims"].update(redistribution_permission="GRANTED"),
            lambda r: r["member_relations"].clear(),
            lambda r: r["member_relations"][0].update(origin_ids=["invented"]),
            lambda r: r["gaps"].clear(),
            lambda r: r["context"].update(run_attempt=True),
            lambda r: r.update(unadmitted="field"),
        ]
        for mutation in changes:
            changed = copy.deepcopy(report)
            mutation(changed)
            with self.subTest(mutation=mutation), self.assertRaises(inventory.InventoryError):
                inventory.validate(changed)

    def test_consumer_refuses_duplicate_case_alias_missing_parent_and_wrong_stat_kind(self):
        self.write("folder/file", b"original")
        report = self.report()
        changed = copy.deepcopy(report)
        row = copy.deepcopy(changed["roots"][0]["members"][-1])
        row["path"] = "folder/FILE"
        changed["roots"][0]["members"].append(row)
        with self.assertRaisesRegex(inventory.InventoryError, "case_alias"):
            inventory.validate(changed)
        changed = copy.deepcopy(report)
        changed["roots"][0]["members"].pop(0)
        with self.assertRaisesRegex(inventory.InventoryError, "parent_missing"):
            inventory.validate(changed)
        changed = copy.deepcopy(report)
        changed["roots"][0]["members"][-1]["stat"]["st_mode"] = 0o040755
        with self.assertRaisesRegex(inventory.InventoryError, "stat_kind"):
            inventory.validate(changed)

    def test_deb_data_only_is_not_a_control_archive_observation(self):
        self.write("usr/bin/motionwright", b"inert")
        report = self.report("deb")
        self.assertEqual(report["format_observation"]["control_namespace"], "NOT_OBSERVED")
        self.assertIn("DEB_CONTROL_NAMESPACE_NOT_OBSERVED", [g["code"] for g in report["gaps"]])
        self.write("DEBIAN/control", b"Package: inert-fixture\n")
        report = self.report("deb")
        self.assertEqual(report["format_observation"]["control_namespace"], "OBSERVED_IN_RAW_EXTRACTION")
        self.assertEqual(report["claims"]["container_member_namespace"], "NOT_VERIFIED")

    def test_dmg_reads_outside_application_and_compares_every_copied_member(self):
        volume = self.payload
        app = volume / "Motionwright.app"
        (app / "Contents/empty").mkdir(parents=True)
        (app / "Contents/binary").write_bytes(b"inert app")
        self.write("outside.txt", b"outside application")
        copied = self.base / "copied.app"
        shutil.copytree(app, copied)
        roots = [inventory.observe_tree(volume, "volume"), inventory.observe_tree(copied, "application")]
        report = inventory.compose(inputs("dmg"), roots)
        self.assertEqual(report["format_observation"]["volume_members_outside_application"], 1)
        self.assertEqual(report["format_observation"]["application_copy"], "ALL_OBSERVED_MEMBERS_BYTE_AND_LINK_EXACT")
        (copied / "Contents/empty").rmdir()
        with self.assertRaisesRegex(inventory.InventoryError, "copy_namespace"):
            inventory.compose(inputs("dmg"),
                              [roots[0], inventory.observe_tree(copied, "application")])

    def test_dmg_copy_root_cannot_alias_the_original_application(self):
        roots = self.dmg_roots()
        report = inventory.compose(inputs("dmg"), roots)
        alias = inventory.observe_tree(self.payload / "Motionwright.app", "application")
        with self.assertRaisesRegex(inventory.InventoryError, "copy_root_aliases_source"):
            inventory.compose(inputs("dmg"), [roots[0], alias])
        report["roots"][0] = alias
        with self.assertRaisesRegex(inventory.InventoryError, "copy_root_aliases_source"):
            inventory.validate(report)

    def test_dmg_copy_root_permissions_are_part_of_the_comparison(self):
        roots = self.dmg_roots()
        report = inventory.compose(inputs("dmg"), roots)
        self.assertEqual(report["format_observation"]["application_root_permissions"],
                         "MATCH_OBSERVED_SOURCE")
        # Corrupt retained metadata; this does not emulate macOS chmod behavior.
        roots[1]["root_stat"]["st_mode"] ^= 0o200
        with self.assertRaisesRegex(inventory.InventoryError, "copy_root_permissions_differ"):
            inventory.compose(inputs("dmg"), roots)
        with self.assertRaisesRegex(inventory.InventoryError, "copy_root_permissions_differ"):
            inventory.validate(report)

    def test_dmg_namespace_cannot_promote_mount_or_detach_observation(self):
        report = inventory.compose(inputs("dmg"), self.dmg_roots())
        expected = {"readonly_mount": "NOT_OBSERVED", "mount_device_identity": "NOT_OBSERVED",
                    "detach": "NOT_OBSERVED", "process_subtree_closure": "NOT_VERIFIED"}
        for key, value in expected.items():
            self.assertEqual(report["format_observation"][key], value)
            changed = copy.deepcopy(report)
            changed["format_observation"][key] = "PASS"
            with self.subTest(field=key), self.assertRaisesRegex(
                    inventory.InventoryError, "relations_or_claims_changed"):
                inventory.validate(changed)
        for code in ("DMG_READONLY_MOUNT_NOT_OBSERVED", "DMG_MOUNT_DEVICE_IDENTITY_NOT_BOUND",
                     "DMG_DETACH_NOT_OBSERVED"):
            self.assertIn(code, [row["code"] for row in report["gaps"]])
            changed = copy.deepcopy(report)
            changed["gaps"] = [row for row in changed["gaps"] if row["code"] != code]
            with self.subTest(gap=code), self.assertRaisesRegex(
                    inventory.InventoryError, "relations_or_claims_changed"):
                inventory.validate(changed)

    def test_dmg_cannot_relabel_linux_shaped_receipts_as_mount_custody(self):
        roots = self.dmg_roots()
        report = inventory.compose(inputs("dmg"), roots)
        # Inert byte identities test refusal only; no process is represented.
        identity = inventory.digest(b"inert unexecuted receipt fixture")
        forged = {"status": "DIRECT_CAPTURED_EXTRACTION", "input": identity,
                  "operations": [{"label": "extract", **{key: identity for key in (
                      "request", "completion", "stdout", "stderr", "subtree", "binding", "closure")}}]}
        with self.assertRaisesRegex(inventory.InventoryError, "dmg_mount_custody_unavailable"):
            inventory.compose(inputs("dmg"), roots, custody=forged)
        report["custody"] = forged
        with self.assertRaisesRegex(inventory.InventoryError, "dmg_mount_custody_unavailable"):
            inventory.validate(report)

    def test_dmg_detached_consumer_refuses_promoted_claim_with_matching_digest(self):
        report = inventory.compose(inputs("dmg"), self.dmg_roots())
        report["format_observation"]["detach"] = "OBSERVED"
        output = self.base / "retained-originals"
        output.mkdir()
        raw = inventory.capture.encoded(report)
        (output / "inventory.json").write_bytes(raw)
        with mock.patch.object(inventory, "authenticate_inputs") as authenticate:
            with self.assertRaisesRegex(inventory.InventoryError, "relations_or_claims_changed"):
                inventory.consume(repo=self.base, source="1" * 40, kind="dmg",
                                  artifact=self.base / "unread.dmg", receipt=self.base / "unread.json",
                                  directory=output, expected_sha256=inventory.capture.sha(raw))
            authenticate.assert_not_called()
        self.assertEqual((output / "inventory.json").read_bytes(), raw)

    def test_dmg_volume_and_copy_share_the_aggregate_byte_limit(self):
        roots = self.dmg_roots()
        self.assertEqual(sum(root["file_bytes"] for root in roots), 8)
        with mock.patch.object(inventory, "MAX_TOTAL", 7):
            with self.assertRaisesRegex(inventory.InventoryError, "budget"):
                inventory.compose(inputs("dmg"), roots)

    def test_dmg_same_size_changed_copy_is_rejected(self):
        app = self.payload / "Motionwright.app"
        app.mkdir()
        (app / "file").write_bytes(b"aaaa")
        copied = self.base / "copied.app"
        shutil.copytree(app, copied)
        (copied / "file").write_bytes(b"bbbb")
        with self.assertRaisesRegex(inventory.InventoryError, "copy_namespace"):
            inventory.compose(inputs("dmg"), [inventory.observe_tree(self.payload, "volume"),
                                              inventory.observe_tree(copied, "application")])

    def test_fixed_extraction_commands_preserve_linux_operations_and_refuse_dmg(self):
        artifact = self.base / "candidate"
        work = self.base / "fresh"
        roots, commands = inventory._layout("deb", artifact, work)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][1][1], "--raw-extract")
        self.assertEqual(roots, {"payload": work / "payload"})
        _, commands = inventory._layout("appimage", artifact, work)
        self.assertEqual(commands[0][1], [str(artifact), "--appimage-extract"])
        with self.assertRaisesRegex(inventory.InventoryError, "full_process_closure_unavailable"):
            inventory._layout("dmg", artifact, work)
        with self.assertRaisesRegex(inventory.InventoryError, "no_nsis_install_authority"):
            inventory._layout("nsis", artifact, work)

    def test_exit_zero_and_closed_streams_are_not_owned_extraction_custody(self):
        directory = self.base / "originals"
        directory.mkdir()
        command = [sys.executable, "-I", "-B", "-c", "print('inert inventory fixture process')"]
        inventory.capture.run_captured(command, self.base, directory, "extract",
                                       stream_limit=1024, timeout=10)
        complete = inventory.capture.parse_json((directory / "extract.completion.json").read_bytes())
        self.assertEqual(complete["returncode"], 0)
        self.assertEqual(complete["errors"], [])
        with self.assertRaises((ValueError, OSError)):
            inventory._operation_summary(directory, [("extract", command, self.base)])
        self.assertFalse((directory / "inventory.json").exists())

    def test_package_descriptor_close_cannot_replace_a_retained_owner_failure(self):
        import candidate_package_extraction as extraction
        retained = object()  # Pure exception identity, never a process assertion.
        primary = extraction.ExtractionError("pure owner failure", retained=retained)
        with self.assertRaises(extraction.ExtractionError) as caught:
            try:
                raise primary
            finally:
                inventory._close_package_reader(-1, sys.exception())
        self.assertIs(caught.exception, primary)
        self.assertIs(caught.exception.retained, retained)
        self.assertEqual(primary.__notes__, ["inventory_package_descriptor_close_failed:OSError"])
        with self.assertRaises(OSError):
            inventory._close_package_reader(-1, None)

    def test_macos_extraction_is_refused_before_effects_without_kernel_closure(self):
        work, output = self.base / "never-mounted", self.base / "never-inventoried"
        with mock.patch.object(inventory, "_profile") as profile, \
             mock.patch.object(inventory, "_layout") as commands, \
             mock.patch.object(inventory.capture, "new_directory") as create:
            with self.assertRaisesRegex(inventory.InventoryError, "full_process_closure_unavailable"):
                inventory.extract_and_observe(
                    repo=self.base, source="1" * 40, kind="dmg", artifact=self.base / "not-executed",
                    receipt=self.base / "not-read", work=work, directory=output)
            profile.assert_not_called()
            commands.assert_not_called()
            create.assert_not_called()
        self.assertFalse(work.exists())
        self.assertFalse(output.exists())

    def test_link_relations_do_not_normalize_through_regular_file_or_symlink_prefix(self):
        roots = [{"role": "payload", "members": [
            {"path": "file", "kind": "file"},
            {"path": "target", "kind": "file"},
            {"path": "bad-parent", "kind": "symlink", "target": "file/../target"},
            {"path": "prefix", "kind": "symlink", "target": "file"},
            {"path": "bad-prefix", "kind": "symlink", "target": "prefix/child"},
            {"path": "cycle", "kind": "symlink", "target": "cycle"},
        ]}]
        found = {r["member"]: r["status"] for r in inventory._link_relations(roots)}
        self.assertEqual(found["payload:bad-parent"], "COMPLEX_LITERAL_NOT_RESOLVED")
        self.assertEqual(found["payload:bad-prefix"], "COMPLEX_LITERAL_NOT_RESOLVED")
        self.assertEqual(found["payload:cycle"], "CYCLE_NOT_FOLLOWED")
        self.assertEqual(found["payload:prefix"], "INTERNAL_MEMBER_LITERAL")

    def test_missing_cargo_root_and_index_are_explicit_even_with_notice_coverage(self):
        raw = b"notice fixture"
        self.write("notice", raw)
        report = self.report(original=inputs(origins=[git_origin("notice", raw)],
                                             notices=[inventory.digest(raw)["sha256"]]))
        self.assertEqual(report["closure"]["cargo_notice_presence"], "CLOSED")
        self.assertEqual(report["closure"]["captured_root_binary"], "GAP")
        self.assertTrue({"CAPTURED_CARGO_ROOT_BYTES_NOT_OBSERVED",
                         "AUTHENTICATED_CARGO_INDEX_BYTES_NOT_OBSERVED"}
                        <= {g["code"] for g in report["gaps"]})

    def test_nsis_selected_original_identity_must_equal_actual_uninstaller(self):
        raw = b"inert uninstaller fixture " * 4
        self.write("uninstall.exe", raw)
        original = listing(("main.exe", ""), (nsis.MEMBER, ""))
        view = {**nsis.inventory_listing(original, 1234),
                "listing": inventory.digest(original), "selected_identity": inventory.digest(raw),
                "static_reference_sha256": "7" * 64}
        roots = [inventory.observe_tree(self.payload, "application")]
        report = inventory.compose(inputs("nsis"), roots, nsis_listing=view)
        self.assertEqual(report["format_observation"]["all_listed_member_bytes"], "NOT_OBSERVED")
        self.assertIn("NSIS_LISTED_MEMBER_BYTES_NOT_ALL_OBSERVED",
                      [g["code"] for g in report["gaps"]])
        changed = copy.deepcopy(view)
        changed["selected_identity"] = inventory.digest(b"different")
        with self.assertRaisesRegex(inventory.InventoryError, "nsis_selected_identity"):
            inventory.compose(inputs("nsis"), roots, nsis_listing=changed)
        changed = copy.deepcopy(report)
        changed["format_observation"]["original_setup_listing"]["members"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(inventory.InventoryError, "nsis_member_fields"):
            inventory.validate(changed)

    def test_extraction_api_refuses_missing_existing_scope_before_effects(self):
        work = self.base / "must-remain-absent"
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "scope_refused"):
                inventory.extract_and_observe(
                    repo=self.base, source="1" * 40, kind="appimage",
                    artifact=self.base / "not-executed", receipt=self.base / "not-read",
                    work=work, directory=self.base / "not-created")
        self.assertFalse(work.exists())
        self.assertFalse((self.base / "not-created").exists())


class InventoryNsisListingTests(unittest.TestCase):
    def test_original_listing_preserves_unknown_sizes_and_every_member_without_fake_hashes(self):
        raw = listing(("main.exe", ""), ("assets\\file.txt", "10"), (nsis.MEMBER, ""))
        result = nsis.inventory_listing(raw, 1234)
        self.assertEqual(len(result["members"]), 3)
        self.assertEqual(result["members"][1]["listed_bytes"], None)
        self.assertTrue(all("sha256" not in row for row in result["members"]))
        self.assertEqual(result["members"][0]["properties"]["Path"], "assets\\file.txt")

    def test_nonselected_traversal_aliases_and_file_prefixes_are_refused(self):
        for names in [
            [("../payload", "1"), (nsis.MEMBER, "")],
            [("A\\file", "1"), ("a/file", "1"), (nsis.MEMBER, "")],
            [("A/file", "1"), ("a/other", "1"), (nsis.MEMBER, "")],
            [("FOLDER", "1"), ("folder/file", "1"), (nsis.MEMBER, "")],
            [("folder", "1"), ("folder/file", "1"), (nsis.MEMBER, "")],
            [("trailing./file", "1"), (nsis.MEMBER, "")],
        ]:
            with self.subTest(names=names), self.assertRaises(ValueError):
                nsis.inventory_listing(listing(*names), 1234)


class AuthenticatedInventoryFixtureTests(unittest.TestCase):
    def setUp(self):
        from test_ci_linux_dependency_capture import LinuxDependencyCaptureTests
        self.fixture = LinuxDependencyCaptureTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.package_fixture()
        self.env = mock.patch.dict(os.environ, {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.repo = self.fixture.root
        row = next(r for r in self.fixture.candidate["artifacts"] if r["bundle_kind"] == "appimage")
        import ci_linux_dependency_capture as linux
        self.artifact = self.repo / linux.BINARY.parent / "bundle/appimage" / row["filename"]
        self.payload = self.repo / "inert-extracted-payload"
        self.payload.mkdir()
        (self.payload / "LICENSE").write_bytes((self.repo / "LICENSE").read_bytes())
        (self.payload / "empty").mkdir()
        self.directory = self.repo / "ci-evidence/package-inventory"
        self.common = dict(repo=self.repo, source=self.fixture.sha, kind="appimage",
                           artifact=self.artifact,
                           receipt=self.repo / "ci-evidence/linux-candidate.json",
                           directory=self.directory)

    def test_authentic_source_and_inert_package_bytes_support_sealed_live_consumer(self):
        reference = inventory.observe_existing(**self.common, roots={"payload": self.payload})
        import ci_linux_dependency_capture as linux
        observed = linux.verify_package_inventory(
            self.repo, self.fixture.sha, kind="appimage", directory=self.directory,
            expected_sha256=reference["sha256"], payload=self.payload)
        self.assertEqual(observed["readback"], "LIVE_ROOTS_REVALIDATED")
        self.assertEqual(observed["full_package_inventory"], "NOT_VERIFIED")
        self.assertIn("EXTRACTION_CUSTODY_NOT_OBSERVED", observed["gap_codes"])

    def test_seal_source_package_and_observed_tree_cannot_be_substituted(self):
        reference = inventory.observe_existing(**self.common, roots={"payload": self.payload})
        with self.assertRaisesRegex(inventory.InventoryError, "seal_changed"):
            inventory.consume(**self.common, expected_sha256="0" * 64)
        (self.payload / "new-file").write_bytes(b"extra")
        with self.assertRaisesRegex(inventory.InventoryError, "live_member_set_changed"):
            inventory.consume(**self.common, expected_sha256=reference["sha256"],
                              roots={"payload": self.payload})
        original = self.artifact.read_bytes()
        self.artifact.write_bytes(b"changed" + original)
        with self.assertRaises((inventory.InventoryError, inventory.capture.CaptureError, ValueError)):
            inventory.consume(**self.common, expected_sha256=reference["sha256"])

    def test_output_cannot_enter_observed_member_set(self):
        args = {**self.common, "directory": self.payload / "inventory-output"}
        with self.assertRaisesRegex(inventory.InventoryError, "output_overlaps"):
            inventory.observe_existing(**args, roots={"payload": self.payload})
        self.assertFalse((self.payload / "inventory-output").exists())


class AdditionalInventoryAdapterTests(unittest.TestCase):
    def test_macos_adapter_preserves_retained_observation_boundary(self):
        from test_ci_macos_dependency_capture import MacOSDependencyCaptureTests
        import ci_macos_dependency_capture as macos
        case = MacOSDependencyCaptureTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.package_fixture()
        volume = case.root / "inert-volume"
        app = volume / "Motionwright.app"
        (app / "Contents").mkdir(parents=True)
        (app / "Contents/file").write_bytes(b"inert copied fixture")
        copy_root = case.root / "inert-copy.app"
        shutil.copytree(app, copy_root)
        directory = case.root / "ci-evidence/package-inventory"
        with mock.patch.dict(os.environ, {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"}):
            reference = inventory.observe_existing(
                repo=case.root, source=case.sha, kind="dmg", artifact=case.dmg,
                receipt=case.root / "ci-evidence/macos-candidate.json", directory=directory,
                roots={"volume": volume, "application": copy_root})
            observed = macos.verify_package_inventory(
                case.root, case.sha, directory=directory, expected_sha256=reference["sha256"])
        self.assertEqual(observed["readback"], "RETAINED_ORIGINALS_REVALIDATED")
        self.assertEqual(observed["custody"], "NOT_OBSERVED")
        self.assertEqual(observed["full_package_inventory"], "NOT_VERIFIED")

    def test_windows_adapter_refuses_legacy_generated_identity_and_changed_preinstall_seal(self):
        from test_ci_windows_dependency_capture import WindowsDependencyCaptureTests
        import ci_windows_dependency_capture as windows
        case = WindowsDependencyCaptureTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.application_fixture()
        directory = case.root / "ci-evidence/package-inventory"
        arguments = dict(repo=case.root, source=case.sha, run_id="123",
                         install_root=case.install_root, directory=directory)
        with self.assertRaisesRegex(ValueError, "preinstall_seal_changed"):
            windows.package_inventory(**arguments, expected_inventory_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "static_reference_required"):
            windows.package_inventory(**arguments, expected_inventory_sha256=case.inventory_sha)
        self.assertFalse(directory.exists())


if __name__ == "__main__":
    unittest.main()
