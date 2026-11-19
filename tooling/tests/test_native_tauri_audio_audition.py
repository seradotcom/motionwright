"""Focused pure guards; no desktop, Native process, media, keys or acceptance run."""
from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import subprocess
import time
from types import SimpleNamespace
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "tooling" / "native_tauri_audio_audition.py"


def pure_module():
    # Extract the actual pure predicates and once-only dispatcher. Do not import
    # the physical harness, GTK, installed-runtime tooling or native producers.
    tree = ast.parse(PATH.read_text(encoding="utf-8"))
    functions = {"need", "equal_json", "exact_json", "validate_ui_observation",
                 "validate_native_records", "validate_saved_readback", "validate_process_closure",
                 "validate_pcm_projection", "validate_before", "read_only_text_provider",
                 "private_transport_paths", "exact_text_offsets", "screen_rect", "rect_contains",
                 "atspi_read_no_reply", "validate_close_keyboard_target", "parse_closure_process_status",
                 "unmapped_action_provider", "accessible_action_index", "source_key_sequence",
                 "source_key_receipt_name", "source_key_journal_plan", "journal_readback"}
    classes = {"AudioError", "AudioUI"}
    constants = {"PREVIEW", "AUDITION", "AUDITION_PCM_READY",
                 "REQUIRED_ACTIONS", "SCROLL_ACTIONS", "SHA256", "X11_INPUT_FOCUS_PROBE"}
    selected = [node for node in tree.body if
                isinstance(node, ast.FunctionDef) and node.name in functions
                or isinstance(node, ast.ClassDef) and node.name in classes
                or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                                                        and target.id in constants
                                                        for target in node.targets)]
    observed = {node.name for node in selected if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    if observed != functions | classes:
        raise AssertionError("pure guard extraction is incomplete")
    namespace = {"uuid": uuid, "re": re, "json": json, "time": time, "os": os, "hashlib": hashlib, "sys": sys,
                 "pcm": SimpleNamespace(WAV_BYTES=384044, FRAMES=96000),
                 "accessible": SimpleNamespace(VISIBLE=frozenset(("visible", "showing")),
                                                NotReady=type("NotReady", (RuntimeError,), {}),
                                                unique=lambda items, _: items[0] if len(items) == 1 else None)}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(PATH), "exec"), namespace)
    return SimpleNamespace(**namespace)


GUARDS = pure_module()


def data():
    ids = [str(uuid.UUID(int=value)) for value in (11, 12, 13, 14)]
    before = {
        "project_id": ids[0], "generation": ids[1], "deliverable_profile_id": ids[2],
        "revision_before": 1, "database_path": "/private/profile/data/app/motionwright.sqlite3",
        "database_identity": "unix:10:20",
        "cas_root": {"path": "/private/profile/data/app/blobs",
                     "identity": {"device": 10, "inode": 21, "mode": 16832, "uid": 1000, "gid": 1000}},
        "expected_change": {"type": "set_delivery_recipe", "deliverable_profile_id": ids[2],
                            "recipe": {"original_mix": {"music_asset_id": "music", "sfx_asset_id": "sfx", "sfx_gain_db": -8.0}}},
        "assets": [{"bus": name, "asset_id": name, "sha256": str(index) * 64}
                   for index, name in enumerate(("voice", "music", "sfx"), 1)],
        "history": [{"revision": 1, "change": {"type": "prior_event"}}],
    }
    binding = {"pid": 7001, "window": "0xa00", "start_time": "23456"}
    context = {"source_sha": "a" * 40, "sdk_sha": "b" * 40, "run_id": "12345"}
    before_id, manifest_id = {"sha256": "c" * 64}, {"sha256": "d" * 64}
    ui = {"status": "PASS", "binding": deepcopy(binding),
          "project_id": ids[0], "generation": ids[1], "revision_before": 1, "revision_after": 2,
          "opened": {"text": "r1", "matches": 1},
          "draft_source_bindings": {"text": "Draft source bindings: music music; SFX sfx.", "matches": 1},
          "saved": {"text": "Saved recipe at revision r2.", "matches": 1},
          "preview": {"text": GUARDS.PREVIEW, "matches": 1},
          "preview_binding": deepcopy(binding),
          "keyboard_target": {"active_window": binding["window"], "input_focus_ancestry": [binding["window"]]},
          "actions": list(GUARDS.REQUIRED_ACTIONS), "normal_close_requested": True,
          "playback_or_human_listening_claimed": False,
          "formal_G2": False, "formal_G3": False, "automatic_retry": False}
    common = {**context, "run_attempt": "1", "project_id": ids[0], "generation": ids[1],
              "deliverable_profile_id": ids[2], "desktop_pid": binding["pid"],
              "database_path": before["database_path"], "database_identity": before["database_identity"],
              "cas_path": before["cas_root"]["path"], "cas_identity": before["cas_root"]["identity"],
              "manifest_sha256": manifest_id["sha256"], "before_receipt_sha256": before_id["sha256"],
              "workspace_session": ids[3], "revision": 2,
              "formal_G2": False, "formal_G3": False, "human_approval": False,
              "release_authorized": False, "automatic_retry": False,
              "source_classification": "SYNTHETIC_TONES_NOT_SPEECH"}
    records = {}
    for name, schema in (
        ("startup", "startup"), ("intent", "intent"), ("prepared-response", "prepared-response"),
        ("writer-handoff-intent", "writer-handoff-intent"), ("writer-handoff", "writer-handoff"),
    ):
        records[name + ".original.json"] = {
            **deepcopy(common), "schema": "motionwright.development-audition-" + schema + "/1"}
    records["startup.original.json"].update(revision=1, native_effect_or_owner_granted=False)
    records["prepared-response.original.json"].update(
        prepared_for_original_tauri_response=True, ipc_delivery_observed=False,
        ui_observation_required=True, saved_recipe=deepcopy(before["expected_change"]["recipe"]))
    records["writer-handoff.original.json"].update(
        writer_handoff_prepared=True, writer_process_closed=False,
        closure_requires_independent_process_observation=True)
    readback = {
        "schema": "motionwright.tauri-audio-independent-readback/1",
        "reader_authority": "StudioServiceReader", "source_witness_sha256": before_id["sha256"],
        **{key: deepcopy(before[key]) for key in
           ("project_id", "generation", "deliverable_profile_id", "expected_change",
            "database_path", "database_identity", "cas_root", "assets")},
        "previous_revision": 1, "revision": 2, "additional_journal_events": 0,
        "verified_original_assets": 3, "verified_original_bytes": 3 * 384044,
        "writer_handoff_verified": True, "same_physical_database": True,
        "same_physical_cas_root": True, "exactly_one_recipe_save": True,
        "source_classification": "SYNTHETIC_TONES_NOT_SPEECH",
        "tauri_owner_invocation": "REQUIRES_EXTERNAL_UI_AND_CAPTURE_JOIN",
        "mixed_pcm_read_or_generated_by_reader": False, "human_approval": False,
        "release_authorized": False, "formal_G2": False, "formal_G3": False, "automatic_retry": False,
        "observed_history": [*deepcopy(before["history"]),
                             {"revision": 2, "change": deepcopy(before["expected_change"])}],
        "observed_project": {"id": ids[0], "generation": ids[1], "revision": 2,
                             "production_design": {"delivery_recipes": {
                                 ids[2]: deepcopy(before["expected_change"]["recipe"])}}},
    }
    return before, binding, context, before_id, manifest_id, ui, records, readback


