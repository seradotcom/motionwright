"""Offline modal readback controls; synthetic GI is never physical evidence."""
from __future__ import annotations

import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import kdenlive_gui_actions as actions


NO_REPLY = (
    "Did not receive a reply. Possible causes include: the remote application "
    "did not send a reply, the message bus security policy blocked the reply, "
    "the reply timeout expired, or the network connection was broken."
)
PROFILES = {
    "otio_import": ("OpenTimelineIO Import...", "OpenTimelineIO Import"),
    "save_as": ("Save As...", "Save As"),
    "otio_export": ("OpenTimelineIO Export...", "OpenTimelineIO Export"),
    "duration": ("Edit Duration", "Duration"),
    "delayed_render": ("Generate Script", "Delayed Rendering"),
    "discard_preferences": ("Quit", "Warning"),
}


class SyntheticGlibError(Exception):
    """Explicitly injected stand-in, not a claimed live GI exception."""

    def __init__(self, message=NO_REPLY, *, domain="atspi_error", code=1):
        super().__init__(message)
        self.message = message
        self.domain = domain
        self.code = code


def row(key, name, role, *, modal=False, enabled=True, visible=True, pid=41):
    return {
        "key": key, "name": name, "role": role, "pid": pid,
        "visible": visible, "enabled": enabled, "modal": modal,
        "focused": False, "editable": False, "text": "", "rect": [0, 0, 20, 20],
    }


def trees(kind="otio_import", source_name="initial.kdenlive"):
    action_name, title = PROFILES[kind]
    before = [row("app/main", "Kdenlive", "frame")]
    if kind == "delayed_render":
        before.append(row("app/render", "Rendering", "dialog"))
        target = row("app/render/generate", action_name, "push button")
    else:
        target = row("app/main/target", action_name, "menu item")
    before.append(target)
    after = copy.deepcopy(before)
    if kind == "delayed_render":
        # QInputDialog::getText(nullptr, ...) creates a sibling, not a child.
        after[1]["enabled"] = False
    dialog = row("app/modal", title, "dialog", modal=True)
    after.append(dialog)
    if kind == "discard_preferences":
        after.append(row(
            "app/modal/prompt",
            'The project <b>"' + source_name
            + '"</b> has been changed.\nDo you want to save your changes?',
            "label",
        ))
        for index, name in enumerate(("Save", "Don't Save", "Cancel")):
            after.append(row("app/modal/button" + str(index), name, "push button"))
    return before, after, target


def snapshot(rows, *, complete=True, same_object=True, advance=0.0):
    return {
        "rows": rows, "complete": complete, "same_object": same_object,
        "advance": advance,
    }


class Scenario:
    """One controlled effect plus retained, read-only complete-tree observations."""

    def __init__(self, kind="otio_import", *, source_name="initial.kdenlive"):
        self.kind = kind
        self.source_name = source_name if kind == "discard_preferences" else None
        self.before, self.after, self.target = trees(kind, source_name)
        self.snapshots = [snapshot(self.before), snapshot(self.after)]
        self.query_count = 0
        self.now = 0.0
        self.events = []
        self.emission_states = []
        self.emit_hook = None
        self.guard_hook = None
        self.original = SyntheticGlibError()
        self.response = self.original
        self.effect = Mock(side_effect=self._effect)
        self.guard = Mock(side_effect=self._guard)
        self.journal = actions.ActionJournal(self._emit, self.guard)
        self.session = actions.AtspiSession.__new__(actions.AtspiSession)
        self.session.pid = 41
        self.session.glib_error_type = SyntheticGlibError
        self.session.journal = self.journal
        self.session.last_snapshot = None
        self.session.nodes = {}
        self.interface = SimpleNamespace(
            get_n_actions=Mock(return_value=1),
            get_action_name=Mock(return_value="press"),
            do_action=self.effect,
        )
        self.node = SimpleNamespace(
            get_process_id=Mock(return_value=41),
            get_action_iface=Mock(return_value=self.interface),
        )
        self.session.tree = Mock(side_effect=self._tree)

    def _guard(self):
        if self.guard_hook:
            self.guard_hook(self)

    def _emit(self, event):
        self.emission_states.append((event["state"], self.journal.blocked))
        if self.emit_hook:
            self.emit_hook(event, self)
        self.events.append(copy.deepcopy(event))

    def _effect(self, index):
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response

    def _tree(self):
        current = self.snapshots[min(self.query_count, len(self.snapshots) - 1)]
        self.query_count += 1
        self.now += current["advance"]
        rows = current["rows"]
        self.session.last_snapshot = {
            "complete": current["complete"],
            "rows": rows if current["same_object"] else list(rows),
        }
        self.session.nodes = {
            self.target["key"]: self.node,
        }
        return rows

    def sleep(self, seconds):
        self.now += seconds

    def run(self, step="open.once"):
        with patch.object(actions.time, "monotonic", side_effect=lambda: self.now), \
             patch.object(actions.time, "monotonic_ns",
                          side_effect=lambda: int(self.now * 1_000_000_000)), \
             patch.object(actions.time, "sleep", side_effect=self.sleep):
            return self.session.modal_action(step, self.kind, source_name=self.source_name)


