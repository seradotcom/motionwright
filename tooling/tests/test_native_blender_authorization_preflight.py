"""Closed preflight regressions only; no Native launch or G2 declaration."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tooling import native_blender_e2e as blender


def descriptor(name, risk="mutating", consent=False):
    return {"name": name, "risk": risk, "interactive_consent": consent}


def capability(name, state="allow", risk="mutating"):
    return {"id": name, "risk": risk, "available": True,
            "policy_preview": {"state": state, "preview_only": True, "execution_rechecks": True}}


class BlenderAuthorizationPreflightTests(unittest.TestCase):
    def test_save_and_reopen_need_original_operator_without_lowering_descriptors(self):
        rows = [descriptor("blender.file.save", "destructive", True),
                descriptor("blender.file.open", "destructive", True),
                descriptor("blender.render")]
        before = json.dumps(rows, sort_keys=True)
        with self.assertRaises(blender.BlenderOwnerApprovalRequired) as caught:
            blender.inspect_headless_blender_descriptors(
                {"driver.blender.file.save", "driver.blender.file.open", "driver.blender.render"}, rows)
        self.assertEqual(caught.exception.commands,
                         ("driver.blender.file.open", "driver.blender.file.save"))
        self.assertEqual(json.dumps(rows, sort_keys=True), before)

    def test_reversible_source_operations_are_not_synthetic_permission_grants(self):
        rows = [descriptor("blender.render"), descriptor("blender.object.transform", "mutating_reversible")]
        self.assertIsNone(blender.inspect_headless_blender_descriptors(
            {"driver.blender.render", "driver.blender.object.transform", "driver.blender.semantic.objects"}, rows))
        with self.assertRaisesRegex(ValueError, "preview"):
            blender.require_headless_blender_catalog([{"id": "driver.blender.render"}], {"driver.blender.render"})

    def test_interactive_descriptor_cannot_be_silently_accepted_as_mutating(self):
        with self.assertRaises(blender.BlenderOwnerApprovalRequired):
            blender.inspect_headless_blender_descriptors(
                {"driver.blender.render"}, [descriptor("blender.render", consent=True)])

    def test_incompatible_plan_stops_before_owner_files_or_any_native_process(self):
        with tempfile.TemporaryDirectory(prefix="blender-consent-preflight-") as tmp:
            root = Path(tmp).resolve()
            runtime = root / "runtime"
            runtime.mkdir()
            original = json.dumps([descriptor("blender.file.save", "destructive", True)]).encode()
            read = subprocess.CompletedProcess([], 0, stdout=original, stderr=b"")
            with mock.patch.object(blender.subprocess, "run", return_value=read) as git_read, \
                    mock.patch.object(blender.subprocess, "Popen") as launch:
                with self.assertRaises(blender.BlenderOwnerApprovalRequired):
                    with blender.blender_context({"runtime": runtime}, {}, root, {},
                                                 required={"driver.blender.file.save"}):
                        self.fail("A client connection was published without approval")
            launch.assert_not_called()
            self.assertEqual(len(git_read.call_args_list), 1)
            self.assertEqual(git_read.call_args.args[0][-1], blender.PIN + ":schemas/commands.json")
            self.assertEqual(git_read.call_args.kwargs["env"]["GIT_NO_LAZY_FETCH"], "1")
            self.assertEqual(list(root.iterdir()), [runtime])
            self.assertEqual(list(runtime.iterdir()), [])

    def test_source_missing_duplicate_or_unknown_risk_fails_closed(self):
        required = {"driver.blender.render"}
        for rows in ([], [descriptor("blender.render"), descriptor("blender.render")],
                     [descriptor("blender.render", "unknown")]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                blender.inspect_headless_blender_descriptors(required, rows)

    def test_live_confirmation_unavailability_and_ambiguity_remain_denials(self):
        required = {"driver.blender.semantic.objects"}
        for state in ("deny", "requires_arguments", "requires_target", "unknown"):
            with self.subTest(state=state), self.assertRaises(ValueError):
                blender.require_headless_blender_catalog([capability(next(iter(required)), state)], required)
        with self.assertRaises(blender.BlenderOwnerApprovalRequired):
            blender.require_headless_blender_catalog([capability(next(iter(required)), "require_confirmation")], required)
        allowed = capability(next(iter(required)))
        self.assertIsNone(blender.require_headless_blender_catalog([allowed], required))
        with self.assertRaises(ValueError):
            blender.require_headless_blender_catalog([allowed, allowed], required)
        allowed["available"] = False
        with self.assertRaises(ValueError):
            blender.require_headless_blender_catalog([allowed], required)


if __name__ == "__main__":
    unittest.main()
