"""Synthetic playlist-refresh controls; no GUI, renderer or Native evidence.

The model and serialized playlist below deliberately begin with different final
inpoints. Editing a field is separate from accepting it; native rejection can
undo the change and still close the dialog, as ClipDurationDialog::accept does.
"""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

import kdenlive_gui_actions as actions


PREFIX = "import.playlist-refresh"


def code(frames):
    return f"{frames // 108000:02}:{frames // 1800 % 60:02}:{frames // 30 % 60:02}:{frames % 30:02}"


def row(key, *, name="", text="", role="text", editable=False, rect=None, modal=False, pid=41):
    return {"key": key, "name": name, "text": text, "role": role, "editable": editable,
            "visible": True, "enabled": True, "rect": rect or [0, 0, 100, 20],
            "focused": False, "modal": modal, "pid": pid}


class SyntheticEditor:
    """Explicit fake API; changing pending text does not change the clip model."""

    def __init__(self, **faults):
        self.faults = faults
        self.events = []
        self.audit = []
        self.effects = []
        self.openings = 0
        self.accepts = []
        self.model_duration = 30
        self.model_inpoints = [0, 30, 60]
        self.playlist_inpoints = [0, 30, 0]
        self.clip_identity = object()
        self.initial_identity = self.clip_identity
        self.pending = None
        self.dialog_key = None
        self.seek = "00:00:00:00"
        self.original = RuntimeError("synthetic native first error")
        self.custody_error = OSError("synthetic original custody error")
        self.guard_error = RuntimeError("synthetic owner guard changed")
        self.ui = actions.AtspiSession.__new__(actions.AtspiSession)
        self.ui.pid = 41
        self.ui.last_snapshot = None
        self.ui.journal = actions.ActionJournal(self.emit, self.guard)
        self.ui.tree = self.tree
        self.ui.text = self.text
        self.ui.key = self.key
        self.ui.menu = self.menu
        self.ui.action = self.action

    def guard(self):
        if self.faults.get("guard_after_first_accept") and len(self.accepts) == 1:
            raise self.guard_error

    def emit(self, event):
        self.audit.append(("custody", event["step"], event["state"]))
        fail = self.faults.get("custody")
        if ((fail == "initial-fields" and event["step"] == PREFIX + ".shorten.before")
                or (fail == "shorten-return" and event["step"] == PREFIX + ".shorten.accept-once"
                    and event["state"] == "REQUEST_RETURNED")
                or (fail == "applied-fields" and event["step"] == PREFIX + ".shorten.applied-readback")
                or (fail == "error" and event["state"] == "ERROR_OR_UNKNOWN")
                or (fail == "result" and event["step"] == PREFIX + ".result")):
            raise self.custody_error
        self.events.append(copy.deepcopy(event))

    def tree(self):
        self.audit.append(("tree", self.openings, self.model_duration, self.pending is not None))
        rows = [row("app/main", name="Untitled — Kdenlive", role="frame"),
                row("app/main/monitor", name="Project Monitor", role="panel"),
                row("app/main/monitor/time", text=self.seek, editable=True)]
        if self.pending is not None:
            rows.append(row(self.dialog_key, name="Duration — Kdenlive", role="dialog", modal=True))
            for index, (name, label) in enumerate((
                    ("position", "Position:"), ("crop", "Crop start:"), ("duration", "Duration:"))):
                y = 40 * index
                rows.append(row(self.dialog_key + "/label-" + name, name=label, role="label",
                                rect=[0, y, 75, 20]))
                rows.append(row(self.dialog_key + "/" + name, text=self.pending[name],
                                editable=True, rect=[100, y, 100, 20]))
            rows.append(row(self.dialog_key + "/okay", name="OK", role="push button"))
            if self.faults.get("multiple") == self.openings:
                rows.append(row(self.dialog_key + "/multiple", name="Apply duration to all items",
                                role="check box"))
            if self.faults.get("ripple") == self.openings:
                rows.append(row(self.dialog_key + "/mode", name="Resizing in Ripple mode.", role="label"))
        if self.faults.get("unexpected_after_accept") and self.accepts and self.pending is None:
            rows.append(row("app/error", name="Error — Kdenlive", role="alert", modal=True))
        foreign = self.faults.get("foreign")
        if foreign == "initial" and self.openings == 0 or foreign == "second" and self.openings == 2:
            rows[-1]["pid"] = 99
        incomplete = (self.faults.get("incomplete") == "initial" and self.openings == 0
                      or self.faults.get("incomplete") == "after-accept"
                      and self.accepts and self.pending is None)
        if incomplete:
            rows = []
        self.ui.last_snapshot = {"complete": not incomplete, "rows": rows}
        return rows

    def perform(self, step, effect):
        def observed():
            self.effects.append(step)
            self.audit.append(("effect", step))
            return effect()
        return self.ui.journal.perform(step, {"synthetic_api": True}, observed)

    def menu(self, step, *names):
        def invoke():
            if names == ("Sequence", "Current Item", "Edit Duration"):
                self.openings += 1
                self.dialog_key = "app/duration" + str(self.openings)
                values = self.faults.get("initial_tuple", (60, 60, self.model_duration))
                if self.openings == 2:
                    values = self.faults.get("second_tuple", (60, 60, self.model_duration))
                self.pending = {name: code(value) if type(value) is int else value
                                for name, value in zip(("position", "crop", "duration"), values)}
                self.audit.append(("opened-model", self.openings, self.model_duration))
                if self.faults.get("opener_unknown") == self.openings:
                    raise self.original
            else:
                assert names == ("Sequence", "Selection", "Select Clip")
            return True
        self.perform(step, invoke)

    def text(self, step, selected, value):
        def invoke():
            if selected["key"] == "app/main/monitor/time":
                self.seek = value
            else:
                assert selected["key"] == self.dialog_key + "/duration"
                self.pending["duration"] = value
            return True
        self.perform(step, invoke)

    def key(self, step, symbol):
        def invoke():
            assert symbol in (0xFF0D, 0x0031)
            late = self.faults.get("late_tuple")
            if late and step == PREFIX + "." + late[0] + ".commit-field":
                self.pending = {k: code(v) for k, v in zip(("position", "crop", "duration"), late[1])}
            return True
        self.perform(step, invoke)

    def action(self, step, selected, **kwargs):
        assert selected["key"] == self.dialog_key + "/okay"
        def accept():
            desired = actions.timecode_frames(self.pending["duration"])
            self.accepts.append((step, desired))
            number = len(self.accepts)
            behavior = self.faults.get("accept_behavior", {}).get(number, "apply")
            if behavior == "reject":
                return False
            if behavior == "unknown-before":
                raise self.original
            if behavior != "rollback":
                self.model_duration = desired
                # The fake models the documented resize_clip refresh, distinct
                # from an in-memory field change or a dialog closing.
                self.playlist_inpoints = list(self.model_inpoints)
            if not self.faults.get("keep_dialog_open"):
                self.pending = None
            if behavior == "unknown-after":
                raise self.original
            return True
        self.perform(step, accept)

    def run(self):
        return self.ui.refresh_third_clip_playlist()


class PlaylistRefreshTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        def sleep(_seconds):
            self.now += actions.WAIT_SECONDS + 1
        self.clock = patch.object(actions.time, "monotonic", side_effect=lambda: self.now)
        self.sleep = patch.object(actions.time, "sleep", side_effect=sleep)
        self.clock.start()
        self.sleep.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(self.sleep.stop)

    def assert_blocked_without_more_effects(self, fake):
        self.assertTrue(fake.ui.journal.blocked)
        prior = fake.ui.journal.last_failure
        count = len(fake.effects)
        with self.assertRaises(actions.GuiRejected):
            fake.run()
        self.assertEqual(len(fake.effects), count)
        self.assertIs(fake.ui.journal.last_failure, prior)

    def test_real_end_trim_pair_refreshes_last_playlist_cut_without_replacing_clip(self):
        fake = SyntheticEditor()
        # Causal baseline: the old route had no refresh, so a correct model
        # could still serialize 0,30,0. No missing-method exception is the RED.
        if callable(getattr(fake.ui, "refresh_third_clip_playlist", None)):
            result = fake.run()
        else:
            result = None
        self.assertEqual(fake.playlist_inpoints, [0, 30, 60])
        self.assertEqual(fake.model_duration, 30)
        self.assertIs(fake.clip_identity, fake.initial_identity)
        self.assertEqual([n for _, n in fake.accepts], [29, 30])
        self.assertEqual(fake.openings, 2)
        self.assertEqual(result["state"], "RESTORE_REQUEST_RETURNED_MODEL_READBACK_REQUIRED")
        self.assertTrue(result["export_readback_required"])
        for field in ("restored_duration_verified", "native_xml_playlist_verified", "native_save_verified",
                      "native_operation_retried"):
            self.assertIs(result[field], False)

    def test_second_open_reads_applied_duration_before_restore(self):
        fake = SyntheticEditor()
        result = fake.run()
        event = next(e for e in fake.events if e["step"] == PREFIX + ".shorten.applied-readback")
        self.assertTrue(event["after_new_open_following_accept"])
        self.assertTrue(event["complete"])
        self.assertEqual(event["duration_frames"], 29)
        self.assertEqual(event["expected_pid"], 41)
        self.assertTrue(result["shortened_duration_reobserved"])
        first = fake.audit.index(("effect", PREFIX + ".shorten.accept-once"))
        opened = fake.audit.index(("opened-model", 2, 29))
        retained = fake.audit.index(("custody", PREFIX + ".shorten.applied-readback",
                                    "DURATION_FIELDS_OBSERVED"))
        restore = fake.audit.index(("effect", PREFIX + ".restore.duration30"))
        self.assertLess(first, opened)
        self.assertLess(opened, retained)
        self.assertLess(retained, restore)

    def test_native_rollback_on_shorten_cannot_be_mistaken_for_applied_29(self):
        fake = SyntheticEditor(accept_behavior={1: "rollback"})
        with self.assertRaisesRegex(actions.GuiRejected, "duration_changed"):
            fake.run()
        self.assertEqual(fake.openings, 2)
        self.assertEqual(len(fake.accepts), 1)
        self.assertEqual(fake.model_duration, 30)
        self.assertFalse(any(".restore.duration30" in s for s in fake.effects))
        self.assert_blocked_without_more_effects(fake)

    def test_native_rollback_on_restore_still_requires_export_readback(self):
        fake = SyntheticEditor(accept_behavior={2: "rollback"})
        result = fake.run()
        self.assertEqual(fake.model_duration, 29)
        self.assertFalse(result["restored_duration_verified"])
        self.assertTrue(result["export_readback_required"])
        self.assertFalse(result["native_save_verified"])
        self.assertEqual(len(fake.accepts), 2)

    def test_wrong_initial_clip_ripple_or_multiple_selection_blocks_before_accept(self):
        cases = [{"initial_tuple": value} for value in (
            (59, 60, 30), (60, 59, 30), (60, 60, 29), (60, 60, "00:00:00:30"),
            (60, 60, True), (60, 60, ""))]
        cases += [{"multiple": 1}, {"ripple": 1}]
        for case in cases:
            with self.subTest(case=case):
                fake = SyntheticEditor(**case)
                with self.assertRaises(actions.GuiRejected):
                    fake.run()
                self.assertEqual(fake.accepts, [])
                self.assertFalse(any(".shorten.duration29" in s for s in fake.effects))
                self.assert_blocked_without_more_effects(fake)

    def test_changed_applied_readback_never_issues_restore(self):
        cases = [{"second_tuple": value} for value in (
            (61, 60, 29), (60, 61, 29), (60, 60, 30), (60, 60, 28))]
        cases += [{"multiple": 2}, {"ripple": 2}, {"foreign": "second"}]
        for case in cases:
            with self.subTest(case=case):
                fake = SyntheticEditor(**case)
                with self.assertRaises(actions.GuiRejected):
                    fake.run()
                self.assertEqual(len(fake.accepts), 1)
                self.assertFalse(any(".restore.duration30" in s for s in fake.effects))
                self.assert_blocked_without_more_effects(fake)

    def test_pending_fields_are_rechecked_before_each_accept(self):
        for phase, values, accepts in (("shorten", (60, 60, 28), 0),
                                        ("shorten", (61, 60, 29), 0),
                                        ("restore", (60, 59, 30), 1),
                                        ("restore", (60, 60, 31), 1)):
            with self.subTest(phase=phase, values=values):
                fake = SyntheticEditor(late_tuple=(phase, values))
                with self.assertRaises(actions.GuiRejected):
                    fake.run()
                self.assertEqual(len(fake.accepts), accepts)
                self.assert_blocked_without_more_effects(fake)

    def test_rejected_or_uncertain_first_accept_never_reopens(self):
        for behavior in ("reject", "unknown-before", "unknown-after"):
            with self.subTest(behavior=behavior):
                fake = SyntheticEditor(accept_behavior={1: behavior})
                with self.assertRaises(actions.GuiUncertain) as caught:
                    fake.run()
                self.assertEqual(fake.openings, 1)
                self.assertEqual(len(fake.accepts), 1)
                self.assertEqual(fake.effects[-1], PREFIX + ".shorten.accept-once")
                if behavior != "reject":
                    self.assertIs(caught.exception.__cause__, fake.original)
                self.assert_blocked_without_more_effects(fake)

    def test_uncertain_restore_preserves_first_error_and_stops(self):
        fake = SyntheticEditor(accept_behavior={2: "unknown-after"})
        with self.assertRaises(actions.GuiUncertain) as caught:
            fake.run()
        self.assertIs(caught.exception.__cause__, fake.original)
        self.assertIs(fake.ui.journal.last_failure["original"], fake.original)
        self.assertEqual(fake.effects[-1], PREFIX + ".restore.accept-once")
        self.assertFalse(any(e["step"] == PREFIX + ".result" for e in fake.events))
        self.assert_blocked_without_more_effects(fake)

    def test_modal_opener_failure_is_not_retried(self):
        for opening in (1, 2):
            with self.subTest(opening=opening):
                fake = SyntheticEditor(opener_unknown=opening)
                with self.assertRaises(actions.GuiUncertain) as caught:
                    fake.run()
                self.assertIs(caught.exception.__cause__, fake.original)
                self.assertEqual(fake.openings, opening)
                self.assertEqual(len(fake.accepts), opening - 1)
                self.assertTrue(fake.effects[-1].endswith(".open"))
                self.assert_blocked_without_more_effects(fake)

    def test_incomplete_owned_or_foreign_snapshot_cannot_authorize_edits(self):
        for case in ({"incomplete": "initial"}, {"foreign": "initial"}):
            with self.subTest(case=case):
                fake = SyntheticEditor(**case)
                with self.assertRaises((actions.GuiRejected, actions.GuiUncertain)):
                    fake.run()
                self.assertEqual(fake.effects, [])
                self.assert_blocked_without_more_effects(fake)

    def test_dialog_closure_requires_complete_snapshot_and_has_deadline(self):
        for case in ({"incomplete": "after-accept"}, {"keep_dialog_open": True}):
            with self.subTest(case=case):
                fake = SyntheticEditor(**case)
                with self.assertRaises(actions.GuiUncertain):
                    fake.run()
                self.assertEqual(fake.openings, 1)
                self.assertEqual(len(fake.accepts), 1)
                self.assertFalse(any(".restore." in s for s in fake.effects))
                self.assert_blocked_without_more_effects(fake)

    def test_guard_and_custody_failures_block_next_effect(self):
        cases = ({"guard_after_first_accept": True}, {"custody": "initial-fields"},
                 {"custody": "shorten-return"}, {"custody": "applied-fields"}, {"custody": "result"})
        for case in cases:
            with self.subTest(case=case):
                fake = SyntheticEditor(**case)
                with self.assertRaises((actions.GuiUncertain, OSError)):
                    fake.run()
                if case.get("custody") == "initial-fields":
                    self.assertEqual(fake.accepts, [])
                if case.get("custody") in ("shorten-return", "applied-fields"):
                    self.assertEqual(len(fake.accepts), 1)
                    self.assertFalse(any(".restore.duration30" in s for s in fake.effects))
                self.assert_blocked_without_more_effects(fake)

    def test_custody_failure_cannot_replace_first_native_failure(self):
        fake = SyntheticEditor(accept_behavior={1: "unknown-after"}, custody="error")
        with self.assertRaises(actions.GuiUncertain) as caught:
            fake.run()
        self.assertIs(caught.exception.__cause__, fake.original)
        self.assertIs(fake.ui.journal.last_failure["original"], fake.original)
        self.assertFalse(fake.ui.journal.last_failure["retained"])
        self.assertTrue(any("native_gui_journal_recording_failed:OSError" in note
                            for note in fake.original.__notes__))
        self.assertEqual(fake.openings, 1)
        self.assert_blocked_without_more_effects(fake)

    def test_duplicate_invocation_and_prior_unknown_never_repeat_effects(self):
        fake = SyntheticEditor()
        fake.run()
        effects = list(fake.effects)
        with self.assertRaisesRegex(actions.GuiRejected, "replay_refused"):
            fake.run()
        self.assertEqual(fake.effects, effects)
        self.assert_blocked_without_more_effects(fake)
        unknown = SyntheticEditor()
        unknown.ui.journal._record_failure("earlier", "ERROR_OR_UNKNOWN", unknown.original)
        self.assert_blocked_without_more_effects(unknown)
        self.assertEqual(unknown.effects, [])

    def test_unexpected_dialog_after_accept_blocks_reopen(self):
        fake = SyntheticEditor(unexpected_after_accept=True)
        with self.assertRaisesRegex(actions.GuiRejected, "unexpected_dialog"):
            fake.run()
        self.assertEqual(fake.openings, 1)
        self.assertEqual(len(fake.accepts), 1)
        self.assert_blocked_without_more_effects(fake)


if __name__ == "__main__":
    unittest.main()
