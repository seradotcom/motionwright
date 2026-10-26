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
        cases = (
            ("action-rejected", None, False),
            ("intent-before-write", "INTENT", False),
            ("intent-after-write", "INTENT", True),
            ("return-before-write", "REQUEST_RETURNED", False),
            ("return-after-write", "REQUEST_RETURNED", True),
        )
        for label, failed_state, partial_write in cases:
            with self.subTest(case=label):
                events, attempts = [], []
                storage_error = OSError("original journal storage failure")
                def emit(event):
                    attempts.append(event)
                    if event["state"] == failed_state:
                        if partial_write:
                            events.append(event)
                        raise storage_error
                    events.append(event)
                journal = actions.ActionJournal(emit, Mock())
                first = Mock(return_value=failed_state is not None)
                following = Mock()
                expected = actions.GuiUncertain if failed_state is None else OSError
                with self.assertRaises(expected) as caught:
                    journal.perform("save", {}, first)
                self.assertTrue(journal.blocked)
                with self.assertRaises(actions.GuiRejected):
                    journal.perform("quit", {}, following)
                following.assert_not_called()
                if failed_state == "INTENT":
                    first.assert_not_called()
                    self.assertEqual([event["state"] for event in attempts], ["INTENT"])
                else:
                    first.assert_called_once()
                    self.assertEqual([event["state"] for event in attempts], [
                        "INTENT", failed_state or "ERROR_OR_UNKNOWN"])
                if failed_state is None:
                    self.assertIsInstance(caught.exception.__cause__, actions.GuiRejected)
                    self.assertEqual(events[-1]["state"], "ERROR_OR_UNKNOWN")
                else:
                    self.assertIs(caught.exception, storage_error)
                    if failed_state == "REQUEST_RETURNED":
                        self.assertFalse(attempts[-1]["semantic_effect_verified_here"])

    def test_effect_exception_retains_original_error_without_retry(self):
        for error_type in (RuntimeError, KeyboardInterrupt):
            for failed_record in (False, True):
                with self.subTest(error_type=error_type.__name__,
                                  failed_record=failed_record):
                    events, attempts = [], []
                    original = error_type("original-native-unknown")
                    def emit(event):
                        attempts.append(event)
                        if failed_record and event["state"] == "ERROR_OR_UNKNOWN":
                            raise OSError("unknown terminal storage failure")
                        events.append(event)
                    journal = actions.ActionJournal(emit, Mock())
                    effect = Mock(side_effect=original)
                    expected = (actions.GuiUncertain
                                if isinstance(original, Exception) else error_type)
                    with self.assertRaises(expected) as caught:
                        journal.perform("export", {}, effect)
                    if isinstance(original, Exception):
                        self.assertIs(caught.exception.__cause__, original)
                    else:
                        self.assertIs(caught.exception, original)
                    self.assertEqual(attempts[-1]["state"], "ERROR_OR_UNKNOWN")
                    self.assertEqual(attempts[-1]["error"], "original-native-unknown")
                    self.assertFalse(attempts[-1]["replayed"])
                    if failed_record:
                        self.assertIn("native_gui_journal_recording_failed:OSError",
                                      original.__notes__)
                        self.assertEqual([e["state"] for e in events], ["INTENT"])
                    else:
                        self.assertEqual(events[-1]["error"], "original-native-unknown")
                    self.assertTrue(journal.blocked)
                    effect.assert_called_once()
                    following = Mock()
                    with self.assertRaises(actions.GuiRejected):
                        journal.perform("following", {}, following)
                    following.assert_not_called()

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
        with self.subTest(case="later-read-only-observation"):
            query = Mock(side_effect=[None, {"ready": True}])
            with patch.object(actions.time, "sleep"):
                result = self.journal.wait("ready", query)
            self.assertEqual(result, {"ready": True})
            self.assertEqual(query.call_count, 2)
            self.assertEqual(self.events, [])
            self.assertFalse(self.journal.blocked)
        with self.subTest(case="truthy-result-before-deadline"):
            now = [0.0]
            original = {"ready": True}
            def observe():
                now[0] = 0.999
                return original
            query = Mock(side_effect=observe)
            journal = actions.ActionJournal(self.events.append, Mock())
            with patch.object(actions.time, "monotonic", side_effect=lambda: now[0]), \
                 patch.object(actions.time, "sleep") as sleep:
                result = journal.wait("ready", query, timeout=1)
            self.assertIs(result, original)
            query.assert_called_once()
            sleep.assert_not_called()
            self.assertFalse(journal.blocked)
            self.assertEqual(self.events, [])

    def test_timeout_blocks_all_further_effects(self):
        cases = (
            ("guard-exhausted-deadline", 1.0, None, True, False, 0),
            ("late-false-query", None, 1.1, False, False, 1),
            ("truthy-query-at-deadline", None, 1.0, True, False, 1),
            ("truthy-query-after-deadline", None, 1.1, True, False, 1),
            ("sleep-exhausted-deadline", None, None, False, True, 1),
        )
        for label, guard_time, query_time, ready, expires_in_sleep, expected_queries in cases:
            for failed_record in (False, True):
                with self.subTest(case=label, failed_record=failed_record):
                    now, attempts = [0.0], []
                    def guard():
                        if guard_time is not None:
                            now[0] = guard_time
                    def observe():
                        if query_time is not None:
                            now[0] = query_time
                        return {"ready": True} if ready else None
                    def sleep(_):
                        self.assertTrue(expires_in_sleep)
                        now[0] = 1.0
                    def emit(event):
                        attempts.append(event)
                        if failed_record:
                            raise OSError("timeout journal storage failure")
                    query = Mock(side_effect=observe)
                    guarded = Mock(side_effect=guard)
                    journal = actions.ActionJournal(emit, guarded)
                    with patch.object(actions.time, "monotonic", side_effect=lambda: now[0]), \
                         patch.object(actions.time, "sleep", side_effect=sleep) as slept, \
                         self.assertRaises(actions.GuiUncertain) as caught:
                        journal.wait("exported", query, timeout=1)
                    self.assertEqual(str(caught.exception), "exported")
                    self.assertTrue(journal.blocked)
                    self.assertEqual(query.call_count, expected_queries)
                    guarded.assert_called_once()
                    self.assertEqual(slept.call_count, int(expires_in_sleep))
                    self.assertEqual([event["state"] for event in attempts],
                                     ["TIMED_OUT_UNKNOWN"])
                    self.assertFalse(attempts[0]["replayed"])
                    if failed_record:
                        self.assertIn("native_gui_journal_recording_failed:OSError",
                                      caught.exception.__notes__)
                    following = Mock()
                    with self.assertRaises(actions.GuiRejected):
                        journal.perform("following", {}, following)
                    following.assert_not_called()

    def test_observation_exception_blocks_following_effect(self):
        for error_type in (ValueError, KeyboardInterrupt):
            for late_error in (False, True):
                for failed_record in (False, True):
                    with self.subTest(error_type=error_type.__name__,
                                      late_error=late_error, failed_record=failed_record):
                        now, attempts = [0.0], []
                        original = error_type("original-read-error")
                        def observe():
                            if late_error:
                                now[0] = 2.0
                            raise original
                        def emit(event):
                            attempts.append(event)
                            if failed_record:
                                raise OSError("observation journal storage failure")
                        query = Mock(side_effect=observe)
                        journal = actions.ActionJournal(emit, Mock())
                        with patch.object(actions.time, "monotonic", side_effect=lambda: now[0]), \
                             self.assertRaises(error_type) as caught:
                            journal.wait("state", query, timeout=1)
                        self.assertIs(caught.exception, original)
                        self.assertTrue(journal.blocked)
                        self.assertEqual([event["state"] for event in attempts],
                                         ["OBSERVATION_ERROR_OR_UNKNOWN"])
                        self.assertEqual(attempts[0]["error"], "original-read-error")
                        self.assertFalse(attempts[0]["replayed"])
                        if failed_record:
                            self.assertIn("native_gui_journal_recording_failed:OSError",
                                          original.__notes__)
                        query.assert_called_once()
                        following = Mock()
                        with self.assertRaises(actions.GuiRejected):
                            journal.perform("following", {}, following)
                        following.assert_not_called()

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



