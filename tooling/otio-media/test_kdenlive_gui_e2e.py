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


class EditedProjectReopenContracts(unittest.TestCase):
    """A successful route must retain the saved edit after a separate reopen."""

    def run_mocked_route(self, *, mutate_third=None, final_preview=None,
                         third_close_error=None, changed_edited_bytes=False,
                         changed_readback=False):
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
                                    self.records, profile)

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


if __name__ == "__main__":
    unittest.main()