class AudioJoinTests(unittest.TestCase):
    def native(self, values):
        before, binding, context, before_id, manifest_id, ui, records, _ = values
        return GUARDS.validate_native_records(records, context, before, before_id, manifest_id, binding, ui)

    def test_private_socket_directories_fit_and_remain_in_the_same_owner(self):
        root = Path("/home/runner/work/_temp/motionwright-tauri-audition-44081392672-1")
        paths = GUARDS.private_transport_paths(root)
        self.assertEqual(set(paths), {"XDG_RUNTIME_DIR", "TMPDIR"})
        self.assertTrue(all(path.parent == root for path in paths.values()))
        self.assertTrue(all(len(os.fsencode(path)) <= 70 for path in paths.values()))
        self.assertGreater(len(os.fsencode(root / "profile" / "runtime")), 70)
        with self.assertRaisesRegex(GUARDS.AudioError, "PRIVATE_TRANSPORT_PATH_TOO_LONG"):
            GUARDS.private_transport_paths(root / ("extra" * 8))

    def test_readonly_text_provider_cannot_be_hidden_editable_or_defunct(self):
        states = frozenset(("visible", "showing"))
        interfaces = frozenset(("Text", "Component"))
        self.assertTrue(GUARDS.read_only_text_provider(states, interfaces))
        for invalid in (states - {"showing"}, states - {"visible"},
                        states | {"editable"}, states | {"defunct"}):
            self.assertFalse(GUARDS.read_only_text_provider(invalid, interfaces))
        self.assertFalse(GUARDS.read_only_text_provider(states, interfaces | {"EditableText"}))
        self.assertFalse(GUARDS.read_only_text_provider(states, interfaces - {"Text"}))

    def test_unmapped_native_action_requires_real_usable_noneditable_interfaces(self):
        states = frozenset(("visible", "enabled", "sensitive"))
        interfaces = frozenset(("Accessible", "Action", "Component"))
        # An off-screen control may be scrolled through its real Component
        # interface; click still independently requires SHOWING and ownership.
        self.assertTrue(GUARDS.unmapped_action_provider(states, interfaces))
        for invalid in (states - {"visible"}, states - {"enabled"},
                        states - {"sensitive"}, states | {"editable"}, states | {"defunct"}):
            self.assertFalse(GUARDS.unmapped_action_provider(invalid, interfaces))
        for invalid in (interfaces - {"Action"}, interfaces - {"Component"},
                        interfaces | {"EditableText"}):
            self.assertFalse(GUARDS.unmapped_action_provider(states, invalid))

    def test_source_keyboard_plans_are_bounded_to_original_two_assets(self):
        self.assertEqual(GUARDS.source_key_sequence(1, 3), ("Home", "Down", "Tab"))
        self.assertEqual(GUARDS.source_key_sequence(2, 3),
                         ("Home", "Down", "Down", "Tab"))
        for index, count in ((0, 3), (3, 3), (1, 4), (1, 2), (True, 3), (2, 3.0)):
            with self.subTest(index=index, count=count), self.assertRaisesRegex(
                    GUARDS.AudioError, "SOURCE_KEYBOARD_OPTION_BUDGET"):
                GUARDS.source_key_sequence(index, count)

    def test_keyboard_receipts_match_actual_disk_journal_without_collisions(self):
        import re
        observed = set()
        for label in ("select-music", "select-sfx"):
            for i in range(4):
                for phase in ("intent", "return"):
                    name = GUARDS.source_key_receipt_name(label, i, phase)
                    self.assertRegex(name, r"^[a-z-]+$")
                    self.assertNotIn(name, observed)
                    observed.add(name)
        self.assertEqual(len(observed), 16)
        for label, index, phase in (("select-music", 4, "intent"),
                                    ("select-sfx", -1, "intent"),
                                    ("select-voice", 1, "return"),
                                    ("select-music", True, "intent"),
                                    ("select-sfx", 2, "unknown")):
            with self.assertRaisesRegex(GUARDS.AudioError, "UI_SOURCE_JOURNAL_NAME_BOUND"):
                GUARDS.source_key_receipt_name(label, index, phase)

    def test_original_key_journal_plan_has_seven_bounded_actual_events(self):
        plan = GUARDS.source_key_journal_plan()
        self.assertEqual([label for label, _, _ in plan],
                         ["select-music"] * 3 + ["select-sfx"] * 4)
        self.assertEqual([key for _, _, key in plan],
                         ["Home", "Down", "Tab", "Home", "Down", "Down", "Tab"])
        names = [GUARDS.source_key_receipt_name(label, i, phase)
                 for label, i, _ in plan for phase in ("intent", "return")]
        self.assertEqual(len(names), 14)
        self.assertEqual(len(set(names)), 14)

    def test_complete_original_journal_roster_accepts_only_bound_key_receipts(self):
        _, binding, _, _, _, ui, _, _ = data()
        records = {}
        for label in ui["actions"]:
            intent = {"binding": deepcopy(binding), "automatic_retry": False}
            if label == "window-close":
                intent["method"] = "AT_SPI_HARDWARE_ALT_F4"
                intent["preview_binding"] = deepcopy(binding)
                intent["keyboard_target"] = deepcopy(ui["keyboard_target"])
                returned = {"keyboard_dispatch_acknowledged": True,
                            "writer_handoff_not_inferred": True,
                            "process_exit_not_inferred": True}
            else:
                returned = {"accepted": True}
            records[label + "-intent.json"] = intent
            records[label + "-return.json"] = returned
        for label, index, key in GUARDS.source_key_journal_plan():
            number = {"Home": 110, "Down": 116, "Tab": 23}[key]
            records[GUARDS.source_key_receipt_name(label, index, "intent") + ".json"] = {
                "key": key, "hardware_keycode": number, "automatic_retry": False}
            records[GUARDS.source_key_receipt_name(label, index, "return") + ".json"] = {
                "hardware_press_and_release_acknowledged": True}
        for name, number in (("close-alt", 64), ("close-f", 70)):
            for phase in ("press", "release"):
                records[name + "-" + phase + "-intent.json"] = {"hardware_keycode": number}
                if phase == "press":
                    records[name + "-" + phase + "-intent.json"].update(
                        binding=deepcopy(binding), keyboard_target=deepcopy(ui["keyboard_target"]))

        class VirtualPath:
            def __init__(self, relative=""):
                self.relative = relative
            def __truediv__(self, part):
                return VirtualPath(self.relative + "/" + part)
            @property
            def name(self):
                return self.relative.split("/")[-1]
            def iterdir(self):
                return [VirtualPath("/" + name) for name in records]

        namespace = GUARDS.journal_readback.__globals__
        previous = namespace.get("read_json")
        namespace["read_json"] = lambda path, maximum, root: (
            deepcopy(records[path.name]), {"archive_path": path.name, "bytes": 1,
                                          "sha256": "a" * 64})
        try:
            witnesses = GUARDS.journal_readback(VirtualPath(), ui, binding)
            self.assertEqual(len(witnesses), len(records))
            chosen = GUARDS.source_key_receipt_name("select-sfx", 2, "intent") + ".json"
            records[chosen]["key"] = "Home"
            with self.assertRaisesRegex(GUARDS.AudioError, "SOURCE_HARDWARE_KEY_JOURNAL"):
                GUARDS.journal_readback(VirtualPath(), ui, binding)
            records[chosen]["key"] = "Down"
            records["close-f-press-intent.json"]["keyboard_target"]["input_focus_ancestry"] = ["0xb00"]
            with self.assertRaisesRegex(GUARDS.AudioError, "UI_CLOSE_INPUT_FOCUS_NOT_OWNED"):
                GUARDS.journal_readback(VirtualPath(), ui, binding)
            records["close-f-press-intent.json"]["keyboard_target"] = deepcopy(ui["keyboard_target"])
            records.pop(chosen)
            with self.assertRaisesRegex(GUARDS.AudioError, "UI_ORIGINAL_JOURNAL_ROSTER"):
                GUARDS.journal_readback(VirtualPath(), ui, binding)
        finally:
            if previous is None:
                namespace.pop("read_json", None)
            else:
                namespace["read_json"] = previous

    def test_keyboard_source_selection_rejects_foreign_process_before_focus(self):
        owner_queries = []
        bridge = SimpleNamespace(owner_pid=lambda _: owner_queries.append(True) or 7002)
        ui = GUARDS.AudioUI(bridge, None, {"pid": 7001}, {}, None, time.monotonic() + 2)
        combo = SimpleNamespace(role="combo", node=object(),
                                interfaces=frozenset(("Component",)))
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_SOURCE_KEYBOARD_OWNER"):
            ui.dispatch_source_keyboard(combo, ("Home", "Down", "Tab"), "select-music")
        self.assertEqual(owner_queries, [True])

    def test_source_binding_witness_cannot_be_omitted_or_substituted(self):
        before, binding, _, _, _, ui, _, _ = data()
        self.assertIsNone(GUARDS.validate_ui_observation(ui, before, binding))
        altered = deepcopy(ui)
        altered["draft_source_bindings"]["text"] = "Draft source bindings: music music; SFX none."
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_TEXT_OBSERVATION_MISSING"):
            GUARDS.validate_ui_observation(altered, before, binding)
        altered = deepcopy(ui)
        altered.pop("draft_source_bindings")
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_TEXT_OBSERVATION_MISSING"):
            GUARDS.validate_ui_observation(altered, before, binding)

    def test_react_draft_status_refreshes_owned_cached_node_before_matching_ids(self):
        expected = "Draft source bindings: music music; SFX sfx."
        observations = []
        node = SimpleNamespace(clear_cache_single=lambda: observations.append("fresh"))
        old = SimpleNamespace(node=node, role="status",
                              name="Draft source bindings: music none; SFX none.",
                              states=frozenset(("visible",)),
                              interfaces=frozenset(("Component",)))
        new = SimpleNamespace(node=node, role="status", name=expected,
                              states=frozenset(("visible", "showing")),
                              interfaces=frozenset(("Component",)))
        bridge = SimpleNamespace(owner_pid=lambda _: 7,
            call=lambda operation, *args: operation(*args),
            view=lambda _: new)
        ui = GUARDS.AudioUI(bridge, None, {"pid": 7}, {}, None, time.monotonic() + 2)
        ui.views = lambda: [old]
        self.assertIs(ui.refreshed_source_status(expected), new)
        bridge.view = lambda _: old
        with self.assertRaisesRegex(RuntimeError, "UI_REACT_SOURCE_BINDINGS_NOT_COMMITTED"):
            ui.refreshed_source_status(expected)
        bridge.owner_pid = lambda _: 8
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_SOURCE_STATUS_OWNER_CHANGED"):
            ui.refreshed_source_status(expected)
        self.assertEqual(observations, ["fresh", "fresh"])

    def test_original_save_ack_uses_real_reaction_not_stale_form_projection(self):
        # The physical original 44232653680 showed "Saved recipe at revision r14."
        # after the writer committed r14. The generic form's "Showing saved"
        # condition is not the original ACK and may remain hidden while dirty.
        draft = (ROOT / "apps/studio/src/deliveryRecipeDraft.ts").read_text(encoding="utf-8")
        receipt = draft.index("const receipt = await commit(change);")
        acknowledged = draft.index("const acknowledged = acknowledgeRecipeOriginal(", receipt)
        original_ack = draft.index('session.message = "Saved recipe at revision r"', acknowledged)
        self.assertLess(receipt, acknowledged)
        self.assertLess(acknowledged, original_ack)
        view = (ROOT / "apps/studio/src/DeliveryProfiles.tsx").read_text(encoding="utf-8")
        self.assertIn('{controller.message && <p className="production-help" role="status">', view)
        before, binding, _, _, _, ui, _, _ = data()
        self.assertIsNone(GUARDS.validate_ui_observation(ui, before, binding))
        altered = deepcopy(ui)
        altered["saved"]["text"] = "Showing saved recipe values at revision r2."
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_TEXT_OBSERVATION_MISSING"):
            GUARDS.validate_ui_observation(altered, before, binding)
        altered["saved"]["text"] = "The recipe save result is unconfirmed."
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_TEXT_OBSERVATION_MISSING"):
            GUARDS.validate_ui_observation(altered, before, binding)

    def test_original_pcm_ready_name_reads_exact_owned_button_without_children(self):
        # The real 44340138059 snapshot proved even the button's parent
        # reports ACCESSIBLE_CHILD_LIMIT once the browser audio UI is built.
        # This in-memory test cannot represent a physical result; it verifies
        # that the one known accessible node is reused, without traversal.
        events = []
        node = SimpleNamespace(clear_cache_single=lambda: events.append("refresh"))
        state = {"name": GUARDS.AUDITION, "owner": 7001, "role": "button",
                 "states": frozenset(("visible", "showing", "enabled", "sensitive"))}
        def view(target):
            self.assertIs(target, node)
            return SimpleNamespace(node=node, name=state["name"], role=state["role"],
                 states=state["states"], interfaces=frozenset(("Action", "Component")))
        bridge = SimpleNamespace(call=lambda f,*args: f(*args),
            drain=lambda: None, glib=SimpleNamespace(Error=OSError),
            owner_pid=lambda _: state["owner"], view=view,
            children=lambda _: self.fail("Native audio child traversal forbidden"),
            applications=lambda: self.fail("Whole app traversal forbidden"))
        ui = GUARDS.AudioUI(bridge, None, {"pid": 7001}, {}, None, time.monotonic()+5)
        ui.identity = lambda: None
        original = SimpleNamespace(node=node, role="button", name=GUARDS.AUDITION)
        with self.assertRaisesRegex(RuntimeError, "UI_ORIGINAL_PCM_RESPONSE_NOT_YET_IN_REACT"):
            ui.observe_original_pcm_near_owned_button(original)
        state["name"] = GUARDS.AUDITION_PCM_READY
        self.assertEqual(ui.observe_original_pcm_near_owned_button(original),
                         {"text": GUARDS.PREVIEW, "matches": 1})
        self.assertIs(ui._refreshed_close_button(original).node, node)
        self.assertEqual(events, ["refresh", "refresh", "refresh"])
        state["owner"] = 7002
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_PCM_PREVIEW_ANCHOR_NOT_OWNED"):
            ui.observe_original_pcm_near_owned_button(original)
        state["owner"] = 7001
        state["role"] = "status"
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_CLOSE_BUTTON_ROLE_CHANGED"):
            ui.observe_original_pcm_near_owned_button(original)
        state["role"] = "button"
        state["states"] = frozenset(("visible", "showing"))
        # React can commit currentAudition before setWorking(false). Read-only
        # PCM acknowledgement is valid while its action remains disabled.
        self.assertEqual(ui.observe_original_pcm_near_owned_button(original),
                         {"text": GUARDS.PREVIEW, "matches": 1})
        with self.assertRaisesRegex(RuntimeError, "UI_CLOSE_BUTTON_STILL_WORKING"):
            ui._refreshed_close_button(original)
        state["states"] = frozenset(("visible", "showing", "enabled", "sensitive"))
        self.assertIs(ui._refreshed_close_button(original).node, node)
        state["states"] = frozenset(("visible", "showing", "enabled"))
        with self.assertRaisesRegex(RuntimeError, "UI_CLOSE_BUTTON_STILL_WORKING"):
            ui._refreshed_close_button(original)
        state["states"] = frozenset(("enabled", "sensitive"))
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_CLOSE_BUTTON_VISIBILITY_CHANGED"):
            ui.observe_original_pcm_near_owned_button(original)
        state["states"] = frozenset(("visible", "showing", "enabled", "sensitive", "defunct"))
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_CLOSE_BUTTON_VISIBILITY_CHANGED"):
            ui.observe_original_pcm_near_owned_button(original)

    def _pcm_read_case(self, failures):
        class AtspiError(Exception):
            domain = "atspi_error"
            code = 1
            message = "Did not receive a reply. The reply timeout expired."
        events = []
        state = {"owner": 7001, "role": "button"}
        node = SimpleNamespace(clear_cache_single=lambda: events.append("refresh"))
        pending = iter(failures)
        def view(target):
            self.assertIs(target, node)
            events.append("view")
            outcome = next(pending, None)
            if outcome == "timeout":
                raise AtspiError()
            if isinstance(outcome, BaseException):
                raise outcome
            if outcome == "foreign":
                state["owner"] = 7002
                raise AtspiError()
            return SimpleNamespace(node=node, name=GUARDS.AUDITION_PCM_READY,
                role=state["role"], states=frozenset(("visible", "showing", "enabled", "sensitive")),
                interfaces=frozenset(("Action", "Component")))
        bridge = SimpleNamespace(call=lambda f,*args: f(*args),
            glib=SimpleNamespace(Error=AtspiError),
            drain=lambda: events.append("drain"),
            owner_pid=lambda _: state["owner"], view=view)
        ui = GUARDS.AudioUI(bridge, None, {"pid": 7001}, {}, None, time.monotonic()+3)
        ui.identity = lambda: GUARDS.need(time.monotonic() < ui.deadline, "UI_DEADLINE")
        ui.effect = lambda *args: self.fail("Read-only recovery must not dispatch an effect")
        original = SimpleNamespace(node=node, role="button", name=GUARDS.AUDITION)
        return ui, original, events, state, AtspiError

    def test_pcm_no_reply_recovers_only_a_complete_same_node_read(self):
        ui, original, events, _, _ = self._pcm_read_case(["timeout"])
        deadline = ui.deadline
        result = ui.wait(lambda: ui.observe_original_pcm_near_owned_button(original))
        self.assertEqual(result, {"text": GUARDS.PREVIEW, "matches": 1})
        self.assertEqual(ui.deadline, deadline)
        self.assertEqual(events, ["drain", "refresh", "view"] * 2)
        self.assertEqual(ui.events, [])
        self.assertEqual(len(ui.pcm_read_timeouts), 1)
        self.assertEqual(ui.pcm_read_timeouts[0]["stage"], "owned_button_view")
        self.assertEqual(ui.pcm_read_timeouts[0]["code"], 1)

    def test_pcm_no_reply_cannot_hide_persistent_timeout_or_owner_change(self):
        ui, original, events, _, _ = self._pcm_read_case(["timeout"] * 3)
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_PCM_READ_TIMEOUT_LIMIT"):
            ui.wait(lambda: ui.observe_original_pcm_near_owned_button(original))
        self.assertEqual(events.count("view"), 3)
        self.assertEqual(len(ui.pcm_read_timeouts), 3)
        self.assertEqual(ui.events, [])
        ui, original, events, _, _ = self._pcm_read_case(["foreign"])
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_PCM_PREVIEW_ANCHOR_NOT_OWNED"):
            ui.wait(lambda: ui.observe_original_pcm_near_owned_button(original))
        self.assertEqual(events.count("view"), 1)

    def test_pcm_read_retry_cannot_accept_wrong_role_or_renew_deadline(self):
        ui, original, events, state, _ = self._pcm_read_case(["timeout"])
        with self.assertRaisesRegex(RuntimeError, "UI_PCM_READ_NO_REPLY"):
            ui.observe_original_pcm_near_owned_button(original)
        state["role"] = "status"
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_CLOSE_BUTTON_ROLE_CHANGED"):
            ui.observe_original_pcm_near_owned_button(original)
        ui, original, events, _, _ = self._pcm_read_case(["timeout"])
        with self.assertRaisesRegex(RuntimeError, "UI_PCM_READ_NO_REPLY"):
            ui.observe_original_pcm_near_owned_button(original)
        ui.deadline = time.monotonic()-1
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_DEADLINE"):
            ui.observe_original_pcm_near_owned_button(original)
        self.assertEqual(events.count("view"), 1)

    def test_pcm_no_reply_owner_cache_mismatch_recovers_same_node_only(self):
        ui, original, events, state, _ = self._pcm_read_case(["timeout"])
        sequence = iter((7001, 7001, 7002, 7001, 7001, 7001, 7001))
        ui.bridge.owner_pid = lambda _: next(sequence)
        result = ui.wait(lambda: ui.observe_original_pcm_near_owned_button(original))
        self.assertEqual(result, {"text": GUARDS.PREVIEW, "matches": 1})
        self.assertEqual(len(ui.pcm_read_timeouts), 1)
        self.assertEqual(len(ui.pcm_owner_observations), 1)
        self.assertEqual(ui.pcm_owner_observations[0]["stale_pid"], 7002)
        self.assertEqual(ui.pcm_owner_observations[0]["refreshed_pid"], 7001)
        self.assertEqual(ui.events, [])
        self.assertEqual(events.count("view"), 2)

    def test_pcm_final_view_owner_guard_denies_persistent_foreign_owner(self):
        ui, original, events, _, _ = self._pcm_read_case([])
        sequence = iter((7001, 7001, 7002, 7002))
        ui.bridge.owner_pid = lambda _: next(sequence)
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_PCM_PREVIEW_ANCHOR_NOT_OWNED"):
            ui.observe_original_pcm_near_owned_button(original)
        self.assertEqual(len(ui.pcm_owner_observations), 1)
        self.assertEqual(ui.pcm_owner_observations[0]["refreshed_pid"], 7002)
        self.assertIsNone(ui.pcm_preview)
        self.assertEqual(ui.events, [])

    def test_pcm_no_reply_classifier_rejects_denial_defunct_and_unknown_errors(self):
        for field, value in (("domain", "g-dbus-error-quark"), ("code", 0),
                             ("code", True), ("message", "Access denied"),
                             ("message", "Process is defunct"),
                             ("message", "The process appears to be hung.")):
            with self.subTest(field=field, value=value):
                ui, original, events, _, error_type = self._pcm_read_case([])
                error = error_type()
                setattr(error, field, value)
                ui.bridge.view = lambda _: (_ for _ in ()).throw(error)
                with self.assertRaises(error_type):
                    ui.observe_original_pcm_near_owned_button(original)
                self.assertEqual(ui.pcm_read_timeouts, [])
        self.assertFalse(GUARDS.atspi_read_no_reply(RuntimeError("Did not receive a reply.")))

    def test_pcm_owner_refresh_accepts_only_the_same_original_node_back_in_owner(self):
        ui, original, events, state, _ = self._pcm_read_case([])
        owners = iter((7002, 7001))
        ui.bridge.owner_pid = lambda node: next(owners)
        self.assertIsNone(ui.original_pcm_owner(original, "preview"))
        self.assertEqual(len(ui.pcm_owner_observations), 1)
        receipt = ui.pcm_owner_observations[0]
        self.assertEqual(receipt["stale_pid"], 7002)
        self.assertEqual(receipt["refreshed_pid"], 7001)
        self.assertEqual(receipt["expected_pid"], 7001)
        self.assertEqual(receipt["extra_effects"], 0)
        self.assertEqual(events, ["refresh"])

    def test_pcm_owner_refresh_rejects_foreign_owner_even_after_cache_refresh(self):
        ui, original, events, state, _ = self._pcm_read_case([])
        ui.bridge.owner_pid = lambda _: 7002
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_PCM_PREVIEW_ANCHOR_NOT_OWNED"):
            ui.original_pcm_owner(original, "preview")
        self.assertEqual(events, ["refresh"])
        self.assertEqual(len(ui.pcm_owner_observations), 1)
        self.assertEqual(ui.events, [])

    def test_pcm_owner_refresh_rechecks_x11_and_limits_all_further_reads(self):
        ui, original, events, state, _ = self._pcm_read_case([])
        owners = iter((7002, 7001))
        ui.bridge.owner_pid = lambda _: next(owners)
        calls = []
        def identity():
            calls.append(True)
            if len(calls) == 2:
                raise GUARDS.AudioError("UI_PROCESS_CHANGED")
        ui.identity = identity
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_PROCESS_CHANGED"):
            ui.original_pcm_owner(original, "preview")
        self.assertEqual(len(calls), 2)
        self.assertEqual(events, ["refresh"])
        self.assertEqual(ui.events, [])
        ui, original, events, state, _ = self._pcm_read_case([])
        ui.bridge.owner_pid = lambda _: 7002
        with self.assertRaises(GUARDS.AudioError):
            ui.original_pcm_owner(original, "preview")
        with self.assertRaises(GUARDS.AudioError):
            ui.original_pcm_owner(original, "preview")
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_PCM_OWNER_REFRESH_LIMIT"):
            ui.original_pcm_owner(original, "preview")
        self.assertEqual(events, ["refresh", "refresh"])

    def test_pcm_owner_invalid_pid_is_not_refreshable(self):
        for owner in (0, -1, None, "7001", True):
            ui, original, events, state, _ = self._pcm_read_case([])
            ui.bridge.owner_pid = lambda _: owner
            with self.subTest(owner=owner), self.assertRaises(GUARDS.AudioError):
                ui.original_pcm_owner(original, "preview")
            self.assertEqual(events, [])
            self.assertEqual(ui.events, [])

    def test_pcm_ready_label_depends_on_real_current_audition_and_proven_close(self):
        source=(ROOT/"apps/studio/src/NativeOriginalMixReview.tsx").read_text(encoding="utf-8")
        self.assertIn('aria-label={currentAudition', source)
        self.assertIn('? "'+GUARDS.AUDITION_PCM_READY+'"', source)
        self.assertIn(': "'+GUARDS.AUDITION+'"}', source)
        self.assertIn('{currentAudition && <div aria-label="Unmastered original PCM preview"', source)
        self.assertIn('const [playbackEnabled, setPlaybackEnabled] = useState(false);', source)
        self.assertIn('{playbackEnabled', source)
        self.assertIn('<audio controls preload="none" src={currentAudition.url}', source)
        self.assertIn('aria-label="Enable local original PCM playback controls"', source)
        self.assertNotIn('<audio controls preload="metadata" src={currentAudition.url}', source)
        harness=PATH.read_text(encoding="utf-8")
        self.assertIn('audition_button = self.click(AUDITION, {"button"}, "original-audition")', harness)
        self.assertIn('self.observe_original_pcm_near_owned_button(audition_button)', harness)
        self.assertIn('self.close_window()', harness)
        self.assertIn('if item.name != AUDITION_PCM_READY:', harness)
        self.assertNotIn('preview = self.wait(lambda: self.text(PREVIEW))', harness)
        self.assertNotIn('self.close_window(audition_button)', harness)
        self.assertNotIn('preview-result-scroll', harness)
        read_segment=harness.split('    def observe_original_pcm_near_owned_button(', 1)[1].split(
            '    def _refreshed_close_button(', 1)[0]
        self.assertIn('self._refreshed_close_button(original_button, require_usable=False)', read_segment)
        refresh_segment=harness.split('    def _refreshed_close_button(', 1)[1].split(
            '    def close_window(', 1)[0]
        self.assertIn('original_button.node.clear_cache_single', refresh_segment)
        self.assertIn('UI_CLOSE_BUTTON_STILL_WORKING', refresh_segment)
        for forbidden in ("self.bridge.children(", "self.views(", "accessible.walk(",
                          "self.bridge.applications(", "self.effect("):
            self.assertNotIn(forbidden, read_segment)
            self.assertNotIn(forbidden, refresh_segment)
        before,binding,_,_,_,observed,_,_=data()
        self.assertIsNone(GUARDS.validate_ui_observation(observed, before, binding))
        invalid=deepcopy(observed)
        invalid["actions"].insert(invalid["actions"].index("window-close"),"preview-result-scroll")
        with self.assertRaises(GUARDS.AudioError):
            GUARDS.validate_ui_observation(invalid, before, binding)
    def test_original_pcm_ui_cannot_create_media_before_user_playback_request(self):
        source=(ROOT/"apps/studio/src/NativeOriginalMixReview.tsx").read_text(encoding="utf-8")
        production=source.split('{currentAudition && <div aria-label="Unmastered original PCM preview"', 1)[1]
        self.assertIn('{playbackEnabled', production)
        self.assertIn('onClick={() => setPlaybackEnabled(true)}', production)
        self.assertIn('preload="none"', production)
        self.assertNotIn('preload="metadata"', production)
        self.assertEqual(source.count("setPlaybackEnabled(false);"), 4)
        self.assertIn('aria-label={currentAudition', source)
        self.assertIn('URL.createObjectURL(new Blob([owned.buffer]', source)
        playwright=(ROOT/"apps/studio/tests/native-original-mix.spec.ts").read_text(encoding="utf-8")
        self.assertIn("await expect(desk.locator('audio[controls]')).toHaveCount(0);", playwright)
        self.assertIn("await playback.click();", playwright)
        self.assertIn("audio[controls][preload=", playwright)

    def test_source_draft_status_may_scroll_but_must_retain_exact_live_text(self):
        before, binding, _, _, _, ui, _, _ = data()
        expected = ui["draft_source_bindings"]["text"]
        scroll = "source-draft-status-scroll"
        ui["actions"].insert(ui["actions"].index("save-recipe"), scroll)
        self.assertIsNone(GUARDS.validate_ui_observation(ui, before, binding))
        ui["draft_source_bindings"]["text"] = expected + "stale"
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_TEXT_OBSERVATION_MISSING"):
            GUARDS.validate_ui_observation(ui, before, binding)

    def test_scroll_ack_refreshes_only_the_owned_control_without_resending(self):
        calls, fresh = [], []
        node = SimpleNamespace(clear_cache_single=lambda: fresh.append(True))
        item = SimpleNamespace(node=node, states=frozenset(("visible",)),
                               interfaces=frozenset(("Component",)))
        def scroll(target, placement):
            self.assertIs(target, node)
            calls.append(("scroll", placement))
            return True
        bridge = SimpleNamespace(call=lambda operation, *args: operation(*args),
            api=SimpleNamespace(Component=SimpleNamespace(scroll_to=scroll),
                                ScrollType=SimpleNamespace(ANYWHERE=7)))
        ui = GUARDS.AudioUI(bridge, None, {"pid": 7}, {}, None, time.monotonic() + 2)
        def control(name, roles, *, showing=True):
            self.assertEqual(name, "original disclosure")
            if showing and not fresh:
                raise RuntimeError("stale cached SHOWING")
            return item
        ui.control = control
        ui.effect = lambda label, target, operation: (
            calls.append(("intent", label)), self.assertIs(target, item),
            self.assertIs(operation(), True))
        self.assertIs(ui.visible("original disclosure", {"native_action"}, "open-original-mix"), item)
        self.assertEqual(calls, [("intent", "open-original-mix-scroll"), ("scroll", 7)])
        self.assertEqual(fresh, [True])

    def test_checkbox_action_uses_the_observed_state_only(self):
        self.assertEqual(GUARDS.accessible_action_index(["check"], "checkbox", set()), 0)
        self.assertEqual(GUARDS.accessible_action_index(["uncheck"], "checkbox", {"checked"}), 0)
        for name, states in (("uncheck", set()), ("check", {"checked"})):
            with self.assertRaisesRegex(GUARDS.AudioError, "AMBIGUOUS_OR_UNSUPPORTED"):
                GUARDS.accessible_action_index([name], "checkbox", states)

    def test_checkbox_verbs_cannot_activate_other_roles(self):
        for role in ("button", "toggle", "native_action", "entry"):
            for name in ("check", "uncheck"):
                with self.subTest(role=role, name=name), self.assertRaises(GUARDS.AudioError):
                    GUARDS.accessible_action_index([name], role, set())
        self.assertEqual(GUARDS.accessible_action_index(["press"], "button", set()), 0)

    def test_ambiguous_actions_and_unbounded_observation_still_fail(self):
        for names in ([], ["check", "toggle"], ["check", "check"], ["delete"]):
            with self.assertRaisesRegex(GUARDS.AudioError, "AMBIGUOUS_OR_UNSUPPORTED"):
                GUARDS.accessible_action_index(names, "checkbox", set())
        for names in (["check"] * 17, ["x" * 129], [None], ("check",)):
            with self.assertRaisesRegex(GUARDS.AudioError, "OBSERVATION_BOUND"):
                GUARDS.accessible_action_index(names, "checkbox", set())

    def test_checked_readback_refreshes_only_its_owned_control(self):
        refreshed = []
        node = SimpleNamespace(clear_cache_single=lambda: refreshed.append(True))
        bridge = SimpleNamespace(call=lambda operation, *args: operation(*args))
        ui = GUARDS.AudioUI(bridge, None, {"pid": 7}, {}, None, time.monotonic() + 2)
        def control(name, roles):
            self.assertEqual((name, roles), ("fixture checkbox", {"checkbox"}))
            return SimpleNamespace(node=node, states={"checked"} if refreshed else set())
        ui.control = control
        self.assertIn("checked", ui.checked("fixture checkbox", True).states)
        self.assertEqual(refreshed, [True])

    def test_readonly_text_still_verifies_original_value_and_process(self):
        value = "original-project-identity"
        bridge = SimpleNamespace(owner_pid=lambda _: 7, read_text=lambda _: value, text_ranges=[])
        ui = GUARDS.AudioUI(bridge, None, {"pid": 7}, {}, None, time.monotonic() + 5)
        item = SimpleNamespace(node=object(), role="readonly_text", name=value,
                               states=frozenset(("visible", "showing")),
                               interfaces=frozenset(("Text",)))
        observed_targets = []
        def views():
            observed_targets.append(bridge.expected_texts)
            return [item]
        ui.views = views
        self.assertEqual(ui.text(value), {"text": value, "matches": 1})
        self.assertEqual(observed_targets, [frozenset((value,))])
        self.assertEqual(bridge.expected_texts, frozenset())
        bridge.read_text = lambda _: "a different actual Text value"
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_NAME_TEXT_DISAGREE"):
            ui.text(value)
        bridge.read_text = lambda _: value
        bridge.owner_pid = lambda _: 8
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_TEXT_OWNER_CHANGED"):
            ui.text(value)
        bridge.owner_pid = lambda _: 7
        item.interfaces = frozenset(("Text", "EditableText"))
        with self.assertRaisesRegex(RuntimeError, "EXACT_UI_TEXT_NOT_OBSERVED"):
            ui.text(value)
        self.assertEqual(bridge.expected_texts, frozenset())

    def test_inline_text_offsets_preserve_exact_characters_and_token_boundaries(self):
        value = "01a1e973-9421-72e5-bfeb-a5e9c4fedf44"
        text = "Native render E2E\nRevision 13 · today\n" + value + "\nOpen"
        start = text.index(value)
        self.assertEqual(GUARDS.exact_text_offsets(text, value), [(start, start + len(value))])
        for changed in ("x" + value, value + "f", value + "-copy", value.upper()):
            self.assertEqual(GUARDS.exact_text_offsets(changed, value), [])
        self.assertEqual(GUARDS.exact_text_offsets("r130 r13x xr13", "r13"), [])
        self.assertEqual(GUARDS.exact_text_offsets("Version r13 · saved", "r13"), [(8, 11)])
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_TEXT_PROVIDER_BOUND"):
            GUARDS.exact_text_offsets("a" * 4097, "a")
        with self.assertRaisesRegex(GUARDS.AudioError, "UI_TEXT_RANGE_AMBIGUITY"):
            GUARDS.exact_text_offsets("r13 " * 9, "r13")

    def inline_text_model(self):
        value = "original-project-identity"
        content = "Stored project\n" + value + "\nOpen"
        start = content.index(value)
        rectangle = lambda x,y,w,h: SimpleNamespace(x=x,y=y,width=w,height=h)
        node = SimpleNamespace(owner=7, content=content, actual=value,
            states={"visible","showing"}, interfaces={"Text","Component"},
            bounds=rectangle(20,20,500,200), text_bounds=rectangle(40,80,230,18))
        node.get_state_set = lambda: SimpleNamespace(contains=lambda key: key in node.states)
        node.get_interfaces = lambda: node.interfaces
        frame_node = SimpleNamespace(owner=7, bounds=rectangle(0,0,1280,900))
        frame = SimpleNamespace(node=frame_node, role="frame", name="Motionwright",
            states=frozenset(("visible","showing")), interfaces=frozenset(("Component",)))
        api = SimpleNamespace(CoordType=SimpleNamespace(SCREEN=0),
            Component=SimpleNamespace(get_extents=lambda item, _: item.bounds),
            Text=SimpleNamespace(get_character_count=lambda item: len(item.content),
                get_text=lambda item, begin, end: item.content if (begin,end)==(0,len(item.content)) else item.actual,
                get_range_extents=lambda item, begin, end, _: item.text_bounds))
        witness = {"node":node,"role":"list-item","interfaces":frozenset(node.interfaces),
            "start":start,"end":start+len(value),"characters":len(content),
            "provider_text_sha256":hashlib.sha256(content.encode()).hexdigest(),"text":value}
        bridge = SimpleNamespace(api=api, owner_pid=lambda item:item.owner,
            states={key:key for key in ("visible","showing","editable","defunct")},
            call=lambda function,*args:function(*args), text_ranges=[witness])
        ui = GUARDS.AudioUI(bridge,None,{"pid":7},{},None,time.monotonic()+5)
        identities = []
        ui.identity = lambda: identities.append(True)
        return ui, node, frame, value, identities

    def test_inline_text_range_keeps_original_offsets_and_visible_owner(self):
        ui,node,frame,value,identities = self.inline_text_model()
        result = ui.observed_text_ranges(value,[frame])
        self.assertEqual(result["text"],value)
        self.assertEqual(result["matches"],1)
        proof = result["original_text_ranges"][0]
        self.assertEqual(node.content[proof["start"]:proof["end"]],value)
        self.assertEqual(proof["screen_rectangle"],[40,80,230,18])
        self.assertEqual(identities,[True])

    def test_inline_text_same_length_provider_change_cannot_reuse_old_hash(self):
        ui,node,frame,value,_ = self.inline_text_model()
        start = node.content.index(value)
        original = node.content
        node.content = original[:start-1] + "x" + original[start:]
        self.assertEqual(len(node.content),len(original))
        self.assertEqual(node.content[start:start+len(value)],value)
        self.assertEqual(GUARDS.exact_text_offsets(node.content,value),[])
        with self.assertRaisesRegex(RuntimeError,"UI_TEXT_PROVIDER_CHANGED"):
            ui.observed_text_ranges(value,[frame])

    def test_inline_text_range_rechecks_actual_text_pid_and_readonly_state(self):
        changes = (
            ("owner",8,"UI_TEXT_OWNER_CHANGED"),
            ("actual","another project","UI_TEXT_RANGE_DISAGREE"),
            ("content","changed","UI_TEXT_PROVIDER_CHANGED"),
            ("states",{"visible"},"UI_TEXT_PROVIDER_NOT_READONLY"),
            ("states",{"visible","showing","editable"},"UI_TEXT_PROVIDER_NOT_READONLY"),
            ("states",{"visible","showing","defunct"},"UI_TEXT_PROVIDER_NOT_READONLY"),
            ("interfaces",{"Text","Component","EditableText"},"UI_TEXT_PROVIDER_NOT_READONLY"),
        )
        for attribute,changed,reason in changes:
            with self.subTest(reason=reason,attribute=attribute):
                ui,node,frame,value,_ = self.inline_text_model()
                setattr(node,attribute,changed)
                with self.assertRaisesRegex((GUARDS.AudioError,RuntimeError),reason):
                    ui.observed_text_ranges(value,[frame])
        ui,node,frame,value,_ = self.inline_text_model()
        frame.node.owner = 8
        with self.assertRaisesRegex(GUARDS.AudioError,"UI_TEXT_WINDOW_OWNER_CHANGED"):
            ui.observed_text_ranges(value,[frame])

    def test_inline_text_range_rejects_clipped_offscreen_and_invalid_rectangles(self):
        for attribute,rectangle in (
            ("text_bounds",(40,800,230,18)),
            ("bounds",(-10,20,500,200)),
            ("text_bounds",(40,80,0,18)),
            ("text_bounds",(40,80,True,18)),
        ):
            with self.subTest(attribute=attribute,rectangle=rectangle):
                ui,node,frame,value,_ = self.inline_text_model()
                setattr(node,attribute,SimpleNamespace(**dict(zip(("x","y","width","height"),rectangle))))
                with self.assertRaisesRegex((GUARDS.AudioError,RuntimeError),"UI_TEXT_RECT_INVALID|NOT_VISIBLE"):
                    ui.observed_text_ranges(value,[frame])

    def test_actual_empty_recipe_serialization_is_admitted_but_saved_recipe_is_not(self):
        root = Path("/private")
        database = root / "profile/data/app/motionwright.sqlite3"
        before, binding, context, before_id, manifest_id, ui, records, readback = data()
        before.update(schema="motionwright.tauri-audio-fixture-before/1", private_root=str(root),
                      database_path=str(database),
                      revision=1, expected_revision=2,
                      project={"id": before["project_id"], "generation": before["generation"],
                               "revision": 1, "production_design": {}, "assets": [{}, {}, {}]},
                      preparation_tauri_owner_invocation="NOT_EXERCISED",
                      expected_save_authority="REAL_TAURI_UI_NOT_ATTESTED_BY_FIXTURE",
                      writer_handoff_verified=True, source_classification="SYNTHETIC_TONES_NOT_SPEECH",
                      formal_G2=False, formal_G3=False, human_approval=False,
                      release_authorized=False, automatic_retry=False)
        before["expected_change"]["recipe"]["original_mix"].update(
            duck_attenuation_db=14, duck_attack_samples=2400, duck_release_samples=4800)
        sources = {}
        before["assets"] = []
        for index, bus in enumerate(("voice", "music", "sfx"), 1):
            name = "saved-mix-" + bus + ".wav"
            witness = {"path": str(root / "sources" / name), "sha256": str(index) * 64,
                       "bytes": 384044}
            sources[name] = witness
            before["assets"].append({"bus": bus, "source": deepcopy(witness),
                                     "cas": {"sha256": witness["sha256"]}})
        # ProductionDesign skips an empty delivery_recipes map during serde.
        # This is the original pre-Save shape, not a prepared saved recipe.
        GUARDS.validate_before(before, context, root, database, sources)
        before["project"]["production_design"]["delivery_recipes"] = {}
        GUARDS.validate_before(before, context, root, database, sources)
        for invalid in ({before["deliverable_profile_id"]: before["expected_change"]["recipe"]},
                        None, [], True):
            before["project"]["production_design"]["delivery_recipes"] = invalid
            with self.subTest(value=invalid), self.assertRaises(GUARDS.AudioError):
                GUARDS.validate_before(before, context, root, database, sources)

    def test_exact_pre_save_startup_and_post_save_join(self):
        values = data()
        shared, session = self.native(values)
        self.assertEqual(shared["revision"], 2)
        self.assertEqual(session, str(uuid.UUID(int=14)))
        GUARDS.validate_saved_readback(values[-1], values[0], values[3])

    def test_native_source_store_session_or_manifest_substitution_denied(self):
        for field, replacement in (
            ("source_sha", "e" * 40), ("sdk_sha", "f" * 40), ("run_id", "777"),
            ("run_attempt", "2"), ("project_id", str(uuid.UUID(int=100))),
            ("generation", str(uuid.UUID(int=101))), ("deliverable_profile_id", str(uuid.UUID(int=102))),
            ("desktop_pid", 7002), ("database_path", "/copied.sqlite3"),
            ("database_identity", "unix:10:99"), ("cas_path", "/copied/blobs"),
            ("cas_identity", {"device": True, "inode": 21, "mode": 16832, "uid": 1000, "gid": 1000}),
            ("manifest_sha256", "e" * 64), ("before_receipt_sha256", "f" * 64),
            ("workspace_session", str(uuid.UUID(int=103))),
        ):
            with self.subTest(field=field):
                values = data()
                values[6]["intent.original.json"][field] = replacement
                with self.assertRaises(GUARDS.AudioError):
                    self.native(values)

    def test_startup_cannot_claim_save_and_later_cannot_stay_before(self):
        for name, revision in (("startup", 2), ("startup", True), ("intent", 1),
                               ("prepared-response", 3), ("writer-handoff", 1)):
            with self.subTest(name=name, revision=revision):
                values = data()
                values[6][name + ".original.json"]["revision"] = revision
                with self.assertRaises(GUARDS.AudioError):
                    self.native(values)

    def test_native_cannot_claim_ipc_closure_or_approval(self):
        for name, key, value in (
            ("startup", "native_effect_or_owner_granted", True),
            ("prepared-response", "ipc_delivery_observed", True),
            ("prepared-response", "ui_observation_required", False),
            ("writer-handoff", "writer_process_closed", True),
            ("writer-handoff", "writer_handoff_prepared", False),
            ("intent", "formal_G2", True), ("intent", "human_approval", 0),
        ):
            with self.subTest(name=name, key=key):
                values = data()
                values[6][name + ".original.json"][key] = value
                with self.assertRaises(GUARDS.AudioError):
                    self.native(values)

    def test_recipe_and_native_roster_are_exact(self):
        for mutation in ("recipe", "missing", "extra"):
            values = data()
            if mutation == "recipe":
                values[6]["prepared-response.original.json"]["saved_recipe"]["original_mix"]["sfx_gain_db"] = -7.0
            elif mutation == "missing":
                values[6].pop("writer-handoff.original.json")
            else:
                values[6]["retry.original.json"] = {}
            with self.subTest(mutation=mutation), self.assertRaises(GUARDS.AudioError):
                self.native(values)

    def test_ui_save_audition_and_close_must_be_once_in_order(self):
        for change in ("repeat-save", "repeat-audition", "omit-close", "reorder", "unknown", "repeat-scroll"):
            values = data()
            actions = values[5]["actions"]
            if change == "repeat-save":
                actions.insert(7, "save-recipe")
            elif change == "repeat-audition":
                actions.append("original-audition")
            elif change == "omit-close":
                actions.pop()
            elif change == "reorder":
                actions[-2], actions[-1] = actions[-1], actions[-2]
            elif change == "unknown":
                actions.insert(1, "injected-invoke")
            else:
                actions[0:0] = ["open-project-scroll", "open-project-scroll"]
            with self.subTest(change=change), self.assertRaises(GUARDS.AudioError):
                self.native(values)

    def test_optional_observed_scroll_does_not_replace_action(self):
        values = data()
        values[5]["actions"].insert(0, "open-project-scroll")
        self.native(values)

    def test_missing_preview_changed_ui_revision_or_pid_denied(self):
        for field, value in (("status", "FAIL"), ("revision_before", True),
                             ("revision_after", 3), ("normal_close_requested", 1),
                             ("preview", {"text": "prepared natively only", "matches": 1}),
                             ("saved", {"text": "Saved recipe at revision r2.", "matches": True}),
                             ("binding", {"pid": 7002, "window": "0xa00", "start_time": "23456"}),
                             ("playback_or_human_listening_claimed", True)):
            values = data()
            values[5][field] = value
            with self.subTest(field=field), self.assertRaises(GUARDS.AudioError):
                self.native(values)

    def test_reader_copy_wrong_hash_assets_or_boolean_event_denied(self):
        for field, value in (("database_identity", "copy"), ("source_witness_sha256", "e" * 64),
                             ("assets", []), ("additional_journal_events", False),
                             ("revision", 3), ("previous_revision", True),
                             ("reader_authority", "Writer"), ("mixed_pcm_read_or_generated_by_reader", True)):
            before, _, _, identity, _, _, _, readback = data()
            readback[field] = value
            with self.subTest(field=field), self.assertRaises(GUARDS.AudioError):
                GUARDS.validate_saved_readback(readback, before, identity)

    def test_reader_prior_history_extra_save_or_saved_recipe_changed_denied(self):
        for change in ("prior", "extra", "recipe"):
            before, _, _, identity, _, _, _, readback = data()
            if change == "prior":
                readback["observed_history"][0]["change"] = {"type": "rewritten"}
            elif change == "extra":
                readback["observed_history"].append(deepcopy(readback["observed_history"][-1]))
            else:
                readback["observed_project"]["production_design"]["delivery_recipes"] = {}
            with self.subTest(change=change), self.assertRaises(GUARDS.AudioError):
                GUARDS.validate_saved_readback(readback, before, identity)

    def test_empty_tree_requires_exact_supervisor_and_no_success_signals(self):
        closure = {"status": "PASS", "supervisor_pid": 100, "signals": [],
                   "complete_empty_descendant_tree": {
                       "root_observed": True, "truncated": False, "processes": [{"pid": 100}]}}
        GUARDS.validate_process_closure(closure, 100, natural=True)
        for change in ("root", "truncated", "child", "signal", "bool"):
            bad = deepcopy(closure)
            if change == "root":
                bad["complete_empty_descendant_tree"]["processes"][0]["pid"] = 101
            elif change == "truncated":
                bad["complete_empty_descendant_tree"]["truncated"] = True
            elif change == "child":
                bad["complete_empty_descendant_tree"]["processes"].append({"pid": 101})
            elif change == "signal":
                bad["signals"] = [{"pid": 101, "signal": 15}]
            else:
                bad["supervisor_pid"] = True
            with self.subTest(change=change), self.assertRaises(GUARDS.AudioError):
                GUARDS.validate_process_closure(bad, 100, natural=True)

    def test_duplicate_json_keys_and_nonfinite_numbers_denied(self):
        for raw in ('{"pid":1,"pid":2}', '{"revision":NaN}', '{"gain":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(GUARDS.AudioError):
                GUARDS.exact_json(raw)
        self.assertFalse(GUARDS.equal_json({"inode": True}, {"inode": 1}))


    def pcm_data(self):
        before = data()[0]
        before["project"] = {"deliverables": [{"id": before["deliverable_profile_id"], "language": "en"}]}
        proof = {"source_wav_sha256": dict(voice="1"*64, music="2"*64, sfx="3"*64),
                 "source_pcm_sha256": dict(voice="4"*64, music="5"*64, sfx="6"*64),
                 "mixed_pcm_sha256": "7"*64}
        plan = {
            "project_id": before["project_id"], "generation": before["generation"],
            "project_revision": 2, "deliverable_profile_id": before["deliverable_profile_id"],
            "language": "en", "sample_frames": 96000, "sample_rate": 48000, "channels": 2,
            "voice_gain_db": 0.0, "music_gain_db": -12.0, "sfx_gain_db": -8.0,
            "duck_attenuation_db": 14.0, "duck_attack_samples": 2400, "duck_release_samples": 4800,
            "voice_windows": [{"start_sample": 24000, "end_exclusive": 48000}],
            "owner_mixing_approval_authenticated": False, "is_mastered": False,
        }
        for bus, key in (("voice", "measured_voice"), ("music", "original_music"), ("sfx", "original_sfx")):
            plan[key] = {"asset_id": bus, "source_asset_sha256": proof["source_wav_sha256"][bus],
                         "decoded_pcm_sha256": proof["source_pcm_sha256"][bus], "sample_frames": 96000}
        evidence = {
            "source_plan_sha256": "8"*64, "rendered_pcm_sha256": proof["mixed_pcm_sha256"],
            "language": "en", "sample_rate": 48000, "channels": 2, "frames": 96000,
            "input_source_checks": "ALL_THREE_FULL_PCM_SHA256_VERIFIED", "rendered_media": "RAW_PCM_S16LE_ONLY",
            "owner_release_approved": False, "listening_intelligibility_approved": False,
            "independently_verified_lufs": False, "independently_verified_true_peak": False,
        }
        return {"plan": plan, "evidence": evidence, "audio_inputs_sha256": "9"*64}, before, proof

    def test_pcm_projection_binds_each_original_asset_and_project(self):
        prepared, before, proof = self.pcm_data()
        GUARDS.validate_pcm_projection(prepared, before, proof)
        for field, value in (("asset_id", "voice"), ("source_asset_sha256", "a"*64),
                             ("decoded_pcm_sha256", "b"*64), ("sample_frames", True)):
            bad = deepcopy(prepared)
            bad["plan"]["original_music"][field] = value
            with self.subTest(field=field), self.assertRaises(GUARDS.AudioError):
                GUARDS.validate_pcm_projection(bad, before, proof)
        bad = deepcopy(prepared)
        bad["plan"]["project_revision"] = 3
        with self.assertRaises(GUARDS.AudioError):
            GUARDS.validate_pcm_projection(bad, before, proof)

    def test_pcm_evidence_cannot_replace_observed_wav_clock_or_claim_approval(self):
        prepared, before, proof = self.pcm_data()
        for field, value in (("rendered_pcm_sha256", "0"*64), ("sample_rate", 44100),
                             ("channels", True), ("frames", 96001),
                             ("rendered_media", "AAC"), ("owner_release_approved", True),
                             ("listening_intelligibility_approved", True),
                             ("independently_verified_true_peak", True),
                             ("independently_verified_lufs", 0)):
            bad = deepcopy(prepared)
            bad["evidence"][field] = value
            with self.subTest(field=field), self.assertRaises(GUARDS.AudioError):
                GUARDS.validate_pcm_projection(bad, before, proof)


class OnceDispatcherTests(unittest.TestCase):
    """Fault models for dispatch latching; never represent a real UI result."""
    def subject(self, fail_journal=False):
        entries, effects = [], []
        def put(label, value):
            entries.append(label)
            if fail_journal:
                raise OSError("unit fault before native effect")
        ui = GUARDS.AudioUI(SimpleNamespace(owner_pid=lambda _: 7), None, {"pid": 7}, {},
                            SimpleNamespace(put=put), time.monotonic() + 5)
        ui.identity = lambda: None
        item = SimpleNamespace(node=object(), role="button", name="unit-only")
        def effect():
            effects.append("attempt")
            return True
        return ui, item, effect, entries, effects

    def test_second_effect_is_denied_before_journal_or_action(self):
        ui, item, action, entries, effects = self.subject()
        ui.effect("save-recipe", item, action)
        with self.assertRaises(GUARDS.AudioError):
            ui.effect("save-recipe", item, action)
        self.assertEqual(effects, ["attempt"])
        self.assertEqual(entries, ["save-recipe-intent", "save-recipe-return"])

    def test_uncertain_effect_cannot_be_retried(self):
        for answer in (False, None, "raises"):
            ui, item, _, entries, effects = self.subject()
            def uncertain():
                effects.append("attempt")
                if answer == "raises":
                    raise TimeoutError("unit uncertain return")
                return answer
            with self.subTest(answer=answer):
                with self.assertRaises((GUARDS.AudioError, TimeoutError)):
                    ui.effect("original-audition", item, uncertain)
                with self.assertRaises(GUARDS.AudioError):
                    ui.effect("original-audition", item, uncertain)
                self.assertEqual(effects, ["attempt"])

    def test_failed_durable_intent_consumes_attempt_before_effect(self):
        ui, item, action, entries, effects = self.subject(fail_journal=True)
        with self.assertRaises(OSError):
            ui.effect("save-recipe", item, action)
        with self.assertRaises(GUARDS.AudioError):
            ui.effect("save-recipe", item, action)
        self.assertEqual(effects, [])
        self.assertEqual(entries, ["save-recipe-intent"])

    def test_foreign_accessible_owner_cannot_arm_or_dispatch(self):
        ui, item, action, entries, effects = self.subject()
        ui.bridge.owner_pid = lambda _: 99
        with self.assertRaises(GUARDS.AudioError):
            ui.effect("save-recipe", item, action)
        self.assertEqual(ui.events, [])
        self.assertEqual(entries, [])
        self.assertEqual(effects, [])



    def test_closure_status_preserves_webkit_crash_distinction_without_private_fields(self):
        raw=b"Name:\tWebKitWebProces\nPPid:\t7001\nUid:\t1000\t1000\t1000\t1000\nState:\tI (idle)\nCoreDumping:\t1\nThreads:\t3\nPrivate:\tnever retain this\n"
        record=GUARDS.parse_closure_process_status(raw,7001,1000)
        self.assertEqual(record,{"role":"WebKitWebProces","state":"I","core_dumping":True,"threads":3})
        other=GUARDS.parse_closure_process_status(raw.replace(b"WebKitWebProces",b"private-process-title"),7001,1000)
        self.assertEqual(other["role"],"other")
        self.assertNotIn("private",json.dumps(other))
        self.assertFalse(GUARDS.parse_closure_process_status(raw.replace(b"CoreDumping:\t1",b"CoreDumping:\t0"),7001,1000)["core_dumping"])

    def test_closure_status_denies_reparenting_uid_substitution_and_unbounded_input(self):
        raw=b"Name:\tWebKitWebProces\nPPid:\t7001\nUid:\t1000\t1000\t1000\t1000\nState:\tI\nCoreDumping:\t0\nThreads:\t3\n"
        for changed,parent,uid in (
            (raw,7002,1000),(raw,7001,1001),
            (raw.replace(b"1000\t1000\t1000\t1000",b"1000\t0\t1000\t1000"),7001,1000),
            (raw+b"PPid:\t7002\n",7001,1000),
            (raw.replace(b"CoreDumping:\t0",b"CoreDumping:\tunknown"),7001,1000),
            (raw+b"x"*16385,7001,1000),
        ):
            with self.subTest(parent=parent,uid=uid), self.assertRaises(GUARDS.AudioError):
                GUARDS.parse_closure_process_status(changed,parent,uid)

    def test_close_focus_record_rejects_foreign_root_cycle_and_overlong_ancestry(self):
        binding = {"window": "0xa00"}
        GUARDS.validate_close_keyboard_target(
            {"active_window": "0xa00", "input_focus_ancestry": ["0xa05", "0xa00"]}, binding)
        for record in (
            {"active_window": "0xb00", "input_focus_ancestry": ["0xa00"]},
            {"active_window": "0xa00", "input_focus_ancestry": ["0xb00"]},
            {"active_window": "0xa00", "input_focus_ancestry": ["0x0", "0xa00"]},
            {"active_window": "0xa00", "input_focus_ancestry": ["0x1", "0xa00"]},
            {"active_window": "0xa00", "input_focus_ancestry": ["0xa05", "0xa05", "0xa00"]},
            {"active_window": "0xa00", "input_focus_ancestry": [hex(n) for n in range(10,27)]+["0xa00"]},
            {"active_window": "0xa00", "input_focus_ancestry": ["0xa00"], "fallback": True},
        ):
            with self.subTest(record=record), self.assertRaises(GUARDS.AudioError):
                GUARDS.validate_close_keyboard_target(record,binding)

    def test_close_cannot_start_without_original_bound_pcm_observation(self):
        ui, _, _, entries, effects = self.subject()
        ui.binding["window"] = "0xa00"
        ui.events = ["original-audition"]
        ui.owned_close_keyboard_target = lambda: self.fail("No window access before PCM witness")
        for preview in (None, {"text": GUARDS.PREVIEW, "matches": 1, "binding": {"pid":99}}):
            ui.pcm_preview = preview
            with self.assertRaisesRegex(GUARDS.AudioError, "UI_CLOSE_REQUIRES_BOUND_PCM_RESPONSE"):
                ui.close_window()
        self.assertEqual(entries, [])
        self.assertEqual(effects, [])

    def test_close_focus_probe_failure_and_changed_identity_cannot_dispatch(self):
        ui, _, _, entries, effects = self.subject()
        ui.binding["window"] = "0xa00"
        globals_ = GUARDS.AudioUI.close_window.__globals__
        previous = globals_.get("subprocess")
        seen = []
        def run(args, **kwargs):
            seen.append(args[0])
            self.assertGreater(kwargs["timeout"],0)
            self.assertLessEqual(kwargs["timeout"],3)
            if args[0]=="xprop":
                return SimpleNamespace(returncode=0, stdout="_NET_ACTIVE_WINDOW(WINDOW): window id # 0xa00\n")
            raise subprocess.TimeoutExpired(args,kwargs["timeout"])
        globals_["subprocess"]=SimpleNamespace(DEVNULL=-1,run=run)
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                ui.owned_close_keyboard_target()
            self.assertEqual(seen,["xprop",sys.executable])
            self.assertEqual(entries,[])
            self.assertEqual(effects,[])
            globals_["subprocess"].run=lambda args,**kwargs: SimpleNamespace(
                returncode=0, stderr="", stdout=("_NET_ACTIVE_WINDOW(WINDOW): window id # 0xa00\n"
                    if args[0]=="xprop" else '{"input_focus_ancestry":["0xa00"]}'))
            checks=[]
            def identity():
                checks.append(True)
                GUARDS.need(len(checks)<3,"UI_PROCESS_CHANGED")
            ui.identity=identity
            with self.assertRaisesRegex(GUARDS.AudioError,"UI_PROCESS_CHANGED"):
                ui.owned_close_keyboard_target()
        finally:
            if previous is None:
                globals_.pop("subprocess",None)
            else:
                globals_["subprocess"]=previous

    def close_case(self):
        ui, item, _, _, _ = self.subject()
        ui.binding["window"] = "0xa00"
        ui.pcm_preview = {"text": GUARDS.PREVIEW, "matches": 1, "binding": deepcopy(ui.binding)}
        ui.events = ["original-audition"]
        item.name = GUARDS.AUDITION
        item.node = SimpleNamespace(clear_cache_single=lambda: None)
        item.states = frozenset(("visible", "showing", "enabled", "sensitive"))
        item.interfaces = frozenset(("Action", "Component"))
        ui.bridge.view = lambda _: SimpleNamespace(
            node=item.node, role=item.role, name=GUARDS.AUDITION_PCM_READY,
            states=item.states, interfaces=item.interfaces)
        ui.bridge.focus_title = lambda _: True
        ui.bridge.call = lambda function, *args: function(*args)
        ui.bridge.drain = lambda: None
        ui.bridge.glib = SimpleNamespace(Error=OSError)
        pressed = []
        def keyboard(number, _, kind):
            pressed.append((number, kind))
            return True
        ui.bridge.api = SimpleNamespace(
            generate_keyboard_event=keyboard,
            KeySynthType=SimpleNamespace(PRESS="press", RELEASE="release"))
        keymap = SimpleNamespace(
            get_modifier_state=lambda: 0,
            get_entries_for_keyval=lambda key: (True, [SimpleNamespace(
                keycode=64 if key == "Alt_L" else 70, group=0, level=0)]))
        ui.gdk = SimpleNamespace(
            Keymap=SimpleNamespace(get_for_display=lambda _: keymap),
            Display=SimpleNamespace(get_default=lambda: object()),
            ModifierType=SimpleNamespace(SHIFT_MASK=1, CONTROL_MASK=2, MOD1_MASK=4, SUPER_MASK=8),
            keyval_from_name=lambda name: name)
        return ui, pressed

    def test_close_releases_uncertain_keys_even_if_release_journal_fails(self):
        ui, pressed = self.close_case()
        def journal(label, value):
            if label.endswith("-release-intent"):
                raise OSError("unit disk failure during release")
        ui.journal.put = journal
        globals_ = GUARDS.AudioUI.close_window.__globals__
        previous = globals_.get("subprocess")
        globals_["subprocess"] = SimpleNamespace(DEVNULL=-1, run=lambda args, **kwargs: SimpleNamespace(
            returncode=0, stderr="", stdout=("_NET_ACTIVE_WINDOW(WINDOW): window id # 0xa00\n"
                if args[0] == "xprop" else '{"input_focus_ancestry":["0xa00"]}')))
        try:
            with self.assertRaises(OSError):
                ui.close_window()
        finally:
            if previous is None:
                globals_.pop("subprocess", None)
            else:
                globals_["subprocess"] = previous
        self.assertEqual(pressed, [(64, "press"), (70, "press"), (70, "release"), (64, "release")])
        self.assertEqual(ui.events.count("window-close"), 1)

    def test_focus_lost_after_alt_releases_only_the_owned_held_key(self):
        ui, pressed = self.close_case()
        checks=[]
        def target():
            checks.append(True)
            if len(checks)==3:
                raise GUARDS.AudioError("UI_CLOSE_INPUT_FOCUS_NOT_OWNED")
            return {"active_window":"0xa00","input_focus_ancestry":["0xa00"]}
        ui.owned_close_keyboard_target=target
        ui.journal.put=lambda label,value: None
        with self.assertRaisesRegex(GUARDS.AudioError,"UI_CLOSE_INPUT_FOCUS_NOT_OWNED"):
            ui.close_window()
        self.assertEqual(pressed,[(64,"press"),(64,"release")])
        self.assertEqual(ui.events.count("window-close"),1)
        with self.assertRaises(GUARDS.AudioError):
            ui.close_window()
        self.assertEqual(pressed,[(64,"press"),(64,"release")])

    def test_xlib_read_probe_checks_focus_consistency_and_closes_its_connection(self):
        import contextlib
        import ctypes
        import io
        from unittest.mock import patch
        cases=(
            (0xa00,0xa00,{},0),
            (0xa05,0xa05,{0xa05:0xa00},0),
            (0xa05,0xb00,{0xa05:0xa00},8),
            (0xa05,0xa05,{0xa05:0xa05},5),
            (0xb00,0xb00,{0xb00:0x100},6),
            (0,0,{},5),(1,1,{},5),
        )
        for focus,confirm,parents,expected in cases:
            with self.subTest(focus=focus,confirm=confirm,parents=parents):
                calls=[]
                allocations=[]
                def get_focus(display,pointer,revert):
                    count=sum(item=="focus" for item in calls)
                    calls.append("focus")
                    pointer._obj.value=focus if count==0 else confirm
                    revert._obj.value=0  # RevertToNone is legal with a real focus.
                    return 1
                def query(display,window,root,parent,children,count):
                    calls.append("tree")
                    root._obj.value=0x100
                    parent._obj.value=parents[window]
                    allocated=(ctypes.c_ulong*1)(0xaaa)
                    allocations.append(allocated)
                    ctypes.cast(children,ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)))[0]=ctypes.cast(
                        allocated,ctypes.POINTER(ctypes.c_ulong))
                    count._obj.value=1
                    return 1
                api=SimpleNamespace(XOpenDisplay=lambda _:42,XGetInputFocus=get_focus,XQueryTree=query,
                    XFree=lambda _:calls.append("free"),XCloseDisplay=lambda _:calls.append("close"))
                output=io.StringIO()
                code=0
                with patch.object(ctypes,"CDLL",return_value=api), patch.object(sys,"argv",["probe","0xa00"]), contextlib.redirect_stdout(output):
                    try:
                        exec(compile(GUARDS.X11_INPUT_FOCUS_PROBE,"read-only-x11-focus","exec"),{})
                    except SystemExit as error:
                        code=error.code
                self.assertEqual(code,expected)
                self.assertEqual(calls[-1],"close")
                self.assertEqual(calls.count("free"),calls.count("tree"))
                if expected==0:
                    value=json.loads(output.getvalue())
                    self.assertEqual(value["input_focus_ancestry"][-1],"0xa00")
                    self.assertEqual(calls.count("focus"),2)
                else:
                    self.assertEqual(output.getvalue(),"")


if __name__ == "__main__":
    unittest.main()