def verified_snapshot_fixture(context):
    """Synthetic bytes only; this fixture makes no physical Kdenlive claim."""
    import hashlib
    import tarfile
    members = {
        "owner/initial.kdenlive": b"synthetic initial project\n",
        "owner/edited.kdenlive": b"synthetic edited project\n",
        "capsule/timeline.otio": b"synthetic original timeline\n",
        "owner/received.otio": b"synthetic received timeline\n",
        "owner/edited.otio": b"synthetic edited timeline\n",
        "owner/reopened-edited.otio": b"synthetic reopened timeline\n",
        "owner/owner-cut-commit-receipt.json": b'{"synthetic_commit_only":true}\n',
        "owner/owner-cut-reopen-readback.json": b'{"synthetic_readback_only":true}\n',
    }
    fields = {
        "initial_project_sha256": "owner/initial.kdenlive",
        "edited_project_sha256": "owner/edited.kdenlive",
        "original_otio_sha256": "capsule/timeline.otio",
        "received_otio_sha256": "owner/received.otio",
        "edited_otio_sha256": "owner/edited.otio",
        "reopened_edited_otio_sha256": "owner/reopened-edited.otio",
    }
    result = {key: hashlib.sha256(members[name]).hexdigest()
              for key, name in fields.items()}
    result["owner_cut_completion"] = {
        "sqlite_owner_cut_committed": True,
        "owner_cut_commit_receipt_sha256":
            hashlib.sha256(members["owner/owner-cut-commit-receipt.json"]).hexdigest(),
        "owner_cut_reopen_receipt_sha256":
            hashlib.sha256(members["owner/owner-cut-reopen-readback.json"]).hexdigest(),
        "human_acceptance": False, "release_authorized": False,
    }
    # Closed synthetic files exercise the actual TAR binding, without any
    # assertion that a native application or SQLite import ran in this test.
    members.update({
        "owner/native-source-attach-receipt.json": b'{"synthetic_attach_only":true}\n',
        "owner/native-source-reopen.json": b'{"synthetic_source_reopen_only":true}\n',
        "owner/native-source-bundle-receipt.json": b'{"synthetic_bundle_only":true}\n',
        "owner/native-source-portable-reopen.json": b'{"synthetic_portable_reopen_only":true}\n',
        "owner/native-source-portable/manifest.json": b'{"synthetic_manifest_only":true}\n',
        "owner/capsule-relocation.original.json": b'{"synthetic_relocation_only":true}\n',
        "capsule/bridge-manifest.json": b'{"synthetic_bridge_only":true}\n',
        "capsule/source-project.json": b'{"synthetic_project_only":true}\n',
        "capsule/firstparty-complete-native-G2-input.json": b'{"synthetic_fixture_only":true}\n',
        "capsule/media/6ae70684422274dbc39d90e7e3a045c096a9df3692604c02a351e39fc094f2f1.mp4": b"synthetic media bytes; no codec claim\n",
    })
    cycle_fields = {
        "native_source_import_receipt_sha256": "owner/native-source-attach-receipt.json",
        "native_source_readback_sha256": "owner/native-source-reopen.json",
        "native_source_bundle_receipt_sha256": "owner/native-source-bundle-receipt.json",
        "native_source_portable_readback_sha256": "owner/native-source-portable-reopen.json",
        "bundle_manifest_sha256": "owner/native-source-portable/manifest.json",
    }
    result["native_source_cycle"] = {
        **{key: hashlib.sha256(members[name]).hexdigest()
           for key, name in cycle_fields.items()},
        "native_source_import_committed": True, "source_readback_in_new_process": True,
        "source_blob_reopened_equal": True, "portable_readback_in_new_process": True,
        "fresh_generation_observed": True, "source_fidelity_unavailable": True,
        "native_source_sha256": result["edited_project_sha256"],
        "additional_source_revision_increment": 1, "production_receipt_count": 0,
        "source_generation": "synthetic-original-generation",
        "portable_generation": "synthetic-fresh-generation",
        "bundle_inspection_fingerprint": "f" * 64,
        "local_production_receipts_inherited": False, "execution_authority_issued": False,
        "per_scene_native_semantic_mapping_verified": False,
        "native_editor_dispatched_here": False, "alab_sources_adopted_here": False,
        "automatic_retry": False, "human_acceptance": False, "release_authorized": False,
    }
    result["capsule_relocation"] = {
        "schema": runner.CAPSULE_RELOCATION_SCHEMA,
        "mode": "relative_reference_resolution_after_physical_relocation",
        "receipt_sha256": hashlib.sha256(
            members["owner/capsule-relocation.original.json"]).hexdigest(),
        "members": {
            name.removeprefix("capsule/"): {"bytes": len(raw),
                                          "sha256": hashlib.sha256(raw).hexdigest()}
            for name, raw in members.items() if name.startswith("capsule/")
        },
        "original_location_absent": True, "original_bytes_preserved": True,
        "relative_media_members_preserved": True, "ui_relink_dialog_used": False,
        "native_application_launched_here": False, "source_origin_inferred": False,
        "alab_sources_adopted_here": False, "automatic_retry": False,
        "human_acceptance": False, "release_authorized": False,
    }
    record = {"status": "NATIVE_GUI_BOUNDED_ROUNDTRIP_OBSERVED",
              "source": context, "result": result,
              "recording_errors_before_result": [], "native_operation_retried": False,
              "human_acceptance": False, "release_authorized": False}
    members["owner/original-result.json"] = (
        json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, raw in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
    return result, members, stream.getvalue()


class NativeCustodyContracts(unittest.TestCase):
    def setUp(self):
        self.fake = Mock()
        self.fake.encrypt_snapshot.return_value = b"sealed-original"
        self.fake.sha.return_value = "c" * 64
        self.context = {"source_sha": "a" * 40, "semwright_sha": "b" * 40,
                        "run_id": "123", "run_attempt": "1"}
        self.roots = (Path("/tmp/native"), Path("/tmp/capsule"), Path("/tmp/public"))
        self.result, _, self.snapshot = verified_snapshot_fixture(self.context)
        self.fake.gather_snapshot.return_value = (self.snapshot, True)
        self.fake.MAX_FILES = 4096
        self.fake.json_bytes.side_effect = lambda value: (
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
        ).encode()

    def publish(self, status="NATIVE_GUI_BOUNDED_ROUNDTRIP_OBSERVED"):
        with patch.object(runner, "custody", self.fake):
            return runner.seal_and_publish(*self.roots, {}, self.context, status,
                                           result=self.result)

    def test_single_shared_seal_only_closed_projection_is_public(self):
        result = self.publish()
        self.fake.gather_snapshot.assert_called_once_with(
            {"owner": self.roots[0], "capsule": self.roots[1]}, preserve_partial=True)
        self.fake.encrypt_snapshot.assert_called_once_with(
            self.snapshot, {}, self.context)
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
        cases = (
            ("launch-error", ("terminal",)),
            ("timeout", ("terminal", "stdout")),
            ("nonzero", ("terminal", "stdout")),
            ("stdout-budget", ("terminal", "stdout")),
            ("stderr-budget", ("terminal", "stdout")),
            ("successful-terminal", ("terminal", "stdout")),
        )
        for label, failed_members in cases:
            for failed_member in failed_members:
                with self.subTest(case=label, failed_member=failed_member), \
                     tempfile.TemporaryDirectory() as temporary:
                    prefix = Path(temporary) / "source"
                    original = None
                    if label == "launch-error":
                        original = OSError("original launch error")
                    elif label == "timeout":
                        original = subprocess.TimeoutExpired(
                            ["exporter"], 90, output=b"partial\x00", stderr=b"original warning")
                    result = SimpleNamespace(
                        returncode=2 if label == "nonzero" else 0,
                        stdout=(b"x" * (runner.fixture.MAX_RUST_STDOUT + 1)
                                if label == "stdout-budget" else b"original stdout"),
                        stderr=(b"x" * (32 * 1024 + 1)
                                if label == "stderr-budget" else b"original stderr"))
                    storage_error = OSError(failed_member + " storage failure")
                    writes = []
                    def retain(path, raw):
                        writes.append((path, raw))
                        suffix = ".terminal.json" if failed_member == "terminal" else ".stdout.bin"
                        if path.name.endswith(suffix):
                            raise storage_error
                    run = Mock(side_effect=original) if original is not None else Mock(return_value=result)
                    expected = (type(original) if original is not None
                                else OSError if label == "successful-terminal"
                                else runner.fixture.KdenliveCompleteFixtureRefused)
                    with patch.object(runner.fixture, "_new_original", side_effect=retain), \
                         patch.object(runner.fixture.subprocess, "run", run), \
                         self.assertRaises(expected) as caught:
                        runner.fixture.run_firstparty_native(
                            ["exporter"], timeout=90, evidence_prefix=prefix)
                    if original is not None:
                        self.assertIs(caught.exception, original)
                    elif label == "successful-terminal":
                        self.assertIs(caught.exception, storage_error)
                    else:
                        self.assertEqual(
                            str(caught.exception),
                            "original_Kdenlive_G2_source_generator_or_video_failed")
                    if label != "successful-terminal":
                        self.assertTrue(any(
                            failed_member + " storage failure" in note
                            for note in caught.exception.__notes__))
                    run.assert_called_once_with(
                        ["exporter"], capture_output=True, check=False, timeout=90,
                        stdin=subprocess.DEVNULL)
                    expected_names = ["source.intent.json", "source.terminal.json"]
                    if failed_member == "stdout":
                        expected_names.append("source.stdout.bin")
                    self.assertEqual([path.name for path, _ in writes], expected_names)
                    terminal = json.loads(writes[1][1])
                    self.assertFalse(terminal["invocation_retried"])
                    if original is not None:
                        self.assertEqual(terminal["state"], "ERROR_OR_UNKNOWN")
                        self.assertIsNone(terminal["returncode"])
                    else:
                        self.assertEqual(terminal["state"], "TERMINAL_OBSERVED")
                        self.assertEqual(terminal["returncode"], result.returncode)

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


class EditedProjectReopenContracts(unittest.TestCase):
    """A successful route must retain the saved edit after a separate reopen."""

    def run_mocked_route(self, *, mutate_third=None, final_preview=None,
                         third_close_error=None, changed_edited_bytes=False,
                         changed_readback=False, source_database=None):
        before, after, comparison, preview = admitted_edit()
        final = copy.deepcopy(after)
        if changed_readback:
            final["observed_bound_editorial_clip_semantics"][2]["duration_frames"] = 30
        root = Path("/synthetic/evidence")
        capsule, appdir = Path("/synthetic/capsule"), Path("/synthetic/app")
        binary, profile = appdir / "kdenlive", Path("/synthetic/profile")
        initial, edited = root / "initial.kdenlive", root / "edited.kdenlive"
        self.records = Mock(root=root)
        self.processes = [Mock(), Mock(), Mock()]
        self.receipts = []
        for index, phase in enumerate(("import", "reopen", "reopen")):
            reopen_input = None if index == 0 else initial if index == 1 else edited
            saved = initial if index == 0 else edited
            receipt = {
                "schema": "motionwright.native.kdenlive-live-process/1",
                "phase": phase, "pid": 201 + index,
                "kernel_starttime_ticks": 1001 + index,
                "actual_executable_sha256": "8" * 64,
                "original_Kdenlive_application_version": runner.APPIMAGE_VERSION,
                "source_original_OTIO_sha256": "3" * 64,
                "saved_native_project_path": saved.as_posix(),
                "saved_native_project_sha256": ("1" if index == 0 else "2") * 64,
                "reopen_input_project_path": (
                    None if reopen_input is None else reopen_input.as_posix()),
                "reopen_input_project_sha256": (
                    None if index == 0 else ("1" if index == 1 else "2") * 64),
                "process_arguments": {
                    "saved_project_was_an_exact_launch_argument": index > 0,
                    "native_project_path_checked": (
                        None if reopen_input is None else reopen_input.as_posix())},
                "live_application_binary_and_pid_verified": True,
            }
            self.receipts.append(receipt)
            self.processes[index].witness.return_value = receipt
            self.processes[index].ui.tree.return_value = []
        if mutate_third is not None:
            mutate_third(self.receipts[2])
        if third_close_error is not None:
            self.processes[2].close.side_effect = third_close_error
        hashes = {"initial.kdenlive": "1", "edited.kdenlive": "2",
                  "timeline.otio": "3", "bridge-manifest.json": "4",
                  "received.otio": "5", "edited.otio": "6",
                  "reopened-edited.otio": "7", "kdenlive": "8"}
        def digest(path, _limit):
            if (changed_edited_bytes and path.name == "edited.kdenlive"
                    and self.processes[2].ui.export_otio.called):
                return "9" * 64
            return hashes[path.name] * 64
        with patch.object(runner, "NativeProcess", side_effect=self.processes) as launch, \
             patch.object(runner, "digest", side_effect=digest), \
             patch.object(runner.readback, "inspect_saved_kdenlive",
                          side_effect=[before, after, final]) as inspect, \
             patch.object(runner.readback, "compare_two_native_custody_snapshots",
                          return_value=comparison) as compare, \
             patch.object(runner, "preview_kdenlive_cut", side_effect=[
                 preview, preview if final_preview is None else final_preview]) as cut:
            self.launch, self.inspect, self.compare, self.cut = launch, inspect, compare, cut
            return runner.run_route(appdir, binary, "8" * 64, capsule,
                                    self.records, profile, source_database=source_database)

    def test_saved_edit_is_reopened_exported_and_closed_in_a_third_native_process(self):
        value = self.run_mocked_route()
        self.assertTrue(value.get("three_distinct_native_processes"))
        self.assertTrue(value.get("edited_native_project_reopened"))
        self.assertEqual(value["reopened_edited_otio_sha256"], "7" * 64)
        self.assertEqual(self.launch.call_count, 3)
        self.assertEqual(self.launch.call_args.args[-1], "reopen-edited")
        self.assertEqual(self.launch.call_args.kwargs["reopen_input"],
                         Path("/synthetic/evidence/edited.kdenlive"))
        third = self.processes[2]
        third.ui.edit_third_clip.assert_not_called()
        third.ui.save_as.assert_not_called()
        third.ui.export_otio.assert_called_once_with(
            "reopen-edited.export", Path("/synthetic/evidence/reopened-edited.otio"))
        for process in self.processes:
            process.close.assert_called_once_with()
        self.assertEqual(self.inspect.call_count, 3)
        self.assertEqual(self.compare.call_count, 2)
        self.assertEqual(self.cut.call_count, 2)
        self.assertEqual(self.cut.call_args.args[2]["edited_otio"], "reopened-edited.otio")
        retained = dict(call.args for call in self.records.document.call_args_list)
        self.assertFalse(retained["reopened-edited-cut-preview.json"]["edit_imported"])
        self.assertFalse(
            retained["edited-project-distinct-native-sessions.json"]["human_acceptance"])
        self.assertEqual([item["phase"] for item in self.receipts],
                         ["import", "reopen", "reopen"])

    def test_foreign_or_reused_third_session_cannot_finish_the_route(self):
        def foreign_path(receipt):
            receipt["reopen_input_project_path"] = "/synthetic/foreign.kdenlive"
            receipt["process_arguments"]["native_project_path_checked"] = (
                "/synthetic/foreign.kdenlive")
        mutations = [
            foreign_path,
            lambda receipt: receipt.update(reopen_input_project_sha256="9" * 64),
            lambda receipt: receipt.update(pid=202, kernel_starttime_ticks=1002),
            lambda receipt: receipt.update(kernel_starttime_ticks=1001),
            lambda receipt: receipt.update(actual_executable_sha256="9" * 64),
            lambda receipt: receipt.update(source_original_OTIO_sha256="9" * 64),
            lambda receipt: receipt.update(phase="import"),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                with self.assertRaises(runner.live.NativeKdenliveProcessRejected):
                    self.run_mocked_route(mutate_third=mutation)
                self.assertEqual(self.launch.call_count, 3)

    def test_third_native_export_must_still_contain_the_exact_bounded_trim(self):
        original = admitted_edit()[3]
        lost_trim = copy.deepcopy(original)
        lost_trim["native_change_proposals"] = []
        wrong_scene = copy.deepcopy(original)
        wrong_scene["native_change_proposals"][0]["scene_id"] = "scene-a"
        wrong_duration = copy.deepcopy(original)
        wrong_duration["native_change_proposals"][0]["duration"] = {"num": "1", "den": "1"}
        for preview in (lost_trim, wrong_scene, wrong_duration):
            with self.subTest(preview=preview):
                with self.assertRaises(actions.GuiRejected):
                    self.run_mocked_route(final_preview=preview)
                self.assertEqual(self.launch.call_count, 3)

    def test_unknown_third_terminal_is_not_success_or_an_automatic_retry(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_mocked_route(third_close_error=subprocess.TimeoutExpired(
                "original reopened Kdenlive", 30))
        self.assertEqual(self.launch.call_count, 3)
        self.processes[2].close.assert_called_once_with()

    def test_reopen_rejects_changed_edited_bytes_or_native_readback(self):
        for changes in ({"changed_edited_bytes": True}, {"changed_readback": True}):
            with self.subTest(changes=changes):
                with self.assertRaises(actions.GuiRejected):
                    self.run_mocked_route(**changes)
                self.assertEqual(self.launch.call_count, 3)

    def test_third_process_witness_keeps_reopen_phase_and_unique_record_name(self):
        from contextlib import nullcontext
        native = runner.NativeProcess.__new__(runner.NativeProcess)
        native.phase, native.pid = "reopen-edited", 203
        native.binary, native.binary_sha = Path("/synthetic/kdenlive"), "8" * 64
        native.records = Mock()
        native.native_environment = Mock(return_value=nullcontext())
        witness = {"original_Kdenlive_application_version":
                   runner.live.parse_native_kdenlive_version(
                       f"kdenlive {runner.APPIMAGE_VERSION}".encode("ascii"))}
        args = (Path("/synthetic/timeline.otio"), "3" * 64,
                Path("/synthetic/edited.kdenlive"), "2" * 64)
        with patch.object(runner.live, "inspect_live_native_kdenlive",
                          return_value=witness) as inspect:
            self.assertEqual(native.witness(*args, reopen_input=args[2],
                                            reopen_sha=args[3]), witness)
            self.assertEqual(inspect.call_args.kwargs["phase"], "reopen")
            native.records.document.assert_called_once_with(
                "reopen-edited-live-process.json", witness)
            native.phase = "unknown"
            with self.assertRaisesRegex(actions.GuiRejected, "phase_unrecognized"):
                native.witness(*args)
            inspect.assert_called_once()


    def test_original_owner_cut_then_source_cycle_use_exact_third_saved_native_bytes(self):
        database = Path("/synthetic/evidence/canonical-source.sqlite3")
        completion = {"sqlite_owner_cut_committed": True}
        cycle = {"native_source_import_committed": True}
        order = []
        def commit(*args):
            self.assertEqual(args[2], database)
            self.assertTrue(all(p.close.called for p in self.processes))
            order.append("owner-cut")
            return completion
        def attach(*args):
            self.assertEqual(order, ["owner-cut"])
            self.assertEqual(args[2], database)
            self.assertEqual(args[3], Path("/synthetic/evidence/edited.kdenlive"))
            self.assertEqual(args[4], "2" * 64)
            self.assertIs(args[5], completion)
            order.append("source-cycle")
            return cycle
        with patch.object(runner.owner_cut, "commit_once", side_effect=commit) as owner, \
             patch.object(runner.owner_cut, "source_cycle_once", side_effect=attach) as source:
            result = self.run_mocked_route(source_database=database)
        self.assertEqual(order, ["owner-cut", "source-cycle"])
        self.assertIs(result["native_source_cycle"], cycle)
        owner.assert_called_once()
        source.assert_called_once()
        self.assertEqual(self.launch.call_count, 3)

    def test_unknown_source_cycle_terminal_never_repeats_owner_cut_or_native_route(self):
        original = TimeoutError("original source import state unknown")
        with patch.object(runner.owner_cut, "commit_once",
                          return_value={"sqlite_owner_cut_committed": True}) as owner, \
             patch.object(runner.owner_cut, "source_cycle_once", side_effect=original) as source:
            with self.assertRaises(TimeoutError) as caught:
                self.run_mocked_route(
                    source_database=Path("/synthetic/evidence/canonical-source.sqlite3"))
        self.assertIs(caught.exception, original)
        owner.assert_called_once()
        source.assert_called_once()
        self.assertEqual(self.launch.call_count, 3)
        for process in self.processes:
            process.close.assert_called_once()


class NativeVersionCompositionContracts(unittest.TestCase):
    """Exercise the real parser and live witness with only external I/O replaced."""

    def witness_for_version_output(self, raw):
        from contextlib import nullcontext
        native = runner.NativeProcess.__new__(runner.NativeProcess)
        native.phase, native.pid = "import", 41
        native.binary, native.binary_sha = Path("/synthetic/kdenlive"), "8" * 64
        native.records = Mock()
        native.native_environment = Mock(return_value=nullcontext())
        kernel_stat = b"41 (kdenlive) S " + b" ".join(
            str(value).encode("ascii") for value in range(4, 26))
        commandline = str(native.binary).encode("utf-8") + b"\0"
        def proc_bytes(path):
            if path.name == "stat":
                return kernel_stat
            if path.name == "cmdline":
                return commandline
            raise AssertionError("Unexpected process read in offline version contract")
        environment = {"CI": "true", "GITHUB_ACTIONS": "true", "RUNNER_OS": "Linux",
                       "MOTIONWRIGHT_APPROVED_KDENLIVE_G2_SOURCE": "1"}
        with patch.object(runner.live.sys, "platform", "linux"), \
             patch.dict(runner.live.os.environ, environment), \
             patch.object(Path, "is_absolute", return_value=True), \
             patch.object(Path, "is_file", return_value=True), \
             patch.object(Path, "is_dir", return_value=True), \
             patch.object(Path, "resolve", lambda path, **kwargs: path), \
             patch.object(Path, "read_bytes", proc_bytes), \
             patch.object(runner.live.os, "readlink", return_value=str(native.binary)), \
             patch.object(runner.live, "read_exact_file",
                          side_effect=lambda path, expected, budget:
                          {"sha256": expected, "bytes": 100}), \
             patch.object(runner.live.subprocess, "run", return_value=SimpleNamespace(
                 returncode=0, stdout=raw, stderr=b"")) as version_command:
            result = native.witness(
                Path("/synthetic/timeline.otio"), "3" * 64,
                Path("/synthetic/initial.kdenlive"), "2" * 64)
        version_command.assert_called_once()
        self.assertEqual(version_command.call_args.args[0],
                         [str(native.binary), "--version"])
        return result

    def test_pinned_distribution_version_crosses_real_parser_and_witness(self):
        result, error = None, None
        try:
            result = self.witness_for_version_output(b"kdenlive 25.08.3\n")
        except Exception as original:
            error = original
        self.assertIsNone(error, "The pinned distribution was rejected: " + repr(error))
        self.assertEqual(result["original_Kdenlive_application_version"], "25.8.3")
        self.assertFalse(result["human_media_rights_or_perceptual_review"])
        self.assertFalse(result["native_save_or_export_UI_action_authenticated"])

    def test_different_distribution_version_still_rejects_after_real_parser(self):
        for raw in (b"kdenlive 25.04.3\n", b"kdenlive 25.08.4\n", b"kdenlive 26.04.0\n"):
            with self.subTest(raw=raw), self.assertRaisesRegex(
                    actions.GuiRejected, "version_not_exact_distribution"):
                self.witness_for_version_output(raw)

    def test_malformed_or_unadmitted_version_still_rejects_before_witness(self):
        for raw in (b"", b"Qt 6.8.3", b"unknown-program 25.08.3",
                    b"kdenlive testing unstable", b"\xff\xfe", b"kdenlive 24.12.9\n"):
            with self.subTest(raw=raw), self.assertRaises(
                    runner.live.NativeKdenliveProcessRejected):
                self.witness_for_version_output(raw)


class NativeVerifiedSnapshotContracts(unittest.TestCase):
    """Real snapshot reads and TAR binding; encryption is replaced, no native run."""

    def run_case(self, *, before=None, during=None, after=None, duplicate=False,
                 result_missing=False):
        import hashlib
        import tarfile
        import ci_private_evidence as custody
        context = {"source_sha": "a" * 40, "semwright_sha": "b" * 40,
                   "run_id": "123", "run_attempt": "1"}
        result, members, _ = verified_snapshot_fixture(context)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for name in ("owner", "capsule", "public"):
                (root / name).mkdir()
            for name, raw in members.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_bytes(raw)
            if before is not None:
                before(root, result)
            gather = custody.gather_snapshot
            def capture(roots, **kwargs):
                if during is not None:
                    during(root, result)
                packed, complete = gather(roots, **kwargs)
                if duplicate:
                    with tarfile.open(fileobj=io.BytesIO(packed), mode="r:") as archive:
                        entries = [(item.name, archive.extractfile(item).read())
                                   for item in archive]
                    entries.append(("owner/reopened-edited.otio",
                                    members["owner/reopened-edited.otio"]))
                    modified = io.BytesIO()
                    with tarfile.open(fileobj=modified, mode="w") as archive:
                        for name, raw in entries:
                            item = tarfile.TarInfo(name)
                            item.size = len(raw)
                            archive.addfile(item, io.BytesIO(raw))
                    packed = modified.getvalue()
                if after is not None:
                    after(root, result)
                return packed, complete
            with patch.object(runner, "custody", custody), \
                 patch.object(custody, "gather_snapshot", side_effect=capture) as collect, \
                 patch.object(custody, "encrypt_snapshot",
                              return_value=b"synthetic encrypted test bytes") as encrypt:
                projection = runner.seal_and_publish(
                    root / "owner", root / "capsule", root / "public", {}, context,
                    "NATIVE_GUI_BOUNDED_ROUNDTRIP_OBSERVED",
                    result=None if result_missing else result)
            collect.assert_called_once()
            encrypt.assert_called_once()
            self.assertTrue((root / "public/original-evidence.mwseal").is_file())
            self.assertFalse(projection["human_acceptance"])
            self.assertFalse(projection["release_authorized"])
            packed = encrypt.call_args.args[0]
            with tarfile.open(fileobj=io.BytesIO(packed), mode="r:") as archive:
                captured = {item.name: archive.extractfile(item).read() for item in archive}
        return projection, captured, members

    @staticmethod
    def replace_artifact(root, result):
        path = root / "owner/reopened-edited.otio"
        original = path.read_bytes()
        changed = b"X" + original[1:]
        assert len(changed) == len(original) and changed != original
        path.write_bytes(changed)

    def test_unchanged_verified_members_preserve_success_and_exact_capture(self):
        projection, captured, members = self.run_case()
        self.assertEqual(projection["status"], "NATIVE_GUI_BOUNDED_ROUNDTRIP_OBSERVED")
        self.assertTrue(projection["snapshot_collection_complete"])
        self.assertEqual({name: captured[name] for name in members}, members)

    def test_same_size_artifact_replacement_cannot_inherit_prior_result(self):
        projection, captured, members = self.run_case(before=self.replace_artifact)
        self.assertEqual(projection["status"], "CUSTODY_FAILED")
        name = "owner/reopened-edited.otio"
        self.assertEqual(len(captured[name]), len(members[name]))
        self.assertNotEqual(captured[name], members[name])

    def test_owner_commit_and_readback_receipt_replacements_are_rejected(self):
        for name in ("owner-cut-commit-receipt.json", "owner-cut-reopen-readback.json"):
            def replace(root, result):
                path = root / "owner" / name
                raw = path.read_bytes()
                path.write_bytes(b"X" + raw[1:])
            with self.subTest(name=name):
                projection, captured, _ = self.run_case(before=replace)
                self.assertEqual(projection["status"], "CUSTODY_FAILED")
                self.assertTrue(captured["owner/" + name].startswith(b"X"))

    def test_coordinated_artifact_and_retained_result_cannot_rebase_authority(self):
        import hashlib
        def replace(root, result):
            self.replace_artifact(root, result)
            path = root / "owner/original-result.json"
            retained = json.loads(path.read_bytes())
            retained["result"]["reopened_edited_otio_sha256"] = hashlib.sha256(
                (root / "owner/reopened-edited.otio").read_bytes()).hexdigest()
            path.write_bytes((json.dumps(retained, sort_keys=True,
                separators=(",", ":"), ensure_ascii=True) + "\n").encode())
        projection, _, _ = self.run_case(before=replace)
        self.assertEqual(projection["status"], "CUSTODY_FAILED")

    def test_late_capture_mutation_cannot_rebase_frozen_result_or_retained_bytes(self):
        import hashlib
        def replace(root, result):
            self.replace_artifact(root, result)
            result["reopened_edited_otio_sha256"] = hashlib.sha256(
                (root / "owner/reopened-edited.otio").read_bytes()).hexdigest()
            path = root / "owner/original-result.json"
            retained = json.loads(path.read_bytes())
            retained["result"] = result
            path.write_bytes((json.dumps(retained, sort_keys=True,
                separators=(",", ":"), ensure_ascii=True) + "\n").encode())
        projection, _, _ = self.run_case(during=replace)
        self.assertEqual(projection["status"], "CUSTODY_FAILED")

    def test_disk_change_after_capture_does_not_replace_the_verified_tar(self):
        projection, captured, members = self.run_case(after=self.replace_artifact)
        self.assertEqual(projection["status"], "NATIVE_GUI_BOUNDED_ROUNDTRIP_OBSERVED")
        self.assertEqual(captured["owner/reopened-edited.otio"],
                         members["owner/reopened-edited.otio"])

    def test_missing_or_duplicate_verified_members_are_rejected(self):
        def remove(root, result):
            (root / "owner/edited.otio").unlink()
        for options in ({"before": remove}, {"duplicate": True}):
            with self.subTest(options=tuple(options)):
                projection, _, _ = self.run_case(**options)
                self.assertEqual(projection["status"], "CUSTODY_FAILED")

    def test_absent_verified_result_cannot_claim_roundtrip_even_with_complete_capture(self):
        projection, _, _ = self.run_case(result_missing=True)
        self.assertEqual(projection["status"], "CUSTODY_FAILED")



    def test_source_cycle_receipts_and_bundle_manifest_cannot_change_under_prior_success(self):
        names = ("native-source-attach-receipt.json", "native-source-reopen.json",
                 "native-source-bundle-receipt.json", "native-source-portable-reopen.json",
                 "native-source-portable/manifest.json")
        for name in names:
            def replace(root, result):
                path = root / "owner" / name
                raw = path.read_bytes()
                path.write_bytes(b"X" + raw[1:])
            with self.subTest(name=name):
                projection, captured, _ = self.run_case(before=replace)
                self.assertEqual(projection["status"], "CUSTODY_FAILED")
                self.assertTrue(captured["owner/" + name].startswith(b"X"))

    def test_relocation_receipt_metadata_and_media_cannot_change_under_prior_success(self):
        names = (
            "owner/capsule-relocation.original.json",
            "capsule/bridge-manifest.json", "capsule/source-project.json",
            "capsule/firstparty-complete-native-G2-input.json",
            "capsule/media/6ae70684422274dbc39d90e7e3a045c096a9df3692604c02a351e39fc094f2f1.mp4",
        )
        for name in names:
            def replace(root, result):
                path = root / name
                raw = path.read_bytes()
                path.write_bytes(b"X" + raw[1:])
            with self.subTest(name=name):
                projection, captured, _ = self.run_case(before=replace)
                self.assertEqual(projection["status"], "CUSTODY_FAILED")
                self.assertTrue(captured[name].startswith(b"X"))

    def test_old_location_appearance_before_or_during_capture_blocks_success_without_recapture(self):
        def appear(root, result):
            old = runner.relocation_source_path(root / "capsule")
            old.mkdir()
            (old / "late-member.original").write_bytes(b"late original location")
        for timing in ("before", "during", "after"):
            with self.subTest(timing=timing):
                projection, _captured, _ = self.run_case(**{timing: appear})
                self.assertEqual(projection["status"], "CUSTODY_FAILED")
                # run_case asserts one gather, one encryption of those very bytes.

    def test_source_cycle_and_relocation_claims_cannot_be_promoted_by_flags(self):
        import ci_private_evidence as custody
        mutations = (
            ("native_source_cycle", "source_fidelity_unavailable", False),
            ("native_source_cycle", "local_production_receipts_inherited", True),
            ("native_source_cycle", "execution_authority_issued", True),
            ("native_source_cycle", "per_scene_native_semantic_mapping_verified", True),
            ("native_source_cycle", "native_source_sha256", "f" * 64),
            ("native_source_cycle", "additional_source_revision_increment", True),
            ("capsule_relocation", "original_location_absent", False),
            ("capsule_relocation", "ui_relink_dialog_used", True),
            ("capsule_relocation", "alab_sources_adopted_here", True),
        )
        for field, key, value in mutations:
            result, _, _ = verified_snapshot_fixture({})
            result[field][key] = value
            with self.subTest(field=field, key=key), patch.object(runner, "custody", custody):
                with self.assertRaises(actions.GuiRejected):
                    runner.verified_snapshot_expectations(result, {})


class NativeTerminalCustodyPriorityContracts(unittest.TestCase):
    """Portable flow checks; Popen/GUI/processes are mocks, not native evidence."""

    def exercise(self, operation, terminal, recording_error, *, failure_point,
                 wait_error=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = Mock()
            records.root = root
            records.owned_processes = []
            streams = [Mock(), Mock()]
            process = Mock(pid=411)
            process.poll.return_value = 0
            appdir = root / "extract" / "squashfs-root"
            timeout = 30 if operation == "close" else 180
            native_error = actions.GuiRejected(
                "native_gui_process_nonzero_terminal" if operation == "close"
                else "native_gui_distribution_extraction_failed")
            states = []

            def emit(value):
                states.append(value)
                if (failure_point == "terminal-write"
                        and value["state"] in (
                            "OBSERVED", "TERMINAL_OBSERVED", "UNKNOWN_STILL_RUNNING")):
                    raise recording_error

            records.emit.side_effect = emit
            if failure_point == "first-close":
                streams[0].close.side_effect = recording_error
            elif failure_point == "second-close":
                streams[1].close.side_effect = recording_error

            def wait(**_):
                if wait_error is not None:
                    raise wait_error
                if operation == "extract" and terminal == 0:
                    appdir.mkdir()
                return terminal

            process.wait.side_effect = wait
            owned = runner.NativeProcess.__new__(runner.NativeProcess)
            owned.ui, owned.process, owned.records = Mock(), process, records
            owned.phase, owned.pid, owned.start = "reopen", 411, 1234
            owned.streams = streams
            with patch.object(runner.subprocess, "Popen", return_value=process) as start, \
                    patch.object(Path, "open", side_effect=streams) as open_log, \
                    patch.object(runner, "GuiRejected", return_value=native_error) as rejected:
                observed = None
                try:
                    if operation == "close":
                        owned.close()
                    else:
                        runner.extract_distribution(root / "official.AppImage", appdir, records)
                except BaseException as error:
                    observed = error
                if operation == "close":
                    start.assert_not_called()
                    open_log.assert_not_called()
                    owned.ui.quit.assert_called_once_with("reopen.quit")
                else:
                    start.assert_called_once()
                    self.assertEqual(start.call_args.args[0],
                                     [str(root / "official.AppImage"), "--appimage-extract"])
                    self.assertEqual(start.call_args.kwargs["cwd"], appdir.parent)
                    self.assertIs(start.call_args.kwargs["stdout"], streams[0])
                    self.assertIs(start.call_args.kwargs["stderr"], streams[1])
                    self.assertEqual(open_log.call_count, 2)
                    self.assertEqual(len(records.owned_processes), 1)
                    self.assertIs(records.owned_processes[0].process, process)
                    owned.ui.quit.assert_not_called()
                process.wait.assert_called_once_with(timeout=timeout)
                process.kill.assert_not_called()
                process.terminate.assert_not_called()
                if wait_error is None and terminal != 0:
                    rejected.assert_called_once()
                else:
                    rejected.assert_not_called()

            if wait_error is not None or failure_point == "terminal-write":
                self.assertEqual([stream.close.call_count for stream in streams], [0, 0])
            elif failure_point == "first-close":
                self.assertEqual([stream.close.call_count for stream in streams], [1, 0])
            else:
                self.assertEqual([stream.close.call_count for stream in streams], [1, 1])
            terminals = [value for value in states
                         if value["state"] in (
                             "OBSERVED", "TERMINAL_OBSERVED", "UNKNOWN_STILL_RUNNING")]
            self.assertEqual(len(terminals), 1)
            self.assertEqual(records.emit.call_count, 1 if operation == "close" else 3)
            return observed, native_error, terminals[0]

    def test_original_timeout_survives_unknown_terminal_custody_failure(self):
        for operation in ("close", "extract"):
            for error_type in (OSError, KeyboardInterrupt, SystemExit):
                with self.subTest(operation=operation, recording_error=error_type.__name__):
                    original = subprocess.TimeoutExpired(
                        ["kdenlive"] if operation == "close" else ["official.AppImage"],
                        30 if operation == "close" else 180)
                    recording_error = error_type("terminal record unavailable")
                    observed, _, terminal = self.exercise(
                        operation, 0, recording_error, failure_point="terminal-write",
                        wait_error=original)
                    self.assertIs(observed, original)
                    self.assertIs(observed.__cause__, recording_error)
                    self.assertEqual(terminal["state"], "UNKNOWN_STILL_RUNNING")
                    self.assertIs(terminal["replayed"], False)
                    self.assertNotIn("returncode", terminal)
                    self.assertIn("native_terminal_custody_failed:" + error_type.__name__,
                                  observed.__notes__)

    def test_native_terminal_and_success_keep_custody_error_priority(self):
        for operation in ("close", "extract"):
            for terminal_code in (2, 0):
                for failure_point in ("terminal-write", "first-close", "second-close"):
                    for error_type in (OSError, KeyboardInterrupt, SystemExit):
                        with self.subTest(operation=operation, terminal_code=terminal_code,
                                          failure_point=failure_point,
                                          recording_error=error_type.__name__):
                            recording_error = error_type("terminal custody unavailable")
                            observed, original, terminal = self.exercise(
                                operation, terminal_code, recording_error,
                                failure_point=failure_point)
                            self.assertEqual(terminal["returncode"], terminal_code)
                            self.assertEqual(
                                terminal["state"],
                                "OBSERVED" if operation == "close" else "TERMINAL_OBSERVED")
                            if terminal_code:
                                self.assertIs(observed, original)
                                self.assertIs(observed.__cause__, recording_error)
                                self.assertIn(
                                    "native_terminal_custody_failed:" + error_type.__name__,
                                    observed.__notes__)
                            else:
                                self.assertIs(observed, recording_error)

class NativeCapsuleRelocationContracts(unittest.TestCase):
    """Real bounded file moves; codec, NLE and canonical import stay unexecuted."""

    @staticmethod
    def make_capsule(path):
        import hashlib
        path.mkdir()
        (path / "media").mkdir()
        media = b"synthetic opaque test media, not decoded video"
        digest = hashlib.sha256(media).hexdigest()
        (path / "media" / (digest + ".mp4")).write_bytes(media)
        manifest = {
            "schema": runner.readback.SCHEMA, "output_profile": "portable-media-cut-v2",
            "nle_import_verified": False, "publishing_authorized": False,
            "scene_order": ["a", "b", "c"], "missing_scene_ids": [],
            "bound_media": [
                {"source_sha256": digest, "path": "media/" + digest + ".mp4",
                 "scene_id": scene, "source_start_frame": index * 30,
                 "duration_frames": 30, "source_fps": 30}
                for index, scene in enumerate(("a", "b", "c"))],
        }
        (path / "bridge-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        for name in runner.CAPSULE_METADATA_NAMES:
            if name != "bridge-manifest.json":
                (path / name).write_bytes(b'{"synthetic_only":true}\n')
        return media, digest

    def test_one_physical_move_keeps_relative_originals_and_never_claims_ui_relink(self):
        import ci_private_evidence as custody
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            capsule = root / "capsule"
            source = runner.relocation_source_path(capsule)
            media, digest = self.make_capsule(source)
            (root / "records").mkdir()
            with patch.object(runner, "custody", custody), \
                 patch.object(runner, "NativeProcess") as native:
                records = runner.Recorder(root / "records")
                before = runner.frozen_capsule_members(source)
                try:
                    result = runner.relocate_capsule_once(source, capsule, records)
                finally:
                    records.close()
                self.assertFalse(source.exists())
                self.assertEqual(result["members"], before)
                self.assertEqual(runner.frozen_capsule_members(capsule), before)
                self.assertEqual((capsule / "media" / (digest + ".mp4")).read_bytes(), media)
                self.assertTrue(result["original_bytes_preserved"])
                self.assertFalse(result["ui_relink_dialog_used"])
                self.assertFalse(result["alab_sources_adopted_here"])
                self.assertTrue((root / "records/capsule-relocation.intent.json").is_file())
                retained = root / "records/capsule-relocation.original.json"
                self.assertEqual(result["receipt_sha256"], custody.sha(retained.read_bytes()))
                native.assert_not_called()

    def test_foreign_or_existing_destination_or_changed_media_refuses_before_move(self):
        import ci_private_evidence as custody
        for defect in ("existing-destination", "foreign-source", "changed-media"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                capsule = root / "capsule"
                source = runner.relocation_source_path(capsule)
                _, digest = self.make_capsule(source)
                (root / "records").mkdir()
                if defect == "existing-destination":
                    capsule.mkdir()
                elif defect == "foreign-source":
                    source = root / "different-source"
                else:
                    (source / "media" / (digest + ".mp4")).write_bytes(b"changed original")
                with patch.object(runner, "custody", custody), \
                     patch.object(Path, "rename") as move:
                    records = runner.Recorder(root / "records")
                    try:
                        with self.assertRaises((actions.GuiRejected,
                                               runner.readback.KdenliveReadbackRejected)):
                            runner.relocate_capsule_once(source, capsule, records)
                    finally:
                        records.close()
                    move.assert_not_called()
                    self.assertFalse((root / "records/capsule-relocation.intent.json").exists())

    def test_existing_intent_blocks_any_second_move(self):
        import ci_private_evidence as custody
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            capsule = root / "capsule"
            source = runner.relocation_source_path(capsule)
            self.make_capsule(source)
            (root / "records").mkdir()
            with patch.object(runner, "custody", custody), patch.object(Path, "rename") as move:
                records = runner.Recorder(root / "records")
                records.document("capsule-relocation.intent.json", {"state": "UNKNOWN"})
                try:
                    with self.assertRaises(FileExistsError):
                        runner.relocate_capsule_once(source, capsule, records)
                finally:
                    records.close()
                move.assert_not_called()
                self.assertTrue(source.is_dir())
                self.assertFalse(capsule.exists())

    def test_move_original_error_survives_terminal_custody_failure_without_retry(self):
        import ci_private_evidence as custody
        for original in (TimeoutError("original move terminal unknown"),
                         KeyboardInterrupt("original move interrupted")):
            with self.subTest(type=type(original).__name__), \
                 tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                capsule = root / "capsule"
                source = runner.relocation_source_path(capsule)
                self.make_capsule(source)
                (root / "records").mkdir()
                with patch.object(runner, "custody", custody), \
                     patch.object(Path, "rename", side_effect=original) as move:
                    records = runner.Recorder(root / "records")
                    emit = records.emit
                    def fail_terminal(value):
                        if value["state"] == "ERROR_OR_UNKNOWN":
                            raise OSError("terminal retention failed")
                        return emit(value)
                    records.emit = fail_terminal
                    try:
                        with self.assertRaises(type(original)) as caught:
                            runner.relocate_capsule_once(source, capsule, records)
                    finally:
                        records.close()
                    self.assertIs(caught.exception, original)
                    self.assertIsInstance(original.__cause__, OSError)
                    move.assert_called_once_with(capsule)
                    self.assertTrue((root / "records/capsule-relocation.intent.json").is_file())
                    self.assertFalse((root / "records/capsule-relocation.original.json").exists())

    def test_full_selection_relocates_before_native_route_and_uses_same_source_database(self):
        import ci_private_evidence as custody
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            capsule = root / "capsule"
            old = runner.relocation_source_path(capsule)
            (root / "records").mkdir()
            def prepare(destination, **kwargs):
                self.assertEqual(destination, old)
                self.assertEqual(kwargs["source_database"], root / "records/canonical-source.sqlite3")
                self.make_capsule(destination)
                return {"synthetic_source_only": True}
            def route(appdir, binary, binary_sha, observed_capsule, records, profile, **kwargs):
                self.assertEqual(observed_capsule, capsule)
                self.assertFalse(old.exists())
                self.assertTrue(capsule.is_dir())
                self.assertEqual(kwargs["source_database"], root / "records/canonical-source.sqlite3")
                self.assertTrue((records.root / "capsule-relocation.original.json").is_file())
                return {"owner_cut_completion": {"sqlite_owner_cut_committed": True},
                        "native_source_cycle": {"native_source_import_committed": True,
                                               "portable_readback_in_new_process": True}}
            with patch.object(runner, "custody", custody), \
                 patch.object(runner.fixture, "prepare_original", side_effect=prepare) as producer, \
                 patch.object(runner, "run_route", side_effect=route) as native:
                records = runner.Recorder(root / "records")
                try:
                    status, result = runner.run_selected_native(
                        "kdenlive-gui", root / "app", root / "binary", "a" * 64,
                        capsule, records, root / "profile")
                finally:
                    records.close()
            self.assertEqual(status, "NATIVE_GUI_BOUNDED_ROUNDTRIP_OBSERVED")
            self.assertTrue(result["capsule_relocation"]["original_location_absent"])
            producer.assert_called_once()
            native.assert_called_once()

    def test_post_move_custody_failure_blocks_native_route_and_preserves_actual_move(self):
        import ci_private_evidence as custody
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            capsule = root / "capsule"
            old = runner.relocation_source_path(capsule)
            (root / "records").mkdir()
            def prepare(destination, **kwargs):
                self.make_capsule(destination)
                return {"synthetic_source_only": True}
            with patch.object(runner, "custody", custody), \
                 patch.object(runner.fixture, "prepare_original", side_effect=prepare) as producer, \
                 patch.object(runner, "run_route") as native:
                records = runner.Recorder(root / "records")
                document = records.document
                original = OSError("original move receipt storage failure")
                def fail_receipt(name, value):
                    if name == "capsule-relocation.original.json":
                        raise original
                    return document(name, value)
                records.document = fail_receipt
                try:
                    with self.assertRaises(OSError) as caught:
                        runner.run_selected_native(
                            "kdenlive-gui", root / "app", root / "binary", "a" * 64,
                            capsule, records, root / "profile")
                finally:
                    records.close()
            self.assertIs(caught.exception, original)
            self.assertFalse(old.exists())
            self.assertTrue(capsule.is_dir())
            self.assertTrue((root / "records/capsule-relocation.intent.json").is_file())
            producer.assert_called_once()
            native.assert_not_called()



class AlabNativeGuiContracts(unittest.TestCase):
    """Explicit ALab profile contracts; simulated UI/Native calls are never G2 evidence."""

    @staticmethod
    def context():
        return {"source_sha": "a" * 40, "semwright_sha": "b" * 40, "run_id": "123", "run_attempt": "1"}

    def source_fixture(self, root):
        import test_kdenlive_owner_cut as owner_controls
        helper = owner_controls.KdenliveAlabCanonicalSourceContracts()
        helper.prepare(root)
        helper.workspace = helper.evidence / "alab-source"
        helper.database = helper.evidence / "canonical-source.sqlite3"
        return helper

    @staticmethod
    def source_scope(helper):
        from contextlib import ExitStack
        import bridge as media_bridge
        stack = ExitStack()
        for entry in (
            patch.object(runner.owner_cut, "OWNER_EXAMPLE", helper.binary),
            patch.object(runner.owner_cut, "_ALAB_SOURCE_NOTICES", helper.notice_pins),
            patch.object(media_bridge, "_ALAB_SOURCE_NOTICES", helper.notice_pins),
            patch.object(media_bridge, "_ALAB_RETAINED_ORIGINALS", helper.pins),
            patch.object(media_bridge, "_ALAB_RETAINED_MEDIA", helper.media_pins),
            patch.object(media_bridge, "file_probe", side_effect=AssertionError("no new ALab decoder")),
            patch.object(media_bridge.subprocess, "Popen", side_effect=AssertionError("no native subprocess in GUI unit test")),
        ):
            stack.enter_context(entry)
        return stack

    def input_value(self, helper):
        import bridge as media_bridge
        pins = {role: (size, sha) for role, size, sha in helper.pins}
        pins.update({"media" + str(i): (row[2], row[3]) for i, row in enumerate(helper.media_pins)})
        return {"schema": runner.ALAB_GUI_INPUT_SCHEMA, "profile": media_bridge.ALAB_RETAINED_PROFILE,
                "source": self.context(),
                "originals": {role: {"path": str(helper.originals[role]), "bytes": size, "sha256": sha}
                              for role, (size, sha) in pins.items()},
                "notices": {role: {"path": str(helper.notices[role]), "bytes": size, "sha256": sha}
                            for role, _, size, sha in helper.notice_pins},
                "owner_review": helper.owner_review, "media_rights": helper.rights}

    def test_alab_input_is_exact_run_bound_originals_before_any_native_process(self):
        import ci_private_evidence as custody
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); helper = self.source_fixture(root)
            path = root / "admitted-alab-input.json"
            raw = (json.dumps(self.input_value(helper), sort_keys=True) + "\n").encode(); path.write_bytes(raw)
            with self.source_scope(helper), patch.object(runner, "custody", custody), \
                 patch.object(runner, "NativeProcess") as native, \
                 patch.object(runner, "extract_distribution") as extract:
                admitted, original = runner.load_alab_gui_inputs(path, self.context(), root)
            self.assertEqual(original, raw)
            self.assertEqual(admitted["admission_sha256"], custody.sha(raw))
            self.assertEqual(admitted["originals"], helper.originals)
            self.assertEqual(admitted["notices"], helper.notices)
            native.assert_not_called(); extract.assert_not_called()

    def test_alab_input_wrong_context_roles_or_identity_refuses_before_original_leaf_access(self):
        import ci_private_evidence as custody
        for defect in ("source", "run", "role", "extra", "sha", "size", "boolean-size"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); helper = self.source_fixture(root)
                value = self.input_value(helper)
                if defect == "source": value["source"]["source_sha"] = "c" * 40
                elif defect == "run": value["source"]["run_id"] = "124"
                elif defect == "role": value["originals"].pop("otio")
                elif defect == "extra": value["grant"] = True
                elif defect == "sha": value["originals"]["otio"]["sha256"] = "0" * 64
                elif defect == "size": value["originals"]["otio"]["bytes"] += 1
                else: value["originals"]["otio"]["bytes"] = True
                path = root / "bad-input.json"; path.write_text(json.dumps(value), encoding="utf-8")
                with self.source_scope(helper), patch.object(runner, "custody", custody), \
                     patch.object(runner.readback, "read_closed_bytes", side_effect=AssertionError("original accessed before descriptor admission")) as read, \
                     patch.object(runner, "NativeProcess") as native:
                    with self.assertRaises(actions.GuiRejected): runner.load_alab_gui_inputs(path, self.context(), root)
                read.assert_not_called(); native.assert_not_called()

    def make_ui(self, *, before=(52, 0, 182), late_duration=None, multiple=False):
        def code(frame):
            whole, residual = divmod(frame, 24)
            return f"00:00:{whole:02}:{residual:02}"
        monitor = row("app/monitor", name="Project Monitor", role="panel", editable=False)
        seek = row("app/monitor/seek", text="00:00:00:00")
        dialog = row("app/dialog", name="Duration", role="dialog", editable=False)
        fields, labels = [], []
        for i, (name, value) in enumerate(zip(("Position:", "Crop start:", "Duration:"), before)):
            labels.append(row(f"app/dialog/label{i}", name=name, role="label", editable=False,
                              rect=[0, 20 + i * 40, 70, 20]))
            fields.append(row(f"app/dialog/field{i}", text=code(value), rect=[100, 20 + i * 40, 100, 20]))
        if multiple: labels.append(row("app/dialog/multiple", name="Apply duration to all items", editable=False))
        state = {"closed": False}
        ui = Mock()
        ui.tree.side_effect = lambda: [monitor, seek, dialog, *labels, *fields]
        ui.dialog.return_value = dialog
        ui.descendants.side_effect = lambda selected: [*labels, *fields]
        ui.journal.wait.side_effect = lambda step, query: query()
        ui.text.side_effect = lambda step, selected, value: selected.update(text=value)
        def key(step, symbol):
            if step == "alab.commit-duration-field" and late_duration is not None:
                fields[2]["text"] = code(late_duration)
        ui.key.side_effect = key
        okay = row("app/dialog/ok", name="OK", role="push button", editable=False)
        ui.find.side_effect = lambda names, roles, **kwargs: okay if names == ("OK",) else (None if state["closed"] else dialog)
        ui.action.side_effect = lambda step, selected: state.update(closed=True)
        return ui, fields, seek

    def test_alab_duration_action_uses_only_existing_controls_and_24fps_second_clip_fields(self):
        ui, fields, seek = self.make_ui()
        runner.edit_alab_second_clip(ui)
        self.assertEqual(seek["text"], "00:00:04:04")
        self.assertEqual([field["text"] for field in fields], ["00:00:02:04", "00:00:00:00", "00:00:07:02"])
        self.assertEqual(ui.key.call_args_list[0].args, ("alab.commit-seek", 0xFF0D))
        self.assertEqual(ui.key.call_args_list[1].args, ("alab.select-video-track1", 0x0031))
        ui.action.assert_called_once()
        ui.edit_third_clip.assert_not_called()
        self.assertEqual(ui.journal.emit.call_args.args[0]["duration_frames"], 182)
        self.assertEqual(ui.journal.emit.call_args.args[0]["new_duration_frames"], 170)
        for value in ("00:00:00:24", "00:60:00:00", "0:0:7:2"):
            with self.subTest(timecode=value), self.assertRaises(actions.GuiRejected): runner._alab_timecode24(value)

    def test_alab_duration_selection_or_late_field_change_never_accepts_the_wrong_clip(self):
        cases = ({"before": (51, 0, 182)}, {"before": (52, 1, 182)}, {"before": (52, 0, 181)},
                 {"multiple": True}, {"late_duration": 169})
        for case in cases:
            with self.subTest(case=case):
                ui, _fields, _seek = self.make_ui(**case)
                with self.assertRaises(actions.GuiRejected): runner.edit_alab_second_clip(ui)
                ui.action.assert_not_called()
                ui.edit_third_clip.assert_not_called()

    @staticmethod
    def selected_edit():
        before = {"observed_bound_editorial_clip_semantics": [
            {"scene_id": "scene-a", "original_inpoint_frame": 0, "duration_frames": 52},
            {"scene_id": "scene-b", "original_inpoint_frame": 0, "duration_frames": 182}]}
        after = copy.deepcopy(before); after["observed_bound_editorial_clip_semantics"][1]["duration_frames"] = 170
        comparison = {"bounded_native_end_trims_observed": [{"scene_id": "scene-b",
            "initial_duration_frames": 182, "reopened_duration_frames": 170}]}
        preview = {"status": "BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED", "nle_receiver_acceptance": "NOT_VERIFIED",
            "edit_imported": False, "human_review_required": True, "publication_approved": False,
            "native_change_proposals": [{"type": "set_scene_duration", "scene_id": "scene-b",
                                        "duration": {"num": "85", "den": "12"}}]}
        return before, after, comparison, preview

    def test_alab_native_readback_and_returned_preview_must_agree_on_only_twelve_second_clip_frames(self):
        runner.check_alab_edit(*self.selected_edit())
        for defect in ("inpoint", "first-duration", "second-duration", "comparison", "proposal", "promoted"):
            with self.subTest(defect=defect):
                before, after, comparison, preview = self.selected_edit()
                if defect == "inpoint": after["observed_bound_editorial_clip_semantics"][1]["original_inpoint_frame"] = 12
                elif defect == "first-duration": after["observed_bound_editorial_clip_semantics"][0]["duration_frames"] = 40
                elif defect == "second-duration": after["observed_bound_editorial_clip_semantics"][1]["duration_frames"] = 169
                elif defect == "comparison": comparison["bounded_native_end_trims_observed"][0]["scene_id"] = "scene-a"
                elif defect == "proposal": preview["native_change_proposals"][0]["duration"] = {"num": "1", "den": "2"}
                else: preview["edit_imported"] = True
                with self.assertRaises(actions.GuiRejected): runner.check_alab_edit(before, after, comparison, preview)

    def mocked_alab_route(self, *, failed_close=None, primary=None):
        before, after, comparison, preview = self.selected_edit()
        root, capsule, appdir = Path("/synthetic/evidence"), Path("/synthetic/capsule"), Path("/synthetic/app")
        records = Mock(root=root); binary = appdir / "kdenlive"
        initial, edited = root / "initial.kdenlive", root / "edited.kdenlive"
        self.processes = [Mock(), Mock(), Mock()]
        for index, process in enumerate(self.processes):
            saved = initial if index == 0 else edited
            reopen = None if index == 0 else initial if index == 1 else edited
            process.witness.return_value = {"schema": "motionwright.native.kdenlive-live-process/1",
                "phase": "import" if index == 0 else "reopen", "pid": 401 + index,
                "kernel_starttime_ticks": 8001 + index, "actual_executable_sha256": "8" * 64,
                "original_Kdenlive_application_version": runner.APPIMAGE_VERSION,
                "source_original_OTIO_sha256": "3" * 64,
                "saved_native_project_path": saved.as_posix(),
                "saved_native_project_sha256": ("1" if index == 0 else "2") * 64,
                "reopen_input_project_path": None if reopen is None else reopen.as_posix(),
                "reopen_input_project_sha256": None if index == 0 else ("1" if index == 1 else "2") * 64,
                "process_arguments": {"saved_project_was_an_exact_launch_argument": index > 0,
                                      "native_project_path_checked": None if reopen is None else reopen.as_posix()},
                "live_application_binary_and_pid_verified": True}
            process.ui.tree.return_value = []
        if failed_close is not None: self.processes[failed_close].close.side_effect = primary
        hashes = {"initial.kdenlive": "1", "edited.kdenlive": "2", "timeline.otio": "3",
                  "bridge-manifest.json": "4", "received.otio": "5", "edited.otio": "6",
                  "reopened-edited.otio": "7", "kdenlive": "8"}
        self.retained, self.source_input = object(), {"unit_source_input": True}
        completion = {"unit_mocked_returned_source": True}
        with patch.object(runner, "NativeProcess", side_effect=self.processes) as launch, \
             patch.object(runner, "digest", side_effect=lambda path, maximum: hashes[path.name] * 64), \
             patch.object(runner.readback, "read_capsule") as source_check, \
             patch.object(runner.readback, "inspect_saved_kdenlive", side_effect=[before, after, after]) as inspect, \
             patch.object(runner.readback, "compare_two_native_custody_snapshots", return_value=comparison), \
             patch.object(runner, "preview_kdenlive_cut", side_effect=[preview, preview]) as preview_call, \
             patch.object(runner, "edit_alab_second_clip") as edit, \
             patch.object(runner.owner_cut, "finish_alab_return_once", return_value=completion) as finish:
            self.launch, self.inspect, self.edit, self.finish, self.preview_call = launch, inspect, edit, finish, preview_call
            return runner.run_route(appdir, binary, "8" * 64, capsule, records, root / "profile",
                source_database=root / "canonical-source.sqlite3", retained_alab=self.retained,
                alab_source_input=self.source_input)

    def test_alab_route_retains_three_original_native_sessions_and_only_second_scene_edit(self):
        result = self.mocked_alab_route()
        self.assertEqual(self.launch.call_count, 3)
        self.assertEqual(self.launch.call_args.args[-1], "reopen-edited")
        self.edit.assert_called_once_with(self.processes[1].ui)
        for process in self.processes:
            process.ui.edit_third_clip.assert_not_called()
            process.close.assert_called_once()
        self.assertEqual(result["exact_end_trim_frames"], 12)
        self.assertTrue(result["edited_native_project_reopened"])
        self.assertEqual(result["alab_returned_source"], {"unit_mocked_returned_source": True})
        self.assertEqual(self.finish.call_args.args[-2:], (self.source_input, self.retained))
        self.assertTrue(all(call.kwargs["retained_alab"] is self.retained for call in self.inspect.call_args_list))
        self.assertTrue(all(call.kwargs["retained_alab"] is self.retained for call in self.preview_call.call_args_list))

    def test_alab_native_unknown_terminal_stops_before_owner_commit_without_retry(self):
        for phase in (0, 1, 2):
            with self.subTest(phase=phase):
                original = subprocess.TimeoutExpired(["unit-native"], 30)
                with self.assertRaises(subprocess.TimeoutExpired) as caught:
                    self.mocked_alab_route(failed_close=phase, primary=original)
                self.assertIs(caught.exception, original)
                self.assertEqual(self.launch.call_count, phase + 1)
                self.finish.assert_not_called()
                self.assertTrue(all(process.close.call_count <= 1 for process in self.processes))

    def test_alab_profile_cannot_be_sent_to_accessibility_or_without_both_source_bindings(self):
        with patch.object(runner, "run_selected_alab") as selected, \
             patch.object(runner, "NativeProcess") as native:
            with self.assertRaises(actions.GuiRejected):
                runner.run_selected_native("accessibility-only", Path("a"), Path("b"), "0" * 64,
                    Path("c"), Mock(), Path("p"), alab_inputs={})
            with self.assertRaises(actions.GuiRejected):
                runner.run_route(Path("a"), Path("b"), "0" * 64, Path("c"), Mock(), Path("p"),
                                 retained_alab=object())
        selected.assert_not_called(); native.assert_not_called()

    def test_alab_physical_relocation_preserves_two_MOVs_original_notices_and_standalone_ZIP(self):
        import ci_private_evidence as custody
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); helper = self.source_fixture(root)
            capsule = root / "selected-alab"
            helper.destination = runner.relocation_source_path(capsule)
            _source, retained = helper.invoke()
            with self.source_scope(helper), patch.object(runner, "custody", custody), \
                 patch.object(runner, "NativeProcess") as native:
                records = runner.Recorder(helper.evidence)
                before = runner.frozen_capsule_members(helper.destination, retained_alab=retained)
                try: moved = runner.relocate_capsule_once(helper.destination, capsule, records, retained_alab=retained)
                finally: records.close()
                self.assertEqual(runner.frozen_capsule_members(capsule, retained_alab=retained), before)
            self.assertEqual(len(moved["members"]), 12)
            self.assertFalse(helper.destination.exists())
            self.assertFalse(moved["ui_relink_dialog_used"])
            self.assertFalse(moved["alab_sources_adopted_here"])
            self.assertIn("portable-cut.zip", moved["members"])
            for _, name, size, sha in helper.notice_pins:
                self.assertEqual(moved["members"][name], {"bytes": size, "sha256": sha})
            native.assert_not_called()

    def test_alab_changed_notice_or_standalone_ZIP_refuses_before_physical_relocation(self):
        import ci_private_evidence as custody
        import bridge as media_bridge
        for target in ("ALab-LICENSE.original.txt", "ALab-README.original.txt", "ALab-SELECTION-NOTICE.original.txt", "portable-cut.zip"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); helper = self.source_fixture(root)
                capsule = root / "selected-alab"; helper.destination = runner.relocation_source_path(capsule)
                _source, retained = helper.invoke()
                path = helper.destination / target; raw = path.read_bytes(); path.write_bytes(b"X" + raw[1:])
                with self.source_scope(helper), patch.object(runner, "custody", custody), \
                     patch.object(Path, "rename") as move:
                    records = runner.Recorder(helper.evidence)
                    try:
                        with self.assertRaises((actions.GuiRejected, runner.readback.KdenliveReadbackRejected, media_bridge.BridgeRefused)):
                            runner.relocate_capsule_once(helper.destination, capsule, records, retained_alab=retained)
                    finally: records.close()
                move.assert_not_called()
                self.assertTrue(helper.destination.exists()); self.assertFalse(capsule.exists())

    def prepared_snapshot(self, root):
        import ci_private_evidence as custody
        import bridge as media_bridge
        import test_kdenlive_owner_cut as owner_controls
        helper = owner_controls.KdenliveAlabReturnedSourceContracts()
        helper.prepare_return(root)
        capsule = root / "snapshot-alab-capsule"
        before = runner.relocation_source_path(capsule)
        # Synthetic fixture arrangement only; the next call is the sole observed
        # production relocation. No retained historical original is touched.
        helper.capsule.rename(before)
        records = runner.Recorder(helper.evidence)
        try:
            with helper.original_scope(), patch.object(runner, "custody", custody):
                relocation = runner.relocate_capsule_once(before, capsule, records, retained_alab=helper.retained)
                helper.capsule = capsule
                completion = helper.invoke_return()
                admission = (json.dumps(self.input_value(helper.base), sort_keys=True) + "\n").encode()
                custody.new_bytes(helper.evidence / "alab-gui-input.original.json", admission)
                # The caller's native mocks already retained the same argv,
                # terminal and exact stream bytes before the first parse.
                for name in ("initial.kdenlive", "received.otio", "edited.otio", "reopened-edited.otio"):
                    custody.new_bytes(helper.evidence / name, ("unit-only original " + name).encode())
                result = {"source_profile": media_bridge.ALAB_RETAINED_PROFILE,
                    "owner_cut_completion": None, "native_source_cycle": None,
                    "alab_source_adoption": helper.source_input, "alab_returned_source": completion,
                    "alab_input_admission_sha256": custody.sha(admission), "capsule_relocation": relocation,
                    "initial_project_sha256": custody.sha((helper.evidence / "initial.kdenlive").read_bytes()),
                    "edited_project_sha256": custody.sha(helper.native.read_bytes()),
                    "original_otio_sha256": custody.sha((capsule / "timeline.otio").read_bytes()),
                    "received_otio_sha256": custody.sha((helper.evidence / "received.otio").read_bytes()),
                    "edited_otio_sha256": custody.sha((helper.evidence / "edited.otio").read_bytes()),
                    "reopened_edited_otio_sha256": custody.sha((helper.evidence / "reopened-edited.otio").read_bytes()),
                    "two_distinct_native_processes": True, "three_distinct_native_processes": True,
                    "edited_native_project_reopened": True, "exact_end_trim_frames": 12}
                result["alab_original_bindings"] = runner.observe_alab_original_bindings(
                    helper.evidence, helper.source_input, completion)
                record = {"status": "NATIVE_GUI_BOUNDED_ROUNDTRIP_OBSERVED", "source": self.context(), "result": result,
                          "recording_errors_before_result": [], "native_operation_retried": False,
                          "human_acceptance": False, "release_authorized": False}
                custody.new_bytes(helper.evidence / "original-result.json", custody.json_bytes(record))
        finally:
            records.close()
        return helper, capsule, result

    def test_alab_existing_single_snapshot_and_crypto_recover_exact_originals_not_native_credit(self):
        import base64
        import tarfile
        import ci_private_evidence as custody
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            helper, capsule, result = self.prepared_snapshot(root)
            public = root / "public"; public.mkdir()
            key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
            der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
            recipient = {"spki_der_base64": base64.b64encode(der).decode(), "sha256": custody.sha(der)}
            with helper.original_scope(), patch.object(runner, "custody", custody), \
                 patch.object(custody, "gather_snapshot", wraps=custody.gather_snapshot) as gather, \
                 patch.object(custody, "encrypt_snapshot", wraps=custody.encrypt_snapshot) as encrypt, \
                 patch.object(runner, "NativeProcess") as native:
                expected = runner.verified_snapshot_expectations(result, self.context())
                projection = runner.seal_and_publish(helper.evidence, capsule, public, recipient,
                    self.context(), "NATIVE_GUI_BOUNDED_ROUNDTRIP_OBSERVED", result=result)
                raw, context = custody.decrypt_snapshot((public / "original-evidence.mwseal").read_bytes(), key)
                runner.require_verified_snapshot(raw, expected)
            gather.assert_called_once(); encrypt.assert_called_once(); native.assert_not_called()
            self.assertEqual(context, self.context())
            self.assertEqual(projection["status"], "NATIVE_GUI_BOUNDED_ROUNDTRIP_OBSERVED")
            self.assertFalse(projection["human_acceptance"]); self.assertFalse(projection["release_authorized"])
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
                for name in ("alab-source/alab-adopt-cli.stderr.bin", "alab-returned-cut-cli.stderr.bin"):
                    self.assertEqual(archive.extractfile("owner/" + name).read(), b"")
                for name in ("ALab-LICENSE.original.txt", "ALab-README.original.txt", "ALab-SELECTION-NOTICE.original.txt", "portable-cut.zip"):
                    self.assertEqual(archive.extractfile("capsule/" + name).read(), (capsule / name).read_bytes())
                self.assertEqual(archive.extractfile("owner/alab-returned-source-receipt.json").read(),
                                 (helper.evidence / "alab-returned-source-receipt.json").read_bytes())
            self.assertEqual(result["alab_returned_source"]["total_original_history_events"], 9)
            self.assertIs(result["alab_returned_source"]["native_editor_dispatched_here"], False)

    def test_alab_changed_or_missing_originals_and_coherent_result_rewrite_cannot_inherit_snapshot_success(self):
        import tarfile
        import ci_private_evidence as custody
        for defect in ("source-stage", "license", "empty-stderr", "coherent-return", "duplicate-return"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); helper, capsule, result = self.prepared_snapshot(root)
                public = root / "public"; public.mkdir()
                original_gather = custody.gather_snapshot
                def capture(roots, **kwargs):
                    if defect == "source-stage":
                        path = helper.evidence / "alab-source/alab-adoption-receipt.stage-02.result.original.json"
                        body = path.read_bytes(); path.write_bytes(b"X" + body[1:])
                    elif defect == "license": (capsule / "ALab-LICENSE.original.txt").unlink()
                    elif defect == "empty-stderr":
                        (helper.evidence / "alab-returned-cut-cli.stderr.bin").write_bytes(b"changed original")
                    elif defect == "coherent-return":
                        name = "alab-returned-source-receipt.json"
                        path = helper.evidence / name; body = path.read_bytes(); path.write_bytes(b" " + body)
                        new = path.read_bytes(); digest = custody.sha(new)
                        result["alab_returned_source"]["returned_source_receipt_sha256"] = digest
                        result["alab_original_bindings"]["members"][name] = {"bytes": len(new), "sha256": digest}
                        record_path = helper.evidence / "original-result.json"
                        record = custody.strict(record_path.read_bytes()); record["result"] = result
                        record_path.write_bytes(custody.json_bytes(record))
                    packed, complete = original_gather(roots, **kwargs)
                    if defect == "duplicate-return":
                        with tarfile.open(fileobj=io.BytesIO(packed), mode="r:") as archive:
                            entries = [(item.name, archive.extractfile(item).read()) for item in archive]
                        duplicate = "owner/alab-returned-source-receipt.json"
                        entries.append((duplicate, (helper.evidence / "alab-returned-source-receipt.json").read_bytes()))
                        stream = io.BytesIO()
                        with tarfile.open(fileobj=stream, mode="w") as archive:
                            for name, body in entries:
                                item = tarfile.TarInfo(name); item.size = len(body)
                                archive.addfile(item, io.BytesIO(body))
                        packed = stream.getvalue()
                    return packed, complete
                with helper.original_scope(), patch.object(runner, "custody", custody), \
                     patch.object(custody, "gather_snapshot", side_effect=capture) as gather, \
                     patch.object(custody, "encrypt_snapshot", return_value=b"unit opaque seal; crypto covered separately") as encrypt:
                    projection = runner.seal_and_publish(helper.evidence, capsule, public, {}, self.context(),
                        "NATIVE_GUI_BOUNDED_ROUNDTRIP_OBSERVED", result=result)
                gather.assert_called_once(); encrypt.assert_called_once()
                self.assertEqual(projection["status"], "CUSTODY_FAILED")
                self.assertTrue((public / "original-evidence.mwseal").is_file())

    def test_alab_verified_scope_rejects_promoted_flags_wrong_counts_and_out_of_roster_originals(self):
        import ci_private_evidence as custody
        cases = (("alab_source_adoption", "source_adoption_history_events", True),
                 ("alab_source_adoption", "new_decode", True),
                 ("alab_returned_source", "additional_history_events", True),
                 ("alab_returned_source", "execution_authority_issued", True),
                 ("capsule_relocation", "ui_relink_dialog_used", True),
                 ("capsule_relocation", "alab_sources_adopted_here", True))
        with tempfile.TemporaryDirectory() as temporary:
            helper, _capsule, original = self.prepared_snapshot(Path(temporary).resolve())
            with helper.original_scope(), patch.object(runner, "custody", custody):
                for section, key, value in cases:
                    with self.subTest(section=section, key=key):
                        changed = copy.deepcopy(original); changed[section][key] = value
                        with self.assertRaises(actions.GuiRejected): runner.verified_snapshot_expectations(changed, self.context())
                for defect in ("missing", "extra", "wrong-phase-path", "boolean-bytes"):
                    with self.subTest(defect=defect):
                        changed = copy.deepcopy(original)
                        members = changed["alab_original_bindings"]["members"]
                        key = "alab-returned-cut-cli.stderr.bin"
                        if defect == "missing": members.pop(key)
                        elif defect == "extra": members["foreign-original.bin"] = members[key]
                        elif defect == "wrong-phase-path": members["alab-source/" + key] = members.pop(key)
                        else: members[key]["bytes"] = False
                        with self.assertRaises(actions.GuiRejected): runner.verified_snapshot_expectations(changed, self.context())


if __name__ == "__main__":
    unittest.main()
