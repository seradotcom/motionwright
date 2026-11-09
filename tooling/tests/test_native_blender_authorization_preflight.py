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


    def test_foreground_operator_is_explicit_terminal_bound_and_cannot_approve(self):
        # This validates only source admission. The physical Broker must
        # still issue and verify a fresh human challenge for each action.
        required = {"driver.blender.file.save", "driver.blender.file.open"}
        with mock.patch.dict(blender.os.environ, {
            "MOTIONWRIGHT_BLENDER_OPERATOR_CONSOLE": "headless",
        }):
            self.assertFalse(blender.foreground_operator_requested(required))
        with mock.patch.dict(blender.os.environ, {
            "MOTIONWRIGHT_BLENDER_OPERATOR_CONSOLE": "yes",
        }):
            with self.assertRaises(ValueError):
                blender.foreground_operator_requested(required)
        with mock.patch.dict(blender.os.environ, {
            "MOTIONWRIGHT_BLENDER_OPERATOR_CONSOLE": "foreground",
        }), mock.patch.object(blender.sys.stdin, "isatty", return_value=False):
            with self.assertRaises(blender.BlenderOwnerApprovalRequired):
                blender.foreground_operator_requested(required)
        with mock.patch.dict(blender.os.environ, {
            "MOTIONWRIGHT_BLENDER_OPERATOR_CONSOLE": "foreground",
        }), mock.patch.object(blender.os, "name", "posix"), \
                mock.patch.object(blender.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(blender.sys.stdout, "isatty", return_value=True), \
                mock.patch.object(blender.os, "open", return_value=13) as open_tty, \
                mock.patch.object(blender.os, "close") as close_tty:
            self.assertTrue(blender.foreground_operator_requested(required))
            open_tty.assert_called_once()
            self.assertEqual(open_tty.call_args.args[0], "/dev/tty")
            close_tty.assert_called_once_with(13)
            with self.assertRaises(ValueError):
                blender.foreground_operator_requested({"driver.blender.render"})

        rows = [descriptor("blender.file.save", "destructive", True),
                descriptor("blender.file.open", "destructive", True)]
        with self.assertRaises(blender.BlenderOwnerApprovalRequired):
            blender.inspect_headless_blender_descriptors(required, rows)
        self.assertIsNone(blender.inspect_headless_blender_descriptors(
            required, rows, operator_console=True))
        rows[0]["interactive_consent"] = False
        with self.assertRaises(blender.BlenderOwnerApprovalRequired):
            blender.inspect_headless_blender_descriptors(
                required, rows, operator_console=True)
        for name in required:
            consent = capability(name, "require_confirmation", "destructive")
            with self.assertRaises(blender.BlenderOwnerApprovalRequired):
                blender.require_headless_blender_catalog([consent], {name})
            self.assertIsNone(blender.require_headless_blender_catalog(
                [consent], {name}, operator_console=True))
            consent["policy_preview"]["state"] = "allow"
            with self.assertRaises(blender.BlenderOwnerApprovalRequired):
                blender.require_headless_blender_catalog(
                    [consent], {name}, operator_console=True)

    def test_real_foreground_daemon_group_owns_tty_then_restores_parent(self):
        # Synthetic POSIX API calls verify the release logic only; neither an
        # operator challenge nor a Native Blender effect is exercised here.
        import inspect
        daemon_pid, parent_pgid, fd = 71, 23, 17
        with mock.patch.object(blender.os, "open", return_value=fd), \
                mock.patch.object(blender.os, "close") as close, \
                mock.patch.object(blender.os, "getpgrp", return_value=parent_pgid, create=True), \
                mock.patch.object(blender.os, "getpgid", return_value=daemon_pid, create=True), \
                mock.patch.object(blender.os, "tcgetpgrp",
                                  side_effect=[parent_pgid, daemon_pid], create=True), \
                mock.patch.object(blender.os, "tcsetpgrp", create=True) as set_foreground, \
                mock.patch.object(blender.signal, "SIGTTOU", 22, create=True), \
                mock.patch.object(blender.signal, "signal",
                                  return_value=blender.signal.SIG_DFL) as set_handler:
            lease = blender.acquire_foreground_daemon_terminal(daemon_pid)
            self.assertEqual(lease, (fd, parent_pgid))
            blender.restore_foreground_daemon_terminal(*lease, daemon_pid)
            self.assertEqual(set_foreground.call_args_list,
                             [mock.call(fd, daemon_pid), mock.call(fd, parent_pgid)])
            self.assertEqual(set_handler.call_args_list,
                             [mock.call(22, blender.signal.SIG_IGN),
                              mock.call(22, blender.signal.SIG_DFL)])
            close.assert_called_once_with(fd)

        with mock.patch.object(blender.os, "open", return_value=fd), \
                mock.patch.object(blender.os, "close") as close, \
                mock.patch.object(blender.os, "getpgrp", return_value=parent_pgid, create=True), \
                mock.patch.object(blender.os, "getpgid", return_value=99, create=True), \
                mock.patch.object(blender.os, "tcsetpgrp", create=True) as changed:
            with self.assertRaisesRegex(RuntimeError, "owned process group"):
                blender.acquire_foreground_daemon_terminal(daemon_pid)
            changed.assert_not_called()
            close.assert_called_once_with(fd)

        source = inspect.getsource(blender.blender_context)
        self.assertIn("foreground_terminal = acquire_foreground_daemon_terminal(daemon.pid)",
                      source)
        self.assertIn("restore_foreground_daemon_terminal(", source)
        self.assertIn("stdin=subprocess.DEVNULL", source)
        self.assertIn("stdout=subprocess.PIPE if bounded_static else log_stream", source)
        self.assertIn("stderr=subprocess.STDOUT if bounded_static else log_stream", source)


if __name__ == "__main__":
    unittest.main()
