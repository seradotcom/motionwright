"""Fail-closed Debian original archive and md5sum controls; inert fixture only."""
from __future__ import annotations

import copy
from contextlib import redirect_stdout
import hashlib
import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import candidate_package_inventory as inventory
import linux_deb_archive_audit as auditor

SOURCE = "1" * 40
FILENAME = "Motionwright_0.1.0_amd64.deb"
STANDARD_CONTROL = (
    b"Package: motionwright\n"
    b"Version: 0.1.0\n"
    b"Architecture: amd64\n"
    b"Maintainer: Inert Test Fixture\n"
    b"Depends: libwebkit2gtk-4.1-0, libgtk-3-0\n"
    b"Description: Source-bound local fixture\n"
)
FILES = {
    "usr/bin/motionwright-desktop": b"ELF-inert-test-binary-not-executable\n",
    "usr/share/applications/Motionwright.desktop": b"[Desktop Entry]\nName=Motionwright\n",
}
DIRS = ("usr", "usr/bin", "usr/share", "usr/share/applications")


def tar_gz(files, directories=(), symlinks=None):
    memory = io.BytesIO()
    with tarfile.open(fileobj=memory, mode="w:gz", format=tarfile.USTAR_FORMAT) as out:
        for name in directories:
            row = tarfile.TarInfo(name)
            row.type = tarfile.DIRTYPE
            row.mode = 0o755
            out.addfile(row)
        for name, body in files.items():
            row = tarfile.TarInfo(name)
            row.mode = 0o644
            row.size = len(body)
            out.addfile(row, io.BytesIO(body))
        for name, target in (symlinks or {}).items():
            row = tarfile.TarInfo(name)
            row.type = tarfile.SYMTYPE
            row.linkname = target
            out.addfile(row)
    return memory.getvalue()


def make_ar(entries):
    out = bytearray(b"!<arch>\n")
    for name, body in entries:
        assert len(name) <= 16
        header = (
            f"{name:<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(body):<10}"
        ).encode() + bytes((96, 10))
        assert len(header) == 60
        out.extend(header)
        out.extend(body)
        if len(body) % 2:
            out.extend(b"\n")
    return bytes(out)


def md5_manifest(files):
    return b"".join(
        hashlib.md5(body, usedforsecurity=False).hexdigest().encode() +
        b"  " + name.encode() + b"\n"
        for name, body in sorted(files.items())
    )


def package(control=STANDARD_CONTROL, *, md5=None, files=None,
            extra_control=None, extra_ar=None, raw_data=None):
    files = FILES if files is None else files
    entries = {"control": control, "md5sums": md5_manifest(files) if md5 is None else md5}
    if extra_control is not None:
        entries["postinst"] = extra_control
    ar = [
        ("debian-binary", b"2.0\n"),
        ("control.tar.gz", tar_gz(entries)),
        ("data.tar.gz", tar_gz(files, DIRS) if raw_data is None else raw_data),
    ]
    return make_ar(ar + (extra_ar or []))


def inert_context(data):
    return {
        "source": SOURCE, "source_tree": "2" * 40,
        "kind": "deb", "platform": "linux", "arch": "x86_64",
        "run_id": "120", "run_attempt": 1,
        "candidate_receipt": inventory.digest(b"valid-inert-candidate"),
        "package": {"filename": FILENAME, **inventory.digest(data)},
        "cargo_index": inventory.digest(b"valid-inert-cargo"),
        "artifact_identities_sha256": "3" * 64,
        "generated_metadata_sha256": "4" * 64,
    }


def custody():
    ident = inventory.digest(b"inert-captured-extraction")
    return {
        "status": "DIRECT_CAPTURED_EXTRACTION", "input": ident,
        "operations": [{
            "label": "extract",
            **{name: dict(ident) for name in (
                "request", "completion", "stdout", "stderr", "subtree",
                "binding", "closure"
            )},
        }],
    }


class DebOriginalArchiveTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(
            prefix="mw-original-deb-inert-", dir=os.environ.get("TEMP"))
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name).resolve(strict=True)
        self.payload = self.root / "payload"
        self.payload.mkdir()
        self.current_files = dict(FILES)
        self.current_control = STANDARD_CONTROL
        self.current_md5 = md5_manifest(self.current_files)

    def report(self, data, *, control=None, md5=None, files=None, source=SOURCE):
        files = self.current_files if files is None else files
        control = self.current_control if control is None else control
        md5 = self.current_md5 if md5 is None else md5
        for path, raw in {
            **files, "DEBIAN/control": control, "DEBIAN/md5sums": md5
        }.items():
            dest = self.payload / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
        context = inert_context(data)
        context["source"] = source
        inputs = {"context": context, "origins": [], "components": []}
        return inventory.compose(
            inputs, [inventory.observe_tree(self.payload, "payload")],
            custody=custody(),
        )

    def test_all_ar_control_and_data_members_match_extracted_original(self):
        data = package()
        result = auditor.audit_deb_bytes(data, self.report(data), SOURCE)
        self.assertEqual(result["status"],
                         "ORIGINAL_DEB_AR_CONTROL_DATA_AND_MD5SUMS_EXACT")
        self.assertEqual(result["data_tar"]["regular_files"], 2)
        self.assertEqual(result["data_tar"]["directories"], 4)
        self.assertEqual(result["md5sums"]["recorded_entries"], 2)
        self.assertEqual(result["control"]["package"], "motionwright")
        self.assertEqual(result["gaps"]["formal_G3"], "NOT_GRANTED")
        self.assertFalse(result["gaps"]["release_authorized"])

    def test_modified_package_refuses_without_rebinding_inventory(self):
        data = package()
        original = self.report(data)
        changed = data[:-1] + bytes([data[-1] ^ 1])
        with self.assertRaisesRegex(ValueError, "package_bytes_changed"):
            auditor.audit_deb_bytes(changed, original, SOURCE)

    def test_missing_md5_entry_refuses_despite_rebinding_inventory(self):
        only = md5_manifest({"usr/bin/motionwright-desktop": FILES["usr/bin/motionwright-desktop"]})
        data = package(md5=only)
        with self.assertRaisesRegex(ValueError, "md5sums_not_complete"):
            auditor.audit_deb_bytes(data, self.report(data, md5=only), SOURCE)

    def test_added_md5_entry_refuses(self):
        extra = self.current_md5 + b"0" * 32 + b"  usr/bin/phantom\n"
        data = package(md5=extra)
        with self.assertRaisesRegex(ValueError, "md5sums_not_complete"):
            auditor.audit_deb_bytes(data, self.report(data, md5=extra), SOURCE)

    def test_duplicate_md5_entry_refuses(self):
        duplicate = self.current_md5 + self.current_md5.splitlines(keepends=True)[0]
        data = package(md5=duplicate)
        with self.assertRaisesRegex(ValueError, "md5sums_duplicate_or_path"):
            auditor.audit_deb_bytes(data, self.report(data, md5=duplicate), SOURCE)

    def test_forged_md5_digest_refuses_even_when_original_md5sums_rebound(self):
        mutated = b"f" * 32 + self.current_md5[32:]
        data = package(md5=mutated)
        with self.assertRaisesRegex(ValueError, "original_bytes_or_md5_mismatch"):
            auditor.audit_deb_bytes(data, self.report(data, md5=mutated), SOURCE)

    def test_extra_postinstall_control_script_refuses(self):
        data = package(extra_control=b"#!/bin/sh\nfalse\n")
        with self.assertRaisesRegex(ValueError, "control_member_not_closed"):
            auditor.audit_deb_bytes(data, self.report(data), SOURCE)

    def test_wrong_control_version_refuses(self):
        bad = self.current_control.replace(b"Version: 0.1.0", b"Version: 2.0.0")
        data = package(control=bad)
        with self.assertRaisesRegex(ValueError, "control_candidate_identity"):
            auditor.audit_deb_bytes(data, self.report(data, control=bad), SOURCE)

    def test_missing_required_runtime_dependency_refuses(self):
        bad = self.current_control.replace(b"libwebkit2gtk-4.1-0", b"libwebkit0")
        data = package(control=bad)
        with self.assertRaisesRegex(ValueError, "required_runtime_relations"):
            auditor.audit_deb_bytes(data, self.report(data, control=bad), SOURCE)

    def test_duplicate_control_field_refuses(self):
        bad = self.current_control + b"Version: 0.1.0\n"
        data = package(control=bad)
        with self.assertRaisesRegex(ValueError, "duplicate_or_empty_field"):
            auditor.audit_deb_bytes(data, self.report(data, control=bad), SOURCE)

    def test_wrong_source_cannot_claim_another_run(self):
        data = package()
        with self.assertRaisesRegex(ValueError, "original_inventory_context"):
            auditor.audit_deb_bytes(data, self.report(data), "9" * 40)

    def test_additional_payload_file_not_in_original_inventory_refuses(self):
        original = self.current_files
        changed = {**original, "usr/bin/foreign-binary": b"foreign"}
        data = package(files=changed, md5=self.current_md5)
        with self.assertRaisesRegex(ValueError, "data_tar_extra_or_duplicate"):
            auditor.audit_deb_bytes(data, self.report(data, files=original), SOURCE)

    def test_changed_payload_bytes_refuse_even_with_new_ar_identity(self):
        changed = dict(self.current_files)
        changed["usr/bin/motionwright-desktop"] += b"foreign suffix"
        data = package(files=changed, md5=self.current_md5)
        with self.assertRaisesRegex(ValueError, "data_file_type_or_size_changed"):
            auditor.audit_deb_bytes(data, self.report(data), SOURCE)

    def test_trailing_ar_member_refuses(self):
        data = package(extra_ar=[("other", b"not-authorized")])
        with self.assertRaisesRegex(ValueError, "extra_member_or_version"):
            auditor.audit_deb_bytes(data, self.report(data), SOURCE)

    def test_noncanonical_tar_path_refuses(self):
        bad_data = tar_gz({"../usr/bin/motionwright-desktop": b"malicious"}, DIRS)
        data = package(raw_data=bad_data)
        with self.assertRaisesRegex(ValueError, "member_path_invalid"):
            auditor.audit_deb_bytes(data, self.report(data), SOURCE)

    def test_symlink_cannot_replace_regular_file(self):
        data_tar = tar_gz(
            {"usr/share/applications/Motionwright.desktop":
                FILES["usr/share/applications/Motionwright.desktop"]},
            DIRS,
            {"usr/bin/motionwright-desktop": "foreign"},
        )
        data = package(raw_data=data_tar)
        with self.assertRaisesRegex(ValueError, "data_symlink_literal_changed"):
            auditor.audit_deb_bytes(data, self.report(data), SOURCE)

    def test_additional_inventory_member_cannot_be_silently_ignored(self):
        data = package()
        altered = self.report(data)
        new_member = copy.deepcopy(altered["roots"][0]["members"][-1])
        new_member["path"] = "usr/bin/not-in-package"
        altered["roots"][0]["members"].append(new_member)
        with self.assertRaises(inventory.InventoryError):
            auditor.audit_deb_bytes(data, altered, SOURCE)


    def test_runtime_package_name_substrings_do_not_satisfy_dependencies(self):
        for before, after in (
            (b"libwebkit2gtk-4.1-0", b"not-libwebkit2gtk-4.1-0"),
            (b"libwebkit2gtk-4.1-0", b"libwebkit2gtk-4.1-0-fake"),
            (b"libgtk-3-0", b"libgtk-3-0-fake"),
        ):
            with self.subTest(after=after):
                control = STANDARD_CONTROL.replace(before, after)
                data = package(control=control)
                with self.assertRaisesRegex(ValueError, "required_runtime_relations"):
                    auditor.audit_deb_bytes(data, self.report(data, control=control), SOURCE)

    def test_runtime_alternative_cannot_make_required_package_optional(self):
        for alternative in (
            b"libwebkit2gtk-4.1-0 | unrelated",
            b"unrelated | libwebkit2gtk-4.1-0",
            b"libwebkit2gtk-4.1-0:any",
            b"libwebkit2gtk-4.1-0:arm64",
        ):
            with self.subTest(alternative=alternative):
                control = STANDARD_CONTROL.replace(b"libwebkit2gtk-4.1-0", alternative)
                with self.assertRaisesRegex(ValueError, "required_runtime_relations"):
                    auditor.control_fields(control, FILENAME)

    def test_versioned_exact_runtime_relations_and_unrelated_alternatives_pass(self):
        value = (
            b"libwebkit2gtk-4.1-0:amd64 (>= 2.40.0), "
            b"libgtk-3-0 (>= 1:3.24.0-1ubuntu2), optional-one | optional-two"
        )
        control = STANDARD_CONTROL.replace(b"libwebkit2gtk-4.1-0, libgtk-3-0", value)
        result = auditor.control_fields(control, FILENAME)
        self.assertEqual(result["Depends"], value.decode())

    def test_malformed_runtime_relation_and_source_only_restrictions_refuse(self):
        for replacement in (
            b"libwebkit2gtk-4.1-0 (>=)",
            b"libwebkit2gtk-4.1-0 (> 1.0)",
            b"libwebkit2gtk-4.1-0 (>= 1:)",
            b"libwebkit2gtk-4.1-0 (>= 1-)",
            b"libwebkit2gtk-4.1-0 [amd64]",
            b"libwebkit2gtk-4.1-0 <stage1>",
            b"libwebkit2gtk-4.1-0 |",
        ):
            with self.subTest(replacement=replacement):
                control = STANDARD_CONTROL.replace(b"libwebkit2gtk-4.1-0", replacement)
                with self.assertRaisesRegex(ValueError, "runtime_relation"):
                    auditor.control_fields(control, FILENAME)

    def cli_inputs(self):
        data = package()
        original = self.root / "original.deb"
        report = self.root / "inventory.json"
        output = self.root / "audit.json"
        original.write_bytes(data)
        raw = inventory.capture.encoded(self.report(data))
        report.write_bytes(raw)
        args = [
            "linux_deb_archive_audit.py", "--deb", str(original),
            "--inventory", str(report), "--expected-source", SOURCE,
            "--expected-inventory-sha256", auditor.checksum(raw),
            "--output", str(output),
        ]
        return original, report, output, args

    def run_cli(self, args):
        with mock.patch.object(sys, "argv", args), redirect_stdout(io.StringIO()):
            auditor.main()

    def test_cli_authenticates_original_regular_files(self):
        _, _, output, args = self.cli_inputs()
        self.run_cli(args)
        result = inventory.capture.parse_json(output.read_bytes())
        self.assertEqual(result["context"]["source"], SOURCE)
        self.assertEqual(result["data_tar"]["regular_files"], 2)

    def test_cli_rejects_oversize_inventory_before_opening_it(self):
        _, report, output, args = self.cli_inputs()
        original_open = Path.open

        def refuse_content_read(path, *open_args, **kwargs):
            if path == report:
                raise AssertionError("oversize inventory was opened")
            return original_open(path, *open_args, **kwargs)

        with mock.patch.object(inventory, "MAX_REPORT", report.stat().st_size - 1):
            with mock.patch.object(Path, "open", refuse_content_read):
                with self.assertRaisesRegex(inventory.capture.CaptureError, "file_budget"):
                    self.run_cli(args)
        self.assertFalse(output.exists())

    def test_cli_rejects_oversize_archive_before_opening_it(self):
        original, _, output, args = self.cli_inputs()
        original_open = Path.open

        def refuse_content_read(path, *open_args, **kwargs):
            if path == original:
                raise AssertionError("oversize archive was opened")
            return original_open(path, *open_args, **kwargs)

        with mock.patch.object(auditor, "MAX_ARCHIVE", original.stat().st_size - 1):
            with mock.patch.object(Path, "open", refuse_content_read):
                with self.assertRaisesRegex(inventory.capture.CaptureError, "file_budget"):
                    self.run_cli(args)
        self.assertFalse(output.exists())

    def test_cli_rejects_original_replacement_between_admission_and_open(self):
        for selected in ("inventory", "deb"):
            with self.subTest(selected=selected):
                original, report, output, args = self.cli_inputs()
                target = report if selected == "inventory" else original
                replacement = self.root / ("replacement-" + selected)
                replacement.write_bytes(target.read_bytes())
                original_open = Path.open
                replaced = False

                def replace_before_open(path, *open_args, **kwargs):
                    nonlocal replaced
                    if path == target and not replaced:
                        replaced = True
                        target.rename(self.root / ("prior-" + selected))
                        replacement.rename(target)
                    return original_open(path, *open_args, **kwargs)

                with mock.patch.object(Path, "open", replace_before_open):
                    with self.assertRaisesRegex(inventory.capture.CaptureError,
                                                "file_changed_during_read"):
                        self.run_cli(args)
                self.assertTrue(replaced)
                self.assertFalse(output.exists())

    def test_cli_rejects_symlinked_inventory(self):
        _, report, output, args = self.cli_inputs()
        real_report = self.root / "real-inventory.json"
        report.rename(real_report)
        try:
            report.symlink_to(real_report)
        except OSError as error:
            self.skipTest("symlink creation unavailable: " + str(error))
        with self.assertRaisesRegex(inventory.capture.CaptureError, "redirected_path"):
            self.run_cli(args)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
