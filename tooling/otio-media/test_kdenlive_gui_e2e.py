"""Offline contracts for the single-attempt native Kdenlive GUI lane.

No application, display, media producer, network request or native subprocess
is used by these tests. Existing XML/OTIO negative fixtures remain unchanged.
"""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import kdenlive_gui_actions as actions
import kdenlive_gui_e2e as runner


def row(key="app/dialog/field", **updates):
    value = {"key": key, "name": "", "role": "text", "text": "",
             "visible": True, "enabled": True, "editable": True,
             "rect": [100, 20, 100, 20], "focused": False, "pid": 41}
    value.update(updates)
    return value


def duration_rows():
    return [row("app/dialog/label", name="Duration:", role="label",
                editable=False, rect=[0, 20, 70, 20]),
            row(text="00:00:01:00")]


def admitted_edit():
    before = {"observed_bound_editorial_clip_semantics": [
        {"scene_id": name, "original_inpoint_frame": start, "duration_frames": 30}
        for name, start in zip(("scene-a", "scene-b", "scene-c"), (0, 30, 60))]}
    after = copy.deepcopy(before)
    after["observed_bound_editorial_clip_semantics"][2]["duration_frames"] = 15
    comparison = {"bounded_native_end_trims_observed": [
        {"scene_id": "scene-c", "initial_duration_frames": 30,
         "reopened_duration_frames": 15}]}
    preview = {"status": "BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED",
               "nle_receiver_acceptance": "NOT_VERIFIED", "edit_imported": False,
               "human_review_required": True, "publication_approved": False,
               "native_change_proposals": [{"type": "set_scene_duration",
                    "scene_id": "scene-c", "duration": {"num": "1", "den": "2"}}]}
    return before, after, comparison, preview


class ActionJournalTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.guard = Mock()
        self.journal = actions.ActionJournal(self.events.append, self.guard)

    def test_intent_precedes_one_effect_and_return_is_not_semantic_pass(self):
        effect = Mock(side_effect=lambda: self.assertEqual(self.events[-1]["state"], "INTENT"))
        self.journal.perform("save", {"pid": 41}, effect)
        effect.assert_called_once()
        self.assertEqual([e["state"] for e in self.events], ["INTENT", "REQUEST_RETURNED"])
        self.assertFalse(self.events[-1]["semantic_effect_verified_here"])

    def test_same_action_cannot_be_replayed(self):
        effect = Mock(return_value=True)
        self.journal.perform("save", {}, effect)
        with self.assertRaises(actions.GuiRejected):
            self.journal.perform("save", {}, effect)
        effect.assert_called_once()

    def test_rejected_action_blocks_next_effect(self):
        first, following = Mock(return_value=False), Mock()
        with self.assertRaises(actions.GuiUncertain):
            self.journal.perform("save", {}, first)
        with self.assertRaises(actions.GuiRejected):
            self.journal.perform("quit", {}, following)
        first.assert_called_once()
        following.assert_not_called()
        self.assertEqual(self.events[-1]["state"], "ERROR_OR_UNKNOWN")

    def test_effect_exception_retains_original_error_without_retry(self):
        effect = Mock(side_effect=RuntimeError("original-native-unknown"))
        with self.assertRaises(actions.GuiUncertain):
            self.journal.perform("export", {}, effect)
        self.assertEqual(self.events[-1]["error"], "original-native-unknown")
        self.assertFalse(self.events[-1]["replayed"])
        effect.assert_called_once()

    def test_changed_pid_after_action_blocks_following_effect(self):
        self.guard.side_effect = [None, ValueError("PID changed")]
        with self.assertRaises(actions.GuiUncertain):
            self.journal.perform("edit", {}, Mock(return_value=True))
        self.assertTrue(self.journal.blocked)
        self.assertEqual(self.events[-1]["error"], "PID changed")

    def test_quit_defers_terminal_to_owned_process_observer(self):
        self.journal.perform("quit", {}, Mock(return_value=True), guard_after=False)
        self.guard.assert_called_once()

    def test_read_only_wait_can_observe_later_without_repeating_effect(self):
        query = Mock(side_effect=[None, {"ready": True}])
        with patch.object(actions.time, "sleep"):
            result = self.journal.wait("ready", query)
        self.assertEqual(result, {"ready": True})
        self.assertEqual(self.events, [])

    def test_timeout_blocks_all_further_effects(self):
        effect = Mock()
        with patch.object(actions.time, "monotonic", side_effect=[0.0, 2.0]):
            with self.assertRaises(actions.GuiUncertain):
                self.journal.wait("exported", lambda: None, timeout=1)
        with self.assertRaises(actions.GuiRejected):
            self.journal.perform("retry", {}, effect)
        effect.assert_not_called()
        self.assertEqual(self.events[-1]["state"], "TIMED_OUT_UNKNOWN")

    def test_observation_exception_blocks_following_effect(self):
        with self.assertRaisesRegex(ValueError, "original-read-error"):
            self.journal.wait("state", Mock(side_effect=ValueError("original-read-error")))
        self.assertTrue(self.journal.blocked)
        self.assertEqual(self.events[-1]["state"], "OBSERVATION_ERROR_OR_UNKNOWN")

    def test_initial_guard_failure_never_dispatches(self):
        self.guard.side_effect = ValueError("wrong PID")
        effect = Mock()
        with self.assertRaises(ValueError):
            self.journal.perform("open", {}, effect)
        effect.assert_not_called()
        self.assertEqual(self.events, [])


