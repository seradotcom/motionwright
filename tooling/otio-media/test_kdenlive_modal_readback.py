"""Offline modal readback controls; synthetic GI is never physical evidence."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from types import SimpleNamespace
import unittest
import zlib
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


SNAPSHOT_SCHEMA = "motionwright.native.atspi-modal-snapshot/1"
SNAPSHOT_COLUMNS = [
    "key", "name", "role", "text", "visible", "enabled", "editable", "rect",
    "focused", "modal", "pid",
]


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      allow_nan=False).encode()


def independent_snapshot(rows, expected_pid=41):
    """Reference envelope for size checks, never an observed Native snapshot."""
    return {
        "schema": SNAPSHOT_SCHEMA, "columns": list(SNAPSHOT_COLUMNS),
        "rows": [[item[column] for column in SNAPSHOT_COLUMNS] for item in rows],
        "row_count": len(rows), "expected_pid": expected_pid,
        "source_rows_sha256": hashlib.sha256(canonical_bytes(rows)).hexdigest(),
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
        # These legacy scenarios exercise synthetic AT-SPI modality only.
        # Independent X11 behavior is covered by test_kdenlive_x11_modal.
        self.session._observe_modal_parent = Mock(return_value=None)
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
    def decode_snapshot(self, value):
        """Independent bounded decoder; no product codec helper is reused."""
        self.assertIs(type(value), dict)
        self.assertLessEqual(len(canonical_bytes(value)), 128 * 1024)
        if value.get("schema") == "motionwright.native.atspi-modal-snapshot/2":
            envelope = value
            self.assertEqual(set(envelope), {
                "schema", "encoding", "decoded_schema", "decoded_bytes",
                "decoded_sha256", "source_rows_sha256", "row_count",
                "expected_pid", "payload",
            })
            self.assertEqual(envelope["encoding"], "zlib+base64")
            self.assertEqual(envelope["decoded_schema"], SNAPSHOT_SCHEMA)
            self.assertIs(type(envelope["decoded_bytes"]), int)
            self.assertGreater(envelope["decoded_bytes"], 0)
            self.assertLessEqual(envelope["decoded_bytes"], 1024 * 1024)
            self.assertIs(type(envelope["row_count"]), int)
            self.assertGreater(envelope["row_count"], 0)
            self.assertLessEqual(envelope["row_count"], 4096)
            self.assertIs(type(envelope["expected_pid"]), int)
            self.assertGreater(envelope["expected_pid"], 0)
            self.assertLessEqual(envelope["expected_pid"], 0xffffffff)
            self.assertIs(type(envelope["payload"]), str)
            compressed = base64.b64decode(envelope["payload"], validate=True)
            inflater = zlib.decompressobj()
            raw = inflater.decompress(compressed, envelope["decoded_bytes"] + 1)
            self.assertLessEqual(len(raw), envelope["decoded_bytes"])
            self.assertEqual(len(raw), envelope["decoded_bytes"])
            self.assertTrue(inflater.eof)
            self.assertEqual(inflater.unconsumed_tail, b"")
            self.assertEqual(inflater.unused_data, b"")
            self.assertEqual(envelope["decoded_sha256"], hashlib.sha256(raw).hexdigest())
            def closed_object(pairs):
                result = {}
                for key, item in pairs:
                    self.assertNotIn(key, result)
                    result[key] = item
                return result
            value = json.loads(raw.decode("ascii"), object_pairs_hook=closed_object,
                parse_constant=lambda _: self.fail("Non-finite JSON is not an original snapshot"))
            self.assertEqual(raw, canonical_bytes(value))
            for field in ("source_rows_sha256", "row_count", "expected_pid"):
                self.assertEqual(value[field], envelope[field])
        self.assertIs(type(value), dict)
        self.assertEqual(set(value), {
            "schema", "columns", "rows", "row_count", "expected_pid",
            "source_rows_sha256",
        })
        self.assertEqual(value["schema"], SNAPSHOT_SCHEMA)
        self.assertEqual(value["columns"], SNAPSHOT_COLUMNS)
        self.assertIs(type(value["row_count"]), int)
        self.assertGreater(value["row_count"], 0)
        self.assertLessEqual(value["row_count"], 4096)
        self.assertIs(type(value["expected_pid"]), int)
        self.assertGreater(value["expected_pid"], 0)
        self.assertLessEqual(value["expected_pid"], 0xffffffff)
        self.assertIs(type(value["rows"]), list)
        self.assertEqual(value["row_count"], len(value["rows"]))
        decoded, keys = [], set()
        for values in value["rows"]:
            self.assertIs(type(values), list)
            self.assertEqual(len(values), len(SNAPSHOT_COLUMNS))
            item = dict(zip(SNAPSHOT_COLUMNS, values, strict=True))
            for field in ("key", "name", "role", "text"):
                self.assertIs(type(item[field]), str)
            for field in ("visible", "enabled", "editable", "focused", "modal"):
                self.assertIs(type(item[field]), bool)
            self.assertIs(type(item["pid"]), int)
            self.assertEqual(item["pid"], value["expected_pid"])
            self.assertIs(type(item["rect"]), list)
            self.assertEqual(len(item["rect"]), 4)
            for number in item["rect"]:
                self.assertIs(type(number), int)
            self.assertNotIn(item["key"], keys)
            keys.add(item["key"])
            decoded.append(item)
        self.assertEqual(value["source_rows_sha256"],
                         hashlib.sha256(canonical_bytes(decoded)).hexdigest())
        return decoded

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
                self.assertNotIn("rows", before[0])
                self.assertNotIn("rows", after[0])
                self.assertEqual(self.decode_snapshot(before[0]["snapshot"]), scenario.before)
                self.assertEqual(self.decode_snapshot(after[0]["snapshot"]), scenario.after)
                self.assertLessEqual(len(canonical_bytes(before[0])), 512 * 1024)
                self.assertLessEqual(len(canonical_bytes(after[0])), 512 * 1024)
                self.assertEqual(after[0]["request_response"], "NO_REPLY")
                self.assertEqual(after[0]["verified_effect"], "owned_dialog_open_only")
                self.assertFalse(after[0]["native_save_or_import_verified"])
                self.assertFalse(after[0]["native_operation_retried"])
                self.assertTrue(all(blocked for state, blocked in scenario.emission_states
                                    if state in ("OBSERVED", "MODAL_OPEN_RECONCILED")))

    def test_compact_snapshot_roundtrip_preserves_all_fields_and_source_hash(self):
        rows = [
            row("app", "Kdenlive", "application"),
            row("app/0", "Caf\u00e9 \u65e5\u672c\u8a9e \U0001f680",
                "frame", enabled=False, visible=False),
            row("app/0/0", "Duration", "dialog", modal=True),
        ]
        rows[0].update(text='a\nb\t"quoted"\\path', focused=True,
                       editable=True, rect=[-17, 21, 0, 44])
        rows[1].update(text="\u2028\u00a0\u00f1", rect=[0, -2, 640, 360])
        original = copy.deepcopy(rows)
        value = actions.modal_snapshot(rows, 41)
        self.assertEqual(self.decode_snapshot(value), original)
        self.assertEqual(rows, original)
        self.assertEqual(value["row_count"], 3)
        self.assertEqual(len(value["columns"]), 11)
        self.assertEqual(actions.MODAL_SNAPSHOT_BYTES, 128 * 1024)
        self.assertEqual(actions.MAX_NODES, 4096)
        for original_row, restored in zip(original, self.decode_snapshot(value), strict=True):
            for field in SNAPSHOT_COLUMNS:
                self.assertIs(type(restored[field]), type(original_row[field]))
        changed = copy.deepcopy(rows)
        changed[1]["visible"] = True
        changed_value = actions.modal_snapshot(changed, 41)
        self.assertNotEqual(value["source_rows_sha256"], changed_value["source_rows_sha256"])
        self.assertEqual(self.decode_snapshot(changed_value), changed)

    def test_large_complete_tree_fits_lossless_compact_form_without_new_budget(self):
        scenario = Scenario()
        scenario.before.extend(
            row("app/main/node" + str(index), "Synthetic panel " + str(index),
                "panel") for index in range(768)
        )
        scenario.after = copy.deepcopy(scenario.before) + [
            row("app/modal", "OpenTimelineIO Import", "dialog", modal=True),
        ]
        scenario.snapshots = [snapshot(scenario.before), snapshot(scenario.after)]
        self.assertGreater(len(canonical_bytes(scenario.before)), 128 * 1024)
        self.assertLessEqual(
            len(canonical_bytes(independent_snapshot(scenario.before))), 128 * 1024)
        self.assertEqual(scenario.run()["key"], "app/modal")
        scenario.effect.assert_called_once_with(0)
        retained = [event for event in scenario.events
                    if event["step"].endswith((".modal-before", ".modal-readback"))]
        self.assertEqual(len(retained), 2)
        self.assertEqual(self.decode_snapshot(retained[0]["snapshot"]), scenario.before)
        self.assertEqual(self.decode_snapshot(retained[1]["snapshot"]), scenario.after)
        self.assertTrue(all(len(canonical_bytes(event)) <= 512 * 1024 for event in retained))
        self.assertFalse(scenario.journal.blocked)

    def test_compact_overflow_still_blocks_before_any_effect(self):
        scenario = Scenario()
        for index in range(256):
            item = row("app/main/text" + str(index), "Text " + str(index), "text")
            item["text"] = hashlib.shake_128(
                ("synthetic-text-" + str(index)).encode()).hexdigest(512)
            self.assertEqual(len(item["text"]), 1024)
            scenario.before.append(item)
        reference = independent_snapshot(scenario.before)
        raw = canonical_bytes(reference)
        self.assertGreater(len(raw), 128 * 1024)
        self.assertLessEqual(len(raw), 1024 * 1024)
        compressed = {
            "schema": "motionwright.native.atspi-modal-snapshot/2",
            "encoding": "zlib+base64", "decoded_schema": SNAPSHOT_SCHEMA,
            "decoded_bytes": len(raw), "decoded_sha256": hashlib.sha256(raw).hexdigest(),
            "source_rows_sha256": reference["source_rows_sha256"],
            "row_count": len(scenario.before), "expected_pid": 41,
            "payload": base64.b64encode(zlib.compress(raw, level=6)).decode("ascii"),
        }
        self.assertGreater(len(canonical_bytes(compressed)), 128 * 1024)
        with self.assertRaisesRegex(actions.GuiRejected, "native_gui_modal_snapshot_budget"):
            scenario.run()
        scenario.effect.assert_not_called()
        self.assert_blocked(scenario)

    def test_lossless_compressed_tree_retains_all_2837_rows_before_one_effect(self):
        scenario = Scenario()
        scenario.before.extend(
            row("app/main/node" + str(index), "Synthetic panel " + str(index), "panel")
            for index in range(2835)
        )
        scenario.after = copy.deepcopy(scenario.before) + [
            row("app/modal", "OpenTimelineIO Import", "dialog", modal=True),
        ]
        scenario.snapshots = [snapshot(scenario.before), snapshot(scenario.after)]
        self.assertEqual(len(scenario.before), 2837)
        before_raw = canonical_bytes(independent_snapshot(scenario.before))
        self.assertGreater(len(before_raw), 128 * 1024)
        self.assertLessEqual(len(before_raw), 1024 * 1024)
        self.assertEqual(scenario.run()["key"], "app/modal")
        scenario.effect.assert_called_once_with(0)
        retained = [e for e in scenario.events
                    if e["step"].endswith((".modal-before", ".modal-readback"))]
        self.assertEqual(len(retained), 2)
        for event, original in zip(retained, (scenario.before, scenario.after), strict=True):
            self.assertEqual(event["snapshot"]["schema"],
                             "motionwright.native.atspi-modal-snapshot/2")
            self.assertEqual(self.decode_snapshot(event["snapshot"]), original)
            self.assertEqual(event["snapshot"]["row_count"], len(original))
            self.assertLessEqual(len(canonical_bytes(event["snapshot"])), 128 * 1024)
            self.assertLessEqual(len(canonical_bytes(event)), 512 * 1024)
        unknown = [e for e in scenario.events if e["state"] == "ERROR_OR_UNKNOWN"]
        self.assertEqual(len(unknown), 1)
        self.assertFalse(unknown[0]["replayed"])
        self.assertEqual(retained[-1]["state"], "MODAL_OPEN_RECONCILED")
        self.assertFalse(retained[-1]["native_save_or_import_verified"])
        self.assertFalse(retained[-1]["native_operation_retried"])
        self.assertFalse(scenario.journal.blocked)
        self.assertEqual(scenario.query_count, 2)

    def test_compressed_snapshot_preserves_unicode_types_hashes_and_immutability(self):
        rows = [row("app/" + str(i), "Synthetic panel " + str(i), "panel")
                for i in range(2837)]
        rows[0].update(name="Caf\u00e9 \u65e5\u672c\u8a9e \U0001f680",
                       text='a\nb\t"quoted"\\path\u2028\u00a0\u00f1',
                       rect=[-17, 21, 0, 44], focused=True, editable=True)
        rows[1].update(visible=False, enabled=False, modal=True, rect=[0, -2, 640, 360])
        original = copy.deepcopy(rows)
        value = actions.modal_snapshot(rows, 41)
        self.assertEqual(value["schema"], "motionwright.native.atspi-modal-snapshot/2")
        self.assertEqual(rows, original)
        self.assertEqual(self.decode_snapshot(value), original)
        self.assertEqual(value["source_rows_sha256"],
                         hashlib.sha256(canonical_bytes(original)).hexdigest())
        self.assertEqual(value["decoded_sha256"],
                         hashlib.sha256(canonical_bytes(independent_snapshot(original))).hexdigest())
        for restored, observed in zip(self.decode_snapshot(value), original, strict=True):
            for field in SNAPSHOT_COLUMNS:
                self.assertIs(type(restored[field]), type(observed[field]))
        retained = canonical_bytes(value)
        rows[0]["rect"][0] = 999
        rows[0]["name"] = "Changed caller buffer"
        rows[1]["visible"] = True
        self.assertEqual(canonical_bytes(value), retained)
        self.assertEqual(self.decode_snapshot(value), original)
        changed = actions.modal_snapshot(rows, 41)
        self.assertNotEqual(value["source_rows_sha256"], changed["source_rows_sha256"])
        self.assertNotEqual(value["decoded_sha256"], changed["decoded_sha256"])
        self.assertEqual(self.decode_snapshot(changed), rows)
        # The independent reader rejects mismatched metadata, unfinished streams,
        # concatenated members and inflation beyond the declared hard ceiling.
        variants = []
        for field, altered in (
            ("encoding", "raw"), ("decoded_schema", "foreign-schema"),
            ("decoded_bytes", True), ("decoded_bytes", 0), ("decoded_bytes", 1024 * 1024 + 1),
            ("decoded_bytes", value["decoded_bytes"] - 1),
            ("decoded_sha256", "0" * 64), ("source_rows_sha256", "0" * 64),
            ("row_count", value["row_count"] + 1), ("expected_pid", 42),
            ("payload", "invalid-base64"),
        ):
            item = copy.deepcopy(value); item[field] = altered; variants.append(item)
        extra = copy.deepcopy(value); extra["undeclared"] = True; variants.append(extra)
        compressed = base64.b64decode(value["payload"], validate=True)
        for packed in (compressed[:-1], compressed + b"unselected-tail",
                       compressed + zlib.compress(b"second-member", level=6)):
            item = copy.deepcopy(value)
            item["payload"] = base64.b64encode(packed).decode("ascii")
            variants.append(item)
        inflated = b"x" * (1024 * 1024 + 1)
        item = copy.deepcopy(value)
        item.update(decoded_bytes=1024 * 1024,
                    decoded_sha256=hashlib.sha256(inflated).hexdigest(),
                    payload=base64.b64encode(zlib.compress(inflated, level=6)).decode("ascii"))
        variants.append(item)
        for index, item in enumerate(variants):
            with self.subTest(invalid_envelope=index), self.assertRaises(
                    (AssertionError, ValueError, zlib.error)):
                self.decode_snapshot(item)
        self.assertEqual(actions.MODAL_SNAPSHOT_DECODED_BYTES, 1024 * 1024)
        self.assertEqual(actions.MODAL_SNAPSHOT_BYTES, 128 * 1024)
        self.assertEqual(actions.MAX_NODES, 4096)

    def test_decoded_ceiling_rejects_compressible_tree_before_any_effect(self):
        scenario = Scenario()
        for index in range(1040):
            item = row("app/main/text" + str(index), "Text " + str(index), "text")
            item["text"] = "x" * 1024  # Valid per-node size, deliberately compressible.
            scenario.before.append(item)
        raw = canonical_bytes(independent_snapshot(scenario.before))
        self.assertGreater(len(raw), 1024 * 1024)
        self.assertLess(len(base64.b64encode(zlib.compress(raw, level=6))), 128 * 1024)
        with patch.object(actions.zlib, "compress") as compress, \
                self.assertRaisesRegex(actions.GuiRejected, "native_gui_modal_snapshot_budget") as caught:
            scenario.run()
        compress.assert_not_called()
        self.assertTrue(any("decoded_limit_bytes=1048576" in note
                            for note in getattr(caught.exception, "__notes__", [])))
        scenario.effect.assert_not_called()
        self.assert_blocked(scenario)

    def test_snapshot_schema_pid_keys_and_node_count_are_unambiguous(self):
        base = [row("app", "Kdenlive", "application")]
        variants = []
        extra = copy.deepcopy(base); extra[0]["parents"] = []; variants.append(extra)
        missing = copy.deepcopy(base); del missing[0]["modal"]; variants.append(missing)
        duplicate = copy.deepcopy(base); duplicate.append(copy.deepcopy(base[0]))
        variants.append(duplicate)
        for wrong_pid in (42, 41.0, "41", True):
            rows = copy.deepcopy(base); rows[0]["pid"] = wrong_pid; variants.append(rows)
        variants.extend((
            [],
            [row("app/" + str(index), "", "panel") for index in range(4097)],
            tuple(base),
        ))
        for index, rows in enumerate(variants):
            with self.subTest(variant=index), self.assertRaises(actions.GuiRejected):
                actions.modal_snapshot(rows, 41)
        for expected_pid in (41.0, "41", True, None):
            with self.subTest(expected_pid=expected_pid), self.assertRaises(actions.GuiRejected):
                actions.modal_snapshot(base, expected_pid)

    def test_snapshot_field_types_cannot_change_during_encoding(self):
        base = row("app", "Kdenlive", "application")
        variants = []
        for field in ("key", "name", "role", "text"):
            item = copy.deepcopy(base); item[field] = b"not a string"; variants.append(item)
        for field in ("visible", "enabled", "editable", "focused", "modal"):
            item = copy.deepcopy(base); item[field] = 1; variants.append(item)
        for rectangle in (None, (0, 0, 1, 2), [0, 0, 1], [0, 0, 1, 2, 3],
                          [0, 0, True, 2], [0, 0, 1.0, 2], [0, 0, "1", 2]):
            item = copy.deepcopy(base); item["rect"] = rectangle; variants.append(item)
        for index, item in enumerate(variants):
            with self.subTest(variant=index), self.assertRaises(actions.GuiRejected):
                actions.modal_snapshot([item], 41)

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
                    for index in range(256):
                        item = row("app/overflow/" + str(index), "Text " + str(index), "text")
                        item["text"] = hashlib.shake_128(
                            ("synthetic-text-" + str(index)).encode()).hexdigest(512)
                        after.append(item)
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
                    for index in range(256):
                        item = row("app/overflow/" + str(index), "Text " + str(index), "text")
                        item["text"] = hashlib.shake_128(
                            ("synthetic-text-" + str(index)).encode()).hexdigest(512)
                        scenario.before.append(item)
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

    def test_rejected_modal_retains_one_complete_snapshot_without_reconciliation(self):
        scenario = Scenario()
        scenario.after[-1]["name"] = "Synthetic unexpected warning"
        scenario.after.extend(
            row("app/after/panel" + str(index), "Synthetic panel " + str(index), "panel")
            for index in range(2834)
        )
        original_after = copy.deepcopy(scenario.after)
        with self.assertRaises(actions.GuiUncertain) as caught:
            scenario.run()
        self.assert_original_unknown(scenario, caught)
        rejected = [e for e in scenario.events if e["state"] == "MODAL_READBACK_REJECTED"]
        self.assertEqual(len(rejected), 1)
        event = rejected[0]
        self.assertEqual(event["step"], "open.once.modal-readback")
        self.assertEqual(event["request_response"], "NO_REPLY")
        self.assertEqual(event["profile"], "otio_import")
        self.assertEqual(event["expected_pid"], 41)
        self.assertIs(event["complete"], True)
        self.assertEqual(event["classification_error"], "native_gui_modal_unexpected_dialog")
        self.assertEqual(event["snapshot"]["schema"], "motionwright.native.atspi-modal-snapshot/2")
        self.assertEqual(event["snapshot"]["row_count"], 2837)
        self.assertEqual(self.decode_snapshot(event["snapshot"]), original_after)
        self.assertEqual(event["snapshot"]["source_rows_sha256"],
                         hashlib.sha256(canonical_bytes(original_after)).hexdigest())
        self.assertTrue(all(r["pid"] == 41 for r in self.decode_snapshot(event["snapshot"])))
        self.assertLessEqual(len(canonical_bytes(event["snapshot"])), 128 * 1024)
        self.assertLessEqual(len(canonical_bytes(event)), 512 * 1024)
        for field in ("semantic_effect_verified_here", "native_save_or_import_verified",
                      "native_operation_retried", "reconciliation_granted"):
            self.assertIs(event[field], False)
        self.assertTrue(all(blocked for state, blocked in scenario.emission_states
                            if state == "MODAL_READBACK_REJECTED"))
        self.assertFalse(any(e["state"] in ("MODAL_OPEN_RECONCILED", "MODAL_OPEN_OBSERVED")
                             for e in scenario.events))
        failures = [e for e in scenario.events if e["state"] == "OBSERVATION_ERROR_OR_UNKNOWN"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["error"], "native_gui_modal_unexpected_dialog")
        self.assertTrue(any("native_gui_modal_readback_unresolved:GuiRejected:"
                            "native_gui_modal_unexpected_dialog" in note
                            for note in caught.exception.__notes__))
        self.assertEqual(scenario.query_count, 2)
        scenario.effect.assert_called_once_with(0)
        self.assert_blocked(scenario)

    def test_rejected_snapshot_custody_guard_or_deadline_preserves_primary_error(self):
        for response in ("NO_REPLY", "REQUEST_RETURNED"):
            for failure in ("custody", "guard-before", "guard-after",
                            "deadline-before", "deadline-after"):
                with self.subTest(response=response, failure=failure):
                    scenario = Scenario()
                    if response == "REQUEST_RETURNED":
                        scenario.response = True
                    scenario.after[-1]["name"] = "Synthetic unexpected warning"
                    retained = {}
                    classify = scenario.session._modal_readback
                    def observe_classification(rows, kind, source_name, *, before=False, deadline=None):
                        try:
                            return classify(rows, kind, source_name, before=before, deadline=deadline)
                        except actions.GuiRejected as error:
                            if not before:
                                retained["classification"] = error
                                if failure == "deadline-before":
                                    scenario.now += 31.0
                            raise
                    scenario.session._modal_readback = observe_classification
                    def emit(event, current):
                        if event["state"] == "MODAL_READBACK_REJECTED":
                            self.assertTrue(current.journal.blocked)
                            retained["attempt"] = copy.deepcopy(event)
                            retained["emitted"] = True
                            if failure == "custody":
                                raise OSError("synthetic rejected snapshot custody failed")
                            if failure == "deadline-after":
                                current.now += 31.0
                    def guard(current):
                        if "classification" in retained:
                            self.assertTrue(current.journal.blocked)
                            if failure == "guard-before" and not retained.get("emitted"):
                                raise RuntimeError("synthetic owner changed before custody")
                            if failure == "guard-after" and retained.get("emitted"):
                                raise RuntimeError("synthetic owner changed after custody")
                    scenario.emit_hook = emit
                    scenario.guard_hook = guard
                    expected = actions.GuiUncertain if response == "NO_REPLY" else actions.GuiRejected
                    with self.assertRaises(expected) as caught:
                        scenario.run()
                    classification = retained["classification"]
                    self.assertEqual(str(classification), "native_gui_modal_unexpected_dialog")
                    if response == "NO_REPLY":
                        self.assert_original_unknown(scenario, caught)
                    else:
                        self.assertIs(caught.exception, classification)
                        self.assertFalse(any(e["state"] == "ERROR_OR_UNKNOWN" for e in scenario.events))
                    error_type = ("OSError" if failure == "custody" else
                                  "RuntimeError" if failure.startswith("guard") else "GuiRejected")
                    self.assertIn("native_gui_rejected_readback_custody_failed:" + error_type,
                                  classification.__notes__)
                    expected_attempts = 0 if failure in ("guard-before", "deadline-before") else 1
                    expected_retained = 1 if failure in ("guard-after", "deadline-after") else 0
                    self.assertEqual(sum(state == "MODAL_READBACK_REJECTED"
                                         for state, _ in scenario.emission_states), expected_attempts)
                    rejected = [e for e in scenario.events if e["state"] == "MODAL_READBACK_REJECTED"]
                    self.assertEqual(len(rejected), expected_retained)
                    if expected_attempts:
                        attempt = retained["attempt"]
                        self.assertEqual(attempt["request_response"], response)
                        self.assertIs(attempt["reconciliation_granted"], False)
                        self.assertEqual(self.decode_snapshot(attempt["snapshot"]), scenario.after)
                    self.assertFalse(any(e["state"] in ("MODAL_OPEN_RECONCILED", "MODAL_OPEN_OBSERVED")
                                         for e in scenario.events))
                    self.assertEqual(scenario.query_count, 2)
                    scenario.effect.assert_called_once_with(0)
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
                if path == ("Sequence", "Current Item", "Edit Duration"):
                    # The literal duration path performs a read-only owned
                    # preflight before actions. Test its closed-menu branch
                    # with a complete typed tree, rather than bypassing its
                    # journal on an uninitialized synthetic session.
                    observed_events = []
                    session.pid = 41
                    session.journal = actions.ActionJournal(
                        observed_events.append, lambda: None)
                    observed_rows = [
                        row("app", "Kdenlive", "application"),
                        row("app/main", "Untitled — Kdenlive", "frame"),
                        row("app/main/menubar", "", "menu bar"),
                        row("app/main/menubar/sequence", "Sequence", "menu item"),
                        row("app/main/menubar/sequence/menu", "", "menu",
                            visible=False),
                        row("app/main/menubar/sequence/menu/current",
                            "Current Item", "menu item", visible=False),
                    ]
                    def observe_owned_menu():
                        session.last_snapshot = {
                            "complete": True, "rows": observed_rows}
                        return observed_rows
                    session.tree = observe_owned_menu
                session.menu("route", *path)
                session.modal_action.assert_called_once_with(
                    "route." + str(len(path) - 1), kind,
                )
                self.assertEqual(session.control.call_count, len(path) - 1)
                if path == ("Sequence", "Current Item", "Edit Duration"):
                    self.assertEqual(
                        [event["state"] for event in observed_events],
                        ["COMPLETE_READ_ONLY_SELECTION"])
                    self.assertFalse(observed_events[0]["skip_top_level_toggle"])
                    self.assertFalse(observed_events[0]["native_action_performed_here"])
                    self.assertFalse(observed_events[0]["native_operation_retried"])
                    self.assertFalse(observed_events[0]["formal_acceptance"])
                    self.assertFalse(session.journal.blocked)

        # The authenticated pre-render tree of run 43660784941 contains
        # File/Render… and toolbar-Render/Render… simultaneously. A global
        # lookup is ambiguous; the selected item must be owned by File.
        file_row = row("app/1/2/0", "File", "menu item")
        file_render = row("app/1/2/0/0/12", "Render…", "menu item")
        toolbar_render = row("app/1/5/1/0/0", "Render…", "menu item")
        routed = actions.AtspiSession.__new__(actions.AtspiSession)
        routed.journal = actions.ActionJournal(lambda _event: None, lambda: None)
        routed.tree = Mock(return_value=[file_row, file_render, toolbar_render])
        routed.action = Mock()
        with self.assertRaisesRegex(actions.GuiRejected,
                                    "native_gui_control_not_unique"):
            routed.find(("Render...",), ("menu item",))
        routed.menu("editor-before.menu", "File", "Render...")
        self.assertEqual(routed.action.call_count, 2)
        self.assertEqual(
            [(call.args[0], call.args[1]["key"])
             for call in routed.action.call_args_list],
            [("editor-before.menu.0", file_row["key"]),
             ("editor-before.menu.1", file_render["key"])])
        self.assertFalse(routed.journal.blocked)

        # A duplicate *under File* must still stop; foreign toolbar entries
        # cannot serve as fallbacks or grant an extra Native action.
        duplicated = actions.AtspiSession.__new__(actions.AtspiSession)
        duplicated.journal = actions.ActionJournal(
            lambda _event: None, lambda: None)
        duplicated.tree = Mock(return_value=[
            file_row, file_render, toolbar_render,
            row("app/1/2/0/0/13", "Render…", "menu item")])
        duplicated.action = Mock()
        with self.assertRaisesRegex(actions.GuiRejected,
                                    "native_gui_control_not_unique"):
            duplicated.menu("editor-before.menu", "File", "Render...")
        duplicated.action.assert_called_once()
        self.assertEqual(duplicated.action.call_args.args[1]["key"],
                         file_row["key"])
        self.assertTrue(duplicated.journal.blocked)
        denied = actions.AtspiSession.__new__(actions.AtspiSession)
        denied.journal = actions.ActionJournal(lambda _event: None, lambda: None)
        denied.journal.blocked = True
        denied.tree = Mock(return_value=[file_row, file_render, toolbar_render])
        denied.action = Mock()
        with self.assertRaises(actions.GuiRejected):
            denied.menu("editor-before.menu", "File", "Render...")
        denied.action.assert_not_called()
        denied.tree.assert_not_called()

        # Import may populate >512 descendants in one timeline node after
        # Submit. Original dialog closure must check every *direct* owned
        # application window, not increase that 512-node traversal budget.
        class RootNode:
            def __init__(self, role, name="", *, pid=41, visible=False,
                         modal=False, children=()):
                self.role, self.name, self.pid = role, name, pid
                self.visible, self.modal, self.children = visible, modal, list(children)
                self.child_count_calls = 0

            def get_process_id(self): return self.pid
            def get_role_name(self): return self.role
            def get_name(self): return self.name
            def clear_cache_single(self): return None
            def get_child_count(self):
                self.child_count_calls += 1
                return len(self.children)
            def get_child_at_index(self, index): return self.children[index]
            def get_state_set(self):
                return SimpleNamespace(contains=lambda state:
                    self.visible if state == "SHOWING"
                    else self.modal if state == "MODAL" else False)

        timeline = RootNode("frame", "Untitled / HD 1080p 25 fps - Kdenlive",
                            visible=True,
                            children=[RootNode("filler")] * (actions.MAX_CHILDREN + 1))
        dialog = RootNode("dialog", "OpenTimelineIO Import — Kdenlive",
                          visible=True)
        app = RootNode("application", children=[
            RootNode("filler"), timeline, RootNode("frame"),
            RootNode("filler"), dialog,
        ])
        desktop = RootNode("desktop", children=[
            RootNode("application", pid=99), app,
        ])
        observed = actions.AtspiSession.__new__(actions.AtspiSession)
        observed.pid = 41
        observed.journal = actions.ActionJournal(lambda _event: None, lambda: None)
        observed.api = SimpleNamespace(
            get_desktop=lambda index: desktop if index == 0 else None,
            StateType=SimpleNamespace(SHOWING="SHOWING", MODAL="MODAL"))
        original = row("app/4", "OpenTimelineIO Import — Kdenlive", "dialog")
        before = observed._owned_top_level_windows()
        self.assertEqual(len(before), 5)
        self.assertEqual(before[4]["visible"], True)
        self.assertIsNone(actions.closed_original_file_dialog(
            original, ("OpenTimelineIO Import",), before, 41))
        self.assertEqual(timeline.child_count_calls, 0)
        dialog.visible = False
        closed = observed._observed_file_dialog_closed(
            original, ("OpenTimelineIO Import",))
        self.assertTrue(closed["original_dialog_not_showing"])
        self.assertEqual(closed["consistent_direct_root_observations"], 2)
        self.assertEqual(closed["top_level_count"], 5)
        self.assertFalse(closed["whole_accessibility_tree_scanned"])
        self.assertFalse(closed["native_import_verified"])
        self.assertFalse(closed["native_save_verified"])
        self.assertFalse(closed["automatic_retry"])
        self.assertFalse(closed["formal_acceptance"])
        self.assertEqual(timeline.child_count_calls, 0)
        self.assertEqual(app.child_count_calls, 3)

        dialog.visible = True
        dialog.name = "Unexpected Warning — Kdenlive"
        with self.assertRaisesRegex(actions.GuiRejected,
                                    "native_gui_root_close_unexpected_dialog"):
            actions.closed_original_file_dialog(
                original, ("OpenTimelineIO Import",),
                observed._owned_top_level_windows(), 41)
        dialog.pid = 99
        with self.assertRaisesRegex(actions.GuiRejected,
                                    "native_gui_root_child_foreign_pid"):
            observed._owned_top_level_windows()
        dialog.pid = 41
        app.children.extend([RootNode("filler")] * 28)
        with self.assertRaisesRegex(actions.GuiRejected,
                                    "native_gui_root_window_budget"):
            observed._owned_top_level_windows()
        self.assertEqual(timeline.child_count_calls, 0)

        for path in (("File", "Quit"), ("Other", "Save As..."),
                     ("File", "OpenTimelineIO Import")):
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


class RenderFieldCustodyTests(unittest.TestCase):
    """Offline selection/custody regressions; never live Native evidence."""

    def observed(self, fields):
        label = row("app/1/output-label", "Output file", "label")
        label["rect"] = [10, 10, 80, 20]
        dialog = row("app/1", "Rendering", "dialog")
        rows = [dialog, label] + fields
        session = actions.AtspiSession.__new__(actions.AtspiSession)
        session.pid = 41
        events, reads = [], []
        def tree():
            reads.append("original-observation")
            session.last_snapshot = {"complete": True, "rows": rows}
            return rows
        session.tree = tree
        session.journal = SimpleNamespace(emit=events.append)
        def forbidden(*_args, **_kwargs):
            self.fail("Field resolution/custody must not perform a Native action")
        session.text = session.action = session.focus = forbidden
        return session, dialog, rows, events, reads

    def field(self, key, rect=(100, 10, 200, 20)):
        value = row(key, "", "text")
        value.update(editable=True, text="/original/output.mov", rect=list(rect))
        return value

    def test_unique_and_nested_editable_leaf_keep_original_selection(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                leaf = self.field("app/1/field/line")
                fields = [self.field("app/1/field"), leaf] if nested else [leaf]
                session, dialog, rows, events, reads = self.observed(fields)
                selected = session.render_output_field("before-edit", dialog)
                self.assertIs(selected, leaf)
                self.assertEqual(reads, ["original-observation"])
                self.assertEqual(events, [])

    def test_zero_and_multiple_candidates_retain_exact_original_tree(self):
        for fields in ([], [self.field("app/1/a"), self.field("app/1/b")]):
            with self.subTest(candidate_count=len(fields)):
                session, dialog, rows, events, reads = self.observed(fields)
                with self.assertRaisesRegex(actions.GuiRejected,
                                            "native_gui_labeled_field_not_unique"):
                    session.render_output_field("before-edit", dialog)
                self.assertEqual(reads, ["original-observation"])
                self.assertEqual(len(events), 1)
                event = events[0]
                self.assertEqual(event["label_keys"], ["app/1/output-label"])
                self.assertEqual(event["geometric_candidate_keys"], [r["key"] for r in fields])
                self.assertEqual(event["editable_leaf_keys"], [r["key"] for r in fields])
                self.assertEqual(event["snapshot"], independent_snapshot(rows))
                for flag in ("new_accessibility_query", "native_render_dispatched_here",
                             "native_operation_retried", "semantic_effect_verified", "formal_acceptance"):
                    self.assertIs(event[flag], False)

    def test_recording_error_preserves_selection_error_without_requery(self):
        session, dialog, _rows, events, reads = self.observed([])
        def failed_sink(_event):
            raise OSError("retained sink unavailable")
        session.journal.emit = failed_sink
        with self.assertRaisesRegex(actions.GuiRejected,
                                    "native_gui_labeled_field_not_unique") as caught:
            session.render_output_field("after-edit", dialog)
        self.assertIn("native_render_output_custody_failed:OSError", caught.exception.__notes__)
        self.assertEqual(reads, ["original-observation"])
        self.assertEqual(events, [])

    def test_foreign_snapshot_pid_cannot_replace_original_error(self):
        session, dialog, rows, events, reads = self.observed([])
        rows[1]["pid"] = 99
        with self.assertRaisesRegex(actions.GuiRejected,
                                    "native_gui_labeled_field_not_unique") as caught:
            session.render_output_field("before-edit", dialog)
        self.assertIn("native_render_output_custody_failed:GuiRejected", caught.exception.__notes__)
        self.assertEqual(reads, ["original-observation"])
        self.assertEqual(events, [])



class RenderOutputDialogTests(unittest.TestCase):
    """Synthetic contracts for the original KUrlRequester structure; no UI."""

    @staticmethod
    def output_rows():
        main = row("app/0", "Project - Kdenlive", "frame")
        parent = row("app/1", "Rendering", "dialog")
        label = row("app/1/0/0/2", "Output file", "label")
        label["rect"] = [168, 58, 64, 32]
        requester = row("app/1/0/0/3", "Output file", "filler")
        requester["rect"] = [238, 58, 459, 32]
        editor = row(requester["key"] + "/0", "", "filler")
        editor["rect"] = [238, 60, 421, 28]
        clear = row(editor["key"] + "/0", "", "push button")
        clear["rect"] = [633, 65, 22, 18]
        browse = row(requester["key"] + "/1", "", "push button")
        browse["rect"] = [665, 58, 32, 32]
        return [main, parent, label, requester, editor, clear, browse]

    @staticmethod
    def x11_parent():
        return {"schema": "motionwright.native.x11-modal-parent/1",
                "complete": True, "expected_pid": 41, "window": 21,
                "name": "Rendering",
                "allowed_types": ["_NET_WM_WINDOW_TYPE_NORMAL", "_NET_WM_WINDOW_TYPE_DIALOG"],
                "records": [{"synthetic": True}],
                "properties": {"pid": 41, "name": "Rendering",
                               "types": ["_NET_WM_WINDOW_TYPE_DIALOG"], "states": [],
                               "transient_for": 11, "client_leader": 11}}

    def test_direct_requester_button_excludes_nested_clear_and_unrelated_controls(self):
        rows = self.output_rows()
        rows.append(row("app/1/unrelated", "", "push button"))
        selected = actions.render_output_browse_button(rows, 41)
        self.assertEqual(selected["key"], "app/1/0/0/3/1")
        self.assertEqual(selected["url_requester"], rows[3])
        self.assertEqual(selected["output_label"], rows[2])
        self.assertFalse(rows[4]["editable"])
        with self.assertRaisesRegex(actions.GuiRejected, "labeled_field_not_unique"):
            actions.labeled_field(rows[2:], ("Output file",))

    def test_ambiguous_foreign_hidden_disabled_and_misplaced_buttons_stop(self):
        for mutation in ("duplicate", "foreign", "hidden", "disabled", "outside", "missing", "other-label"):
            with self.subTest(mutation=mutation):
                rows = self.output_rows()
                if mutation == "duplicate":
                    rows.append({**rows[-1], "key": "app/1/0/0/3/2"})
                elif mutation == "foreign": rows[-1]["pid"] = 99
                elif mutation == "hidden": rows[-1]["visible"] = False
                elif mutation == "disabled": rows[-1]["enabled"] = False
                elif mutation == "outside": rows[-1]["rect"][0] = 1000
                elif mutation == "missing": rows.pop()
                else: rows[2]["name"] = "Presets"
                with self.assertRaises(actions.GuiRejected):
                    actions.render_output_browse_button(rows, 41)

    def test_output_modal_requires_original_rendering_parent_and_direct_x11_relation(self):
        session = actions.AtspiSession.__new__(actions.AtspiSession)
        session.pid = 41
        session.journal = actions.ActionJournal(lambda _event: None, lambda: None)
        before = self.output_rows()
        parent = self.x11_parent()
        with patch.object(actions.x11_modal, "observe_parent", return_value=parent) as observe:
            session._modal_x11_parent = session._observe_modal_parent(before, "render_output")
        self.assertEqual(observe.call_args.kwargs["parent_name"], "Rendering")
        after = copy.deepcopy(before)
        after[1]["enabled"] = False
        dialog = row("app/2", "Save As", "dialog", modal=True)
        after.append(dialog)
        with patch.object(actions.x11_modal, "observe_modal", return_value={"complete": True}) as observe:
            self.assertEqual(session._modal_readback(after, "render_output", None, deadline=30), dialog)
        self.assertFalse(observe.call_args.kwargs["group"])
        self.assertIs(observe.call_args.kwargs["parent_observation"], parent)
        after[1]["key"] = "app/changed"
        with self.assertRaisesRegex(actions.GuiRejected, "modal_parent_changed"):
            session._modal_readback(after, "render_output", None, deadline=30)

    def test_no_reply_is_reconciled_by_one_original_browse_without_replay(self):
        for response in ("no-reply", "other-error"):
            with self.subTest(response=response):
                scenario = Scenario()
                scenario.kind = "render_output"
                scenario.before = self.output_rows()
                scenario.target = scenario.before[-1]
                scenario.after = copy.deepcopy(scenario.before)
                scenario.after[1]["enabled"] = False
                scenario.after.append(row("app/2", "Save As", "dialog", modal=True))
                scenario.snapshots = [snapshot(scenario.before), snapshot(scenario.after)]
                scenario.session._observe_modal_parent = actions.AtspiSession._observe_modal_parent.__get__(scenario.session)
                if response == "other-error":
                    scenario.response = SyntheticGlibError(code=2)
                with patch.object(actions.x11_modal, "observe_parent", return_value=self.x11_parent()), \
                     patch.object(actions.x11_modal, "observe_modal", return_value={"complete": True}):
                    if response == "no-reply":
                        self.assertEqual(scenario.run()["name"], "Save As")
                        self.assertFalse(scenario.journal.blocked)
                    else:
                        with self.assertRaises(actions.GuiUncertain): scenario.run()
                        self.assertTrue(scenario.journal.blocked)
                scenario.effect.assert_called_once_with(0)
                self.assertEqual(sum(e["state"] == "ERROR_OR_UNKNOWN" for e in scenario.events), 1)
                self.assertEqual(sum(e["state"] == "INTENT" for e in scenario.events), 1)

    def test_close_permits_only_original_rendering_parent_and_rejects_other_modals(self):
        parent = self.output_rows()[1]
        original = row("app/2", "Save As", "dialog", modal=True)
        fields = ("key", "name", "role", "pid", "visible", "modal")
        windows = [{k: r[k] for k in fields} for r in
                   (self.output_rows()[0], parent, {**original, "visible": False})]
        closed = actions.closed_original_file_dialog(original, ("Save As",), windows, 41, render_parent=parent)
        self.assertEqual(closed["retained_render_parent"]["key"], parent["key"])
        self.assertFalse(closed["native_save_verified"])
        windows[2]["visible"] = True
        self.assertIsNone(actions.closed_original_file_dialog(original, ("Save As",), windows, 41, render_parent=parent))
        for mutation in ("overwrite", "foreign-parent", "missing-parent"):
            changed = copy.deepcopy(windows)
            if mutation == "overwrite": changed[2]["name"] = "Confirm overwrite"
            elif mutation == "foreign-parent": changed[1]["pid"] = 99
            else: changed[1]["visible"] = False
            with self.subTest(mutation=mutation), self.assertRaises(actions.GuiRejected):
                actions.closed_original_file_dialog(original, ("Save As",), changed, 41, render_parent=parent)
        with self.assertRaisesRegex(actions.GuiRejected, "root_close_profile"):
            actions.closed_original_file_dialog(original, ("OpenTimelineIO Import",), windows, 41, render_parent=parent)

    def target_session(self, output, *, observed_text=None):
        session = actions.AtspiSession.__new__(actions.AtspiSession)
        session.pid = 41; events = []
        session.journal = actions.ActionJournal(events.append, lambda: None)
        rows = self.output_rows()
        dialog = row("app/2", "Save As", "dialog", modal=True)
        label = row("app/2/label", "File name:", "label")
        label["rect"] = [0, 0, 50, 20]
        field = row("app/2/filename", "", "text")
        field.update(editable=True, rect=[50, 0, 200, 20],
                     text=str(output) if observed_text is None else observed_text)
        accept = row("app/2/save", "Save", "push button")
        rows.extend((dialog, label, field, accept))
        session._render_output_atspi_parent = dict(rows[1])
        session._modal_x11_parent = self.x11_parent()
        session.modal_action = Mock(return_value=dialog)
        session.dialog = Mock(return_value=dialog)
        session.descendants = Mock(return_value=[label, field, accept])
        session.text = Mock()
        session._complete_modal_tree = Mock(return_value=rows)
        session.last_snapshot = snapshot(rows)
        session.action = Mock()
        fields = ("key", "name", "role", "pid", "visible", "modal")
        closed = [{k: r[k] for k in fields} for r in
                  (rows[0], rows[1], {**dialog, "visible": False})]
        session._owned_top_level_windows = Mock(return_value=closed)
        return session, events

    def test_wrong_filename_cannot_submit_or_claim_requester_readback(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "new.mov"
            session, events = self.target_session(output, observed_text="/foreign.mov")
            with patch.object(actions.x11_modal, "observe_modal", return_value={"complete": True}), \
                 self.assertRaisesRegex(actions.GuiRejected, "filename_readback"):
                session.render_output_target("editor-before.output", output)
            session.action.assert_not_called()
            self.assertTrue(session.journal.blocked)
            session._complete_modal_tree.assert_called_once()
            custody = [e for e in events if e["step"].endswith(".selection-last-complete-tree")]
            self.assertEqual(len(custody), 1)
            self.assertTrue(custody[0]["complete"])
            self.assertEqual(custody[0]["snapshot"], independent_snapshot(session.last_snapshot["rows"]))
            self.assertFalse(custody[0]["new_accessibility_query"])
            self.assertFalse(any("requester_text_read_back" in e for e in events))
            output.write_bytes(b"occupied")
            fresh, _ = self.target_session(output)
            with self.assertRaisesRegex(actions.GuiRejected, "target_not_fresh"):
                fresh.render_output_target("editor-before.output", output)
            fresh.modal_action.assert_not_called()

    def test_selection_retains_exact_name_and_parent_but_requires_playlist_target(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "new.mov"
            for changed_parent in (False, True):
                session, events = self.target_session(output)
                after = self.x11_parent()
                if changed_parent: after["window"] += 1
                with patch.object(actions.x11_modal, "observe_modal", return_value={"complete": True}), \
                     patch.object(actions.x11_modal, "observe_parent", return_value=after):
                    if changed_parent:
                        with self.assertRaisesRegex(actions.GuiRejected, "X11_parent_changed"):
                            session.render_output_target("editor-after.output", output)
                        self.assertTrue(session.journal.blocked)
                    else:
                        selected = session.render_output_target("editor-after.output", output)
                        self.assertEqual(selected["filename_original"]["text"], str(output))
                        self.assertTrue(selected["playlist_target_verification_required"])
                        self.assertFalse(selected["requester_text_read_back"])
                        self.assertFalse(selected["native_render_dispatched_here"])
                        self.assertEqual(selected["closure"]["consistent_direct_root_observations"], 2)
                        filename = [e for e in events if e["step"].endswith(".filename-original")]
                        self.assertEqual(len(filename), 1)
                        self.assertTrue(filename[0]["complete"])
                        self.assertEqual(filename[0]["snapshot"], independent_snapshot(session.last_snapshot["rows"]))
                        self.assertIs(filename[0]["x11_modal"], session.last_x11_modal)
                session.modal_action.assert_called_once_with("editor-after.output.browse", "render_output")
                session.action.assert_called_once()
                self.assertFalse(output.exists())


    def test_x11_rejection_keeps_primary_cause_when_failure_custody_sink_fails(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "new.mov"
            session, events = self.target_session(output)
            original = actions.x11_modal.X11Rejected("synthetic_direct_parent_rejected")
            def failed_custody(event):
                if event["state"] in ("READ_ONLY_FAILURE_CUSTODY", "OBSERVATION_ERROR_OR_UNKNOWN"):
                    raise OSError("synthetic original sink unavailable")
                events.append(event)
            session.journal.emit = failed_custody
            with patch.object(actions.x11_modal, "observe_modal", side_effect=original), \
                 self.assertRaisesRegex(actions.GuiRejected, "synthetic_direct_parent_rejected") as caught:
                session.render_output_target("editor-before.output", output)
            self.assertIs(caught.exception.__cause__, original)
            self.assertTrue(session.journal.blocked)
            self.assertIs(session.journal.last_failure["original"], caught.exception)
            self.assertFalse(session.journal.last_failure["retained"])
            self.assertIn("native_render_output_selection_custody_failed:OSError", caught.exception.__notes__)
            self.assertIn("native_gui_journal_recording_failed:OSError", caught.exception.__notes__)
            session.text.assert_called_once()
            session.action.assert_not_called()
            session._complete_modal_tree.assert_called_once()
            session._owned_top_level_windows.assert_not_called()

    def test_submit_uncertainty_preserves_recorded_primary_and_does_not_observe_close(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "new.mov"
            session, events = self.target_session(output)
            original = SyntheticGlibError()
            effect = Mock(side_effect=original)
            def uncertain(step, target):
                return session.journal.perform(step, target, effect)
            session.action = Mock(side_effect=uncertain)
            with patch.object(actions.x11_modal, "observe_modal", return_value={"complete": True}), \
                 self.assertRaises(actions.GuiUncertain) as caught:
                session.render_output_target("editor-before.output", output)
            self.assertIs(caught.exception.__cause__, original)
            self.assertTrue(session.journal.blocked)
            self.assertIs(session.journal.last_failure["original"], original)
            self.assertEqual(session.journal.last_failure["step"], "editor-before.output.submit")
            self.assertTrue(session.journal.last_failure["retained"])
            self.assertEqual(sum(e["state"] == "ERROR_OR_UNKNOWN" for e in events), 1)
            self.assertEqual(sum(e["state"] == "OBSERVATION_ERROR_OR_UNKNOWN" for e in events), 0)
            effect.assert_called_once()
            session._owned_top_level_windows.assert_not_called()
            next_effect = Mock()
            with self.assertRaisesRegex(actions.GuiRejected, "previous_action_uncertain"):
                session.journal.perform("forbidden.generate-script", {}, next_effect)
            next_effect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
