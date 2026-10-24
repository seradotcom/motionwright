"""Offline closed-scope contracts; no native process or filesystem fixture."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import kdenlive_gui_e2e as runner
from kdenlive_gui_actions import GuiRejected, GuiUncertain


class AccessibilityOnlyContracts(unittest.TestCase):
    def records(self):
        result = Mock()
        result.root = Path("/runner/evidence")
        return result

    def ui(self):
        result = Mock()
        result.pid = 42
        result.init_returncode = 0
        result.discovery_count = 1
        result.first_discovery = result.last_discovery = {
            "desktop_child_count": 1, "observed_pids": [42],
            "owned_application_count": 1, "null_child_count": 0}
        result.wait_ready.return_value = [{"key": "app", "pid": 42}]
        return result

    def test_accessibility_scope_requires_exact_manual_first_attempt(self):
        env = {"GITHUB_ACTIONS": "true", "CI": "true", "RUNNER_OS": "Linux",
               "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
               "MOTIONWRIGHT_KDENLIVE_GUI_SCOPE": "accessibility-only",
               "MOTIONWRIGHT_APPROVED_KDENLIVE_G2_SOURCE": "1"}
        runner.validate_dispatch(env, "linux", 1001, scope="accessibility-only")
        for changed in ({"GITHUB_RUN_ATTEMPT": "2"},
                        {"GITHUB_EVENT_NAME": "push"},
                        {"MOTIONWRIGHT_KDENLIVE_GUI_SCOPE": "kdenlive-gui"}):
            with self.subTest(changed=changed), self.assertRaises(GuiRejected):
                runner.validate_dispatch({**env, **changed}, "linux", 1001,
                                         scope="accessibility-only")
        for scope in ("source", "unknown", "", None):
            with self.subTest(scope=scope), self.assertRaises(GuiRejected):
                runner.validate_dispatch(env, "linux", 1001, scope=scope)

    def test_preflight_selection_never_creates_fixture_import_or_owner_commit(self):
        records = self.records()
        observed = {"scope": "accessibility-only"}
        with patch.object(runner, "run_accessibility_preflight", return_value=observed) as preflight, \
             patch.object(runner.fixture, "prepare_original") as fixture, \
             patch.object(runner, "run_route") as route, \
             patch.object(runner.owner_cut, "commit_once") as owner, \
             patch.object(runner.subprocess, "Popen") as popen:
            status, result = runner.run_selected_native(
                "accessibility-only", Path("/app"), Path("/app/kdenlive"),
                "a" * 64, Path("/runner/unused-capsule"), records, Path("/runner/profile"))
        self.assertEqual(status, "NATIVE_ACCESSIBILITY_OBSERVED")
        self.assertIs(result, observed)
        preflight.assert_called_once()
        fixture.assert_not_called()
        route.assert_not_called()
        owner.assert_not_called()
        popen.assert_not_called()
        records.emit.assert_not_called()

    def test_discovery_stops_before_ready_click_type_import_save_or_quit(self):
        ui, records = self.ui(), self.records()
        rows = runner.initialize_accessibility(
            ui, "accessibility-only", records, accessibility_only=True)
        self.assertEqual(rows, ui.wait_ready.return_value)
        ui.wait_ready.assert_called_once_with("accessibility-only")
        for name in ("ready", "action", "control", "menu", "focus", "text", "key",
                     "file_dialog", "save_as", "export_otio", "edit_third_clip", "quit"):
            getattr(ui, name).assert_not_called()
        retained = records.document.call_args.args[1]
        self.assertEqual(retained["first_discovery"], ui.first_discovery)
        self.assertEqual(retained["last_discovery"], ui.last_discovery)
        self.assertFalse(retained["gui_mutation_attempted"])
        self.assertFalse(retained["startup_acknowledgement_attempted"])

    def test_unknown_discovery_keeps_original_and_cannot_repeat_an_action(self):
        ui, records = self.ui(), self.records()
        original = GuiUncertain("accessibility-only.accessibility-ready")
        ui.wait_ready.side_effect = original
        records.document.side_effect = OSError("private recorder failed")
        with self.assertRaises(GuiUncertain) as caught:
            runner.initialize_accessibility(
                ui, "accessibility-only", records, accessibility_only=True)
        self.assertIs(caught.exception, original)
        self.assertIn("accessibility_preflight_recording_failed:OSError",
                      caught.exception.__notes__)
        ui.wait_ready.assert_called_once()
        ui.ready.assert_not_called()
        ui.action.assert_not_called()
        ui.key.assert_not_called()
        ui.quit.assert_not_called()

    def test_preflight_cannot_reopen_a_project_or_enter_a_gui_phase(self):
        for phase, selected, reopen in (
                ("import", True, None),
                ("accessibility-only", True, Path("/runner/prior.kdenlive")),
                ("accessibility-only", False, None),
                ("accessibility-only", "yes", None)):
            with self.subTest(phase=phase, selected=selected, reopen=reopen), \
                 patch.object(runner.subprocess, "Popen") as popen, \
                 self.assertRaises(GuiRejected):
                runner.NativeProcess(Path("/app"), Path("/app/kdenlive"), "a" * 64,
                                     Path("/runner/profile"), self.records(), phase,
                                     reopen_input=reopen, accessibility_only=selected)
            popen.assert_not_called()

    def test_environment_allowlist_excludes_secrets_and_refuses_duplicate_fields(self):
        raw = (b"DISPLAY=:99\0DBUS_SESSION_BUS_ADDRESS=unix:path=/runner/bus\0"
               b"QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1\0PRIVATE_TOKEN=do-not-retain\0")
        result = runner.project_accessibility_environment(raw)
        self.assertEqual(result["selected_fields"]["DISPLAY"], ":99")
        self.assertEqual(result["excluded_field_count"], 1)
        self.assertEqual(result["original_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertFalse(result["raw_environment_retained"])
        self.assertNotIn("PRIVATE_TOKEN", json.dumps(result))
        self.assertNotIn("do-not-retain", json.dumps(result))
        for invalid in (b"DISPLAY=:99", b"DISPLAY=:99\0DISPLAY=:100\0",
                        b"DISPLAY=" + b"x" * 8193 + b"\0"):
            with self.subTest(size=len(invalid)), self.assertRaises(GuiRejected):
                runner.project_accessibility_environment(invalid)

    def test_observation_seal_omits_the_uncreated_capsule_and_never_claims_roundtrip(self):
        fake = Mock()
        fake.gather_snapshot.return_value = (b"observed originals", True)
        fake.encrypt_snapshot.return_value = b"sealed originals"
        fake.sha.return_value = "b" * 64
        with patch.object(runner, "custody", fake):
            result = runner.seal_and_publish(
                Path("/runner/evidence"), None, Path("/runner/public"), {}, {},
                "NATIVE_ACCESSIBILITY_OBSERVED", Path("/runner/distribution"))
        fake.gather_snapshot.assert_called_once_with(
            {"owner": Path("/runner/evidence"),
             "distribution": Path("/runner/distribution")}, preserve_partial=True)
        self.assertEqual(result["status"], "NATIVE_ACCESSIBILITY_OBSERVED")
        self.assertNotEqual(result["status"], "NATIVE_GUI_BOUNDED_ROUNDTRIP_OBSERVED")
        self.assertFalse(result["human_acceptance"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["native_operation_retried"])

    def test_distribution_escape_fails_before_reading_or_retaining_foreign_bytes(self):
        fake, records = Mock(), self.records()
        with patch.object(runner, "custody", fake), \
             patch.object(runner, "fresh_directory"), \
             patch.object(Path, "resolve", return_value=Path("/foreign/hook")), \
             self.assertRaises(GuiRejected):
            runner.retain_launcher_diagnostics(Path("/app"), records)
        fake.regular.assert_not_called()
        fake.new_bytes.assert_not_called()
        records.document.assert_not_called()

    def test_process_identity_change_prevents_context_publication(self):
        owned, fake = Mock(), Mock()
        owned.pid, owned.start = 42, 123
        owned.phase = "accessibility-only"
        owned.records = self.records()
        original = GuiRejected("process identity changed")
        owned.guard.side_effect = [None, original]
        def read(path, limit):
            return b"DISPLAY=:99\0" if path.name == "environ" else b"original"
        with patch.object(runner, "custody", fake), \
             patch.object(runner, "_bounded_proc_read", side_effect=read), \
             patch.object(runner.live, "process_starttime", return_value=123), \
             self.assertRaises(GuiRejected) as caught:
            runner.retain_accessibility_process_context(owned)
        self.assertIs(caught.exception, original)
        fake.new_bytes.assert_not_called()
        owned.records.document.assert_not_called()


if __name__ == "__main__":
    unittest.main()