class NativeControlTests(unittest.TestCase):
    def session(self):
        instance = actions.AtspiSession.__new__(actions.AtspiSession)
        instance.pid = 41
        instance.journal = actions.ActionJournal(lambda _: None, lambda: None)
        return instance

    def test_exact_duration_and_timecode_clock(self):
        actions.check_duration_fields("00:00:02:00", "00:00:02:00", "00:00:01:00")
        self.assertEqual(actions.timecode_frames("00:00:02:15"), 75)

    def test_wrong_clip_or_malformed_timecode_refused(self):
        for value in ("00:00:00:30", "00:00:60:00", "00:60:00:00",
                      "0:00:00:00", "00:00:00:00\n", "", True):
            with self.subTest(value=value), self.assertRaises(actions.GuiRejected):
                actions.timecode_frames(value)
        with self.assertRaises(actions.GuiRejected):
            actions.check_duration_fields("00:00:02:00", "00:00:01:00", "00:00:01:00")

    def test_real_label_row_selects_unique_editable_leaf(self):
        rows = duration_rows()
        rows.append(row("app/dialog/field/line", text="00:00:01:00"))
        self.assertEqual(actions.labeled_field(rows, ("Duration:",))["key"],
                         "app/dialog/field/line")

    def test_duplicate_label_or_field_fails_closed(self):
        for duplicate in (duration_rows()[0], row("app/dialog/other")):
            rows = duration_rows() + [duplicate]
            with self.subTest(duplicate=duplicate), self.assertRaises(actions.GuiRejected):
                actions.labeled_field(rows, ("Duration:",))

    def test_hidden_disabled_or_other_row_field_not_used(self):
        for updates in ({"visible": False}, {"enabled": False},
                        {"rect": [100, 70, 100, 20]}, {"rect": [10, 20, 20, 20]}):
            rows = duration_rows()
            rows[1].update(updates)
            with self.subTest(updates=updates), self.assertRaises(actions.GuiRejected):
                actions.labeled_field(rows, ("Duration:",))

    def test_accessibility_action_requires_exact_pid(self):
        instance = self.session()
        interface = Mock()
        instance.nodes = {"control": Mock(get_process_id=lambda: 42,
                                           get_action_iface=lambda: interface)}
        with self.assertRaises(actions.GuiRejected):
            instance.action("click", row("control"))
        interface.do_action.assert_not_called()

    def test_accessibility_ambiguous_action_is_not_guessed(self):
        instance = self.session()
        interface = Mock()
        interface.get_n_actions.return_value = 2
        interface.get_action_name.side_effect = ["press", "showmenu"]
        instance.nodes = {"control": Mock(get_process_id=lambda: 41,
                                           get_action_iface=lambda: interface)}
        with self.assertRaises(actions.GuiRejected):
            instance.action("menu", row("control"))
        interface.do_action.assert_not_called()

    def test_keyboard_bound_to_exact_owned_window(self):
        instance = self.session()
        with patch.object(actions.subprocess, "run", side_effect=[
            SimpleNamespace(stdout="123\n"), SimpleNamespace(stdout="41\n"),
            SimpleNamespace(returncode=0), SimpleNamespace(stdout="41\n")]) as execute:
            instance.key("seek", 0xFF0D)
        self.assertEqual(execute.call_args_list[2].args[0],
                         ["xdotool", "key", "--window", "123", "--clearmodifiers",
                          "--delay", "0", "Return"])

    def test_keyboard_foreign_focus_never_sends_key(self):
        instance = self.session()
        with patch.object(actions.subprocess, "run", side_effect=[
            SimpleNamespace(stdout="123\n"), SimpleNamespace(stdout="42\n")]) as execute:
            with self.assertRaises(actions.GuiRejected):
                instance.key("seek", 0xFF0D)
        self.assertEqual(execute.call_count, 2)

    def test_keyboard_uncertain_terminal_prevents_next_key(self):
        instance = self.session()
        with patch.object(actions.subprocess, "run", side_effect=[
            SimpleNamespace(stdout="123\n"), SimpleNamespace(stdout="41\n"),
            subprocess.TimeoutExpired(["xdotool"], 5)]) as execute:
            with self.assertRaises(actions.GuiUncertain):
                instance.key("seek", 0xFF0D)
        self.assertEqual(execute.call_count, 3)
        self.assertTrue(instance.journal.blocked)

    def test_startup_auto_closed_requires_real_main_menu(self):
        instance = self.session()
        instance.tree = Mock(return_value=[row("app/main/file", name="File",
                                             role="menu item", editable=False)])
        instance.action = Mock()
        instance.ready("import")
        instance.action.assert_not_called()

    def test_single_startup_ack_requires_exact_version_and_enabled_ok(self):
        instance = self.session()
        dialog = row("app/wizard", name="Welcome to Kdenlive", role="dialog")
        rows = [dialog,
                row("app/wizard/title", name="Welcome to Kdenlive 25.08.3",
                    role="label", editable=False),
                row("app/wizard/ok", name="OK", role="push button", editable=False)]
        instance.tree = Mock(return_value=rows)
        instance.find = Mock(side_effect=[row(name="File"), None])
        instance.action = Mock()
        instance.ready("import")
        instance.action.assert_called_once()
        self.assertEqual(instance.action.call_args.args[0], "import.startup-acknowledge")

    def test_reopen_must_not_repeat_first_run_bootstrap(self):
        instance = self.session()
        instance.tree = Mock(return_value=[
            row("app/wizard", name="Welcome to Kdenlive", role="dialog")])
        instance.action = Mock()
        with self.assertRaises(actions.GuiRejected):
            instance.ready("reopen")
        instance.action.assert_not_called()

    def test_wrong_startup_version_and_disabled_finish_are_rejected(self):
        for title, enabled in (("Welcome to Kdenlive 26.08", True),
                               ("Welcome to Kdenlive 25.08.3", False)):
            instance = self.session()
            instance.tree = Mock(return_value=[
                row("app/wizard", name="Welcome to Kdenlive", role="dialog"),
                row("app/wizard/title", name=title, role="label", editable=False),
                row("app/wizard/ok", name="OK", role="push button",
                    enabled=enabled, editable=False)])
            instance.action = Mock()
            with self.subTest(title=title, enabled=enabled), self.assertRaises(actions.GuiRejected):
                instance.ready("import")
            instance.action.assert_not_called()


