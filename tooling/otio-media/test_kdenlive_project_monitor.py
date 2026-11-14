"""Synthetic controls for the observed Qt dock tree; no GUI or Native effect.

The sibling tab-list/stack structure is derived from the authenticated
43597271263 pre-Import snapshots. No failure-time tree was retained by that run.
The paths below are deliberately symbolic, not production node coordinates.
"""
from __future__ import annotations

import copy
import itertools
import unittest
from unittest.mock import patch

import kdenlive_gui_actions as actions


PID = 41
DOCK = "app/main/project-dock"
STACK = DOCK + "/pages"
PAGE = STACK + "/project"
BAR = PAGE + "/content/controls"
CLOCK = BAR + "/clock"
TABS = DOCK + "/tabs"


def row(key, **updates):
    value = {"key": key, "name": "", "role": "filler", "text": "",
             "visible": True, "enabled": True, "editable": False,
             "rect": [20, 40, 300, 200], "focused": False, "modal": False,
             "pid": PID}
    value.update(updates)
    return value


def tabbed_rows():
    return [
        row("app", name="kdenlive", role="application"),
        row("app/main", name="Untitled \u2014 Kdenlive", role="frame"),
        row(DOCK), row(STACK, role="layered pane"), row(PAGE),
        row(PAGE + "/content"), row(BAR, role="tool bar"),
        row(CLOCK, role="spin button", editable=True, text="00:00:00:00"),
        row(STACK + "/speech", visible=False), row(STACK + "/notes", visible=False),
        row(TABS, name="Project Monitor", role="page tab list"),
        row(TABS + "/project", name="Project Monitor", role="page tab", focused=True),
        row(TABS + "/speech", name="Speech Editor", role="page tab"),
        row(TABS + "/notes", name="Project Notes", role="page tab"),
        row("app/main/clip-dock"), row("app/main/clip-dock/tabs",
            name="Clip Monitor", role="page tab list"),
        row("app/main/clip-dock/tabs/clip", name="Clip Monitor", role="page tab",
            focused=True),
        row("app/main/timeline", role="panel"),
        row("app/main/timeline/tick", role="label", text="00:00:00:00"),
    ]


def selected(rows, key):
    return next(r for r in rows if r["key"] == key)


class StopBeforeInput(BaseException):
    """A test boundary: the selected row is observed, no input is performed."""


class Boundary:
    def __init__(self, rows, *, complete=True, wrong_rows=False, custody_error=None):
        self.rows = copy.deepcopy(rows)
        self.queries = 0
        self.targets = []
        self.events = []
        self.guard_error = None
        self.custody_error = custody_error
        self.stop = StopBeforeInput("synthetic boundary before any input")
        self.ui = actions.AtspiSession.__new__(actions.AtspiSession)
        self.ui.pid = PID
        self.ui.last_snapshot = None
        self.ui.journal = actions.ActionJournal(self.emit, self.guard)

        def tree():
            self.queries += 1
            self.ui.last_snapshot = {
                "complete": complete, "rows": list(self.rows) if wrong_rows else self.rows}
            return self.rows

        def text(step, target, value):
            self.targets.append((step, copy.deepcopy(target), value))
            raise self.stop

        self.ui.tree = tree
        self.ui.text = text
        self.ui.key = self.unexpected
        self.ui.menu = self.unexpected
        self.ui.action = self.unexpected

    def guard(self):
        if self.guard_error is not None:
            raise self.guard_error

    def emit(self, event):
        if self.custody_error is not None:
            raise self.custody_error
        self.events.append(copy.deepcopy(event))

    def unexpected(self, *args, **kwargs):
        raise AssertionError("a synthetic selector test must stop before GUI input")

    def invoke(self, method):
        try:
            getattr(self.ui, method)()
        except BaseException as error:
            return error
        raise AssertionError("the test boundary was not reached")


