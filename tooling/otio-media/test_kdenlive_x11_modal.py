"""Synthetic controls for X11 observations; none is live Native evidence."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import kdenlive_x11_modal as x11
import test_kdenlive_modal_readback as modal_cases

PID = 41
PARENT = 101
WINDOW = 202
LEADER = 99
FOCUSED = "_NET_WM_STATE_FOCUSED"
IMPORT = "OpenTimelineIO Import \u2014 Kdenlive"


def raw_properties(*, pid=PID, name="Kdenlive", types=(x11.NORMAL,),
                   states=(FOCUSED,), transient=None, leader=LEADER):
    values = (pid, name, types, states, transient, leader)
    lines = []
    for prop, kind, value in zip(x11.PROPERTY_NAMES, x11.PROPERTY_TYPES, values):
        if value is None:
            lines.append(prop + ":  not found.")
        elif kind == "WINDOW":
            lines.append(f"{prop}(WINDOW): window id # 0x{value:x}")
        elif kind == "UTF8_STRING":
            lines.append(f"{prop}({kind}) = " + json.dumps(value, ensure_ascii=False))
        elif kind == "ATOM":
            lines.append(f"{prop}({kind}) = " + ", ".join(value))
        else:
            lines.append(f"{prop}({kind}) = {value}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def descriptor(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "base64": base64.b64encode(raw).decode("ascii")}


class RecordedQueries:
    def __init__(self, answers):
        self.answers = list(answers)
        self.argv = []

    def __call__(self, argv, guard, deadline, records):
        self.argv.append(list(argv))
        value = self.answers.pop(0)
        if isinstance(value, BaseException):
            raise value
        records.append({"argv": list(argv), "pid": 7999, "returncode": 0,
                        "state": "COMPLETE", "captured_bytes": len(value),
                        "stdout": descriptor(value), "stderr": descriptor(b""),
                        "retried": False})
        return value


def observe_parent_fixture(*, name="Kdenlive", props=None):
    props = raw_properties(name=name) if props is None else props
    queries = RecordedQueries([b"101\n", props, props, b"101\n"])
    with patch.object(x11, "_query", side_effect=queries):
        return x11.observe_parent(expected_pid=PID, parent_name=name,
                                  parent_type=(x11.NORMAL, x11.DIALOG),
                                  guard=Mock(), deadline=x11.time.monotonic() + 30)


def modal_answers(*, parent=None, dialog=None, group=False, leader=None):
    parent = raw_properties(states=()) if parent is None else parent
    dialog = (raw_properties(name=IMPORT, types=(x11.DIALOG,), states=(x11.MODAL,),
                             transient=PARENT) if dialog is None else dialog)
    if group:
        return [b"202\n", dialog, parent, leader, dialog, parent, leader, b"202\n"]
    return [b"202\n", dialog, parent, dialog, parent, b"202\n"]


def call_modal(parent, queries, *, group=False, name=IMPORT, evidence=None, guard=None):
    evidence = {} if evidence is None else evidence
    with patch.object(x11, "_query", side_effect=queries):
        result = x11.observe_modal(
            expected_pid=PID, dialog_name=name, parent_observation=parent, group=group,
            guard=Mock() if guard is None else guard, deadline=x11.time.monotonic() + 30,
            evidence=evidence)
    return result


class X11PropertyTests(unittest.TestCase):
    def test_typed_original_unicode_and_hex_window(self):
        raw = raw_properties(name='Open \u2014 Kdenlive "one"', types=(x11.DIALOG,),
                             states=(x11.MODAL, FOCUSED), transient=PARENT)
        value = x11.parse_properties(raw)
        self.assertEqual(value, {"pid": PID, "name": 'Open \u2014 Kdenlive "one"',
                                "types": (x11.DIALOG,), "states": (x11.MODAL, FOCUSED),
                                "transient_for": PARENT, "client_leader": LEADER})

    def test_only_confirmed_absence_reasons_and_spacing(self):
        for reason in ("not found.", "no such atom on any window."):
            for spaces in (" ", "  "):
                raw = "".join(prop + ":" + spaces + reason + "\n"
                              for prop in x11.PROPERTY_NAMES).encode()
                with self.subTest(reason=reason, spaces=len(spaces)):
                    self.assertEqual(x11.parse_properties(raw),
                                     dict.fromkeys(x11.PROPERTY_KEYS))
        for bad in ("not set.", "does not exist.", "   not found.", "not found."):
            raw = raw_properties().replace(b"WM_TRANSIENT_FOR:  not found.",
                                           ("WM_TRANSIENT_FOR:" + bad).encode())
            with self.subTest(bad=bad), self.assertRaises(x11.X11Rejected):
                x11.parse_properties(raw)

    def test_rejects_incomplete_duplicate_unknown_and_wrong_typed_properties(self):
        good = raw_properties()
        variants = [
            good[:-1], good + b"\n", good.replace(b"\n", b"\r\n"), b"\xff\n",
            good.replace(b"_NET_WM_PID(CARDINAL)", b"_NET_WM_PID(WINDOW)"),
            good.replace(b"_NET_WM_NAME(UTF8_STRING)", b"_NET_WM_NAME(STRING)"),
            good.replace(b"_NET_WM_STATE(ATOM)", b"_NET_WM_STATE(CARDINAL)"),
            good.replace(b"WM_CLIENT_LEADER(WINDOW)", b"WM_CLIENT_LEADER(CARDINAL)"),
            good.replace(b"WM_TRANSIENT_FOR:  not found.", b"UNKNOWN:  not found."),
            good.replace(b"WM_TRANSIENT_FOR:  not found.", b"_NET_WM_PID:  not found."),
            b"\n".join(good.splitlines()[:-1]) + b"\n", b"x" * (x11.QUERY_BYTES + 1),
        ]
        for raw in variants:
            with self.subTest(raw=raw[:90]), self.assertRaises(x11.X11Rejected):
                x11.parse_properties(raw)

    def test_rejects_malformed_values_and_property_saturation(self):
        good = raw_properties()
        variants = [
            good.replace(b"= 41\n", b"= 4294967296\n"),
            good.replace(b"= 41\n", b"= -41\n"),
            good.replace(b"= 41\n", b"= 041\n"),
            good.replace(b'"Kdenlive"', b'"Kdenlive'),
            good.replace(b'"Kdenlive"', b'"Kdenlive" trailing'),
            good.replace(b'"Kdenlive"', b'null'),
            good.replace(b'"Kdenlive"', b'["Kdenlive"]'),
            good.replace(b'"Kdenlive"', b'"Kden\\nlive"'),
            good.replace(b'"Kdenlive"', b'"' + b"x" * 1024 + b'"'),
            good.replace(FOCUSED.encode(), (FOCUSED + ", " + FOCUSED).encode()),
            good.replace(FOCUSED.encode(), b"_NET_WM_STATE_MODAL _BAD"),
            good.replace(b"0x63", b"0x100000000"),
            good.replace(b"0x63", b"99"),
        ]
        for raw in variants:
            with self.subTest(raw=raw[:90]), self.assertRaises(x11.X11Rejected):
                x11.parse_properties(raw)


class X11RelationTests(unittest.TestCase):
    def setUp(self):
        self.parent = x11.parse_properties(raw_properties())
        self.dialog = x11.parse_properties(raw_properties(
            name=IMPORT, types=(x11.DIALOG,), states=(x11.MODAL,), transient=PARENT))

    def verify(self, dialog=None, parent=None, **kwargs):
        return x11.verify_dialog(
            self.dialog if dialog is None else dialog,
            self.parent if parent is None else parent,
            expected_pid=PID, dialog_name=IMPORT, parent_name="Kdenlive",
            parent_window=PARENT, **kwargs)

    def test_direct_parent_relation_does_not_change_either_original(self):
        originals = copy.deepcopy((self.dialog, self.parent))
        self.assertEqual(self.verify(), "direct_parent")
        self.assertEqual((self.dialog, self.parent), originals)

    def test_group_client_leader_absent_pid_is_not_invented(self):
        parent = x11.parse_properties(raw_properties(name="Rendering \u2014 Kdenlive"))
        dialog = x11.parse_properties(raw_properties(
            name="Delayed Rendering \u2014 Kdenlive", types=(x11.DIALOG,),
            states=(x11.MODAL,), transient=LEADER))
        leader = x11.parse_properties(raw_properties(pid=None, name="Kdenlive",
                                                     types=None, states=None))
        original = copy.deepcopy(leader)
        relation = x11.verify_dialog(
            dialog, parent, expected_pid=PID, dialog_name=dialog["name"],
            parent_name=parent["name"], parent_window=PARENT, group=True, leader_props=leader)
        self.assertEqual(relation, "qt_client_leader")
        self.assertIsNone(leader["pid"])
        self.assertEqual(leader, original)

    def test_direct_relation_requires_typed_identity_title_modal_and_parent(self):
        for key, value in (("pid", True), ("pid", 42), ("name", "Other"),
                           ("types", (x11.NORMAL,)), ("states", (FOCUSED,)),
                           ("transient_for", LEADER), ("transient_for", None),
                           ("client_leader", True)):
            changed = {**self.dialog, key: value}
            with self.subTest(key=key, value=value), self.assertRaises(x11.X11Rejected):
                self.verify(dialog=changed)
        for key, value in (("pid", 42), ("name", "Other"), ("types", (x11.MODAL,))):
            with self.subTest(parent=key), self.assertRaises(x11.X11Rejected):
                self.verify(parent={**self.parent, key: value})
        for changed in ({**self.dialog, "extra": 1},
                        {k: v for k, v in self.dialog.items() if k != "pid"}):
            with self.assertRaises(x11.X11Rejected):
                self.verify(dialog=changed)

    def test_group_profile_and_chain_are_closed_not_a_root_fallback(self):
        parent = x11.parse_properties(raw_properties(name="Rendering"))
        dialog = x11.parse_properties(raw_properties(
            name="Delayed Rendering", types=(x11.DIALOG,),
            states=(x11.MODAL,), transient=LEADER))
        leader = x11.parse_properties(raw_properties(pid=None, types=None, states=None))
        base = {"expected_pid": PID, "dialog_name": "Delayed Rendering",
                "parent_name": "Rendering", "parent_window": PARENT,
                "group": True, "leader_props": leader}
        cases = [
            ({**dialog, "transient_for": 1}, parent, {}),
            ({**dialog, "client_leader": 1, "transient_for": 1}, parent, {}),
            (dialog, {**parent, "client_leader": 1}, {}),
            (dialog, parent, {"leader_props": {**leader, "client_leader": 1}}),
            (dialog, parent, {"leader_props": {**leader, "pid": 42}}),
            (dialog, parent, {"parent_window": LEADER}),
            ({**dialog, "name": IMPORT}, parent, {"dialog_name": IMPORT}),
            (dialog, {**parent, "name": "Kdenlive"}, {"parent_name": "Kdenlive"}),
        ]
        for dialog_value, parent_value, overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(x11.X11Rejected):
                x11.verify_dialog(dialog_value, parent_value, **{**base, **overrides})


class X11ObservationTests(unittest.TestCase):
    def test_parent_retains_two_reads_active_ids_and_every_original(self):
        evidence = {}
        raw = raw_properties()
        queries = RecordedQueries([b"101\n", raw, raw, b"101\n"])
        guard = Mock()
        with patch.object(x11, "_query", side_effect=queries):
            result = x11.observe_parent(expected_pid=PID, parent_name="Kdenlive",
                                        parent_type=(x11.NORMAL,), guard=guard,
                                        deadline=x11.time.monotonic() + 30, evidence=evidence)
        self.assertIs(result, evidence)
        self.assertTrue(evidence["complete"])
        self.assertEqual((evidence["window"], evidence["active_after"]), (PARENT, PARENT))
        self.assertEqual(len(evidence["records"]), 4)
        self.assertEqual(base64.b64decode(evidence["records"][1]["stdout"]["base64"]), raw)
        self.assertGreaterEqual(guard.call_count, 2)

    def test_parent_instability_pid_type_deadline_and_guard_stay_incomplete(self):
        raw = raw_properties()
        variants = [
            [b"101\n", raw, raw, b"102\n"],
            [b"101\n", raw, raw_properties(states=()), b"101\n"],
            [b"101\n", raw_properties(pid=42), raw_properties(pid=42), b"101\n"],
            [b"101\n", raw_properties(types=(x11.DIALOG,)),
             raw_properties(types=(x11.DIALOG,)), b"101\n"],
        ]
        for answers in variants:
            evidence = {}
            queries = RecordedQueries(answers)
            with self.subTest(answers=answers), patch.object(x11, "_query", side_effect=queries):
                with self.assertRaises(x11.X11Rejected):
                    x11.observe_parent(expected_pid=PID, parent_name="Kdenlive",
                                       parent_type=(x11.NORMAL,), guard=Mock(),
                                       deadline=x11.time.monotonic() + 30, evidence=evidence)
            self.assertFalse(evidence["complete"])
            self.assertEqual(len(evidence["records"]), 4)
        for guard, deadline in ((Mock(), 0), (Mock(side_effect=RuntimeError("identity")), 10**15)):
            evidence = {}
            with patch.object(x11, "_query") as query, self.assertRaises((x11.X11Rejected, RuntimeError)):
                x11.observe_parent(expected_pid=PID, parent_name="Kdenlive",
                                   parent_type=(x11.NORMAL,), guard=guard,
                                   deadline=deadline, evidence=evidence)
            query.assert_not_called()
            self.assertFalse(evidence["complete"])

    def test_modal_joins_parent_identity_but_retains_focus_state_transition(self):
        parent = observe_parent_fixture()
        original = copy.deepcopy(parent)
        queries = RecordedQueries(modal_answers())
        result = call_modal(parent, queries)
        self.assertTrue(result["complete"])
        self.assertEqual(result["relation"], "direct_parent")
        self.assertEqual(result["parent_observation"]["properties"]["states"], (FOCUSED,))
        self.assertEqual(result["parent_properties"]["states"], ())
        self.assertEqual(parent, original)
        self.assertEqual(len(result["records"]), 10)
        self.assertFalse(result["native_operation_retried"])
        self.assertFalse(result["native_save_or_import_verified"])

    def test_group_reads_only_self_bound_qt_leader_twice(self):
        parent = observe_parent_fixture(name="Rendering")
        post = raw_properties(name="Rendering", states=())
        dialog = raw_properties(name="Delayed Rendering", types=(x11.DIALOG,),
                                states=(x11.MODAL,), transient=LEADER)
        leader = raw_properties(pid=None, types=None, states=None)
        queries = RecordedQueries(modal_answers(parent=post, dialog=dialog, group=True, leader=leader))
        result = call_modal(parent, queries, group=True, name="Delayed Rendering")
        self.assertEqual(result["relation"], "qt_client_leader")
        self.assertEqual(result["leader_window"], LEADER)
        leader_queries = [v for v in queries.argv if v[:1] == ["xprop"] and v[4] == str(LEADER)]
        self.assertEqual(len(leader_queries), 2)
        self.assertFalse(any(v[:1] == ["xprop"] and v[4] == "1" for v in queries.argv))

    def test_modal_focus_property_and_parent_identity_changes_reject(self):
        parent = observe_parent_fixture()
        variants = []
        answers = modal_answers()
        answers[-1] = b"203\n"
        variants.append(answers)
        answers = modal_answers()
        answers[3] = raw_properties(name="Other", types=(x11.DIALOG,),
                                    states=(x11.MODAL,), transient=PARENT)
        variants.append(answers)
        variants.append(modal_answers(parent=raw_properties(name="Other", states=())))
        variants.append(modal_answers(parent=raw_properties(pid=42, states=())))
        variants.append(modal_answers(parent=raw_properties(leader=100, states=())))
        variants.append(modal_answers(dialog=raw_properties(
            name=IMPORT, types=(x11.DIALOG,), states=(FOCUSED,), transient=PARENT)))
        for answers in variants:
            evidence = {}
            with self.subTest(answers=answers), self.assertRaises(x11.X11Rejected):
                call_modal(parent, RecordedQueries(answers), evidence=evidence)
            self.assertFalse(evidence["complete"])
            self.assertGreaterEqual(len(evidence["records"]), 6)

    def test_missing_preobservation_and_final_guard_never_grant_proof(self):
        parent = observe_parent_fixture()
        for altered in (None, {}, {**parent, "complete": False},
                        {**parent, "expected_pid": 42}, {**parent, "records": []}):
            evidence = {}
            with patch.object(x11, "_query") as query, self.assertRaises(x11.X11Rejected):
                x11.observe_modal(expected_pid=PID, dialog_name=IMPORT,
                                  parent_observation=altered, group=False,
                                  guard=Mock(), deadline=x11.time.monotonic() + 30,
                                  evidence=evidence)
            query.assert_not_called()
            self.assertFalse(evidence["complete"])
        guard_error = RuntimeError("owned PID changed")
        evidence = {}
        with self.assertRaises(RuntimeError) as caught:
            call_modal(parent, RecordedQueries(modal_answers()), evidence=evidence,
                       guard=Mock(side_effect=[None, guard_error]))
        self.assertIs(caught.exception, guard_error)
        self.assertFalse(evidence["complete"])
        self.assertEqual(len(evidence["records"]), 10)


class FakePipe:
    def __init__(self, fd):
        self.fd = fd
        self.closed = False

    def fileno(self):
        return self.fd

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, *, alive=False, code=0, cleanup_error=None):
        self.pid = 7999
        self.stdout = FakePipe(11)
        self.stderr = FakePipe(12)
        self.alive = alive
        self.code = code
        self.cleanup_error = cleanup_error
        self.kill_count = 0
        self.waits = []

    def poll(self):
        return None if self.alive else self.code

    def kill(self):
        self.kill_count += 1
        self.alive = False
        self.code = -9

    def wait(self, timeout):
        self.waits.append(timeout)
        if self.cleanup_error is not None:
            raise self.cleanup_error
        if self.alive:
            raise x11.subprocess.TimeoutExpired(["synthetic"], timeout)
        return self.code


class SyntheticTransport:
    """Injected pipe/selector/clock; never starts a real local process."""
    def __init__(self, stdout=b"101\n", stderr=b"", *, alive=False, code=0,
                 hanging=False, cleanup_error=None, popen_error=None):
        self.now = 0.0
        self.queues = {11: [stdout, b""] if stdout else [b""],
                       12: [stderr, b""] if stderr else [b""]}
        if hanging:
            self.queues = {11: [], 12: []}
        self.process = FakeProcess(alive=alive, code=code, cleanup_error=cleanup_error)
        self.registered = {}
        self.popen = Mock(side_effect=popen_error, return_value=self.process)
        self.selector = SimpleNamespace(register=self.register, unregister=self.unregister,
                                        get_map=lambda: self.registered,
                                        select=self.select, close=Mock())

    def register(self, pipe, events, label):
        self.registered[pipe.fd] = SimpleNamespace(fileobj=pipe, data=label)

    def unregister(self, pipe):
        del self.registered[pipe.fd]

    def select(self, timeout):
        self.now += timeout
        return [(key, 1) for key in list(self.registered.values())]

    def read(self, fd, count):
        if not self.queues[fd]:
            raise BlockingIOError()
        data = self.queues[fd].pop(0)
        if len(data) > count:
            self.queues[fd].insert(0, data[count:])
            return data[:count]
        return data

    def sleep(self, seconds):
        self.now += seconds

    def __enter__(self):
        self.stack = ExitStack()
        for target, name, kwargs in (
                (x11, "LINUX", {"new": True}),
                (x11.subprocess, "Popen", {"new": self.popen}),
                (x11.selectors, "DefaultSelector", {"return_value": self.selector}),
                (x11.os, "set_blocking", {}),
                (x11.os, "read", {"side_effect": self.read}),
                (x11.time, "monotonic", {"side_effect": lambda: self.now}),
                (x11.time, "sleep", {"side_effect": self.sleep})):
            self.stack.enter_context(patch.object(target, name, **kwargs))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


class X11TransportTests(unittest.TestCase):
    def test_query_originals_stdin_closed_concurrent_streams_and_no_shell(self):
        records = []
        guard = Mock()
        with SyntheticTransport() as transport:
            raw = x11._query(["xdotool", "getwindowfocus"], guard, 30, records)
        self.assertEqual(raw, b"101\n")
        transport.popen.assert_called_once()
        self.assertFalse(transport.popen.call_args.kwargs["shell"])
        self.assertEqual(transport.popen.call_args.kwargs["stdin"], x11.subprocess.DEVNULL)
        self.assertEqual(transport.popen.call_args.kwargs["env"]["LC_ALL"], "C.UTF-8")
        self.assertTrue(records[0]["stdout_eof"] and records[0]["stderr_eof"])
        self.assertEqual(records[0]["stdout"], descriptor(raw))
        self.assertEqual(records[0]["pid"], 7999)
        self.assertEqual(records[0]["returncode"], 0)
        self.assertTrue(records[0]["reaped"])
        self.assertFalse(records[0]["retried"])
        self.assertEqual(records[0]["state"], "COMPLETE")
        self.assertEqual(transport.process.kill_count, 0)
        self.assertEqual(guard.call_count, 2)

    def test_query_accepts_only_closed_readonly_argv(self):
        invalid = [
            ["xdotool", "key", "Return"], ["xdotool", "getwindowfocus", "-f"],
            ["xprop", "-root"], ["sh", "-c", "xprop"], "xdotool getwindowfocus",
            x11._property_argv(PARENT) + ["-set", "_NET_WM_NAME", "other"],
            ["xprop", "-len", "2048", "-id", "101", *x11.PROPERTY_NAMES],
            ["xprop", "-len", "1024", "-id", "0101", *x11.PROPERTY_NAMES],
        ]
        with patch.object(x11.subprocess, "Popen") as popen:
            for argv in invalid:
                with self.subTest(argv=argv), self.assertRaises(x11.X11Rejected):
                    x11._query(argv, Mock(), x11.time.monotonic() + 30, [])
        popen.assert_not_called()

    def test_nonzero_and_stderr_are_retained_failures(self):
        for code, stderr in ((3, b"failure\n"), (0, b"warning\n")):
            records = []
            with self.subTest(code=code), SyntheticTransport(b"partial\n", stderr, code=code):
                with self.assertRaises(x11.X11Rejected):
                    x11._query(["xdotool", "getwindowfocus"], Mock(), 30, records)
            self.assertEqual(records[0]["state"], "REJECTED")
            self.assertEqual(records[0]["stdout"], descriptor(b"partial\n"))
            self.assertEqual(records[0]["stderr"], descriptor(stderr))
            self.assertEqual(records[0]["returncode"], code)
            self.assertTrue(records[0]["stdout_eof"] and records[0]["stderr_eof"])

    def test_launch_failure_and_pre_guard_never_retry(self):
        records = []
        missing = FileNotFoundError("xprop absent")
        with SyntheticTransport(popen_error=missing) as transport:
            with self.assertRaises(x11.X11Rejected) as caught:
                x11._query(["xdotool", "getwindowfocus"], Mock(), 30, records)
        self.assertIs(caught.exception.__cause__, missing)
        transport.popen.assert_called_once()
        self.assertIsNone(records[0]["pid"])
        original = RuntimeError("source identity")
        with SyntheticTransport() as transport:
            with self.assertRaises(RuntimeError) as caught:
                x11._query(["xdotool", "getwindowfocus"], Mock(side_effect=original), 30, [])
        self.assertIs(caught.exception, original)
        transport.popen.assert_not_called()

    def test_per_query_and_shared_capture_bounds_have_explicit_prefixes(self):
        for preceding, limit in (([], x11.QUERY_BYTES),
                                 ([{"captured_bytes": x11.QUERY_BYTES}] * 3
                                  + [{"captured_bytes": x11.QUERY_BYTES - 3}], 3)):
            records = copy.deepcopy(preceding)
            with self.subTest(limit=limit), SyntheticTransport(b"x" * (limit + 12), alive=True) as transport:
                with self.assertRaises(x11.X11Rejected):
                    x11._query(["xdotool", "getwindowfocus"], Mock(), 30, records)
            item = records[-1]
            self.assertEqual(item["captured_bytes"], limit)
            self.assertTrue(item["truncated"])
            self.assertEqual(item["observed_bytes"], limit + 1)
            self.assertEqual(item["stdout"], descriptor(b"x" * limit))
            self.assertEqual(transport.process.kill_count, 1)
            self.assertTrue(item["reaped"])

    def test_query_and_total_budgets_block_before_a_new_child(self):
        for records in ([{"captured_bytes": 0}] * x11.MAX_QUERIES,
                        [{"captured_bytes": x11.QUERY_BYTES}] * 4,
                        [{"captured_bytes": True}]):
            with self.subTest(records=records), SyntheticTransport() as transport:
                with self.assertRaises(x11.X11Rejected):
                    x11._query(["xdotool", "getwindowfocus"], Mock(), 30, records)
            transport.popen.assert_not_called()

    def test_timeout_kills_only_own_live_child_once_and_no_unbounded_eof(self):
        for alive, hanging in ((True, True), (False, True), (True, False)):
            records = []
            with self.subTest(alive=alive, hanging=hanging), SyntheticTransport(
                    alive=alive, hanging=hanging) as transport:
                with self.assertRaisesRegex(x11.X11Rejected, "query_timeout"):
                    x11._query(["xdotool", "getwindowfocus"], Mock(), 30, records)
            self.assertEqual(transport.process.kill_count, int(alive))
            transport.popen.assert_called_once()
            self.assertFalse(records[0]["retried"])
            self.assertTrue(records[0]["reaped"])
            self.assertLessEqual(records[0]["elapsed_ms"], 5000)
            self.assertTrue(transport.process.stdout.closed and transport.process.stderr.closed)

    def test_cleanup_failure_preserves_first_timeout_and_original_capture(self):
        cleanup = OSError("synthetic reap unavailable")
        records = []
        with SyntheticTransport(alive=True, hanging=True, cleanup_error=cleanup) as transport:
            with self.assertRaisesRegex(x11.X11Rejected, "query_timeout") as caught:
                x11._query(["xdotool", "getwindowfocus"], Mock(), 30, records)
        self.assertIn("native_gui_X11_query_cleanup_failed:OSError", caught.exception.__notes__)
        self.assertEqual(records[0]["error"]["message"], "native_gui_X11_query_timeout")
        self.assertFalse(records[0]["reaped"])
        self.assertEqual(transport.process.kill_count, 1)
        self.assertEqual(records[0]["cleanup_errors"][0]["type"], "OSError")

    def test_post_query_identity_failure_preserves_bytes_and_cannot_complete(self):
        original = RuntimeError("PID start time changed")
        records = []
        guard = Mock(side_effect=[None, original, None])
        with SyntheticTransport():
            with self.assertRaises(RuntimeError) as caught:
                x11._query(["xdotool", "getwindowfocus"], guard, 30, records)
        self.assertIs(caught.exception, original)
        self.assertEqual(records[0]["stdout"], descriptor(b"101\n"))
        self.assertEqual(records[0]["state"], "REJECTED")


class X11ModalIntegrationTests(unittest.TestCase):
    def scenario(self):
        scenario = modal_cases.Scenario()
        del scenario.session._observe_modal_parent
        scenario.after[-1]["name"] = IMPORT
        scenario.after[-1]["modal"] = False
        pre = raw_properties()
        queries = RecordedQueries([b"101\n", pre, pre, b"101\n", *modal_answers()])
        return scenario, queries

    def test_decorated_nonmodal_atspi_row_requires_real_x11_api_proof(self):
        scenario, queries = self.scenario()
        with patch.object(x11, "_query", side_effect=queries):
            dialog = scenario.run()
        self.assertFalse(dialog["modal"])
        scenario.effect.assert_called_once()
        reconciled = [e for e in scenario.events if e["state"] == "MODAL_OPEN_RECONCILED"]
        self.assertEqual(len(reconciled), 1)
        self.assertTrue(reconciled[0]["x11_modal"]["complete"])
        self.assertEqual(reconciled[0]["x11_modal"]["relation"], "direct_parent")
        self.assertFalse(reconciled[0]["dialog"]["modal"])
        self.assertFalse(reconciled[0]["native_save_or_import_verified"])
        self.assertTrue(any(e["state"] == "ERROR_OR_UNKNOWN" for e in scenario.events))
        intent = next(e for e in scenario.events if e["state"] == "INTENT")
        self.assertTrue(intent["target"]["x11_modal_parent"]["complete"])
        self.assertFalse(scenario.journal.blocked)

    def test_rejected_x11_proof_keeps_first_no_reply_and_blocks_second_effect(self):
        scenario, queries = self.scenario()
        queries.answers[-1] = b"203\n"
        with patch.object(x11, "_query", side_effect=queries):
            with self.assertRaises(modal_cases.actions.GuiUncertain) as caught:
                scenario.run()
        self.assertIs(caught.exception.__cause__, scenario.original)
        self.assertTrue(scenario.journal.blocked)
        rejected = next(e for e in scenario.events if e["state"] == "MODAL_READBACK_REJECTED")
        self.assertFalse(rejected["x11_modal"]["complete"])
        self.assertFalse(rejected["reconciliation_granted"])
        self.assertFalse(rejected["native_operation_retried"])
        with self.assertRaises(modal_cases.actions.GuiRejected):
            scenario.run("second")
        scenario.effect.assert_called_once()

    def test_decorated_window_titles_do_not_expand_menu_selectors(self):
        actions = modal_cases.actions
        self.assertTrue(actions.window_title_matches(IMPORT, ("OpenTimelineIO Import",)))
        self.assertFalse(actions.window_title_matches("Unlisted \u2014 Kdenlive", ("Unlisted",)))
        self.assertFalse(actions.window_title_matches(
            "OpenTimelineIO Import \u2014 Other", ("OpenTimelineIO Import",)))
        scenario = modal_cases.Scenario()
        scenario.target["name"] += " \u2014 Kdenlive"
        self.assertIsNone(scenario.session.find(
            ("OpenTimelineIO Import...",), ("menu item",)))
        self.assertEqual(scenario.effect.call_count, 0)

    def test_rejected_preparent_is_retained_before_any_opener(self):
        scenario, queries = self.scenario()
        queries.answers[3] = b"102\n"
        with patch.object(x11, "_query", side_effect=queries):
            with self.assertRaises(modal_cases.actions.GuiRejected):
                scenario.run()
        scenario.effect.assert_not_called()
        self.assertTrue(scenario.journal.blocked)
        event = next(e for e in scenario.events if e["state"] == "X11_PARENT_REJECTED")
        self.assertEqual(event["opener_attempts"], 0)
        self.assertFalse(event["observation"]["complete"])
        self.assertEqual(len(event["observation"]["records"]), 4)
        self.assertFalse(any(e["state"] == "INTENT" for e in scenario.events))


if __name__ == "__main__":
    unittest.main()
