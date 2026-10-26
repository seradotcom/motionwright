"""Offline init/discovery diagnostics; no physical cause reproduction."""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import kdenlive_gui_actions as actions


def fake_desktop(children):
    return SimpleNamespace(
        get_child_count=Mock(return_value=len(children)),
        get_child_at_index=Mock(side_effect=lambda index: children[index]),
    )


def fake_api(code=0, desktops=()):
    return SimpleNamespace(
        init=Mock(return_value=code),
        set_timeout=Mock(),
        get_desktop=Mock(side_effect=list(desktops)),
        StateType=SimpleNamespace(SHOWING=1, ENABLED=2, SENSITIVE=3, FOCUSED=4),
        CoordType=SimpleNamespace(SCREEN=0),
    )


def fake_foreign(pid=42):
    return SimpleNamespace(
        get_process_id=Mock(return_value=pid),
        get_state_set=Mock(side_effect=AssertionError("foreign accessible walked")),
    )


def fake_owned():
    return SimpleNamespace(
        get_process_id=Mock(return_value=41),
        get_state_set=Mock(return_value=SimpleNamespace(contains=lambda state: False)),
        get_role_name=Mock(return_value="application"),
        get_editable_text_iface=Mock(return_value=None),
        get_text_iface=Mock(return_value=None),
        get_component_iface=Mock(return_value=None),
        get_name=Mock(return_value="owned"),
        get_child_count=Mock(return_value=0),
    )


def make_session(api, journal):
    gi = ModuleType("gi")
    gi.__path__ = []
    gi.require_version = Mock()
    repository = ModuleType("gi.repository")
    repository.Atspi = api
    gi.repository = repository
    with patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}):
        instance = actions.AtspiSession(41, journal)
    gi.require_version.assert_called_once_with("Atspi", "2.0")
    return instance