class NativeRouteContracts(unittest.TestCase):
    def test_exact_source_bound_trim_retains_review_requirement(self):
        runner.check_edit(*admitted_edit())

    def test_omitted_added_reordered_or_foreign_clip_refused(self):
        for change in ("omit", "add", "reorder", "foreign"):
            values = list(admitted_edit())
            rows = values[1]["observed_bound_editorial_clip_semantics"]
            if change == "omit":
                rows.pop()
            elif change == "add":
                rows.append(copy.deepcopy(rows[0]))
            elif change == "reorder":
                rows[0], rows[1] = rows[1], rows[0]
            else:
                rows[2]["scene_id"] = "foreign"
            with self.subTest(change=change), self.assertRaises(actions.GuiRejected):
                runner.check_edit(*values)

    def test_changed_inpoint_or_unbounded_duration_refused(self):
        for field, value in (("original_inpoint_frame", 61), ("duration_frames", 14),
                             ("duration_frames", 31)):
            values = list(admitted_edit())
            values[1]["observed_bound_editorial_clip_semantics"][2][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(actions.GuiRejected):
                runner.check_edit(*values)

    def test_independent_comparison_must_report_same_exact_trim(self):
        values = list(admitted_edit())
        values[2]["bounded_native_end_trims_observed"][0]["reopened_duration_frames"] = 14
        with self.assertRaises(actions.GuiRejected):
            runner.check_edit(*values)

    def test_existing_preview_must_propose_exact_original_scene_duration(self):
        for change in ("different-id", "wrong-duration", "extra-command", "different-type"):
            values = list(admitted_edit())
            commands = values[3]["native_change_proposals"]
            if change == "different-id":
                commands[0]["scene_id"] = "foreign"
            elif change == "wrong-duration":
                commands[0]["duration"] = {"num": "2", "den": "1"}
            elif change == "extra-command":
                commands.append(copy.deepcopy(commands[0]))
            else:
                commands[0]["type"] = "delete_scene"
            with self.subTest(change=change), self.assertRaises(actions.GuiRejected):
                runner.check_edit(*values)

    def test_preview_never_implies_acceptance_commit_or_publication(self):
        for key, value in (("status", "VERIFIED"), ("nle_receiver_acceptance", "PASS"),
                           ("edit_imported", True), ("human_review_required", False),
                           ("publication_approved", True)):
            values = list(admitted_edit())
            values[3][key] = value
            with self.subTest(key=key), self.assertRaises(actions.GuiRejected):
                runner.check_edit(*values)

    def test_manual_first_attempt_nonroot_is_required(self):
        environment = {"GITHUB_ACTIONS": "true", "CI": "true", "RUNNER_OS": "Linux",
                       "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
                       "MOTIONWRIGHT_KDENLIVE_GUI_SCOPE": "kdenlive-gui",
                       "MOTIONWRIGHT_APPROVED_KDENLIVE_G2_SOURCE": "1"}
        runner.validate_dispatch(environment, "linux", 1001)
        for key, value in (("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_EVENT_NAME", "pull_request"),
                           ("GITHUB_ACTIONS", "false"), ("CI", "false"),
                           ("RUNNER_OS", "Windows"), ("MOTIONWRIGHT_KDENLIVE_GUI_SCOPE", "source"),
                           ("MOTIONWRIGHT_APPROVED_KDENLIVE_G2_SOURCE", "0")):
            with self.subTest(key=key), self.assertRaises(actions.GuiRejected):
                runner.validate_dispatch({**environment, key: value}, "linux", 1001)
        for platform, uid in (("win32", 1001), ("linux", 0), ("linux", True)):
            with self.subTest(platform=platform, uid=uid), self.assertRaises(actions.GuiRejected):
                runner.validate_dispatch(environment, platform, uid)

    def test_quit_unknown_preserves_terminal_without_kill_or_retry(self):
        owned = runner.NativeProcess.__new__(runner.NativeProcess)
        owned.ui, owned.process, owned.records = Mock(), Mock(), Mock()
        owned.phase, owned.pid, owned.start = "import", 41, 1234
        owned.streams = [io.BytesIO(), io.BytesIO()]
        owned.process.wait.side_effect = subprocess.TimeoutExpired(["kdenlive"], 30)
        with self.assertRaises(subprocess.TimeoutExpired):
            owned.close()
        owned.ui.quit.assert_called_once_with("import.quit")
        owned.process.wait.assert_called_once_with(timeout=30)
        owned.process.kill.assert_not_called()
        owned.process.terminate.assert_not_called()
        self.assertEqual(owned.records.emit.call_args.args[0]["state"], "UNKNOWN_STILL_RUNNING")

    def test_nonzero_native_terminal_is_retained_before_rejection(self):
        owned = runner.NativeProcess.__new__(runner.NativeProcess)
        owned.ui, owned.process, owned.records = Mock(), Mock(), Mock()
        owned.phase, owned.pid, owned.start = "reopen", 42, 1235
        owned.streams = [io.BytesIO(), io.BytesIO()]
        owned.process.wait.return_value = 2
        with self.assertRaises(actions.GuiRejected):
            owned.close()
        self.assertEqual(owned.records.emit.call_args.args[0]["returncode"], 2)
        self.assertTrue(all(s.closed for s in owned.streams))

    def test_error_finalization_observes_owned_running_process_without_effect(self):
        with tempfile.TemporaryDirectory() as temporary:
            records = runner.Recorder(Path(temporary))
            records.document = Mock()
            owned = SimpleNamespace(phase="import", pid=41, start=1234,
                                    process=Mock(), guard=Mock(),
                                    streams=[io.BytesIO(), io.BytesIO()])
            owned.process.poll.return_value = None
            records.owned_processes = [owned]
            result = records.observe_terminals()
            records.close()
        self.assertEqual(result[0]["state"], "STILL_RUNNING")
        self.assertTrue(result[0]["live_identity_still_bound"])
        owned.process.kill.assert_not_called()
        owned.process.terminate.assert_not_called()
        self.assertFalse(result[0]["termination_requested_here"])

    def test_error_finalization_retains_changed_identity_as_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            records = runner.Recorder(Path(temporary))
            records.document = Mock()
            owned = SimpleNamespace(phase="reopen", pid=42, start=1235,
                                    process=Mock(), guard=Mock(side_effect=ValueError("PID reused")),
                                    streams=[io.BytesIO(), io.BytesIO()])
            owned.process.poll.return_value = None
            records.owned_processes = [owned]
            result = records.observe_terminals()
            records.close()
        self.assertEqual(result[0]["state"], "OBSERVATION_ERROR_OR_UNKNOWN")
        self.assertEqual(result[0]["error"], "PID reused")

    def test_recording_failure_does_not_skip_remaining_finalization(self):
        records = Mock()
        records.observe_terminals.side_effect = OSError("original observation write error")
        result = runner.finalize_records(records, "ERROR_OR_UNKNOWN", {}, {})
        self.assertEqual(result, "LOCAL_RECORDING_FAILED")
        records.document.assert_called_once()
        records.close.assert_called_once()


class NativeCustodyContracts(unittest.TestCase):
    def setUp(self):
        self.fake = Mock()
        self.fake.gather_snapshot.return_value = (b"original-archive", True)
        self.fake.encrypt_snapshot.return_value = b"sealed-original"
        self.fake.sha.return_value = "c" * 64
        self.context = {"source_sha": "a" * 40, "semwright_sha": "b" * 40,
                        "run_id": "123", "run_attempt": "1"}
        self.roots = (Path("/tmp/native"), Path("/tmp/capsule"), Path("/tmp/public"))

    def publish(self, status="NATIVE_GUI_BOUNDED_ROUNDTRIP_OBSERVED"):
        with patch.object(runner, "custody", self.fake):
            return runner.seal_and_publish(*self.roots, {}, self.context, status)

    def test_single_shared_seal_only_closed_projection_is_public(self):
        result = self.publish()
        self.fake.gather_snapshot.assert_called_once_with(
            {"owner": self.roots[0], "capsule": self.roots[1]}, preserve_partial=True)
        self.fake.encrypt_snapshot.assert_called_once_with(
            b"original-archive", {}, self.context)
        self.fake.new_bytes.assert_called_once_with(
            self.roots[2] / "original-evidence.mwseal", b"sealed-original")
        self.assertEqual(result["ciphertext_sha256"], "c" * 64)
        self.assertEqual(set(result), {
            "schema", "source", "status", "ciphertext_sha256", "snapshot_collection_complete",
            "snapshot_is_atomic_SQLite_or_whole_process", "other_workflow_selections",
            "native_operation_retried", "human_acceptance", "release_authorized"})
        self.assertFalse(result["human_acceptance"])
        self.assertFalse(result["release_authorized"])

    def test_native_failure_stays_failure_after_successful_custody(self):
        result = self.publish("ERROR_OR_UNKNOWN")
        self.assertEqual(result["status"], "ERROR_OR_UNKNOWN")
        self.assertFalse(result["native_operation_retried"])
        self.assertFalse(result["snapshot_is_atomic_SQLite_or_whole_process"])

    def test_missing_or_changed_raw_data_never_grants_roundtrip_pass(self):
        self.fake.gather_snapshot.return_value = (b"partial-archive", False)
        result = self.publish()
        self.assertEqual(result["status"], "CUSTODY_INCOMPLETE")
        self.assertFalse(result["snapshot_collection_complete"])
        self.fake.encrypt_snapshot.assert_called_once()

    def test_seal_failure_never_retries_or_claims_ciphertext(self):
        self.fake.encrypt_snapshot.side_effect = ValueError("original seal error")
        result = self.publish()
        self.assertEqual(result["status"], "CUSTODY_FAILED")
        self.assertIsNone(result["ciphertext_sha256"])
        self.fake.encrypt_snapshot.assert_called_once()
        self.fake.new_bytes.assert_not_called()

    def test_exclusive_publish_failure_does_not_hash_unpublished_bytes(self):
        self.fake.new_bytes.side_effect = OSError("original partial write")
        result = self.publish()
        self.assertEqual(result["status"], "CUSTODY_FAILED")
        self.assertIsNone(result["ciphertext_sha256"])
        self.fake.new_bytes.assert_called_once()
        self.fake.sha.assert_not_called()

    def test_snapshot_failure_still_retains_closed_failure_projection(self):
        self.fake.gather_snapshot.side_effect = OSError("original read error")
        result = self.publish()
        self.assertEqual(result["status"], "CUSTODY_FAILED")
        self.fake.new_json.assert_called_once()
        self.fake.encrypt_snapshot.assert_not_called()



class NativeSourceBindingTests(unittest.TestCase):
    def source(self, current=b"original", expected=b"original", head=None):
        sha = "a" * 40
        fake = Mock()
        fake.regular.return_value = (current, {})
        fake.sha.return_value = "b" * 64
        outputs = [SimpleNamespace(stdout=(head or sha) + "\n"),
                   SimpleNamespace(stdout=expected)]
        with patch.object(runner, "custody", fake), \
             patch.object(runner, "SOURCE_PATHS", ("tooling/original.py",)), \
             patch.object(runner.subprocess, "run", side_effect=outputs) as command:
            value = runner.source_snapshot(sha)
        return value, command, fake

    def test_exact_current_bytes_are_bound_to_git_blob(self):
        value, command, fake = self.source()
        self.assertEqual(value["files"]["tooling/original.py"],
                         {"bytes": 8, "sha256": "b" * 64})
        self.assertEqual(command.call_count, 2)
        self.assertIn("a" * 40 + ":tooling/original.py", command.call_args.args[0])
        fake.regular.assert_called_once()

    def test_dirty_source_bytes_do_not_inherit_commit_identity(self):
        with self.assertRaisesRegex(actions.GuiRejected, "source_differs_from_exact_git_blob"):
            self.source(current=b"modified")

    def test_changed_checkout_is_rejected_before_file_inventory(self):
        with self.assertRaisesRegex(actions.GuiRejected, "source_checkout_changed"):
            self.source(head="c" * 40)

    def test_git_read_failure_cannot_be_promoted_to_source_pass(self):
        fake = Mock(side_effect=subprocess.CalledProcessError(1, ["git"]))
        with patch.object(runner.subprocess, "run", fake):
            with self.assertRaises(subprocess.CalledProcessError):
                runner.source_snapshot("a" * 40)
        fake.assert_called_once()


class DistributionExtractionContracts(unittest.TestCase):
    def extract(self, terminal=0, *, unknown=False, preexisting=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "original"
            raw.mkdir()
            records = runner.Recorder(raw)
            appdir = root / "extract" / "squashfs-root"
            if preexisting:
                appdir.mkdir(parents=True)
            process = Mock(pid=411)
            process.poll.return_value = 0
            def wait(**_):
                if unknown:
                    raise subprocess.TimeoutExpired(["official.AppImage"], 180)
                if terminal == 0:
                    appdir.mkdir()
                return terminal
            process.wait.side_effect = wait
            with patch.object(runner.subprocess, "Popen", return_value=process) as start:
                error = None
                try:
                    runner.extract_distribution(root / "official.AppImage", appdir, records)
                except Exception as observed:
                    error = observed
                records.close()
                for owned in records.owned_processes:
                    for stream in owned.streams:
                        stream.close()
                events = [json.loads(line) for line in records.events.read_text().splitlines()]
            return error, events, start, process, len(records.owned_processes)

    def test_distribution_unpack_is_attempted_once_and_terminal_is_original(self):
        error, events, start, process, owned = self.extract()
        self.assertIsNone(error)
        start.assert_called_once()
        self.assertEqual(start.call_args.args[0][-1], "--appimage-extract")
        self.assertEqual(owned, 1)
        self.assertEqual(events[-1]["state"], "TERMINAL_OBSERVED")
        self.assertEqual(events[-1]["returncode"], 0)
        process.wait.assert_called_once_with(timeout=180)

    def test_failed_unpack_preserves_nonzero_and_cannot_be_gui_success(self):
        error, events, start, process, _ = self.extract(terminal=2)
        self.assertIsInstance(error, actions.GuiRejected)
        self.assertEqual(events[-1]["returncode"], 2)
        start.assert_called_once()
        process.kill.assert_not_called()

    def test_unpack_timeout_never_kills_or_restarts_native_child(self):
        error, events, start, process, _ = self.extract(unknown=True)
        self.assertIsInstance(error, subprocess.TimeoutExpired)
        self.assertEqual(events[-1]["state"], "UNKNOWN_STILL_RUNNING")
        self.assertFalse(events[-1]["replayed"])
        start.assert_called_once()
        process.kill.assert_not_called()
        process.terminate.assert_not_called()

    def test_existing_extracted_tree_is_never_adopted_or_overwritten(self):
        error, _, start, _, owned = self.extract(preexisting=True)
        self.assertIsInstance(error, actions.GuiRejected)
        start.assert_not_called()
        self.assertEqual(owned, 0)

    def test_original_distribution_logs_join_same_single_shared_snapshot(self):
        fake = Mock()
        fake.gather_snapshot.return_value = (b"originals", True)
        fake.encrypt_snapshot.return_value = b"sealed"
        fake.sha.return_value = "a" * 64
        with patch.object(runner, "custody", fake):
            runner.seal_and_publish(Path("/tmp/native"), Path("/tmp/capsule"),
                                    Path("/tmp/public"), {}, {}, "ERROR_OR_UNKNOWN",
                                    Path("/tmp/distribution"))
        fake.gather_snapshot.assert_called_once_with(
            {"owner": Path("/tmp/native"), "capsule": Path("/tmp/capsule"),
             "distribution": Path("/tmp/distribution")}, preserve_partial=True)
        fake.encrypt_snapshot.assert_called_once()


class FirstpartyOriginalRetentionContracts(unittest.TestCase):
    def test_default_workspace_keeps_existing_cleanup_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            for failure in (False, True):
                original = None
                try:
                    with runner.fixture.firstparty_workspace(temp) as original:
                        (original / "partial.bin").write_bytes(b"original")
                        if failure:
                            raise ValueError("original producer error")
                except ValueError:
                    pass
                self.assertIsNotNone(original)
                self.assertFalse(original.exists())

    def test_selected_workspace_survives_success_and_original_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            for name, failure in (("success", False), ("failure", True)):
                original = temp / name
                try:
                    with runner.fixture.firstparty_workspace(temp, original) as retained:
                        self.assertEqual(retained, original)
                        (retained / "partial.bin").write_bytes(b"original")
                        if failure:
                            raise ValueError("original producer error")
                except ValueError:
                    pass
                self.assertEqual((original / "partial.bin").read_bytes(), b"original")

    def test_existing_outside_or_runner_root_workspace_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            existing = temp / "existing"
            existing.mkdir()
            marker = existing / "original.bin"
            marker.write_bytes(b"do not overwrite")
            for candidate in (existing, temp, temp.parent / "outside-otio-source"):
                with self.subTest(candidate=candidate):
                    with self.assertRaises(runner.fixture.KdenliveCompleteFixtureRefused):
                        with runner.fixture.firstparty_workspace(temp, candidate):
                            self.fail("Unowned or existing workspace was adopted")
            self.assertEqual(marker.read_bytes(), b"do not overwrite")

    def test_noncanonical_parent_workspace_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            (temp / "parent").mkdir()
            candidate = temp / "parent" / ".." / "escaping"
            with self.assertRaises(runner.fixture.KdenliveCompleteFixtureRefused):
                with runner.fixture.firstparty_workspace(temp, candidate):
                    self.fail("Noncanonical namespace was accepted")
            self.assertFalse((temp / "escaping").exists())

    def test_symlink_namespace_is_refused_without_creating_a_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            candidate = temp / "untrusted-link"
            with patch.object(Path, "is_symlink", return_value=True):
                with self.assertRaises(runner.fixture.KdenliveCompleteFixtureRefused):
                    with runner.fixture.firstparty_workspace(temp, candidate):
                        self.fail("Symlink was accepted")
            self.assertFalse(candidate.exists())

    def test_unselected_source_call_keeps_existing_single_invocation(self):
        result = SimpleNamespace(returncode=0, stdout=b"original output", stderr=b"")
        with patch.object(runner.fixture.subprocess, "run", return_value=result) as run:
            value = runner.fixture.run_firstparty_native(["original-exporter"], timeout=90)
        self.assertEqual(value, b"original output")
        run.assert_called_once_with(["original-exporter"], capture_output=True,
                                    check=False, timeout=90, stdin=subprocess.DEVNULL)

    def test_original_intent_precedes_one_call_and_preserves_exact_streams(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / "source"
            def original_call(*_, **__):
                intent = json.loads(prefix.with_suffix(".intent.json").read_bytes())
                self.assertEqual(intent["state"], "INTENT")
                self.assertFalse(prefix.with_suffix(".terminal.json").exists())
                return SimpleNamespace(returncode=0, stdout=b"original\x00stdout",
                                       stderr=b"original stderr\n")
            with patch.object(runner.fixture.subprocess, "run", side_effect=original_call) as run:
                value = runner.fixture.run_firstparty_native(
                    ["original-exporter"], evidence_prefix=prefix)
            self.assertEqual(value, b"original\x00stdout")
            self.assertEqual(prefix.with_suffix(".stdout.bin").read_bytes(), value)
            self.assertEqual(prefix.with_suffix(".stderr.bin").read_bytes(), b"original stderr\n")
            terminal = json.loads(prefix.with_suffix(".terminal.json").read_bytes())
            self.assertEqual(terminal["state"], "TERMINAL_OBSERVED")
            self.assertEqual(terminal["returncode"], 0)
            self.assertFalse(terminal["invocation_retried"])
            run.assert_called_once()

    def test_original_nonzero_terminal_and_streams_survive_rejection(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / "source"
            result = SimpleNamespace(returncode=2, stdout=b"partial", stderr=b"original failure")
            with patch.object(runner.fixture.subprocess, "run", return_value=result) as run:
                with self.assertRaises(runner.fixture.KdenliveCompleteFixtureRefused):
                    runner.fixture.run_firstparty_native(["exporter"], evidence_prefix=prefix)
            terminal = json.loads(prefix.with_suffix(".terminal.json").read_bytes())
            self.assertEqual(terminal["returncode"], 2)
            self.assertEqual(prefix.with_suffix(".stderr.bin").read_bytes(), b"original failure")
            run.assert_called_once()

    def test_oversized_original_output_is_retained_before_contract_rejection(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / "source"
            raw = b"x" * (32 * 1024 + 1)
            result = SimpleNamespace(returncode=0, stdout=b"", stderr=raw)
            with patch.object(runner.fixture.subprocess, "run", return_value=result) as run:
                with self.assertRaises(runner.fixture.KdenliveCompleteFixtureRefused):
                    runner.fixture.run_firstparty_native(["exporter"], evidence_prefix=prefix)
            self.assertEqual(prefix.with_suffix(".stderr.bin").read_bytes(), raw)
            self.assertEqual(json.loads(prefix.with_suffix(".terminal.json").read_bytes())
                             ["returncode"], 0)
            run.assert_called_once()

    def test_timeout_keeps_partial_streams_unknown_terminal_and_original_exception(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / "source"
            original = subprocess.TimeoutExpired(["exporter"], 90,
                                                  output=b"partial\x00", stderr=b"warning")
            with patch.object(runner.fixture.subprocess, "run", side_effect=original) as run:
                with self.assertRaises(subprocess.TimeoutExpired) as raised:
                    runner.fixture.run_firstparty_native(["exporter"], timeout=90,
                                                          evidence_prefix=prefix)
            self.assertIs(raised.exception, original)
            terminal = json.loads(prefix.with_suffix(".terminal.json").read_bytes())
            self.assertEqual(terminal["state"], "ERROR_OR_UNKNOWN")
            self.assertIsNone(terminal["returncode"])
            self.assertEqual(terminal["timeout_policy"], "existing_subprocess_run")
            self.assertEqual(prefix.with_suffix(".stdout.bin").read_bytes(), b"partial\x00")
            self.assertEqual(prefix.with_suffix(".stderr.bin").read_bytes(), b"warning")
            run.assert_called_once()

    def test_launch_error_never_invents_empty_captured_streams_or_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / "source"
            with patch.object(runner.fixture.subprocess, "run",
                              side_effect=OSError("original launch error")) as run:
                with self.assertRaisesRegex(OSError, "original launch error"):
                    runner.fixture.run_firstparty_native(["exporter"], evidence_prefix=prefix)
            terminal = json.loads(prefix.with_suffix(".terminal.json").read_bytes())
            self.assertEqual(terminal["state"], "ERROR_OR_UNKNOWN")
            self.assertFalse(terminal["captured_streams"]["stdout"]["captured"])
            self.assertIsNone(terminal["captured_streams"]["stdout"]["sha256"])
            self.assertFalse(prefix.with_suffix(".stdout.bin").exists())
            self.assertFalse(prefix.with_suffix(".stderr.bin").exists())
            run.assert_called_once()

    def test_existing_original_evidence_is_never_overwritten_or_replayed(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / "source"
            intent = prefix.with_suffix(".intent.json")
            intent.write_bytes(b"original previous intent")
            with patch.object(runner.fixture.subprocess, "run") as run:
                with self.assertRaises(runner.fixture.KdenliveCompleteFixtureRefused):
                    runner.fixture.run_firstparty_native(["exporter"], evidence_prefix=prefix)
            self.assertEqual(intent.read_bytes(), b"original previous intent")
            run.assert_not_called()

    def test_failed_intent_write_prevents_the_native_invocation(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / "source"
            with patch.object(runner.fixture, "_new_original",
                              side_effect=OSError("intent storage failure")), \
                 patch.object(runner.fixture.subprocess, "run") as run:
                with self.assertRaisesRegex(OSError, "intent storage failure"):
                    runner.fixture.run_firstparty_native(["exporter"], evidence_prefix=prefix)
            run.assert_not_called()

    def test_retention_failure_preserves_original_exception_without_second_call(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / "source"
            original = OSError("original launch error")
            with patch.object(runner.fixture, "_new_original",
                              side_effect=[None, OSError("terminal storage failure")]), \
                 patch.object(runner.fixture.subprocess, "run", side_effect=original) as run:
                with self.assertRaises(OSError) as raised:
                    runner.fixture.run_firstparty_native(["exporter"], evidence_prefix=prefix)
            self.assertIs(raised.exception, original)
            self.assertTrue(any("terminal storage failure" in note
                                for note in raised.exception.__notes__))
            run.assert_called_once()

    def generated_reply(self, media_sha):
        asset = "asset-source"
        return {"schema": "motionwright.real-otio-fixture/1", "owner_media_asset_id": asset,
                "project": {"scenes": [
                    {"id": "original-scene-" + str(n), "duration": {"num": "1", "den": "1"}}
                    for n in range(3)],
                    "assets": [{"id": asset, "media_type": "video/mp4",
                                "content_sha256": media_sha}]},
                "original_otio": {"OTIO_SCHEMA": "Timeline.1", "name": "original"}}

    def test_invalid_rust_reply_preserves_source_video_and_raw_reply_before_decode(self):
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            retained = temp / "source-originals"
            def call(command, **_):
                if command[0] == "ffmpeg":
                    Path(command[-1]).write_bytes(b"original simulated video")
                    return SimpleNamespace(returncode=0, stdout=b"ffmpeg-original", stderr=b"")
                return SimpleNamespace(returncode=0, stdout=b"{invalid-json", stderr=b"rust-warning")
            with patch.object(runner.fixture, "require_ephemeral_approved_source", return_value=temp), \
                 patch.object(runner.fixture, "file_probe",
                              return_value={"fps": 30, "frames": 90, "codec": "h264"}), \
                 patch.object(runner.fixture.subprocess, "run", side_effect=call) as run:
                with self.assertRaises(json.JSONDecodeError):
                    runner.fixture.prepare_original(temp / "capsule", evidence_root=retained)
            self.assertEqual(run.call_count, 2)
            self.assertEqual((retained / "original.mp4").read_bytes(), b"original simulated video")
            self.assertEqual((retained / "rust-exporter.stdout.bin").read_bytes(), b"{invalid-json")
            self.assertEqual((retained / "rust-exporter.stderr.bin").read_bytes(), b"rust-warning")

    def test_bridge_failure_preserves_original_project_otio_and_media_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            retained = temp / "source-originals"
            def call(command, **_):
                if command[0] == "ffmpeg":
                    Path(command[-1]).write_bytes(b"original simulated video")
                    return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
                return SimpleNamespace(returncode=0,
                    stdout=json.dumps(self.generated_reply(command[1])).encode(), stderr=b"")
            with patch.object(runner.fixture, "require_ephemeral_approved_source", return_value=temp), \
                 patch.object(runner.fixture, "file_probe",
                              return_value={"fps": 30, "frames": 90, "codec": "h264"}), \
                 patch.object(runner.fixture.subprocess, "run", side_effect=call) as run, \
                 patch.object(runner.fixture, "export_portable",
                              side_effect=ValueError("original bridge error")) as bridge:
                with self.assertRaisesRegex(ValueError, "original bridge error"):
                    runner.fixture.prepare_original(temp / "capsule", evidence_root=retained)
            self.assertEqual(run.call_count, 2)
            bridge.assert_called_once()
            self.assertEqual(json.loads((retained / "original.otio").read_bytes())["name"], "original")
            self.assertEqual(len(json.loads((retained / "project.json").read_bytes())["scenes"]), 3)
            self.assertTrue((retained / "original.mp4").is_file())
            self.assertEqual(json.loads((retained / "rust-exporter.terminal.json").read_bytes())
                             ["returncode"], 0)

    def test_existing_source_authorization_failure_never_creates_retained_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            retained = temp / "source-originals"
            with patch.object(runner.fixture, "require_ephemeral_approved_source",
                              side_effect=ValueError("original source not authorized")), \
                 patch.object(runner.fixture.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "original source not authorized"):
                    runner.fixture.prepare_original(temp / "capsule", evidence_root=retained)
            self.assertFalse(retained.exists())
            run.assert_not_called()

if __name__ == "__main__":
    unittest.main()