class ModalReadbackTests(unittest.TestCase):
    def assert_blocked(self, scenario):
        self.assertTrue(scenario.journal.blocked)
        next_effect = Mock()
        with self.assertRaisesRegex(actions.GuiRejected, "previous_action_uncertain"):
            scenario.journal.perform("forbidden.second", {}, next_effect)
        next_effect.assert_not_called()

    def assert_original_unknown(self, scenario, caught):
        self.assertIs(caught.exception.__cause__, scenario.original)
        unknown = [event for event in scenario.events
                   if event["state"] == "ERROR_OR_UNKNOWN"]
        self.assertEqual(len(unknown), 1)
        self.assertFalse(unknown[0]["replayed"])

    def test_closed_profiles_reconcile_one_effect_after_two_retained_trees(self):
        for kind in PROFILES:
            with self.subTest(kind=kind):
                scenario = Scenario(kind)
                dialog = scenario.run()
                self.assertEqual(dialog["key"], "app/modal")
                scenario.effect.assert_called_once_with(0)
                self.assertEqual(scenario.query_count, 2)
                self.assertFalse(scenario.journal.blocked)
                self.assertIn("open.once", scenario.journal.used)
                unknown = [e for e in scenario.events if e["state"] == "ERROR_OR_UNKNOWN"]
                self.assertEqual(len(unknown), 1)
                before = [e for e in scenario.events if e["step"].endswith(".modal-before")]
                after = [e for e in scenario.events if e["state"] == "MODAL_OPEN_RECONCILED"]
                self.assertEqual(len(before), 1)
                self.assertEqual(len(after), 1)
                self.assertEqual(before[0]["rows"], scenario.before)
                self.assertEqual(after[0]["rows"], scenario.after)
                self.assertEqual(after[0]["request_response"], "NO_REPLY")
                self.assertEqual(after[0]["verified_effect"], "owned_dialog_open_only")
                self.assertFalse(after[0]["native_save_or_import_verified"])
                self.assertFalse(after[0]["native_operation_retried"])
                self.assertTrue(all(blocked for state, blocked in scenario.emission_states
                                    if state in ("OBSERVED", "MODAL_OPEN_RECONCILED")))

    def test_returned_request_still_requires_fresh_readback_and_custody(self):
        scenario = Scenario()
        scenario.response = True
        dialog = scenario.run()
        self.assertEqual(dialog["key"], "app/modal")
        self.assertEqual(scenario.query_count, 2)
        scenario.effect.assert_called_once_with(0)
        self.assertEqual([e["state"] for e in scenario.events], [
            "OBSERVED", "INTENT", "REQUEST_RETURNED", "MODAL_OPEN_OBSERVED",
        ])
        self.assertFalse(scenario.events[-1]["native_save_or_import_verified"])

    def test_unretained_original_error_cannot_unlock_modal_readback(self):
        scenario = Scenario()
        def reject_original(event, _):
            if event["state"] == "ERROR_OR_UNKNOWN":
                raise OSError("original error custody unavailable")
        scenario.emit_hook = reject_original
        with self.assertRaises(actions.GuiUncertain) as caught:
            scenario.run()
        self.assertIs(caught.exception.__cause__, scenario.original)
        scenario.effect.assert_called_once_with(0)
        self.assertFalse(any(e["state"] == "MODAL_OPEN_RECONCILED"
                             for e in scenario.events))
        self.assert_blocked(scenario)

    def test_preexisting_or_ambiguous_modal_blocks_every_later_effect(self):
        for title in ("OpenTimelineIO Import", "Unexpected warning"):
            with self.subTest(title=title):
                scenario = Scenario()
                scenario.before.append(row("app/prior", title, "dialog", modal=True))
                with self.assertRaises(actions.GuiRejected):
                    scenario.run()
                scenario.effect.assert_not_called()
                self.assert_blocked(scenario)

    def test_only_authentic_exact_no_reply_is_eligible(self):
        errors = [
            RuntimeError(NO_REPLY),
            SyntheticGlibError(domain="g-io-error-quark"),
            SyntheticGlibError(code=2),
            SyntheticGlibError(code=True),
            SyntheticGlibError(code="1"),
            SyntheticGlibError(NO_REPLY + " "),
            SyntheticGlibError("No such object path '/org/a11y/atspi/accessible/1'"),
        ]
        for error in errors:
            with self.subTest(type=type(error).__name__, attrs=vars(error)):
                scenario = Scenario()
                scenario.response = error
                with self.assertRaises(actions.GuiUncertain) as caught:
                    scenario.run()
                self.assertIs(caught.exception.__cause__, error)
                scenario.effect.assert_called_once_with(0)
                self.assertEqual(scenario.query_count, 1)
                self.assert_blocked(scenario)

    def test_false_action_response_is_not_modal_reconciliation(self):
        scenario = Scenario()
        scenario.response = False
        with self.assertRaises(actions.GuiUncertain) as caught:
            scenario.run()
        self.assertIsInstance(caught.exception.__cause__, actions.GuiRejected)
        scenario.effect.assert_called_once_with(0)
        self.assertEqual(scenario.query_count, 1)
        self.assert_blocked(scenario)

    def test_partial_snapshots_never_admit_effect_or_unlock(self):
        scenario = Scenario()
        scenario.snapshots = [
            snapshot([], complete=False),
            snapshot(scenario.before),
            snapshot([], complete=False),
            snapshot(scenario.after),
        ]
        observed_effect_counts = []
        original_tree = scenario.session.tree.side_effect
        def query():
            observed_effect_counts.append(scenario.effect.call_count)
            return original_tree()
        scenario.session.tree.side_effect = query
        self.assertEqual(scenario.run()["key"], "app/modal")
        self.assertEqual(observed_effect_counts, [0, 0, 1, 1])
        scenario.effect.assert_called_once_with(0)
        self.assertFalse(scenario.journal.blocked)

    def test_snapshot_binding_and_size_failures_preserve_original(self):
        for failure in ("copied-list", "foreign-pid", "oversize", "nonfinite"):
            with self.subTest(failure=failure):
                scenario = Scenario()
                after = copy.deepcopy(scenario.after)
                current = snapshot(after)
                if failure == "copied-list":
                    current["same_object"] = False
                elif failure == "foreign-pid":
                    after[-1]["pid"] = 42
                elif failure == "oversize":
                    after[0]["text"] = "x" * (128 * 1024)
                else:
                    after[0]["rect"][0] = float("nan")
                scenario.snapshots[-1] = current
                with self.assertRaises(actions.GuiUncertain) as caught:
                    scenario.run()
                self.assert_original_unknown(scenario, caught)
                scenario.effect.assert_called_once_with(0)
                self.assert_blocked(scenario)

    def test_invalid_before_snapshot_never_dispatches(self):
        for failure in ("copied-list", "foreign-pid", "oversize", "incomplete-deadline"):
            with self.subTest(failure=failure):
                scenario = Scenario()
                current = scenario.snapshots[0]
                if failure == "copied-list":
                    current["same_object"] = False
                elif failure == "foreign-pid":
                    scenario.before[0]["pid"] = 42
                elif failure == "oversize":
                    scenario.before[0]["text"] = "x" * (128 * 1024)
                else:
                    current["complete"] = False
                    current["advance"] = 31.0
                with self.assertRaises((actions.GuiRejected, actions.GuiUncertain)):
                    scenario.run()
                scenario.effect.assert_not_called()
                self.assert_blocked(scenario)

    def test_dispatch_precondition_failures_block_every_later_effect(self):
        for failure in ("accessible-pid", "missing-action", "ambiguous-action", "guard"):
            with self.subTest(failure=failure):
                scenario = Scenario()
                if failure == "accessible-pid":
                    scenario.node.get_process_id.return_value = 42
                elif failure == "missing-action":
                    scenario.node.get_action_iface.return_value = None
                elif failure == "ambiguous-action":
                    scenario.interface.get_n_actions.return_value = 2
                else:
                    def guard(current):
                        if current.guard.call_count == 2:
                            raise RuntimeError("identity changed before dispatch")
                    scenario.guard_hook = guard
                with self.assertRaises((actions.GuiRejected, RuntimeError)):
                    scenario.run()
                scenario.effect.assert_not_called()
                self.assert_blocked(scenario)

    def test_wrong_or_ambiguous_postcondition_never_unlocks(self):
        for failure in ("two-expected", "unexpected-dialog", "disabled", "not-modal", "hidden"):
            with self.subTest(failure=failure):
                scenario = Scenario()
                if failure == "two-expected":
                    scenario.after.append(row("app/second", "OpenTimelineIO Import",
                                              "dialog", modal=True))
                elif failure == "unexpected-dialog":
                    scenario.after.append(row("app/error", "Error", "dialog", modal=True))
                elif failure == "disabled":
                    scenario.after[-1]["enabled"] = False
                elif failure == "not-modal":
                    scenario.after[-1]["modal"] = False
                else:
                    scenario.after[-1]["visible"] = False
                with self.assertRaises(actions.GuiUncertain) as caught:
                    scenario.run()
                self.assert_original_unknown(scenario, caught)
                scenario.effect.assert_called_once_with(0)
                self.assert_blocked(scenario)

    def test_no_modal_or_late_complete_readback_does_not_extend_budget(self):
        for late in (False, True):
            with self.subTest(late=late):
                scenario = Scenario()
                scenario.snapshots[-1] = (
                    snapshot(scenario.after, advance=31.0) if late
                    else snapshot(copy.deepcopy(scenario.before))
                )
                with self.assertRaises(actions.GuiUncertain) as caught:
                    scenario.run()
                self.assert_original_unknown(scenario, caught)
                self.assertGreaterEqual(scenario.now, 30.0)
                self.assertLess(scenario.now, 31.1)
                scenario.effect.assert_called_once_with(0)
                self.assert_blocked(scenario)

    def test_readback_identity_guard_failure_keeps_first_no_reply(self):
        scenario = Scenario()
        def guard(current):
            if current.effect.call_count:
                raise RuntimeError("owned process identity changed")
        scenario.guard_hook = guard
        with self.assertRaises(actions.GuiUncertain) as caught:
            scenario.run()
        self.assert_original_unknown(scenario, caught)
        self.assertTrue(any("owned process identity changed" in note
                            for note in caught.exception.__notes__))
        scenario.effect.assert_called_once_with(0)
        self.assert_blocked(scenario)

    def test_before_or_readback_custody_failure_never_allows_next_effect(self):
        for failed_state, expected_count in (("OBSERVED", 0),
                                             ("INTENT", 0),
                                             ("MODAL_OPEN_RECONCILED", 1)):
            with self.subTest(failed_state=failed_state):
                scenario = Scenario()
                def fail(event, _, wanted=failed_state):
                    if event["state"] == wanted:
                        raise OSError("snapshot custody unavailable")
                scenario.emit_hook = fail
                with self.assertRaises((OSError, actions.GuiUncertain)) as caught:
                    scenario.run()
                self.assertEqual(scenario.effect.call_count, expected_count)
                if expected_count:
                    self.assert_original_unknown(scenario, caught)
                self.assert_blocked(scenario)

    def test_post_custody_guard_or_deadline_failure_cannot_unlock(self):
        for failure in ("guard", "deadline"):
            with self.subTest(failure=failure):
                scenario = Scenario()
                def after_record(event, current):
                    if event["state"] == "MODAL_OPEN_RECONCILED" and failure == "deadline":
                        current.now += 31.0
                def guard(current):
                    if failure == "guard" and any(
                            e["state"] == "MODAL_OPEN_RECONCILED" for e in current.events):
                        raise RuntimeError("owned process changed after custody")
                scenario.emit_hook = after_record
                scenario.guard_hook = guard
                with self.assertRaises(actions.GuiUncertain) as caught:
                    scenario.run()
                self.assert_original_unknown(scenario, caught)
                scenario.effect.assert_called_once_with(0)
                self.assert_blocked(scenario)

    def test_delayed_dialog_is_sibling_but_button_requires_rendering_scope(self):
        scenario = Scenario("delayed_render")
        self.assertFalse(scenario.after[-1]["key"].startswith("app/render/"))
        self.assertEqual(scenario.run()["name"], "Delayed Rendering")
        scenario.effect.assert_called_once_with(0)
        for failure in ("missing-parent", "duplicate-parent", "foreign-scope"):
            with self.subTest(failure=failure):
                scenario = Scenario("delayed_render")
                if failure == "missing-parent":
                    del scenario.before[1]
                elif failure == "duplicate-parent":
                    scenario.before.append(row("app/other-render", "Rendering", "dialog"))
                else:
                    scenario.target["key"] = "app/main/generate"
                with self.assertRaises(actions.GuiRejected):
                    scenario.run()
                scenario.effect.assert_not_called()
                self.assert_blocked(scenario)

    def test_discard_preferences_requires_exact_source_prompt_and_three_buttons(self):
        for source in ("initial.kdenlive", "edited.kdenlive"):
            scenario = Scenario("discard_preferences", source_name=source)
            self.assertEqual(scenario.run()["key"], "app/modal")
            scenario.effect.assert_called_once_with(0)
        for failure in ("wrong-source", "wrong-prompt", "missing-button", "duplicate-button"):
            with self.subTest(failure=failure):
                scenario = Scenario("discard_preferences")
                label = next(r for r in scenario.after if r["role"] == "label")
                if failure == "wrong-source":
                    label["name"] = label["name"].replace("initial.kdenlive", "unrelated.kdenlive")
                elif failure == "wrong-prompt":
                    label["name"] = "initial.kdenlive: Rendering jobs are still running."
                elif failure == "missing-button":
                    scenario.after.pop()
                else:
                    scenario.after.append(row("app/modal/duplicate", "Save", "push button"))
                with self.assertRaises(actions.GuiUncertain) as caught:
                    scenario.run()
                self.assert_original_unknown(scenario, caught)
                scenario.effect.assert_called_once_with(0)
                self.assert_blocked(scenario)

    def test_replay_or_prior_uncertainty_never_dispatches_again(self):
        scenario = Scenario()
        scenario.run()
        scenario.query_count = 0
        with self.assertRaises(actions.GuiRejected):
            scenario.run()
        scenario.effect.assert_called_once_with(0)
        scenario = Scenario()
        scenario.journal.blocked = True
        with self.assertRaises(actions.GuiRejected):
            scenario.run()
        scenario.effect.assert_not_called()
        scenario.session.tree.assert_not_called()

    def test_only_literal_menu_paths_use_modal_opening(self):
        expected = {
            ("File", "OpenTimelineIO Import..."): "otio_import",
            ("File", "Save As..."): "save_as",
            ("File", "OpenTimelineIO Export..."): "otio_export",
            ("Sequence", "Current Item", "Edit Duration"): "duration",
        }
        for path, kind in expected.items():
            with self.subTest(path=path):
                session = actions.AtspiSession.__new__(actions.AtspiSession)
                session.control = Mock()
                session.modal_action = Mock()
                session.menu("route", *path)
                session.modal_action.assert_called_once_with(
                    "route." + str(len(path) - 1), kind,
                )
                self.assertEqual(session.control.call_count, len(path) - 1)
        for path in (("File", "Render..."), ("File", "Quit"),
                     ("Other", "Save As..."), ("File", "OpenTimelineIO Import")):
            with self.subTest(path=path):
                session = actions.AtspiSession.__new__(actions.AtspiSession)
                session.control = Mock()
                session.modal_action = Mock()
                session.menu("route", *path)
                session.modal_action.assert_not_called()
                self.assertEqual(session.control.call_count, len(path))

    def test_unknown_profiles_or_unbound_source_names_never_dispatch(self):
        for kind, source in (("arbitrary", None), ("save_as", "initial.kdenlive"),
                             ("discard_preferences", None),
                             ("discard_preferences", "other.kdenlive")):
            with self.subTest(kind=kind, source=source):
                scenario = Scenario()
                scenario.kind = kind
                scenario.source_name = source
                with self.assertRaises(actions.GuiRejected):
                    scenario.run()
                scenario.effect.assert_not_called()
                scenario.session.tree.assert_not_called()


if __name__ == "__main__":
    unittest.main()
