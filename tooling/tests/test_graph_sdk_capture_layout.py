"""Inert source-captured Semwright binary roster contracts for Graph owner.

These fixtures never start a daemon, approve R74, or claim Native/SQLite PASS.
"""
from __future__ import annotations
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_graph_browser_owner import GraphOwnerFailure, verify_graph_sdk_capture_roster

ROLES = ("semwright", "semwrightd", "semwright_sandbox")
SDK = "/home/runner/work/motionwright/motionwright/v/sdk"

def original():
    source = {"sdk_root": SDK, "sha": "a" * 40, "run_id": "98765"}
    binaries = {role: {
        "path": f"{SDK}/target/mw-r74-captured/{'semwright-sandbox' if role == 'semwright_sandbox' else role}",
        "sha256": format(index + 1, "064x"), "size_bytes": 4096 + index,
    } for index, role in enumerate(ROLES)}
    spec = {"source": source, "binaries": binaries}
    index = copy.deepcopy(spec)
    return spec, index

class GraphSdkCapturedRosterTests(unittest.TestCase):
    def test_exact_captured_sdk_roles_are_the_only_positive_roster(self):
        spec, index = original()
        self.assertTrue(verify_graph_sdk_capture_roster(spec, index))

    def test_legacy_mutable_target_debug_sandbox_denied_even_when_both_agree(self):
        spec, index = original()
        for value in (spec, index):
            value["binaries"]["semwright_sandbox"]["path"] = SDK + "/target/debug/semwright-sandbox"
        with self.assertRaisesRegex(GraphOwnerFailure, "graph-sdk-capture-role-mismatch:semwright_sandbox"):
            verify_graph_sdk_capture_roster(spec, index)

    def test_old_captured_underscore_is_not_runtime_helper(self):
        spec, index = original()
        for value in (spec, index):
            value["binaries"]["semwright_sandbox"]["path"] = SDK + "/target/mw-r74-captured/semwright_sandbox"
        with self.assertRaisesRegex(GraphOwnerFailure, "graph-sdk-capture-role-mismatch:semwright_sandbox"):
            verify_graph_sdk_capture_roster(spec, index)

    def test_tampered_producer_digest_or_length_denied(self):
        for attribute, replacement in (("sha256", "f" * 64), ("size_bytes", 999999)):
            with self.subTest(attribute=attribute):
                spec, index = original()
                index["binaries"]["semwright"][attribute] = replacement
                with self.assertRaisesRegex(GraphOwnerFailure, "graph-sdk-capture-role-mismatch:semwright"):
                    verify_graph_sdk_capture_roster(spec, index)

    def test_malformed_digest_or_zero_size_denied_even_when_both_agree(self):
        for attribute, replacement in (("sha256", "not-a-digest"), ("size_bytes", 0)):
            with self.subTest(attribute=attribute):
                spec, index = original()
                for value in (spec, index):
                    value["binaries"]["semwrightd"][attribute] = replacement
                with self.assertRaisesRegex(GraphOwnerFailure, "graph-sdk-capture-role-mismatch:semwrightd"):
                    verify_graph_sdk_capture_roster(spec, index)

    def test_wrong_sdk_root_denied_even_if_original_matches(self):
        spec, index = original()
        for value in (spec, index):
            value["source"]["sdk_root"] = "/foreign/sdk"
        with self.assertRaisesRegex(GraphOwnerFailure, "graph-sdk-capture-role-mismatch:semwright"):
            verify_graph_sdk_capture_roster(spec, index)

    def test_cross_source_original_denied(self):
        spec, index = original()
        index["source"]["sha"] = "b" * 40
        with self.assertRaisesRegex(GraphOwnerFailure, "graph-sdk-capture-source-mismatch"):
            verify_graph_sdk_capture_roster(spec, index)

    def test_wrong_role_and_missing_role_denied(self):
        spec, index = original()
        spec["binaries"]["semwright"], spec["binaries"]["semwrightd"] = (
            spec["binaries"]["semwrightd"], spec["binaries"]["semwright"])
        with self.assertRaisesRegex(GraphOwnerFailure, "graph-sdk-capture-role-mismatch:semwright"):
            verify_graph_sdk_capture_roster(spec, index)
        spec, index = original()
        index["binaries"].pop("semwright_sandbox")
        with self.assertRaisesRegex(GraphOwnerFailure, "graph-sdk-capture-role-mismatch:semwright_sandbox"):
            verify_graph_sdk_capture_roster(spec, index)

    def test_extra_fields_and_missing_roster_fail_closed(self):
        spec, index = original()
        spec["binaries"]["semwright"]["unreviewed"] = True
        with self.assertRaisesRegex(GraphOwnerFailure, "graph-sdk-capture-role-mismatch:semwright"):
            verify_graph_sdk_capture_roster(spec, index)
        spec, index = original()
        index.pop("binaries")
        with self.assertRaisesRegex(GraphOwnerFailure, "graph-sdk-capture-roster-shape"):
            verify_graph_sdk_capture_roster(spec, index)

if __name__ == "__main__":
    unittest.main()
