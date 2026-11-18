"""Inert source controls for upstream-asset pinning before Tauri bundle.

All downloads are mocked in this unit; no provider call or tool execution.
"""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_tauri_bundle_tool_pins as pins


def fake_originals():
    values = (
        b"\x7fELF" + b"apprun-fixture",
        b"\x7fELF\x02\x01\x01\x00\x11\x22\x33\x44" + b"linuxdeploy-fixture",
        b"\x7fELF" + b"output-plugin-fixture",
    )
    assets = tuple({
        **item, "bytes": len(value),
        "sha256": hashlib.sha256(value).hexdigest(),
    } for item, value in zip(pins.ASSETS, values))
    return assets, values


class PinnedTauriToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="mw-tauri-pinned-", dir=os.environ.get("TEMP"))
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve(strict=True) / "cache"
        self.home.mkdir()
        self.assets, self.values = fake_originals()
        self.map = {asset["url"]: data for asset, data in
                    zip(self.assets, self.values)}
        self.calls = []

    def open(self, url):
        self.calls.append(url)
        return io.BytesIO(self.map[url])

    def stage(self):
        with mock.patch.object(pins, "ASSETS", self.assets):
            pre = pins.stage(self.home, assets=self.assets, opener=self.open)
            checked = pins.verify_cached(self.home, pre)
        return pre, checked

    def test_source_asset_roster_is_three_exact_original_release_names(self):
        names = [x["cache"] for x in pins.ASSETS]
        self.assertEqual(names, [
            "AppRun-x86_64", "linuxdeploy-07333c6-x86_64.AppImage",
            "linuxdeploy-plugin-appimage.AppImage"])
        self.assertEqual(len({x["asset_id"] for x in pins.ASSETS}), 3)
        self.assertTrue(all(x["url"].startswith("https://github.com/")
                            for x in pins.ASSETS))

    def test_all_three_verifiable_before_any_external_tool_runs(self):
        staged, before = self.stage()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(before["status"], "PRE_BUNDLE_TOOLS_MATCH_PINNED_ORIGINALS")
        self.assertEqual(len(before["tools"]), 3)
        self.assertFalse(staged["claims"]["tools_exec_in_this_phase"])
        self.assertEqual(staged["claims"]["G3"], "STOP")

    def test_linuxdeploy_exact_three_byte_header_zero_post_is_admitted(self):
        staged, _ = self.stage()
        path = self.home / "tauri/linuxdeploy-07333c6-x86_64.AppImage"
        raw = path.read_bytes()
        changed = raw[:8] + b"\x00\x00\x00" + raw[11:]
        self.assertNotEqual(raw, changed)
        path.write_bytes(changed)
        with mock.patch.object(pins, "ASSETS", self.assets):
            after = pins.verify_cached(self.home, staged, post=True)
        self.assertEqual(after["status"], "POST_BUNDLE_TOOLS_MATCH_PINNED_TRANSFORM")
        self.assertEqual(after["claims"]["G3"], "STOP")

    def test_unmodified_linuxdeploy_not_accepted_as_patch_when_patch_required(self):
        staged, _ = self.stage()
        with mock.patch.object(pins, "ASSETS", self.assets):
            with self.assertRaisesRegex(ValueError, "cache_post_changed"):
                pins.verify_cached(self.home, staged, post=True)

    def test_arbitrary_linuxdeploy_mutation_rejected_post(self):
        staged, _ = self.stage()
        path = self.home / "tauri/linuxdeploy-07333c6-x86_64.AppImage"
        raw = bytearray(path.read_bytes())
        raw[12] ^= 0x80
        raw[8:11] = b"\x00\x00\x00"
        path.write_bytes(raw)
        with mock.patch.object(pins, "ASSETS", self.assets):
            with self.assertRaisesRegex(ValueError, "cache_post_changed"):
                pins.verify_cached(self.home, staged, post=True)

    def test_plugin_mutation_rejected_post(self):
        staged, _ = self.stage()
        path = self.home / "tauri/linuxdeploy-plugin-appimage.AppImage"
        path.write_bytes(path.read_bytes() + b"modified")
        with mock.patch.object(pins, "ASSETS", self.assets):
            with self.assertRaisesRegex(ValueError, "cache_post_changed"):
                pins.verify_cached(self.home, staged, post=True)

    def test_mutated_app_run_rejected_pre(self):
        staged, _ = self.stage()
        path = self.home / "tauri/AppRun-x86_64"
        raw = path.read_bytes()
        path.write_bytes(raw[:-1] + b"X")
        with mock.patch.object(pins, "ASSETS", self.assets):
            with self.assertRaisesRegex(ValueError, "cache_pre_changed"):
                pins.verify_cached(self.home, staged)

    def test_same_size_wrong_vendor_download_refused(self):
        item = self.assets[0]
        with self.assertRaisesRegex(ValueError, "sha256_mismatch"):
            pins.pinned_bytes(item["url"], item["bytes"], item["sha256"],
                              opener=lambda url: io.BytesIO(b"Z" * item["bytes"]))

    def test_oversized_vendor_download_refused(self):
        item = self.assets[0]
        with self.assertRaisesRegex(ValueError, "size_mismatch"):
            pins.pinned_bytes(item["url"], item["bytes"], item["sha256"],
                              opener=lambda url: io.BytesIO(b"X" * (item["bytes"] + 8)))

    def test_cache_replay_preexisting_refused(self):
        self.stage()
        with mock.patch.object(pins, "ASSETS", self.assets):
            with self.assertRaisesRegex(ValueError, "not_fresh"):
                pins.stage(self.home, assets=self.assets, opener=self.open)

    def test_cache_symlinked_vendor_file_refused(self):
        staged, _ = self.stage()
        path = self.home / "tauri/AppRun-x86_64"
        renamed = path.with_name("AppRun-x86_64.real")
        path.rename(renamed)
        try:
            path.symlink_to(renamed)
        except OSError as exc:
            self.skipTest("symlink privilege unavailable: " + str(exc))
        with mock.patch.object(pins, "ASSETS", self.assets):
            with self.assertRaises((ValueError, OSError,
                                    pins.inventory.capture.CaptureError)):
                pins.verify_cached(self.home, staged)

    def test_modified_source_manifest_not_accepted_as_new_vendor_bytes(self):
        staged, _ = self.stage()
        staged["tools"][0]["original"]["sha256"] = "0" * 64
        with mock.patch.object(pins, "ASSETS", self.assets):
            with self.assertRaisesRegex(ValueError, "source_roster_changed"):
                pins.verify_cached(self.home, staged)

    def test_ambiguous_roster_refused_without_fetch(self):
        with self.assertRaisesRegex(ValueError, "roster_not_closed"):
            pins.stage(self.home, assets=self.assets[:2], opener=self.open)
        self.assertEqual(self.calls, [])

    def test_no_http_url_even_with_expected_bytes(self):
        item = self.assets[0]
        with self.assertRaisesRegex(ValueError, "vendor_identity"):
            pins.pinned_bytes("http://github.com/evil/asset",
                              item["bytes"], item["sha256"], opener=self.open)

    def test_post_cannot_misreport_complete_closure(self):
        staged, _ = self.stage()
        self.assertFalse(staged["claims"]["bundler_operations_traced"])
        self.assertFalse(staged["claims"]["downstream_type2_runtime_download_pinned"])
        self.assertEqual(staged["claims"]["redistribution_permission"], "NOT_INFERRED")


if __name__ == "__main__":
    unittest.main()