class ManualQueryClock:
    """Clock reads are inert; the observed query and sleep advance time."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def after_queries(self, query, elapsed):
        durations = iter(elapsed)
        def observed():
            result = query()
            self.now += next(durations)
            return result
        return observed


class AccessibilityDiscoveryDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.guard = Mock()
        self.journal = actions.ActionJournal(self.events.append, self.guard)

    def test_init_accepts_success_and_already_initialized(self):
        for code in (0, 1):
            with self.subTest(code=code):
                api = fake_api(code)
                instance = make_session(api, self.journal)
                self.assertEqual(instance.init_returncode, code)
                self.assertEqual(instance.discovery_count, 0)
                api.init.assert_called_once_with()
                api.set_timeout.assert_called_once_with(1000, 3000)
                api.get_desktop.assert_not_called()
        self.assertEqual(self.events, [])

    def test_init_error_is_retained_before_discovery(self):
        api = fake_api(2)
        with self.assertRaisesRegex(actions.GuiRejected, "accessibility_init_failed"):
            make_session(api, self.journal)
        api.init.assert_called_once_with()
        api.set_timeout.assert_not_called()
        api.get_desktop.assert_not_called()
        self.assertTrue(self.journal.blocked)
        diagnostic = self.events[-1]
        self.assertEqual(diagnostic["init_returncode"], 2)
        self.assertFalse(diagnostic["init_admitted"])
        self.assertEqual(diagnostic["expected_pid"], 41)
        self.assertEqual(diagnostic["discovery_count"], 0)
        self.assertIsNone(diagnostic["first_discovery"])
        self.assertIsNone(diagnostic["last_discovery"])
        self.assertFalse(diagnostic["native_operation_retried"])

    def test_diagnostic_recording_error_preserves_original_init_failure(self):
        journal = actions.ActionJournal(Mock(side_effect=OSError("private recorder unavailable")),
                                        Mock())
        api = fake_api(2)
        with self.assertRaisesRegex(actions.GuiRejected, "accessibility_init_failed") as caught:
            make_session(api, journal)
        self.assertIn("discovery_diagnostic_recording_failed:OSError", caught.exception.__notes__)
        self.assertTrue(journal.blocked)
        api.set_timeout.assert_not_called()
        api.get_desktop.assert_not_called()

    def test_noninteger_init_is_rejected_without_discovery(self):
        for code in (True, None, "0"):
            with self.subTest(code=code):
                events = []
                journal = actions.ActionJournal(events.append, Mock())
                api = fake_api(code)
                with self.assertRaises(actions.GuiRejected):
                    make_session(api, journal)
                api.get_desktop.assert_not_called()
                self.assertIsNone(events[-1]["init_returncode"])
                self.assertFalse(events[-1]["init_admitted"])
                self.assertTrue(journal.blocked)

    def test_foreign_pid_times_out_without_walk_or_action(self):
        foreign = fake_foreign()
        api = fake_api(desktops=[fake_desktop([foreign])])
        instance = make_session(api, self.journal)
        clock = ManualQueryClock()
        with patch.object(actions.time, "monotonic", side_effect=clock.monotonic), \
             patch.object(actions.time, "sleep", side_effect=clock.sleep) as slept, \
             patch.object(instance, "tree",
                          side_effect=clock.after_queries(instance.tree, (31.0,))) as query:
            with self.assertRaisesRegex(actions.GuiUncertain, "import.accessibility-ready"):
                instance.wait_ready("import")
        api.get_desktop.assert_called_once_with(0)
        self.assertEqual(query.call_count, 1)
        self.assertEqual(slept.call_count, 0)
        foreign.get_process_id.assert_called_once_with()
        foreign.get_state_set.assert_not_called()
        diagnostic = self.events[-1]
        self.assertEqual(diagnostic["discovery_count"], 1)
        self.assertEqual(diagnostic["last_discovery"]["observed_pids"], [42])
        self.assertEqual(diagnostic["last_discovery"]["owned_application_count"], 0)
        self.assertEqual(diagnostic["first_discovery"], diagnostic["last_discovery"])
        effect = Mock()
        with self.assertRaises(actions.GuiRejected):
            self.journal.perform("must-not-run", {}, effect)
        effect.assert_not_called()
        self.assertEqual(self.events[-2]["state"], "TIMED_OUT_UNKNOWN")

    def test_failure_keeps_first_and_last_existing_queries(self):
        foreign = fake_foreign()
        api = fake_api(desktops=[fake_desktop([]), fake_desktop([foreign])])
        instance = make_session(api, self.journal)
        clock = ManualQueryClock()
        with patch.object(actions.time, "monotonic", side_effect=clock.monotonic), \
             patch.object(actions.time, "sleep", side_effect=clock.sleep) as slept, \
             patch.object(instance, "tree",
                          side_effect=clock.after_queries(instance.tree, (0.0, 31.0))) as query:
            with self.assertRaises(actions.GuiUncertain):
                instance.wait_ready("import")
        self.assertEqual(api.get_desktop.call_count, 2)
        self.assertEqual(query.call_count, 2)
        self.assertEqual(slept.call_count, 1)
        diagnostic = self.events[-1]
        self.assertEqual(diagnostic["discovery_count"], 2)
        self.assertEqual(diagnostic["first_discovery"]["desktop_child_count"], 0)
        self.assertEqual(diagnostic["first_discovery"]["observed_pids"], [])
        self.assertEqual(diagnostic["last_discovery"]["desktop_child_count"], 1)
        self.assertEqual(diagnostic["last_discovery"]["observed_pids"], [42])
        self.assertTrue(diagnostic["init_admitted"])
        self.assertEqual(diagnostic["init_returncode"], 0)
        self.assertTrue(self.journal.blocked)

    def test_exact_pid_discovery_succeeds(self):
        api = fake_api(desktops=[fake_desktop([fake_owned()])])
        instance = make_session(api, self.journal)
        rows = instance.wait_ready("import")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pid"], 41)
        self.assertEqual(instance.last_discovery["owned_application_count"], 1)
        self.assertEqual(self.events, [])
        self.assertFalse(self.journal.blocked)
        api.get_desktop.assert_called_once_with(0)

    def test_duplicate_owned_pid_remains_rejected(self):
        api = fake_api(desktops=[fake_desktop([fake_foreign(41), fake_foreign(41)])])
        instance = make_session(api, self.journal)
        with self.assertRaisesRegex(actions.GuiRejected, "owned_application_not_unique"):
            instance.wait_ready("import")
        self.assertTrue(self.journal.blocked)
        self.assertEqual(self.events[-1]["last_discovery"]["owned_application_count"], 2)
        api.get_desktop.assert_called_once_with(0)

    def test_desktop_budget_remains_enforced(self):
        desktop = fake_desktop([])
        desktop.get_child_count.return_value = actions.MAX_CHILDREN + 1
        api = fake_api(desktops=[desktop])
        instance = make_session(api, self.journal)
        with self.assertRaisesRegex(actions.GuiRejected, "desktop_budget"):
            instance.wait_ready("import")
        desktop.get_child_at_index.assert_not_called()
        api.get_desktop.assert_called_once_with(0)
        self.assertEqual(self.events[-1]["discovery_count"], 0)
        self.assertTrue(self.journal.blocked)

    def test_invalid_pid_is_not_recorded_or_adopted(self):
        for pid in (True, -1, 0x100000000):
            with self.subTest(pid=pid):
                events = []
                journal = actions.ActionJournal(events.append, Mock())
                foreign = fake_foreign(pid)
                api = fake_api(desktops=[fake_desktop([foreign])])
                instance = make_session(api, journal)
                with self.assertRaisesRegex(actions.GuiRejected, "accessibility_pid_invalid"):
                    instance.wait_ready("import")
                foreign.get_state_set.assert_not_called()
                self.assertEqual(events[-1]["discovery_count"], 0)
                self.assertTrue(journal.blocked)

    def test_diagnostic_recording_error_preserves_original_timeout(self):
        def emit(event):
            if event["step"].endswith(".discovery-diagnostic"):
                raise OSError("private recorder unavailable")
            self.events.append(event)
        journal = actions.ActionJournal(emit, Mock())
        api = fake_api(desktops=[fake_desktop([])])
        instance = make_session(api, journal)
        clock = ManualQueryClock()
        with patch.object(actions.time, "monotonic", side_effect=clock.monotonic), \
             patch.object(actions.time, "sleep", side_effect=clock.sleep) as slept, \
             patch.object(instance, "tree",
                          side_effect=clock.after_queries(instance.tree, (31.0,))) as query:
            with self.assertRaisesRegex(actions.GuiUncertain, "import.accessibility-ready") as caught:
                instance.wait_ready("import")
        self.assertIn("discovery_diagnostic_recording_failed:OSError", caught.exception.__notes__)
        self.assertTrue(journal.blocked)
        api.get_desktop.assert_called_once_with(0)
        self.assertEqual(query.call_count, 1)
        self.assertEqual(slept.call_count, 0)
        self.assertEqual(self.events[-1]["state"], "TIMED_OUT_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