class ProjectMonitorSelectorTests(unittest.TestCase):
    def assert_rejected_without_input(self, rows, *, method="refresh_third_clip_playlist"):
        case = Boundary(rows)
        error = case.invoke(method)
        self.assertIsInstance(error, actions.GuiRejected)
        self.assertEqual(case.targets, [])
        self.assertTrue(case.ui.journal.blocked)
        queries = case.queries
        second = case.invoke(method)
        self.assertIsInstance(second, actions.GuiRejected)
        self.assertEqual(str(second), "native_gui_previous_action_uncertain")
        self.assertEqual(case.queries, queries)
        self.assertEqual(case.targets, [])
        return error

    def test_observed_sibling_page_clock_reaches_same_boundary_in_both_routes(self):
        for method in ("refresh_third_clip_playlist", "edit_third_clip"):
            with self.subTest(method=method):
                case = Boundary(tabbed_rows())
                before = copy.deepcopy(case.rows)
                error = case.invoke(method)
                self.assertIs(error, case.stop,
                              f"expected the selected clock before input, got {error!r}")
                self.assertEqual(len(case.targets), 1)
                self.assertEqual(case.targets[0][1]["key"], CLOCK)
                self.assertEqual(case.targets[0][2], "00:00:02:15")
                self.assertEqual(case.rows, before)

    def test_multiple_named_tab_groups_or_monitors_are_ambiguous(self):
        for kind in ("second-tab-group", "second-named-panel", "second-project-tab"):
            rows = tabbed_rows()
            if kind == "second-tab-group":
                rows.extend({**r, "key": r["key"].replace(DOCK, "app/main/other-dock", 1)}
                            for r in copy.deepcopy(rows) if r["key"] == DOCK
                            or r["key"].startswith(DOCK + "/"))
            elif kind == "second-named-panel":
                rows.append(row("app/main/other-monitor", name="Project Monitor", role="panel"))
            else:
                rows.append(row(TABS + "/duplicate", name="Project Monitor", role="page tab"))
            with self.subTest(kind=kind):
                self.assert_rejected_without_input(rows)

    def test_named_tab_requires_one_visible_enabled_sibling_page(self):
        cases = ("inactive-tab", "two-focused-tabs", "two-visible-pages", "hidden-page",
                 "disabled-page", "hidden-stack", "disabled-dock", "missing-parent",
                 "wrong-stack-role", "extra-visible-sibling")
        for kind in cases:
            rows = tabbed_rows()
            if kind == "inactive-tab":
                selected(rows, TABS + "/project")["focused"] = False
            elif kind == "two-focused-tabs":
                selected(rows, TABS + "/speech")["focused"] = True
            elif kind == "two-visible-pages":
                selected(rows, STACK + "/speech")["visible"] = True
            elif kind == "hidden-page":
                selected(rows, PAGE)["visible"] = False
            elif kind == "disabled-page":
                selected(rows, PAGE)["enabled"] = False
            elif kind == "hidden-stack":
                selected(rows, STACK)["visible"] = False
            elif kind == "disabled-dock":
                selected(rows, DOCK)["enabled"] = False
            elif kind == "missing-parent":
                rows.remove(selected(rows, DOCK))
            elif kind == "wrong-stack-role":
                selected(rows, STACK)["role"] = "panel"
            else:
                rows.append(row(DOCK + "/unknown-panel", role="panel"))
            with self.subTest(kind=kind):
                self.assert_rejected_without_input(rows)

    def test_clock_must_belong_to_visible_project_page_not_global_or_other_dock(self):
        for kind in ("only-clip-clock", "hidden-page-clock", "outside-stack-clock",
                     "wrong-toolbar", "missing-ancestor", "hidden-ancestor"):
            rows = tabbed_rows()
            clock = selected(rows, CLOCK)
            if kind == "only-clip-clock":
                clock["key"] = "app/main/clip-dock/clock"
            elif kind == "hidden-page-clock":
                clock["key"] = STACK + "/speech/clock"
            elif kind == "outside-stack-clock":
                clock["key"] = DOCK + "/clock"
            elif kind == "wrong-toolbar":
                selected(rows, BAR)["role"] = "panel"
            elif kind == "missing-ancestor":
                rows.remove(selected(rows, PAGE + "/content"))
            else:
                selected(rows, PAGE + "/content")["visible"] = False
            with self.subTest(kind=kind):
                self.assert_rejected_without_input(rows)
        rows = tabbed_rows()
        rows.append(row("app/main/clip-dock/clock", role="spin button",
                        editable=True, text="00:00:05:00"))
        case = Boundary(rows)
        self.assertIs(case.invoke("refresh_third_clip_playlist"), case.stop)
        self.assertEqual(case.targets[0][1]["key"], CLOCK)

    def test_multiple_clocks_foreign_pid_or_invalid_row_cannot_select(self):
        cases = ("two-clocks", "foreign-pid", "boolean-pid", "invalid-clock",
                 "hidden-clock", "disabled-clock", "uneditable-clock",
                 "wrong-clock-role", "duplicate-key", "extra-field")
        for kind in cases:
            rows = tabbed_rows()
            clock = selected(rows, CLOCK)
            if kind == "two-clocks":
                rows.append({**clock, "key": BAR + "/second-clock"})
            elif kind == "foreign-pid":
                clock["pid"] = 99
            elif kind == "boolean-pid":
                clock["pid"] = True
            elif kind == "invalid-clock":
                clock["text"] = "00:00:00:30"
            elif kind == "hidden-clock":
                clock["visible"] = False
            elif kind == "disabled-clock":
                clock["enabled"] = False
            elif kind == "uneditable-clock":
                clock["editable"] = False
            elif kind == "wrong-clock-role":
                clock["role"] = "text"
            elif kind == "duplicate-key":
                rows.append(copy.deepcopy(clock))
            else:
                clock["invented_owner"] = True
            for method in ("refresh_third_clip_playlist", "edit_third_clip"):
                with self.subTest(kind=kind, method=method):
                    self.assert_rejected_without_input(rows, method=method)

    def test_incomplete_snapshot_or_row_identity_never_authorizes_selection(self):
        for method in ("refresh_third_clip_playlist", "edit_third_clip"):
            for wrong_rows in (False, True):
                with self.subTest(method=method, wrong_rows=wrong_rows):
                    case = Boundary(tabbed_rows(), complete=wrong_rows, wrong_rows=wrong_rows)
                    clock = itertools.count(0.0, 10.0)
                    with patch.object(actions.time, "monotonic", side_effect=lambda: next(clock)):
                        error = case.invoke(method)
                    self.assertIsInstance(error, actions.GuiRejected if wrong_rows
                                          else actions.GuiUncertain)
                    self.assertEqual(case.targets, [])
                    self.assertTrue(case.ui.journal.blocked)
                    queries = case.queries
                    self.assertIsInstance(case.invoke(method), actions.GuiRejected)
                    self.assertEqual(case.queries, queries)

    def test_direct_named_panel_remains_scoped_and_shared_without_input(self):
        rows = [row("app", role="application"), row("app/main", role="frame"),
                row("app/main/monitor", name="Project Monitor", role="panel"),
                row("app/main/monitor/time", role="text", editable=True, text="00:00:01:00"),
                row("app/main/other", role="panel"),
                row("app/main/other/time", role="text", editable=True, text="00:00:08:00")]
        for method in ("refresh_third_clip_playlist", "edit_third_clip"):
            with self.subTest(method=method):
                case = Boundary(rows)
                self.assertIs(case.invoke(method), case.stop)
                self.assertEqual(case.targets[0][1]["key"], "app/main/monitor/time")

    def test_selection_failure_custody_or_prior_unknown_preserves_original_and_blocks(self):
        for method in ("refresh_third_clip_playlist", "edit_third_clip"):
            with self.subTest(method=method):
                rows = tabbed_rows()
                selected(rows, TABS + "/project")["focused"] = False
                case = Boundary(rows, custody_error=OSError("synthetic custody failure"))
                error = case.invoke(method)
                self.assertIsInstance(error, actions.GuiRejected)
                self.assertIs(case.ui.journal.last_failure["original"], error)
                self.assertFalse(case.ui.journal.last_failure["retained"])
                self.assertTrue(any("journal_recording_failed" in n
                                    for n in getattr(error, "__notes__", [])))
                self.assertTrue(case.ui.journal.blocked)
                self.assertEqual(case.targets, [])
                prior = case.ui.journal.last_failure
                self.assertIsInstance(case.invoke(method), actions.GuiRejected)
                self.assertIs(case.ui.journal.last_failure, prior)
                guarded = Boundary(tabbed_rows())
                guarded.guard_error = RuntimeError("synthetic PID/starttime guard changed")
                self.assertIs(guarded.invoke(method), guarded.guard_error)
                self.assertTrue(guarded.ui.journal.blocked)
                self.assertEqual(guarded.targets, [])

        rows = tabbed_rows()
        rows.append(row("app/main/sequence", name="Sequence", role="menu"))
        rows.append(row("app/main/sequence/current", name="Current Item",
                        role="menu item", visible=False))
        snapshot = {"complete": True, "rows": rows}
        retained = actions.observed_menu_failure_tree(snapshot, PID)
        self.assertTrue(retained["complete"])
        self.assertEqual(retained["menu_row_count"], 2)
        self.assertEqual({r["name"] for r in retained["menu_rows"]},
                         {"Sequence", "Current Item"})
        self.assertFalse(retained["semantic_effect_verified"]
                         if "semantic_effect_verified" in retained
                         else retained["reconciliation_granted"])
        self.assertFalse(actions.observed_menu_failure_tree(
            {"complete": False, "rows": rows}, PID)["complete"])
        foreign = copy.deepcopy(rows)
        selected(foreign, CLOCK)["pid"] = 99
        self.assertFalse(actions.observed_menu_failure_tree(
            {"complete": True, "rows": foreign}, PID)["complete"])

        events = []
        ui = actions.AtspiSession.__new__(actions.AtspiSession)
        ui.pid = PID
        ui.last_snapshot = snapshot
        ui.journal = actions.ActionJournal(events.append, lambda: None)
        ui.find = lambda *_args, **_kwargs: None
        ui.action = lambda *_args: self.fail("no menu action after unknown")
        with patch.object(actions.time, "monotonic",
                          side_effect=itertools.count(0.0, 30.0)):
            with self.assertRaises(actions.GuiUncertain) as caught:
                ui.control("synthetic.menu.current", ("Current Item",),
                           ("menu", "menu item"))
        self.assertEqual(str(caught.exception),
                         "synthetic.menu.current.discover")
        self.assertTrue(ui.journal.blocked)
        self.assertEqual([e["state"] for e in events],
                         ["TIMED_OUT_UNKNOWN", "READ_ONLY_FAILURE_CUSTODY"])
        self.assertFalse(events[-1]["native_operation_retried"])
        self.assertFalse(events[-1]["semantic_effect_verified"])
        self.assertEqual(events[-1]["observed"], retained)
        self.assertEqual(events[-1]["requested_names"], ["Current Item"])

    def test_refresh_keeps_real_control_flow_with_synthetic_tabbed_dock(self):
        from test_kdenlive_playlist_refresh import SyntheticEditor
        case = SyntheticEditor()
        original_tree = case.ui.tree

        def tree():
            rows = original_tree()
            dock = tabbed_rows()
            selected(dock, CLOCK)["text"] = case.seek
            # Preserve the existing synthetic Duration controls and model.
            rows = dock + [r for r in rows if not r["key"].startswith("app/main")]
            case.ui.last_snapshot = {"complete": True, "rows": rows}
            return rows

        original_text = case.ui.text

        def text(step, target, value):
            if target["key"] == CLOCK:
                def update_seek():
                    case.seek = value
                    return True
                case.perform(step, update_seek)
            else:
                original_text(step, target, value)

        case.ui.tree = tree
        case.ui.text = text
        result = case.ui.refresh_third_clip_playlist()
        self.assertEqual(case.playlist_inpoints, [0, 30, 60])
        self.assertEqual(case.model_duration, 30)
        self.assertEqual(result["accept_attempts"], 2)
        self.assertTrue(result["shortened_duration_reobserved"])
        self.assertFalse(result["restored_duration_verified"])
        self.assertFalse(result["native_save_verified"])
        self.assertFalse(result["native_xml_playlist_verified"])


if __name__ == "__main__":
    unittest.main()
