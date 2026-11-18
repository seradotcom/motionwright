"""Inert cross-package shared-file tests: no builds, executables or Native effects."""
from __future__ import annotations

import copy
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

TOOLING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLING))
import candidate_package_inventory as inventory
import linux_package_parity as parity


def inert_inputs(kind, *, source="1" * 40):
    return {
        "context": {
            "source": source, "source_tree": "2" * 40,
            "kind": kind, "platform": "linux", "arch": "x86_64",
            "run_id": "123456", "run_attempt": 1,
            "candidate_receipt": inventory.digest(b"single original candidate"),
            "package": {
                "filename": "candidate-" + kind + ".fixture",
                **inventory.digest(("candidate-" + kind).encode()),
            },
            "cargo_index": inventory.digest(b"single cargo index"),
            "artifact_identities_sha256": "3" * 64,
            "generated_metadata_sha256": "4" * 64,
        },
        "origins": [], "components": [],
    }


def inert_custody():
    item = inventory.digest(b"inert original extraction -- not a native effect")
    return {
        "status": "DIRECT_CAPTURED_EXTRACTION", "input": item,
        "operations": [{k: (v if k == "label" else dict(item)) for k, v in {
            "label": "extract", "request": None, "completion": None,
            "stdout": None, "stderr": None, "subtree": None,
            "binding": None, "closure": None,
        }.items()}],
    }


class LinuxPackageParityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mw-parity-inert-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(strict=True)
        self.app = self.root / "app"
        self.deb = self.root / "deb"
        self.app.mkdir()
        self.deb.mkdir()
        for path in (self.app, self.deb):
            self.put(path, "usr/bin/motionwright", b"ELF inert application")
            self.put(path, "usr/share/applications/Motionwright.desktop",
                     b"[Desktop Entry]\nName=Motionwright\n")
        self.put(self.app, "AppRun", b"inert runtime")
        self.put(self.app, "usr/lib/libextra.so", b"\x7fELF native extra")
        self.put(self.deb, "DEBIAN/control", b"Package: motionwright\n")
        self.put(self.deb, "DEBIAN/md5sums", b"inert not actual md5\n")

    def put(self, root, path, raw):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return target

    def report(self, kind, *, source="1" * 40, captured=True):
        path = self.app if kind == "appimage" else self.deb
        return inventory.compose(inert_inputs(kind, source=source),
                                 [inventory.observe_tree(path, "payload")],
                                 custody=inert_custody() if captured else None)

    def test_same_app_files_are_equal_with_explicit_appimage_extras(self):
        result = parity.verify_common_payload(self.report("appimage"),
                                              self.report("deb"))
        self.assertEqual(result["status"],
                         "EXTRACTED_COMMON_APPLICATION_PAYLOAD_BYTES_MATCH")
        self.assertEqual(result["shared_regular_files"], 2)
        self.assertEqual(result["deb_control_files_excluded"],
                         ["DEBIAN/control", "DEBIAN/md5sums"])
        self.assertEqual(result["appimage_extra_regular_files"], 2)
        self.assertEqual(result["claims"]["formal_g3"], "NOT_GRANTED")
        self.assertFalse(result["claims"]["release_authorized"])

    def test_same_path_different_bytes_refuses_even_if_other_files_match(self):
        self.put(self.app, "usr/bin/motionwright", b"ELF changed application")
        with self.assertRaisesRegex(ValueError, "shared_payload_changed"):
            parity.verify_common_payload(self.report("appimage"), self.report("deb"))

    def test_missing_installed_application_member_refuses(self):
        (self.app / "usr/bin/motionwright").unlink()
        with self.assertRaisesRegex(ValueError, "appimage_missing"):
            parity.verify_common_payload(self.report("appimage"), self.report("deb"))

    def test_added_debian_maintainer_script_is_not_silently_excluded(self):
        self.put(self.deb, "DEBIAN/postinst", b"#!/bin/sh\nfalse\n")
        with self.assertRaisesRegex(ValueError, "unrecognized_debian_control"):
            parity.verify_common_payload(self.report("appimage"), self.report("deb"))

    def test_original_run_and_source_must_match(self):
        app = self.report("appimage")
        deb = self.report("deb", source="9" * 40)
        with self.assertRaisesRegex(ValueError, "cross_run_context:source"):
            parity.verify_common_payload(app, deb)

    def test_incomplete_extraction_cannot_become_parity_pass(self):
        app = self.report("appimage", captured=False)
        with self.assertRaisesRegex(ValueError, "extraction_not_captured"):
            parity.verify_common_payload(app, self.report("deb"))

    def test_forged_distributable_approval_is_rejected_by_inventory(self):
        app = self.report("appimage")
        app["claims"]["formal_g3"] = "GRANTED"
        with self.assertRaises(inventory.InventoryError):
            parity.verify_common_payload(app, self.report("deb"))

    def test_original_namespace_change_does_not_carry_stale_digest(self):
        app = self.report("appimage")
        altered = copy.deepcopy(app)
        old = altered["roots"][0]["members"][-1]
        old["sha256"] = "0" * 64
        with self.assertRaises(inventory.InventoryError):
            parity.verify_common_payload(altered, self.report("deb"))


    def cli_inputs(self):
        paths = []
        args = ["linux_package_parity.py", "--expected-source", "1" * 40]
        for kind, flag in (("appimage", "--appimage"), ("deb", "--deb")):
            path = self.root / (kind + ".json")
            raw = inventory.capture.encoded(self.report(kind))
            path.write_bytes(raw)
            paths.append(path)
            args.extend([flag, str(path), "--expected-" + kind + "-sha256",
                         inventory.capture.sha(raw)])
        output = self.root / "parity.json"
        args.extend(["--output", str(output)])
        return paths, output, args

    def run_cli(self, args):
        with mock.patch.object(sys, "argv", args), redirect_stdout(io.StringIO()):
            parity.main()

    def test_cli_authenticates_both_original_inventory_files(self):
        _, output, args = self.cli_inputs()
        self.run_cli(args)
        self.assertEqual(inventory.capture.parse_json(output.read_bytes())
                         ["shared_regular_files"], 2)

    def test_cli_rejects_oversize_inventory_before_opening_it(self):
        paths, output, args = self.cli_inputs()
        target = paths[0]
        original_open = Path.open

        def refuse_content_read(path, *open_args, **kwargs):
            if path == target:
                raise AssertionError("oversize inventory was opened")
            return original_open(path, *open_args, **kwargs)

        with mock.patch.object(inventory, "MAX_REPORT", target.stat().st_size - 1):
            with mock.patch.object(Path, "open", refuse_content_read):
                with self.assertRaisesRegex(inventory.capture.CaptureError, "file_budget"):
                    self.run_cli(args)
        self.assertFalse(output.exists())

    def test_cli_rejects_inventory_replacement_even_with_same_bytes(self):
        paths, output, args = self.cli_inputs()
        target = paths[1]
        replacement = self.root / "replacement.json"
        replacement.write_bytes(target.read_bytes())
        original_open = Path.open
        replaced = False

        def replace_before_open(path, *open_args, **kwargs):
            nonlocal replaced
            if path == target and not replaced:
                replaced = True
                target.rename(self.root / "prior-deb.json")
                replacement.rename(target)
            return original_open(path, *open_args, **kwargs)

        with mock.patch.object(Path, "open", replace_before_open):
            with self.assertRaisesRegex(inventory.capture.CaptureError,
                                        "file_changed_during_read"):
                self.run_cli(args)
        self.assertTrue(replaced)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
