#!/usr/bin/env python3
"""One admitted development Tauri Audio UI Save and original PCM audition.

Uses the real compiled desktop, native OwnerInvocation and original SQLite/CAS.
AT-SPI operates the real window; no JavaScript, replacement invoke or Ready shim.
The normal window close must finish the writer handoff. Cleanup signals never
earn success. All originals and detailed failures remain private for the caller
to seal; stdout contains only a bounded status code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
import traceback
import uuid

import linux_first_use_accessible as accessible
import native_host_installed_shared_state as processes
from native_provider_lifecycle import tree_snapshot
import native_saved_mix_av as pcm
import source_checkout_observation as source

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "motionwright.tauri-audio-development/1"
SHA40 = re.compile(r"[a-f0-9]{40}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
MAX_JSON = 512 * 1024
MAX_BUILD = 32 * 1024 * 1024
MAX_STREAM = 4 * 1024 * 1024
MAX_PRIVATE = 128 * 1024 * 1024
MAX_RSS_KIB = 1536 * 1024
SOURCE_PATHS = (
    "SOURCE_LOCK.json", "Cargo.toml", "Cargo.lock",
    ".github/workflows/v05-original-native-mix.yml",
    "tooling/native_tauri_audio_audition.py",
    "tooling/ci_tauri_audio_audition.py", "tooling/ci_tauri_audio_session.py",
    "tooling/ci_private_evidence.py", "tooling/ci_test_runner.py",
    "tooling/tests/test_native_tauri_audio_audition.py",
    "tooling/tests/test_ci_tauri_audio_audition.py",
    "tooling/tests/test_ci_v05_original_mix_impact_route.py",
    "tooling/linux_first_use_accessible.py",
    "tooling/native_host_installed_shared_state.py",
    "tooling/native_provider_lifecycle.py", "tooling/source_checkout_observation.py",
    "tooling/native_saved_mix_av.py", "tooling/native_original_mix_custody.py",
    "apps/desktop/src-tauri/Cargo.toml", "apps/desktop/src-tauri/build.rs",
    "apps/desktop/src-tauri/tauri.conf.json",
    "apps/desktop/src-tauri/src/main.rs", "apps/desktop/src-tauri/src/desktop_owner.rs",
    "apps/desktop/src-tauri/src/original_mix_native.rs",
    "apps/studio/package.json", "apps/studio/package-lock.json",
    "apps/studio/src/App.tsx", "apps/studio/src/ProjectLibraryDialog.tsx",
    "apps/studio/src/AudioWorkspace.tsx", "apps/studio/src/NativeOriginalMixReview.tsx",
    "apps/studio/src/deliveryRecipe.ts", "apps/studio/src/api.ts",
    "apps/studio/src/DeliveryProfiles.tsx", "apps/studio/src/audioRecipeContract.ts",
    "apps/studio/src/nativeMixTypes.ts",
    "crates/native/src/original_mix.rs",
    "crates/native/src/original_mix/development_audition_custody.rs",
    "crates/native/examples/native-render-e2e.rs",
    "crates/native/examples/native_saved_mix_e2e/mod.rs",
    "crates/native/examples/native_saved_mix_e2e/tauri_audio.rs",
    "crates/service/src/read_only.rs", "crates/storage/src/read_only.rs",
    "crates/domain/src/lib.rs", "crates/domain/src/production_design.rs",
    "crates/domain/src/delivery_recipe.rs",
)
SDK_PATHS = ("Cargo.toml", "Cargo.lock", "crates/native-sdk/src/lib.rs")


class AudioError(RuntimeError):
    pass


def need(ok, code):
    if not ok:
        raise AudioError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def code(error):
    text = str(error)
    return text if re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", text) else type(error).__name__[:80]


def exact_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            need(key not in value, "DUPLICATE_JSON_KEY")
            value[key] = item
        return value
    def invalid(_):
        raise AudioError("NONFINITE_JSON_NUMBER")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def node(meta):
    return {"device": meta.st_dev, "inode": meta.st_ino, "mode": meta.st_mode,
            "uid": meta.st_uid, "gid": meta.st_gid}


def leaf(meta):
    need(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1
         and meta.st_uid == os.getuid() and meta.st_mode & 0o077 == 0,
         "PRIVATE_SINGLE_LINK_FILE_REQUIRED")
    return (*node(meta).values(), meta.st_nlink, meta.st_size,
            meta.st_mtime_ns, meta.st_ctime_ns)


def namespace(path, private_root=None):
    need(path.is_absolute() and path == path.resolve(strict=True)
         and len(path.parts) <= 40, "CANONICAL_NAMESPACE_REQUIRED")
    rows = []
    for current in reversed((path, *path.parents)):
        meta = current.lstat()
        need(stat.S_ISDIR(meta.st_mode) and not stat.S_ISLNK(meta.st_mode),
             "DIRECTORY_NAMESPACE_REQUIRED")
        if private_root is not None and current.is_relative_to(private_root):
            need(meta.st_uid == os.getuid() and meta.st_mode & 0o022 == 0,
                 "PRIVATE_NAMESPACE_OTHER_WRITER")
            if current == private_root:
                need(meta.st_mode & 0o077 == 0, "PRIVATE_ROOT_ACCESS_REQUIRED")
        rows.append((str(current), node(meta)))
    if private_root is not None:
        need(path.is_relative_to(private_root), "PATH_OUTSIDE_PRIVATE_ROOT")
    return rows


def read_original(path, maximum, private_root=None):
    ns = namespace(path.parent, private_root)
    before = leaf(path.lstat())
    need(0 < before[-3] <= maximum, "ORIGINAL_FILE_BOUND")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        stream = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    with stream:
        opened_identity = node(os.fstat(stream.fileno()))
        need(leaf(os.fstat(stream.fileno())) == before, "ORIGINAL_NAMESPACE_FD_MISMATCH")
        raw = stream.read(maximum + 1)
        need(len(raw) == before[-3] and len(raw) <= maximum
             and leaf(os.fstat(stream.fileno())) == before
             and leaf(path.lstat()) == before
             and namespace(path.parent, private_root) == ns, "ORIGINAL_CHANGED_DURING_READ")
    return raw, {"path": str(path), "bytes": len(raw), "sha256": sha(raw),
                 "identity": opened_identity, "links": 1}


def read_json(path, maximum=MAX_JSON, private_root=None):
    raw, identity = read_original(path, maximum, private_root)
    value = exact_json(raw)
    need(type(value) is dict, "JSON_OBJECT_REQUIRED")
    return value, identity


def publish(path, value, private_root):
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    need(0 < len(raw) <= MAX_JSON, "PRIVATE_RECEIPT_BOUND")
    before = namespace(path.parent, private_root)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    need(namespace(path.parent, private_root) == before, "OUTPUT_NAMESPACE_CHANGED")
    observed, identity = read_original(path, MAX_JSON, private_root)
    need(observed == raw, "OUTPUT_READBACK_CHANGED")
    return identity


def admission(repo):
    need(sys.platform == "linux" and os.getuid() != 0
         and os.environ.get("CI") == "true" and os.environ.get("GITHUB_ACTIONS") == "true",
         "REAL_ROOTLESS_LINUX_CI_REQUIRED")
    need(Path(sys.executable).resolve() == Path("/usr/bin/python3").resolve(),
         "SYSTEM_PYTHON_GI_REQUIRED")
    expected = os.environ.get("GITHUB_SHA", "")
    run = os.environ.get("GITHUB_RUN_ID", "")
    need(SHA40.fullmatch(expected) and re.fullmatch(r"[1-9][0-9]{0,19}", run),
         "CI_SOURCE_RUN_IDENTITY")
    need(os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
         and os.environ.get("GITHUB_REPOSITORY") == "seradotcom/motionwright"
         and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
         and os.environ.get("MW_AUDIO_DIAGNOSTIC_SELECTION") == "tauri-audition"
         and os.environ.get("GITHUB_WORKFLOW_REF", "").startswith(
             "seradotcom/motionwright/.github/workflows/v05-original-native-mix.yml@refs/heads/"),
         "ORIGINAL_AUDIO_DISPATCH_REQUIRED")
    need(repo.is_absolute() and repo == repo.resolve(strict=True) and repo == ROOT,
         "EXECUTING_SOURCE_CHECKOUT_MISMATCH")
    runner = Path(os.environ.get("RUNNER_TEMP", ""))
    need(runner.is_absolute() and runner == runner.resolve(strict=True),
         "CANONICAL_RUNNER_TEMP_REQUIRED")
    need(os.environ.get("DISPLAY") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"),
         "LIVE_X11_DBUS_REQUIRED")
    need(all(key not in os.environ for key in
             ("WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS", "TAURI_CONFIG", "LD_PRELOAD", "LD_AUDIT")),
         "UNADMITTED_DESKTOP_OVERRIDE")
    lock = exact_json((repo / "SOURCE_LOCK.json").read_bytes())["dependencies"]["semwright"]
    need(lock["repository"] == "https://github.com/seradotcom/semwright"
         and SHA40.fullmatch(lock["revision"]), "LOCKED_SDK_ORIGIN_REQUIRED")
    return {"source_sha": expected, "sdk_sha": lock["revision"], "run_id": run,
            "run_attempt": "1", "workflow_ref": os.environ["GITHUB_WORKFLOW_REF"],
            "private_root": str(runner / f"motionwright-tauri-audition-{run}-1")}


def admitted_build(args, context):
    runner = Path(os.environ["RUNNER_TEMP"])
    need(args.build_original.is_relative_to(runner)
         and args.desktop.is_relative_to(runner) and args.fixture.is_relative_to(runner),
         "BUILD_INPUTS_OUTSIDE_RUNNER_TEMP")
    raw, original = read_original(args.build_original, MAX_BUILD)
    rows = [exact_json(line) for line in raw.splitlines() if line.strip()]
    need(rows and len(rows) <= 20000 and all(type(row) is dict for row in rows),
         "CARGO_ORIGINAL_STRUCTURE")
    terminals = [row for row in rows if row.get("reason") == "build-finished"]
    need(1 <= len(terminals) <= 2 and all(row.get("success") is True for row in terminals)
         and rows[-1].get("reason") == "build-finished", "CARGO_BUILD_NOT_COMPLETE")
    result = {"cargo_original": original, "artifacts": {}}
    for name, path, kind, relative in (
        ("motionwright-desktop", args.desktop, "bin", "apps/desktop/src-tauri/src/main.rs"),
        ("native-render-e2e", args.fixture, "example", "crates/native/examples/native-render-e2e.rs"),
    ):
        found = [row for row in rows if row.get("reason") == "compiler-artifact"
                 and row.get("target", {}).get("name") == name and row.get("executable")]
        need(len(found) == 1, "ONE_ORIGINAL_EXECUTABLE_REQUIRED")
        row = found[0]
        need(row.get("fresh") is False and row["target"]["kind"] == [kind]
             and Path(row["target"]["src_path"]) == args.repo / relative
             and Path(row["executable"]) == path and path.resolve(strict=True) == path,
             "FRESH_EXECUTABLE_SOURCE_BINDING")
        identity = source.regular_file(path, 512 * 1024 * 1024)
        need(os.access(path, os.X_OK), "EXECUTABLE_REQUIRED")
        with path.open("rb") as stream:
            need(stream.read(4) == b"\x7fELF", "REAL_LINUX_EXECUTABLE_REQUIRED")
        result["artifacts"][name] = {"path": str(path), **identity, "cargo_artifact": row}
    need(any(row.get("reason") == "compiler-artifact"
             and row.get("target", {}).get("name") == "tauri"
             and "custom-protocol" in row.get("features", []) for row in rows),
         "TAURI_EMBEDDED_CUSTOM_PROTOCOL_REQUIRED")
    sdks = {row["target"]["src_path"] for row in rows
            if row.get("reason") == "compiler-artifact"
            and row.get("target", {}).get("name") == "semwright_native_sdk"}
    need(len(sdks) == 1, "ONE_COMPILED_SDK_SOURCE_REQUIRED")
    sdk_source = Path(sdks.pop())
    sdk_root = Path(source.git(sdk_source.parent, "rev-parse", "--show-toplevel").decode().strip())
    result["sdk_root"] = str(sdk_root)
    result["sdk_source"] = source.source_observation(sdk_root, context["sdk_sha"], SDK_PATHS)
    return result

RIGHTS = ("I confirm I have permission to use these exact original sources for local audition only. "
          "This is not an independently verified rights grant.")
SUMMARY = "Voice / music / SFX · original source mix"
AUDITION = "Create local original PCM audition"
PREVIEW = "Local source PCM only · 48 kHz · not a mastered deliverable"
NUMBERS = {"SFX gain dB": "-8", "Music duck attenuation dB": "14",
           "Attack (samples at 48 kHz)": "2400", "Release (samples at 48 kHz)": "4800"}


def read_only_text_provider(states, interfaces):
    return ({"visible", "showing"} <= states and "defunct" not in states
            and "editable" not in states and "Text" in interfaces
            and "EditableText" not in interfaces)


def unmapped_action_provider(states, interfaces):
    return ({"visible", "enabled", "sensitive"} <= states
            and not ({"editable", "defunct"} & states)
            and {"Action", "Component"} <= interfaces and "EditableText" not in interfaces)


def accessible_action_index(names, role, states):
    need(type(names) is list and len(names) <= 16
         and all(type(value) is str and len(value) <= 128 for value in names),
         "UI_ACTION_OBSERVATION_BOUND")
    allowed = {"click", "press", "activate", "toggle", "expand or contract"}
    if role == "checkbox":
        allowed.add("uncheck" if "checked" in states else "check")
    choices = [index for index, action in enumerate(names) if action.lower() in allowed]
    need(len(choices) == 1, "UI_ACTION_AMBIGUOUS_OR_UNSUPPORTED")
    return choices[0]


def source_key_sequence(index, count):
    # This physical fixture binds exactly two distinct source options.
    # A keyboard transaction, not AT-SPI Selection.select_child, triggers
    # React's actual input/change handling on the controlled HTML select.
    need(type(index) is int and type(count) is int and count == 3
         and index in (1, 2), "UI_SOURCE_KEYBOARD_OPTION_BUDGET")
    # Tab commits the native HTML select and blurs it; Return can ACK the
    # popup selection without delivering React's change event on WebKit.
    return ("Home",) + ("Down",) * index + ("Tab",)


def source_key_receipt_name(label, index, phase):
    # Original DiskJournal accepts only lowercase letters and hyphens.
    # Preserve the per-key once-only identity without broadening its parser.
    ordinals = ("first", "second", "third", "fourth")
    need(label in ("select-music", "select-sfx")
         and type(index) is int and 0 <= index < len(ordinals)
         and phase in ("intent", "return"), "UI_SOURCE_JOURNAL_NAME_BOUND")
    return label + "-key-" + ordinals[index] + "-" + phase


def source_key_journal_plan():
    # The actual original UI has exactly three options in each source combobox:
    # placeholder, imported music, imported SFX. Keep the two closed plans.
    return tuple((label, key_index, key)
                 for label, option_index in (("select-music", 1), ("select-sfx", 2))
                 for key_index, key in enumerate(source_key_sequence(option_index, 3)))


def exact_text_offsets(text, value):
    need(type(text) is str and len(text) <= 4096, "UI_TEXT_PROVIDER_BOUND")
    need(type(value) is str and 0 < len(value) <= 256, "UI_TEXT_TARGET_BOUND")
    token = lambda char: char.isalnum() or char in "_-"
    result, start = [], 0
    while True:
        at = text.find(value, start)
        if at < 0:
            return result
        end = at + len(value)
        if ((at == 0 or not token(value[0]) or not token(text[at - 1]))
                and (end == len(text) or not token(value[-1]) or not token(text[end]))):
            result.append((at, end))
            need(len(result) <= 8, "UI_TEXT_RANGE_AMBIGUITY")
        start = at + 1


def screen_rect(value):
    rectangle = tuple(getattr(value, key) for key in ("x", "y", "width", "height"))
    need(all(type(item) is int and abs(item) <= 32768 for item in rectangle)
         and rectangle[2] > 0 and rectangle[3] > 0, "UI_TEXT_RECT_INVALID")
    return rectangle


def rect_contains(outer, inner):
    x, y, width, height = outer
    left, top, inner_width, inner_height = inner
    return (x <= left and y <= top
            and left + inner_width <= x + width
            and top + inner_height <= y + height)


def private_transport_paths(root):
    paths = {"XDG_RUNTIME_DIR": root / "r", "TMPDIR": root / "t"}
    need(all(len(os.fsencode(path)) <= 70 for path in paths.values()),
         "PRIVATE_TRANSPORT_PATH_TOO_LONG")
    return paths


class AudioBridge(accessible.AtspiBridge):
    def __init__(self, api, glib, deadline):
        super().__init__(api, glib=glib, deadline=deadline)
        self.expected_texts = frozenset()
        self.role_counts = {}
        self.text_ranges = []
        self.text_candidates = []
        for key, label in (("CHECK_BOX", "checkbox"), ("TOGGLE_BUTTON", "toggle"),
                           ("COMBO_BOX", "combo"), ("MENU", "menu"), ("MENU_ITEM", "option"),
                           ("RADIO_MENU_ITEM", "option"), ("LIST_ITEM", "option"),
                           ("STATIC", "static"), ("LABEL", "label"), ("HEADING", "heading"),
                           ("STATUS_BAR", "status"), ("PARAGRAPH", "paragraph"),
                           ("ALERT", "alert"), ("FRAME", "frame")):
            if hasattr(api.Role, key):
                self.roles[getattr(api.Role, key)] = label
        for key in ("CHECKED", "SELECTED", "EXPANDED", "FOCUSED"):
            self.states[key.lower()] = getattr(api.StateType, key)

    def drain(self):
        context = self.glib.MainContext.default()
        for _ in range(64):
            if not self.call(context.pending):
                return
            self.call(context.iteration, False)

    def view(self, item):
        raw_role = self.call(item.get_role)
        role = self.roles.get(raw_role, "other")
        role_name = str(getattr(raw_role, "value_nick", raw_role))[:80]
        self.role_counts[role_name] = self.role_counts.get(role_name, 0) + 1
        state = self.call(item.get_state_set)
        states = frozenset(name for name, value in self.states.items() if state.contains(value))
        if "defunct" in states:
            return accessible.View(item, role, "", states, frozenset())
        interfaces = frozenset(self.call(item.get_interfaces))
        native_action = role == "other" and unmapped_action_provider(states, interfaces)
        if role == "other" and not native_action and (
                not self.expected_texts or not accessible.VISIBLE <= states):
            return accessible.View(item, role, "", states, interfaces)
        name = self.call(item.get_name)
        need(type(name) is str and len(name) <= 4096, "ACCESSIBLE_NAME_BOUND")
        if native_action:
            # WebKit disclosure controls can carry an unmapped role. Their
            # real Action/Component interfaces, exact name, owner and usable
            # states still govern the single original UI dispatch.
            role = "native_action"
        # WebKit may combine inline spans in a container's Text interface.
        # Keep original character offsets and bytes; never manufacture a
        # target name from an expected UUID or accept an editable provider.
        if self.expected_texts and read_only_text_provider(states, interfaces):
            count = self.call(self.api.Text.get_character_count, item)
            need(type(count) is int and count >= 0, "ACCESSIBLE_TEXT_BOUND")
            if 0 < count <= 4096:
                content = self.call(self.api.Text.get_text, item, 0, count)
                need(type(content) is str and len(content) == count,
                     "ACCESSIBLE_TEXT_LENGTH_CHANGED")
                content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if len(self.text_candidates) < 64:
                    self.text_candidates.append({"role": role_name, "characters": count,
                        "text_prefix": content[:512], "sha256": content_sha,
                        "prefix_truncated": count > 512})
                for value in self.expected_texts:
                    for start, end in exact_text_offsets(content, value):
                        need(len(self.text_ranges) < 32, "UI_TEXT_PROVIDER_AMBIGUITY")
                        self.text_ranges.append({"node": item, "role": role_name,
                            "interfaces": interfaces, "start": start, "end": end,
                            "characters": count, "provider_text_sha256": content_sha,
                            "text": content[start:end]})
                if content in self.expected_texts:
                    name = content
                    if role in {"other", "native_action"}:
                        role = "readonly_text"
        return accessible.View(item, role, name, states, interfaces)


class AudioUI:
    def __init__(self, bridge, gdk, binding, before, journal, deadline):
        self.bridge, self.gdk, self.binding = bridge, gdk, binding
        self.before, self.journal, self.deadline = before, journal, deadline
        self.last_condition = "NOT_OBSERVED"
        self.last_views = []
        self.events = []
        self.action_observations = []

    def identity(self):
        need(time.monotonic() < self.deadline, "UI_DEADLINE")
        need(accessible.x11_identity(deadline=self.deadline) == self.binding, "UI_PROCESS_CHANGED")

    def views(self):
        self.identity()
        self.bridge.drain()
        applications = self.bridge.applications()
        need(len(applications) <= 128, "UI_APPLICATION_BOUND")
        app = accessible.unique([item for item in applications
                                 if self.bridge.pid(item) == self.binding["pid"]], "APPLICATION")
        self.bridge.role_counts = {}
        self.bridge.text_ranges = []
        self.bridge.text_candidates = []
        views = accessible.walk(self.bridge, app)
        self.last_views = views
        self.identity()
        return views

    def wait(self, observe):
        while time.monotonic() < self.deadline:
            try:
                return observe()
            except accessible.NotReady as error:
                self.last_condition = str(error)
                time.sleep(min(0.1, max(0, self.deadline - time.monotonic())))
        raise AudioError("UI_OBSERVATION_DEADLINE")

    def control(self, name, roles, *, showing=True):
        views = [item for item in self.views() if item.name == name and item.role in roles
                 and "defunct" not in item.states and "visible" in item.states
                 and (not showing or "showing" in item.states)]
        item = accessible.unique(views, "AUDIO_CONTROL")
        need(self.bridge.owner_pid(item.node) == self.binding["pid"], "UI_CONTROL_OWNER_CHANGED")
        return item

    def visible(self, name, roles, label):
        item = self.wait(lambda: self.control(name, roles, showing=False))
        if "showing" not in item.states:
            need("Component" in item.interfaces, "UI_SCROLL_INTERFACE_REQUIRED")
            self.effect(label + "-scroll", item, lambda: self.bridge.call(
                self.bridge.api.Component.scroll_to, item.node, self.bridge.api.ScrollType.ANYWHERE))
            def scrolled():
                # Component.scroll_to can ACK before WebKit emits SHOWING.
                # Refresh only this already-owned control, as focus_title
                # does for FOCUSED. Keep the structural application cache.
                self.bridge.call(item.node.clear_cache_single)
                return self.control(name, roles)
            item = self.wait(scrolled)
        return item

    def refreshed_source_status(self, expected):
        # WebKit's structural cache can retain the pre-change status text even
        # after the owned native select reports its new option. Refresh exactly
        # that existing status node, not the full application or another tab.
        need(type(expected) is str and expected.startswith("Draft source bindings: music ")
             and 0 < len(expected) <= 256, "UI_SOURCE_STATUS_EXPECTED_INVALID")
        candidates = [item for item in self.views()
                      if item.role == "status"
                      and item.name.startswith("Draft source bindings: music ")
                      and "visible" in item.states and "defunct" not in item.states]
        item = accessible.unique(candidates, "SOURCE_DRAFT_STATUS")
        need(self.bridge.owner_pid(item.node) == self.binding["pid"],
             "UI_SOURCE_STATUS_OWNER_CHANGED")
        self.bridge.call(item.node.clear_cache_single)
        current = self.bridge.view(item.node)
        need(current.node == item.node and current.role == "status"
             and "visible" in current.states and "defunct" not in current.states
             and "editable" not in current.states
             and "EditableText" not in current.interfaces
             and self.bridge.owner_pid(current.node) == self.binding["pid"],
             "UI_SOURCE_STATUS_INVALID_AFTER_REFRESH")
        if current.name != expected:
            raise accessible.NotReady("UI_REACT_SOURCE_BINDINGS_NOT_COMMITTED")
        return current

    def observe_source_status(self, expected):
        # The two hardware selections are one-time effects. Only the original
        # read may be sampled for a short bounded period; never replay keys.
        deadline = min(self.deadline, time.monotonic() + 5.0)
        while True:
            try:
                item = self.refreshed_source_status(expected)
                break
            except accessible.NotReady as error:
                self.last_condition = str(error)
                if time.monotonic() >= deadline:
                    raise AudioError("UI_REACT_SOURCE_BINDINGS_NOT_COMMITTED") from error
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        if "showing" not in item.states:
            need("Component" in item.interfaces, "UI_SOURCE_STATUS_SCROLL_REQUIRED")
            self.effect("source-draft-status-scroll", item, lambda: self.bridge.call(
                self.bridge.api.Component.scroll_to, item.node,
                self.bridge.api.ScrollType.ANYWHERE))
        return self.wait(lambda: self.text(expected))

    def arm_action(self, label):
        need(type(label) is str and label not in self.events, "UI_ACTION_ALREADY_ATTEMPTED")
        self.events.append(label)  # Consumed even if the durable intent fails.

    def effect(self, label, item, operation):
        self.identity()
        need(self.bridge.owner_pid(item.node) == self.binding["pid"], "UI_EFFECT_OWNER_CHANGED")
        self.arm_action(label)
        self.journal.put(label + "-intent", {"binding": self.binding, "role": item.role,
                                             "name": item.name, "automatic_retry": False})
        result = operation()
        self.journal.put(label + "-return", {"accepted": result is True})
        need(result is True, "UI_ACTION_NOT_ACKNOWLEDGED")
        self.identity()

    def click(self, name, roles, label):
        item = self.visible(name, roles, label)
        need(accessible.USABLE <= item.states and "Action" in item.interfaces,
             "UI_ACTION_NOT_USABLE")
        names = self.bridge.actions(item.node)
        need(type(names) is list and len(names) <= 16
             and all(type(value) is str and len(value) <= 128 for value in names)
             and len(self.action_observations) < 16, "UI_ACTION_OBSERVATION_BOUND")
        self.action_observations.append({"label": label, "role": item.role,
                                         "name": item.name, "actions": names})
        index = accessible_action_index(names, item.role, item.states)
        self.effect(label, item, lambda: self.bridge.invoke(item.node, index))
        return item

    def checked(self, name, value):
        item = self.control(name, {"checkbox"})
        self.bridge.call(item.node.clear_cache_single)
        item = self.control(name, {"checkbox"})
        if ("checked" in item.states) != value:
            raise accessible.NotReady("CHECKBOX_STATE_NOT_OBSERVED")
        return item

    def text(self, value):
        need(type(value) is str and 0 < len(value) <= 256, "UI_TEXT_TARGET_BOUND")
        self.bridge.expected_texts = frozenset((value,))
        try:
            observed = self.views()
        finally:
            self.bridge.expected_texts = frozenset()
        found = [item for item in observed
                 if item.name == value and accessible.VISIBLE <= item.states
                 and item.role in {"static", "label", "heading", "status", "paragraph", "text", "readonly_text"}
                 and "editable" not in item.states and "EditableText" not in item.interfaces]
        if not found:
            return self.observed_text_ranges(value, observed)
        need(len(found) <= 8, "UI_TEXT_AMBIGUITY")
        for item in found:
            need(self.bridge.owner_pid(item.node) == self.binding["pid"], "UI_TEXT_OWNER_CHANGED")
            if "Text" in item.interfaces:
                need(self.bridge.read_text(item.node) == value, "UI_NAME_TEXT_DISAGREE")
        return {"text": value, "matches": len(found)}

    def observed_text_ranges(self, value, observed):
        candidates = [item for item in self.bridge.text_ranges if item["text"] == value]
        if not candidates:
            raise accessible.NotReady("EXACT_UI_TEXT_NOT_OBSERVED")
        frames = [item for item in observed if item.role == "frame"
                  and accessible.VISIBLE <= item.states and "defunct" not in item.states
                  and "Component" in item.interfaces]
        frame = accessible.unique(frames, "TEXT_OWNER_WINDOW")
        need(self.bridge.owner_pid(frame.node) == self.binding["pid"], "UI_TEXT_WINDOW_OWNER_CHANGED")
        api = self.bridge.api
        window = screen_rect(self.bridge.call(
            api.Component.get_extents, frame.node, api.CoordType.SCREEN))
        witnesses = []
        for candidate in candidates:
            node = candidate["node"]
            need(self.bridge.owner_pid(node) == self.binding["pid"], "UI_TEXT_OWNER_CHANGED")
            state = self.bridge.call(node.get_state_set)
            states = frozenset(name for name, flag in self.bridge.states.items() if state.contains(flag))
            interfaces = frozenset(self.bridge.call(node.get_interfaces))
            need(read_only_text_provider(states, interfaces), "UI_TEXT_PROVIDER_NOT_READONLY")
            if "Component" not in interfaces:
                continue
            count = self.bridge.call(api.Text.get_character_count, node)
            if type(count) is not int or count != candidate["characters"]:
                raise accessible.NotReady("UI_TEXT_PROVIDER_CHANGED")
            need(0 < count <= 4096, "UI_TEXT_PROVIDER_BOUND")
            content = self.bridge.call(api.Text.get_text, node, 0, count)
            if (type(content) is not str or len(content) != count
                    or hashlib.sha256(content.encode("utf-8")).hexdigest()
                       != candidate["provider_text_sha256"]):
                raise accessible.NotReady("UI_TEXT_PROVIDER_CHANGED")
            need((candidate["start"], candidate["end"]) in exact_text_offsets(content, value),
                 "UI_TEXT_RANGE_BOUNDARY_CHANGED")
            actual = self.bridge.call(api.Text.get_text, node, candidate["start"], candidate["end"])
            need(type(actual) is str and actual == value, "UI_TEXT_RANGE_DISAGREE")
            provider = screen_rect(self.bridge.call(
                api.Component.get_extents, node, api.CoordType.SCREEN))
            rectangle = screen_rect(self.bridge.call(api.Text.get_range_extents, node,
                candidate["start"], candidate["end"], api.CoordType.SCREEN))
            if not (rect_contains(window, provider) and rect_contains(provider, rectangle)
                    and rect_contains(window, rectangle)):
                continue
            witnesses.append({key: candidate[key] for key in (
                "role", "start", "end", "characters", "provider_text_sha256", "text")})
            witnesses[-1].update(screen_rectangle=list(rectangle),
                                provider_rectangle=list(provider), window_rectangle=list(window))
        if not witnesses:
            raise accessible.NotReady("EXACT_UI_TEXT_RANGE_NOT_VISIBLE")
        need(len(witnesses) <= 8, "UI_TEXT_AMBIGUITY")
        self.identity()
        return {"text": value, "matches": len(witnesses), "original_text_ranges": witnesses}

    def selection(self, combo):
        # Closed HTML select shapes: direct options, or one menu child. The
        # actual AT-SPI Selection container and child indices are observed.
        candidates = [combo.node]
        children = self.bridge.children(combo.node)
        if len(children) == 1 and self.bridge.view(children[0]).role == "menu":
            candidates.append(children[0])
        accepted = []
        for candidate in candidates:
            view = self.bridge.view(candidate)
            if "Selection" not in view.interfaces:
                continue
            options = self.bridge.children(candidate)
            if not 1 <= len(options) <= 16:
                continue
            views = [self.bridge.view(item) for item in options]
            if all(item.role == "option" and item.name for item in views):
                need(all(self.bridge.owner_pid(item.node) == self.binding["pid"] for item in views),
                     "UI_OPTION_OWNER_CHANGED")
                accepted.append((candidate, views))
        need(len(accepted) == 1, "UI_SINGLE_SELECTION_CONTAINER_REQUIRED")
        return accepted[0]

    def selected_name(self, container):
        need(self.bridge.call(self.bridge.api.Selection.get_n_selected_children, container) == 1,
             "UI_SINGLE_SELECTED_OPTION_REQUIRED")
        item = self.bridge.call(self.bridge.api.Selection.get_selected_child, container, 0)
        need(item is not None and self.bridge.owner_pid(item) == self.binding["pid"],
             "UI_SELECTED_OPTION_OWNER_CHANGED")
        return self.bridge.view(item).name

    def dispatch_source_keyboard(self, combo, sequence, label):
        # Focus and real key events are restricted to the owned X11 window.
        # A Selection.select_child ACK is not a React input/change event.
        need(combo.role == "combo" and "Component" in combo.interfaces
             and self.bridge.owner_pid(combo.node) == self.binding["pid"],
             "UI_SOURCE_KEYBOARD_OWNER")
        need(self.bridge.focus_title(combo.node) is True, "UI_SOURCE_FOCUS_UNCONFIRMED")
        self.identity()
        active = subprocess.run(["xprop", "-root", "_NET_ACTIVE_WINDOW"], capture_output=True,
                                text=True, timeout=min(3, max(0.1, self.deadline - time.monotonic())),
                                check=False)
        match = re.fullmatch(r"_NET_ACTIVE_WINDOW\(WINDOW\): window id # (0x[0-9a-fA-F]+)\s*", active.stdout)
        need(active.returncode == 0 and match
             and int(match.group(1), 16) == int(self.binding["window"], 16),
             "UI_SOURCE_KEYBOARD_WRONG_WINDOW")
        keymap = self.gdk.Keymap.get_for_display(self.gdk.Display.get_default())
        need(keymap is not None, "UI_SOURCE_KEYMAP_MISSING")
        modifiers = (self.gdk.ModifierType.SHIFT_MASK | self.gdk.ModifierType.CONTROL_MASK
                     | self.gdk.ModifierType.MOD1_MASK | self.gdk.ModifierType.SUPER_MASK)
        need(not keymap.get_modifier_state() & modifiers, "UI_SOURCE_KEYBOARD_MODIFIERS")
        keys = {}
        for name in set(sequence):
            present, entries = keymap.get_entries_for_keyval(self.gdk.keyval_from_name(name))
            need(present and 1 <= len(entries) <= 16, "UI_SOURCE_HARDWARE_KEY_MISSING")
            codes = {entry.keycode for entry in entries if entry.group == 0 and entry.level == 0}
            need(len(codes) == 1, "UI_SOURCE_HARDWARE_KEY_AMBIGUOUS")
            keys[name] = codes.pop()
        for index, name in enumerate(sequence):
            self.journal.put(source_key_receipt_name(label, index, "intent"),
                {"key": name, "hardware_keycode": keys[name], "automatic_retry": False})
            primary, held = None, False
            try:
                held = True  # Even a missing ACK leaves this press uncertain.
                need(self.bridge.call(self.bridge.api.generate_keyboard_event,
                    keys[name], None, self.bridge.api.KeySynthType.PRESS) is True,
                    "UI_SOURCE_KEY_PRESS_UNCONFIRMED")
            except BaseException as error:
                primary = error
            finally:
                if held:
                    try:
                        need(self.bridge.call(self.bridge.api.generate_keyboard_event,
                            keys[name], None, self.bridge.api.KeySynthType.RELEASE) is True,
                            "UI_SOURCE_KEY_RELEASE_UNCONFIRMED")
                    except BaseException as error:
                        if primary is None:
                            primary = error
            if primary is not None:
                raise primary
            self.journal.put(source_key_receipt_name(label, index, "return"),
                             {"hardware_press_and_release_acknowledged": True})
        self.identity()
        return True

    def choose_source(self, name, placeholder, selected, source_labels, label):
        combo = self.visible(name, {"combo"}, label)
        need(accessible.USABLE <= combo.states, "UI_SOURCE_SELECT_DISABLED")
        container, options = self.selection(combo)
        need([item.name for item in options] == [placeholder, *source_labels],
             "UI_SOURCE_OPTION_IDENTITY_OR_ORDER")
        need(self.selected_name(container) == placeholder, "UI_SOURCE_NOT_ORIGINALLY_UNSELECTED")
        index = next(index for index, item in enumerate(options) if item.name == selected)
        self.effect(label, combo, lambda: self.dispatch_source_keyboard(
            combo, source_key_sequence(index, len(options)), label))
        def observed():
            current = self.control(name, {"combo"})
            need(current.node == combo.node, "UI_SOURCE_SELECT_REPLACED")
            actual, _ = self.selection(current)
            if self.selected_name(actual) != selected:
                raise accessible.NotReady("UI_SOURCE_SELECTION_NOT_OBSERVED")
            return {"control": name, "selected": selected}
        return self.wait(observed)

    def observe_original_pcm_near_owned_button(self, original_button):
        # WebKit's native <audio> controls can expose an enormous internal
        # subtree after the real PCM response. Never walk that subtree or
        # expand the global 2048-node guard. Start from the exact button that
        # dispatched the one native audition, then inspect bounded siblings.
        self.identity()
        need(original_button.role == "button" and original_button.name == AUDITION
             and self.bridge.owner_pid(original_button.node) == self.binding["pid"],
             "UI_PCM_PREVIEW_ANCHOR_NOT_OWNED")
        node = original_button.node
        for _ in range(6):
            parent = self.bridge.call(node.get_parent)
            if parent is None:
                raise accessible.NotReady("UI_PCM_PREVIEW_PARENT_NOT_READY")
            need(parent != node and self.bridge.owner_pid(parent) == self.binding["pid"],
                 "UI_PCM_PREVIEW_PARENT_CHANGED")
            siblings = self.bridge.children(parent)
            need(1 <= len(siblings) <= 96, "UI_PCM_PREVIEW_LOCAL_CHILD_BOUND")
            groups = []
            for sibling in siblings:
                name = self.bridge.call(sibling.get_name)
                need(type(name) is str and len(name) <= 4096, "UI_PCM_PREVIEW_NAME_BOUND")
                if name == "Unmastered original PCM preview":
                    groups.append(sibling)
            need(len(groups) <= 1, "UI_PCM_PREVIEW_GROUP_AMBIGUOUS")
            if groups:
                group = groups[0]
                need(self.bridge.owner_pid(group) == self.binding["pid"],
                     "UI_PCM_PREVIEW_GROUP_OWNER_CHANGED")
                container = self.bridge.view(group)
                need("visible" in container.states and "defunct" not in container.states
                     and "editable" not in container.states,
                     "UI_PCM_PREVIEW_GROUP_NOT_VISIBLE")
                # The actual text/status precedes <audio controls> in Studio.
                # Read direct children only: never descend into the media
                # control subtree, which can exceed the entire AT-SPI budget.
                leaves = self.bridge.children(group)
                need(1 <= len(leaves) <= 16, "UI_PCM_PREVIEW_CHILD_BOUND")
                matching = []
                for leaf in leaves:
                    name = self.bridge.call(leaf.get_name)
                    need(type(name) is str and len(name) <= 4096,
                         "UI_PCM_PREVIEW_TEXT_NAME_BOUND")
                    if name == PREVIEW:
                        matching.append(leaf)
                need(len(matching) <= 1, "UI_PCM_PREVIEW_TEXT_AMBIGUOUS")
                if not matching:
                    raise accessible.NotReady("UI_PCM_PREVIEW_STATUS_NOT_READY")
                item = self.bridge.view(matching[0])
                need(item.role == "status" and item.name == PREVIEW
                     and "visible" in item.states and "defunct" not in item.states
                     and "editable" not in item.states and "EditableText" not in item.interfaces
                     and self.bridge.owner_pid(item.node) == self.binding["pid"],
                     "UI_PCM_PREVIEW_STATUS_NOT_ORIGINAL")
                if "showing" not in item.states:
                    if "preview-result-scroll" in self.events:
                        raise accessible.NotReady("UI_PCM_PREVIEW_NOT_SHOWING")
                    need("Component" in item.interfaces, "UI_PCM_PREVIEW_SCROLL_UNAVAILABLE")
                    self.effect("preview-result-scroll", item, lambda: self.bridge.call(
                        self.bridge.api.Component.scroll_to, item.node,
                        self.bridge.api.ScrollType.ANYWHERE))
                    self.bridge.call(item.node.clear_cache_single)
                    item = self.bridge.view(item.node)
                    if "showing" not in item.states:
                        raise accessible.NotReady("UI_PCM_PREVIEW_NOT_SHOWING")
                need(item.role == "status" and item.name == PREVIEW
                     and accessible.VISIBLE <= item.states and "defunct" not in item.states
                     and "editable" not in item.states and "EditableText" not in item.interfaces
                     and self.bridge.owner_pid(item.node) == self.binding["pid"],
                     "UI_PCM_PREVIEW_STATUS_CHANGED")
                if "Text" in item.interfaces:
                    need(self.bridge.read_text(item.node) == PREVIEW,
                         "UI_PCM_PREVIEW_NAME_TEXT_DISAGREE")
                self.identity()
                return {"text": PREVIEW, "matches": 1}
            node = parent
        raise accessible.NotReady("UI_PCM_PREVIEW_NOT_IN_OWNED_COMPONENT")

    def _refreshed_close_button(self, original_button):
        self.identity()
        item = self.bridge.view(original_button.node)
        if item.name != AUDITION:
            raise accessible.NotReady("UI_CLOSE_BUTTON_NOT_READY")
        need(item.node == original_button.node and item.role == "button"
             and {"visible", "showing", "enabled", "sensitive"} <= item.states
             and "Action" in item.interfaces
             and "Component" in item.interfaces
             and "EditableText" not in item.interfaces
             and self.bridge.owner_pid(item.node) == self.binding["pid"],
             "UI_CLOSE_BUTTON_CHANGED")
        return item

    def close_window(self, original_button):
        # The same original button remains the close-focus anchor. A fresh
        # global traversal after native <audio> construction would exceed the
        # bounded AT-SPI tree and is neither needed nor permitted.
        need(original_button.role == "button" and original_button.name == AUDITION
             and self.bridge.owner_pid(original_button.node) == self.binding["pid"],
             "UI_CLOSE_BUTTON_ANCHOR_CHANGED")
        self.bridge.call(original_button.node.clear_cache_single)
        target = self.wait(lambda: self._refreshed_close_button(original_button))
        # GNOME's API defines PRESS/RELEASE as hardware keycodes. Resolve them
        # from this live GDK keymap; no fixed keycode, xdotool or direct XTest.
        # https://docs.gtk.org/atspi2/func.generate_keyboard_event.html
        # https://docs.gtk.org/gdk3/method.Keymap.get_entries_for_keyval.html
        self.effect("close-focus", target, lambda: self.bridge.focus_title(target.node))
        active = subprocess.run(["xprop", "-root", "_NET_ACTIVE_WINDOW"], capture_output=True,
                                text=True, timeout=min(3, self.deadline - time.monotonic()), check=False)
        match = re.fullmatch(r"_NET_ACTIVE_WINDOW\(WINDOW\): window id # (0x[0-9a-fA-F]+)\s*", active.stdout)
        need(active.returncode == 0 and match and int(match.group(1), 16) == int(self.binding["window"], 16),
             "OWNED_WINDOW_NOT_ACTIVE_FOR_CLOSE")
        keymap = self.gdk.Keymap.get_for_display(self.gdk.Display.get_default())
        need(keymap is not None, "LIVE_GDK_KEYMAP_REQUIRED")
        modifiers = (self.gdk.ModifierType.SHIFT_MASK | self.gdk.ModifierType.CONTROL_MASK
                     | self.gdk.ModifierType.MOD1_MASK | self.gdk.ModifierType.SUPER_MASK)
        need(not keymap.get_modifier_state() & modifiers, "KEYBOARD_MODIFIERS_NOT_NEUTRAL")
        keys = {}
        for name in ("Alt_L", "F4"):
            present, entries = keymap.get_entries_for_keyval(self.gdk.keyval_from_name(name))
            need(present and 1 <= len(entries) <= 16, "CLOSE_KEY_NOT_IN_LIVE_KEYMAP")
            selected = {entry.keycode for entry in entries if entry.group == 0 and entry.level == 0}
            need(len(selected) == 1, "CLOSE_HARDWARE_KEY_AMBIGUOUS")
            keys[name] = selected.pop()
        self.identity()
        self.arm_action("window-close")
        self.journal.put("window-close-intent", {"binding": self.binding,
            "method": "AT_SPI_HARDWARE_ALT_F4", "automatic_retry": False})
        held = set()
        primary = None
        try:
            for name in ("Alt_L", "F4"):
                self.journal.put(("close-alt" if name == "Alt_L" else "close-f") + "-press-intent",
                                 {"hardware_keycode": keys[name]})
                held.add(name)  # A missing return is already an uncertain press.
                need(self.bridge.call(self.bridge.api.generate_keyboard_event,
                    keys[name], None, self.bridge.api.KeySynthType.PRESS) is True,
                    "WINDOW_CLOSE_KEY_PRESS_UNKNOWN")
        except BaseException as error:
            primary = error
        finally:
            for name in ("F4", "Alt_L"):
                if name in held:
                    try:
                        self.journal.put(("close-alt" if name == "Alt_L" else "close-f") + "-release-intent",
                                         {"hardware_keycode": keys[name]})
                    except BaseException as error:
                        if primary is None:
                            primary = error
                    try:
                        # Release an uncertain held key even when its journal
                        # write fails. This cannot earn a successful attempt.
                        need(self.bridge.api.generate_keyboard_event(
                            keys[name], None, self.bridge.api.KeySynthType.RELEASE) is True,
                            "WINDOW_CLOSE_KEY_RELEASE_UNKNOWN")
                    except BaseException as error:
                        if primary is None:
                            primary = error
        if primary is not None:
            raise primary
        self.journal.put("window-close-return", {"keyboard_dispatch_acknowledged": True,
            "writer_handoff_not_inferred": True, "process_exit_not_inferred": True})

    def run(self):
        project = self.before["project"]
        title, revision = project["title"], self.before["revision_before"]
        self.wait(lambda: self.control("Project library", {"dialog"}))
        self.wait(lambda: self.text(self.before["project_id"]))
        self.click("Open " + title, {"button"}, "open-project")
        opened = self.wait(lambda: self.text("r" + str(revision)))
        self.wait(lambda: self.text(title))
        self.click("Audio", {"button"}, "open-audio")
        self.click(SUMMARY, {"button", "toggle", "native_action"}, "open-original-mix")
        self.visible("Enable original mix recipe", {"checkbox"}, "enable-mix")
        self.wait(lambda: self.checked("Enable original mix recipe", False))
        self.click("Enable original mix recipe", {"checkbox"}, "enable-mix")
        self.wait(lambda: self.checked("Enable original mix recipe", True))
        profile = next(item for item in project["deliverables"]
                       if item["id"] == self.before["deliverable_profile_id"])
        combo = self.visible("Delivery language / profile", {"combo"}, "profile")
        container, _ = self.selection(combo)
        need(self.selected_name(container) == profile["name"] + " · " + profile["language"],
             "UI_DELIVERY_PROFILE_CHANGED")
        assets = {item["id"]: item for item in project["assets"]}
        mix = self.before["expected_change"]["recipe"]["original_mix"]
        labels = [assets[mix[key]]["name"] + " · sha256:" + assets[mix[key]]["content_sha256"][:10]
                  for key in ("music_asset_id", "sfx_asset_id")]
        selected = [
            self.choose_source("Original music asset", "Select imported WAV music",
                               labels[0], labels, "select-music"),
            self.choose_source("Original SFX asset", "No imported base; use authored cues in this cut",
                               labels[1], labels, "select-sfx"),
        ]
        draft_label = ("Draft source bindings: music " + mix["music_asset_id"]
                       + "; SFX " + mix["sfx_asset_id"] + ".")
        draft_sources = self.observe_source_status(draft_label)
        for label, expected in NUMBERS.items():
            entry = self.visible(label, {"entry", "text"}, "read-" + label.split()[0].lower())
            need("Text" in entry.interfaces and self.bridge.read_text(entry.node) == expected,
                 "UI_NUMERIC_RECIPE_CHANGED")
        self.click("Save original mix recipe", {"button"}, "save-recipe")
        saved = self.wait(lambda: self.text("Saved recipe at revision r" + str(revision + 1) + "."))
        self.wait(lambda: self.text("r" + str(revision + 1)))
        self.visible(RIGHTS, {"checkbox"}, "rights")
        self.wait(lambda: self.checked(RIGHTS, False))
        self.click(RIGHTS, {"checkbox"}, "confirm-fixture-rights")
        self.wait(lambda: self.checked(RIGHTS, True))
        audition_button = self.click(AUDITION, {"button"}, "original-audition")
        preview = self.wait(lambda: self.observe_original_pcm_near_owned_button(audition_button))
        # The saved r(N+1) is already independently witnessed before audition.
        # Following the new native media subtree must not trigger another
        # global application scan; external post-close readback verifies r(N+1).
        self.close_window(audition_button)
        return {"status": "PASS", "binding": self.binding, "project_id": self.before["project_id"],
                "generation": self.before["generation"], "revision_before": revision,
                "revision_after": revision + 1, "opened": opened, "sources": selected,
                "draft_source_bindings": draft_sources, "saved": saved, "preview": preview,
                "actions": self.events,
                "normal_close_requested": True, "playback_or_human_listening_claimed": False,
                "formal_G2": False, "formal_G3": False, "automatic_retry": False}

    def diagnostic(self):
        rows = [{"role": item.role, "name": item.name[:256], "states": sorted(item.states)}
                for item in self.last_views
                if item.name and item.role in {"button", "checkbox", "combo", "entry", "alert", "dialog"}]
        texts = [{"role": item.role, "name": item.name[:256],
                  "states": sorted(item.states), "interfaces": sorted(item.interfaces)}
                 for item in self.last_views if item.name and item.role in {
                     "static", "label", "heading", "status", "paragraph", "text", "readonly_text"}]
        return {"last_condition": self.last_condition, "actions": self.events,
                "action_observations": self.action_observations,
                "visible_controls": rows[:64], "truncated": len(rows) > 64,
                "text_observations": texts[:128], "text_truncated": len(texts) > 128,
                "role_counts": self.bridge.role_counts,
                "readonly_text_candidates": self.bridge.text_candidates,
                "disclosure_controls": [
                    {"role": item.role, "name": item.name[:256], "states": sorted(item.states),
                     "interfaces": sorted(item.interfaces)}
                    for item in self.last_views if item.name and item.role in {"toggle", "native_action"}
                ][:32]}

def validate_before(before, context, root, database, original_sources):
    need(before.get("schema") == "motionwright.tauri-audio-fixture-before/1"
         and before.get("private_root") == str(root) and before.get("database_path") == str(database),
         "FIXTURE_BEFORE_PATH_OR_SCHEMA")
    for field in ("project_id", "generation", "deliverable_profile_id"):
        value = before.get(field)
        need(type(value) is str and str(uuid.UUID(value)) == value and uuid.UUID(value).int != 0,
             "FIXTURE_IDENTITY_INVALID")
    revision = before.get("revision_before")
    need(type(revision) is int and 0 <= revision < 64 and before.get("revision") == revision
         and type(before.get("revision")) is int and before.get("expected_revision") == revision + 1
         and type(before.get("expected_revision")) is int, "FIXTURE_REVISION_INVALID")
    project = before["project"]
    need(project["id"] == before["project_id"] and project["generation"] == before["generation"]
         and project["revision"] == revision
         and project["production_design"].get("delivery_recipes", {}) == {}
         and len(before["history"]) == revision, "FIXTURE_PREPARATION_ALREADY_SAVED")
    change = before["expected_change"]
    need(set(change) == {"type", "deliverable_profile_id", "recipe"}
         and change["type"] == "set_delivery_recipe"
         and change["deliverable_profile_id"] == before["deliverable_profile_id"],
         "FIXTURE_EXPECTED_CHANGE")
    mix = change["recipe"]["original_mix"]
    for key, expected in (("sfx_gain_db", -8), ("duck_attenuation_db", 14),
                          ("duck_attack_samples", 2400), ("duck_release_samples", 4800)):
        need(type(mix.get(key)) in (int, float) and mix[key] == expected,
             "FIXTURE_EXPECTED_MIX_PARAMETERS")
    need(len(project["assets"]) == 3 and len(before["assets"]) == 3
         and {item["bus"] for item in before["assets"]} == {"voice", "music", "sfx"},
         "FIXTURE_SOURCE_COUNT")
    for item in before["assets"]:
        wanted = original_sources["saved-mix-" + item["bus"] + ".wav"]
        need(item["source"]["path"] == wanted["path"]
             and item["source"]["sha256"] == wanted["sha256"]
             and item["source"]["bytes"] == pcm.WAV_BYTES
             and item["cas"]["sha256"] == wanted["sha256"], "FIXTURE_SOURCE_BYTES_CHANGED")
    need(before.get("preparation_tauri_owner_invocation") == "NOT_EXERCISED"
         and before.get("expected_save_authority") == "REAL_TAURI_UI_NOT_ATTESTED_BY_FIXTURE"
         and before.get("writer_handoff_verified") is True
         and before.get("source_classification") == "SYNTHETIC_TONES_NOT_SPEECH",
         "FIXTURE_SCOPE_INVALID")
    for flag in ("formal_G2", "formal_G3", "human_approval", "release_authorized", "automatic_retry"):
        need(before.get(flag) is False, "FIXTURE_CANNOT_GRANT_ACCEPTANCE")


def private_environment(root):
    profile = root / "profile"
    profile.mkdir(mode=0o700)
    env = dict(os.environ)
    for key, name in (("HOME", "home"), ("XDG_CONFIG_HOME", "config"),
                      ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache")):
        path = profile / name
        path.mkdir(mode=0o700)
        env[key] = str(path)
    # Keep GTK's additional peer socket suffixes within AF_UNIX's path bound.
    # These short directories remain exclusive children of the same private
    # owner. The actual Tauri data directory and SQLite/CAS paths are unchanged.
    for key, path in private_transport_paths(root).items():
        path.mkdir(mode=0o700)
        env[key] = str(path)
    env.update(TMP=env["TMPDIR"], TEMP=env["TMPDIR"], GDK_BACKEND="x11",
               GTK_MODULES="atk-bridge", NO_AT_BRIDGE="0", PYTHONDONTWRITEBYTECODE="1")
    env.pop("MOTIONWRIGHT_SEMWRIGHT_CONNECTION", None)
    env.pop("MOTIONWRIGHT_DEVELOPMENT_AUDITION_MANIFEST", None)
    return env


def resource_limit():
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))


def bounded_resources(root):
    total, count = 0, 0
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            count += 1
            need(count <= 4096, "PRIVATE_NAMESPACE_ENTRY_BOUND")
            try:
                meta = (Path(parent) / name).lstat()
            except FileNotFoundError:
                continue  # A vanished transport entry cannot consume bytes now.
            need(meta.st_uid == os.getuid(), "PRIVATE_NAMESPACE_OWNER")
            # GTK/DBus transport links and sockets are metadata-only here. Never
            # follow or remove them; every authoritative original uses the
            # stricter regular-file + namespace reader below.
            need(stat.S_ISREG(meta.st_mode) or stat.S_ISDIR(meta.st_mode)
                 or stat.S_ISLNK(meta.st_mode) or stat.S_ISSOCK(meta.st_mode)
                 or stat.S_ISFIFO(meta.st_mode), "PRIVATE_NAMESPACE_SPECIAL_DEVICE")
            if stat.S_ISREG(meta.st_mode):
                total += meta.st_size
                need(meta.st_size <= 16 * 1024 * 1024 and total <= MAX_PRIVATE,
                     "PRIVATE_ORIGINAL_BYTE_BOUND")
    tree = tree_snapshot(os.getpid())
    for _ in range(3):
        if tree["root_observed"] and not tree["truncated"]:
            break
        time.sleep(0.02)  # Read-only resampling after a thread/process exits.
        tree = tree_snapshot(os.getpid())
    need(tree["root_observed"] and not tree["truncated"], "OWNED_PROCESS_TREE_INCOMPLETE")
    resident = sum(row["rss_kib"] or 0 for row in tree["processes"])
    need(resident <= MAX_RSS_KIB, "OWNED_PROCESS_MEMORY_BOUND")
    return {"private_bytes": total, "private_entries": count, "tree_rss_kib": resident}


def run_command(argv, label, root, env, timeout, *, poll=None):
    directory = root / "commands" / label
    directory.mkdir(mode=0o700)
    publish(directory / "intent.original.json", {"argv": list(map(str, argv)),
            "timeout_seconds": timeout, "automatic_retry": False}, root)
    result = {"status": "UNKNOWN", "exit_code": None, "automatic_retry": False}
    start = time.monotonic()
    process, primary = None, None
    try:
        with (directory / "stdout.original").open("xb") as out, (directory / "stderr.original").open("xb") as err:
            process = subprocess.Popen(list(map(str, argv)), cwd=ROOT, env=env,
                stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True,
                preexec_fn=resource_limit)
            result["pid"] = process.pid
            while process.poll() is None:
                need(time.monotonic() - start < timeout, "ORIGINAL_COMMAND_DEADLINE")
                bounded_resources(root)
                need(out.tell() <= MAX_STREAM and err.tell() <= MAX_STREAM, "ORIGINAL_COMMAND_STREAM_BOUND")
                if poll is not None:
                    poll()
                time.sleep(0.1)
            result.update(status="DIRECT_PROCESS_EXIT_OBSERVED", exit_code=process.returncode)
        need(process.returncode == 0, "ORIGINAL_COMMAND_FAILED")
    except BaseException as error:
        primary = error
        result.update(failure_type=type(error).__name__, failure_code=code(error))
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - start, 3)
        if process is not None and process.poll() is not None:
            result["exit_code"] = process.returncode
        try:
            for name in ("stdout", "stderr"):
                path = directory / (name + ".original")
                if path.is_file():
                    result[name] = source.regular_file(path, MAX_STREAM)
            publish(directory / "completion.original.json", result, root)
        except BaseException as error:
            if primary is None:
                primary = error
    if primary is not None:
        raise primary
    return directory


def empty_natural_tree(root, seconds):
    samples, reaped = [], []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        observed = tree_snapshot(os.getpid())
        need(observed["root_observed"], "CLOSURE_ROOT_NOT_OBSERVED")
        samples.append(observed)
        if len(samples) > 8:
            samples.pop(0)
        if not observed["truncated"] and len(observed["processes"]) == 1:
            return {"status": "PASS", "supervisor_pid": os.getpid(),
                    "complete_empty_descendant_tree": observed, "signals": [],
                    "reaped": reaped, "samples": samples,
                    "authority": "process_custody_only_not_revocation"}
        for child in observed["processes"]:
            if child["ppid"] == os.getpid() and child["state"] == "Z":
                try:
                    pid, status = os.waitpid(child["pid"], os.WNOHANG)
                    if pid:
                        reaped.append({"pid": pid, "wait_status": status})
                except ChildProcessError:
                    pass
        bounded_resources(root)
        time.sleep(0.1)
    raise AudioError("NORMAL_WINDOW_CLOSE_DID_NOT_CLOSE_DESCENDANTS")


def worker(args):
    root = args.directory
    context = admission(args.repo)
    need(root == Path(context["private_root"]) and math.isfinite(args.deadline)
         and 0 < args.deadline - time.monotonic() <= 125, "UI_WORKER_NAMESPACE_DEADLINE")
    request, _ = read_json(root / "ui" / "intent.original.json", 16384, root)
    need(type(request.get("supervisor_pid")) is int and os.getppid() == request["supervisor_pid"],
         "UI_WORKER_PARENT_CHANGED")
    before, identity = read_json(root / "evidence" / "tauri-audio-before.private.json", MAX_JSON, root)
    need(identity["sha256"] == request["before_receipt_sha256"], "UI_WORKER_SOURCE_WITNESS_CHANGED")
    result = {"status": "FAIL", "binding": request["binding"], "formal_G2": False,
              "formal_G3": False, "automatic_retry": False}
    journal = accessible.DiskJournal(root / "ui" / "journal")
    attempt = None
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Atspi, Gdk, GLib
        need(Atspi.init() in (0, 1), "ATSPI_INIT_FAILED")
        Atspi.set_timeout(800, 2500)
        bridge = AudioBridge(Atspi, GLib, args.deadline)
        attempt = AudioUI(bridge, Gdk, request["binding"], before, journal, args.deadline)
        result = attempt.run()
    except BaseException as error:
        result.update(failure_type=type(error).__name__, failure_code=code(error),
                      failure_traceback="".join(traceback.format_exception(error))[-8192:])
        if attempt is not None:
            result["diagnostic"] = attempt.diagnostic()
    finally:
        journal.close()
        publish(root / "ui" / "observation.original.json", result, root)
    return 0 if result["status"] == "PASS" else 1


REQUIRED_ACTIONS = (
    "open-project", "open-audio", "open-original-mix", "enable-mix",
    "select-music", "select-sfx", "save-recipe", "confirm-fixture-rights",
    "original-audition", "close-focus", "window-close",
)
SCROLL_ACTIONS = frozenset(name + "-scroll" for name in (
    "open-project", "open-audio", "open-original-mix", "enable-mix", "profile",
    "select-music", "select-sfx", "read-sfx", "read-music", "read-attack",
    "read-release", "source-draft-status", "save-recipe", "rights", "confirm-fixture-rights",
    "original-audition", "preview-result", "close-focus",
))


def equal_json(left, right):
    """Type-sensitive equality: bools never stand in for numeric identities."""
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(equal_json(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(equal_json(a, b) for a, b in zip(left, right))
    return left == right


def validate_ui_observation(ui, before, binding):
    need(ui.get("status") == "PASS" and equal_json(ui.get("binding"), binding)
         and ui.get("project_id") == before["project_id"]
         and ui.get("generation") == before["generation"]
         and type(ui.get("revision_before")) is int
         and type(ui.get("revision_after")) is int
         and ui["revision_before"] == before["revision_before"]
         and ui["revision_after"] == before["revision_before"] + 1
         and ui.get("normal_close_requested") is True, "REAL_UI_RESPONSE_NOT_OBSERVED")
    mix = before["expected_change"]["recipe"]["original_mix"]
    need(type(mix.get("music_asset_id")) is str and type(mix.get("sfx_asset_id")) is str,
         "FIXTURE_SOURCE_BINDINGS_REQUIRED")
    for field, expected in (
        ("opened", "r" + str(before["revision_before"])),
        ("draft_source_bindings", "Draft source bindings: music " + mix["music_asset_id"]
         + "; SFX " + mix["sfx_asset_id"] + "."),
        ("saved", "Saved recipe at revision r" + str(before["revision_before"] + 1) + "."),
        ("preview", PREVIEW),
    ):
        item = ui.get(field, {})
        need(type(item) is dict and item.get("text") == expected
             and type(item.get("matches")) is int and 1 <= item["matches"] <= 8,
             "UI_TEXT_OBSERVATION_MISSING")
    actions = ui.get("actions")
    need(type(actions) is list and all(type(item) is str for item in actions)
         and len(actions) == len(set(actions))
         and tuple(item for item in actions if item not in SCROLL_ACTIONS) == REQUIRED_ACTIONS,
         "UI_ACTION_ORDER_OR_ONCE")
    for field in ("playback_or_human_listening_claimed", "formal_G2", "formal_G3", "automatic_retry"):
        need(ui.get(field) is False, "UI_CANNOT_GRANT_ACCEPTANCE")


def validate_native_records(records, context, before, before_identity, manifest_identity, binding, ui):
    schemas = {
        "startup.original.json": "motionwright.development-audition-startup/1",
        "intent.original.json": "motionwright.development-audition-intent/1",
        "prepared-response.original.json": "motionwright.development-audition-prepared-response/1",
        "writer-handoff-intent.original.json": "motionwright.development-audition-writer-handoff-intent/1",
        "writer-handoff.original.json": "motionwright.development-audition-writer-handoff/1",
    }
    need(type(records) is dict and set(records) == set(schemas), "NATIVE_JSON_ROSTER")
    prepared = records["prepared-response.original.json"]
    shared = {
        "source_sha": context["source_sha"], "sdk_sha": context["sdk_sha"],
        "run_id": context["run_id"], "run_attempt": "1",
        "project_id": before["project_id"], "generation": before["generation"],
        "deliverable_profile_id": before["deliverable_profile_id"],
        "desktop_pid": binding["pid"], "database_path": before["database_path"],
        "database_identity": before["database_identity"],
        "cas_path": before["cas_root"]["path"], "cas_identity": before["cas_root"]["identity"],
        "manifest_sha256": manifest_identity["sha256"],
        "before_receipt_sha256": before_identity["sha256"],
    }
    session = prepared.get("workspace_session")
    need(type(session) is str and str(uuid.UUID(session)) == session and uuid.UUID(session).int != 0,
         "NATIVE_WORKSPACE_SESSION_INVALID")
    for name, record in records.items():
        # Startup has not saved or invoked the owner. The four later receipts
        # describe the one expected saved revision and one original audition.
        revision = before["revision_before"] + (name != "startup.original.json")
        need(record.get("schema") == schemas[name] and record.get("workspace_session") == session
             and all(equal_json(record.get(key), value) for key, value in shared.items()),
             "NATIVE_RECEIPT_IDENTITY_CHANGED")
        need(type(record.get("revision")) is int and record["revision"] == revision
             and type(record.get("desktop_pid")) is int, "NATIVE_RECEIPT_REVISION_OR_TYPE")
        for field in ("formal_G2", "formal_G3", "human_approval", "release_authorized", "automatic_retry"):
            need(record.get(field) is False, "NATIVE_RECEIPT_AUTHORITY_ESCALATION")
        need(record.get("source_classification") == "SYNTHETIC_TONES_NOT_SPEECH",
             "NATIVE_SOURCE_CLASSIFICATION_CHANGED")
    need(records["startup.original.json"].get("native_effect_or_owner_granted") is False,
         "STARTUP_CANNOT_GRANT_OWNER")
    need(prepared.get("prepared_for_original_tauri_response") is True
         and prepared.get("ipc_delivery_observed") is False
         and prepared.get("ui_observation_required") is True
         and equal_json(prepared.get("saved_recipe"), before["expected_change"]["recipe"]),
         "NATIVE_PREPARED_RESPONSE_CONTRACT")
    handoff = records["writer-handoff.original.json"]
    need(handoff.get("writer_handoff_prepared") is True
         and handoff.get("writer_process_closed") is False
         and handoff.get("closure_requires_independent_process_observation") is True,
         "NATIVE_WRITER_HANDOFF_UNCONFIRMED")
    validate_ui_observation(ui, before, binding)
    return {**shared, "revision": before["revision_before"] + 1}, session


def validate_saved_readback(value, before, before_identity):
    need(value.get("schema") == "motionwright.tauri-audio-independent-readback/1"
         and value.get("reader_authority") == "StudioServiceReader"
         and value.get("source_witness_sha256") == before_identity["sha256"],
         "INDEPENDENT_READER_SOURCE_BINDING")
    for field, expected in (
        ("project_id", before["project_id"]), ("generation", before["generation"]),
        ("previous_revision", before["revision_before"]), ("revision", before["revision_before"] + 1),
        ("deliverable_profile_id", before["deliverable_profile_id"]),
        ("expected_change", before["expected_change"]), ("database_path", before["database_path"]),
        ("database_identity", before["database_identity"]), ("cas_root", before["cas_root"]),
        ("assets", before["assets"]), ("additional_journal_events", 0),
        ("verified_original_assets", 3), ("verified_original_bytes", 3 * pcm.WAV_BYTES),
    ):
        need(equal_json(value.get(field), expected), "INDEPENDENT_READER_IDENTITY_OR_EVENT")
    for field in ("writer_handoff_verified", "same_physical_database", "same_physical_cas_root",
                  "exactly_one_recipe_save"):
        need(value.get(field) is True, "INDEPENDENT_READER_PHYSICAL_JOIN")
    need(value.get("source_classification") == "SYNTHETIC_TONES_NOT_SPEECH"
         and value.get("tauri_owner_invocation") == "REQUIRES_EXTERNAL_UI_AND_CAPTURE_JOIN",
         "INDEPENDENT_READER_SCOPE")
    for field in ("mixed_pcm_read_or_generated_by_reader", "human_approval", "release_authorized",
                  "formal_G2", "formal_G3", "automatic_retry"):
        need(value.get(field) is False, "INDEPENDENT_READER_AUTHORITY")
    history, project = value["observed_history"], value["observed_project"]
    need(type(history) is list and len(history) == len(before["history"]) + 1
         and equal_json(history[:-1], before["history"])
         and equal_json(history[-1].get("change"), before["expected_change"])
         and type(history[-1].get("revision")) is int
         and history[-1]["revision"] == before["revision_before"] + 1
         and project.get("id") == before["project_id"] and project.get("generation") == before["generation"]
         and type(project.get("revision")) is int and project["revision"] == before["revision_before"] + 1,
         "INDEPENDENT_READER_JOURNAL_JOIN")
    recipes = project["production_design"]["delivery_recipes"]
    need(equal_json(recipes, {before["deliverable_profile_id"]: before["expected_change"]["recipe"]}),
         "INDEPENDENT_READER_SAVED_RECIPE")


def validate_process_closure(value, supervisor_pid, *, natural):
    need(type(supervisor_pid) is int and supervisor_pid > 0
         and value.get("status") == "PASS"
         and type(value.get("supervisor_pid")) is int
         and value["supervisor_pid"] == supervisor_pid, "PROCESS_CLOSURE_SUPERVISOR")
    tree = value.get("complete_empty_descendant_tree", {})
    rows = tree.get("processes")
    need(tree.get("root_observed") is True and tree.get("truncated") is False
         and type(rows) is list and len(rows) == 1 and type(rows[0].get("pid")) is int
         and rows[0]["pid"] == supervisor_pid, "PROCESS_CLOSURE_NOT_EMPTY")
    need(type(value.get("signals")) is list and (not natural or value["signals"] == []),
         "CLEANUP_SIGNALS_CANNOT_EARN_SUCCESS")


def journal_readback(root, ui, binding):
    directory = root / "ui" / "journal"
    names = {label + suffix + ".json" for label in ui["actions"] for suffix in ("-intent", "-return")}
    names.update(name + ".json" for name in (
        "close-alt-press-intent", "close-alt-release-intent",
        "close-f-press-intent", "close-f-release-intent"))
    key_plan = source_key_journal_plan()
    for label, index, _ in key_plan:
        for phase in ("intent", "return"):
            names.add(source_key_receipt_name(label, index, phase) + ".json")
    need({path.name for path in directory.iterdir()} == names, "UI_ORIGINAL_JOURNAL_ROSTER")
    witnesses = {}
    for label in ui["actions"]:
        intent, witnesses[label + "-intent"] = read_json(directory / (label + "-intent.json"), 16384, root)
        returned, witnesses[label + "-return"] = read_json(directory / (label + "-return.json"), 16384, root)
        need(equal_json(intent.get("binding"), binding) and intent.get("automatic_retry") is False,
             "UI_ORIGINAL_JOURNAL_BINDING")
        if label == "window-close":
            need(intent.get("method") == "AT_SPI_HARDWARE_ALT_F4"
                 and returned.get("keyboard_dispatch_acknowledged") is True
                 and returned.get("writer_handoff_not_inferred") is True
                 and returned.get("process_exit_not_inferred") is True, "UI_WINDOW_CLOSE_JOURNAL")
        else:
            need(returned.get("accepted") is True, "UI_ORIGINAL_ACTION_UNACKNOWLEDGED")
    key_codes = {}
    for label, index, key in key_plan:
        intent_name = source_key_receipt_name(label, index, "intent")
        returned_name = source_key_receipt_name(label, index, "return")
        intent, witnesses[intent_name] = read_json(
            directory / (intent_name + ".json"), 16384, root)
        returned, witnesses[returned_name] = read_json(
            directory / (returned_name + ".json"), 16384, root)
        number = intent.get("hardware_keycode")
        need(set(intent) == {"key", "hardware_keycode", "automatic_retry"}
             and intent["key"] == key and type(number) is int and 8 <= number <= 255
             and intent["automatic_retry"] is False
             and returned == {"hardware_press_and_release_acknowledged": True},
             "UI_SOURCE_HARDWARE_KEY_JOURNAL")
        prior = key_codes.setdefault(label, {})
        if key in prior:
            need(prior[key] == number, "UI_SOURCE_REUSED_KEYCODE_CHANGED")
        else:
            need(number not in prior.values(), "UI_SOURCE_DISTINCT_KEYCODE_REQUIRED")
            prior[key] = number
    codes = []
    for key in ("alt", "f"):
        values = []
        for action in ("press", "release"):
            name = "close-" + key + "-" + action + "-intent"
            value, witnesses[name] = read_json(directory / (name + ".json"), 16384, root)
            number = value.get("hardware_keycode")
            need(type(number) is int and 8 <= number <= 255, "UI_CLOSE_KEYCODE")
            values.append(number)
        need(values[0] == values[1], "UI_CLOSE_RELEASE_KEY_CHANGED")
        codes.append(values[0])
    need(codes[0] != codes[1], "UI_CLOSE_KEYS_NOT_DISTINCT")
    return witnesses


def validate_pcm_projection(prepared, before, proof):
    plan, evidence = prepared["plan"], prepared["evidence"]
    profile = next(item for item in before["project"]["deliverables"]
                   if item["id"] == before["deliverable_profile_id"])
    for key, expected in (("project_id", before["project_id"]), ("generation", before["generation"]),
                          ("project_revision", before["revision_before"] + 1),
                          ("deliverable_profile_id", before["deliverable_profile_id"]),
                          ("language", profile["language"])):
        need(equal_json(plan.get(key), expected), "NATIVE_PLAN_PROJECT_BINDING")
    assets = {item["bus"]: item["asset_id"] for item in before["assets"]}
    for bus, key in (("voice", "measured_voice"), ("music", "original_music"), ("sfx", "original_sfx")):
        record = plan[key]
        need(record.get("asset_id") == assets[bus]
             and record.get("source_asset_sha256") == proof["source_wav_sha256"][bus]
             and record.get("decoded_pcm_sha256") == proof["source_pcm_sha256"][bus]
             and type(record.get("sample_frames")) is int and record["sample_frames"] == pcm.FRAMES,
             "NATIVE_PLAN_SOURCE_BYTES_CHANGED")
    for key, expected in (("sample_frames", 96000), ("sample_rate", 48000), ("channels", 2),
                          ("voice_gain_db", 0), ("music_gain_db", -12), ("sfx_gain_db", -8),
                          ("duck_attenuation_db", 14), ("duck_attack_samples", 2400),
                          ("duck_release_samples", 4800)):
        need(type(plan.get(key)) in (int, float) and plan[key] == expected, "NATIVE_PLAN_CLOCK_OR_GAIN")
    need(equal_json(plan.get("voice_windows"), [{"start_sample": 24000, "end_exclusive": 48000}])
         and plan.get("owner_mixing_approval_authenticated") is False and plan.get("is_mastered") is False,
         "NATIVE_PLAN_SCOPE")
    for field in ("owner_release_approved", "listening_intelligibility_approved",
                  "independently_verified_lufs", "independently_verified_true_peak"):
        need(evidence.get(field) is False, "PCM_ORACLE_CANNOT_GRANT_HUMAN_ACCEPTANCE")
    for field, expected in (("rendered_pcm_sha256", proof["mixed_pcm_sha256"]),
                            ("language", profile["language"]), ("sample_rate", 48000),
                            ("channels", 2), ("frames", pcm.FRAMES),
                            ("input_source_checks", "ALL_THREE_FULL_PCM_SHA256_VERIFIED"),
                            ("rendered_media", "RAW_PCM_S16LE_ONLY")):
        need(equal_json(evidence.get(field), expected), "NATIVE_EVIDENCE_PCM_BINDING")
    need(SHA256.fullmatch(prepared.get("audio_inputs_sha256", ""))
         and SHA256.fullmatch(evidence.get("source_plan_sha256", "")), "NATIVE_AUDIO_INPUTS_DIGEST")



def capture_readback(root, context, before, before_identity, manifest_identity, binding, ui):
    directory = root / "evidence" / "native-audition"
    expected_names = {"startup.original.json", "intent.original.json", "audition.original.wav",
        "prepared-response.original.json", "writer-handoff-intent.original.json",
        "writer-handoff.original.json"}
    need({path.name for path in directory.iterdir()} == expected_names, "NATIVE_ORIGINAL_ROSTER")
    records, witnesses = {}, {}
    for name in sorted(expected_names - {"audition.original.wav"}):
        records[name], witnesses[name] = read_json(directory / name, MAX_JSON, root)
    prepared = records["prepared-response.original.json"]
    shared, session = validate_native_records(
        records, context, before, before_identity, manifest_identity, binding, ui)
    journal = journal_readback(root, ui, binding)
    files = {}
    for bus in pcm.BUSES:
        name = "saved-mix-" + bus + ".wav"
        files[name], witnesses[name] = read_original(root / "sources" / name, pcm.WAV_BYTES, root)
    files["saved-mix.wav"], wav_identity = read_original(directory / "audition.original.wav", 3 * 1024 * 1024, root)
    wav = prepared["wav"]
    need(wav.get("name") == "audition.original.wav" and type(wav.get("bytes")) is int
         and wav["bytes"] == wav_identity["bytes"] and wav.get("sha256") == wav_identity["sha256"]
         and equal_json(wav.get("identity"), wav_identity["identity"]) and type(wav.get("links")) is int
         and wav["links"] == 1, "CAPTURED_ORIGINAL_WAV_BINDING")
    proof = pcm.inspect_pcm_files(files)
    validate_pcm_projection(prepared, before, proof)
    witnesses["audition.original.wav"] = wav_identity
    return {"schema": "motionwright.tauri-audio-original-pcm-observation/1",
            "native_identity": shared, "workspace_session": session,
            "originals": witnesses, "ui_journal_originals": journal,
            "pcm": proof, "ui_response_observed": True,
            "normal_writer_handoff_observed": True, "human_listening_claimed": False,
            "formal_G2": False, "formal_G3": False, "automatic_retry": False}


def frontend_observation(repo):
    directory = repo / "apps" / "studio" / "dist"
    namespace(directory)
    observed, total = {}, 0
    for parent, dirs, files in os.walk(directory, followlinks=False):
        need(len(observed) + len(dirs) + len(files) <= 256, "FRONTEND_MEMBER_BOUND")
        for name in dirs:
            namespace(Path(parent) / name)
        for name in sorted(files):
            path = Path(parent) / name
            witness = source.regular_file(path, 16 * 1024 * 1024)
            total += witness["bytes"]
            need(total <= 32 * 1024 * 1024, "FRONTEND_BYTE_BOUND")
            observed[path.relative_to(directory).as_posix()] = witness
    need("index.html" in observed and any(name.endswith(".js") for name in observed),
         "REAL_BUILT_FRONTEND_REQUIRED")
    return {"path": str(directory), "files": observed, "bytes": total,
            "authority": "observed build input; source-pinned workflow owns compilation"}


def wait_window(process, root, deadline):
    while time.monotonic() < deadline:
        need(process.poll() is None, "DESKTOP_EXITED_BEFORE_UI")
        bounded_resources(root)
        try:
            binding = accessible.x11_identity(deadline=deadline)
            need(binding["pid"] == process.pid, "WINDOW_IS_NOT_DIRECT_DESKTOP_CHILD")
            return binding
        except accessible.NotReady:
            time.sleep(0.1)
    raise AudioError("REAL_DESKTOP_WINDOW_DEADLINE")


def unchanged_original(path, witness, root, maximum=MAX_JSON):
    _, current = read_original(path, maximum, root)
    need(equal_json(current, witness), "ORIGINAL_RECEIPT_CHANGED")


def compact_closure(value):
    result = dict(value)
    samples = value.get("samples", [])
    result.update(supervisor_pid=os.getpid(), sample_count=len(samples),
                  samples=samples[-8:], earlier_samples_omitted=max(0, len(samples) - 8),
                  automatic_retry=False, formal_G2=False, formal_G3=False)
    return result


def scenario(args):
    context = admission(args.repo)
    own_source = source.source_observation(args.repo, context["source_sha"], SOURCE_PATHS)
    build = admitted_build(args, context)
    frontend = frontend_observation(args.repo)
    root = Path(context["private_root"])
    need(not root.is_relative_to(args.repo), "PRIVATE_ROOT_INSIDE_CHECKOUT")
    namespace(root.parent)
    need(not root.exists() and not root.is_symlink(), "ORIGINAL_ATTEMPT_EXISTS_NO_RETRY")
    os.umask(0o077)
    root.mkdir(mode=0o700)
    root_namespace = namespace(root, root)
    result = {"schema": SCHEMA, **context, "status": "FAIL", "supervisor_pid": os.getpid(),
              "writer_process_closed": False, "normal_writer_close_observed": False,
              "ui_observed": False, "independent_readback": False, "original_wav_verified": False,
              "human_listening_claimed": False, "formal_G2": False, "formal_G3": False,
              "human_approval": False, "release_authorized": False, "automatic_retry": False}
    desktop, streams, directory, closure, primary = None, [], None, None, None
    intake = {"schema": "motionwright.tauri-audio-source-intake/1", **context,
              "motionwright": own_source, "build": build, "frontend": frontend,
              "formal_G2": False, "formal_G3": False}
    publish(root / "source-intake.original.json", intake, root)
    with processes.supervise_own_processes():
        try:
            (root / "commands").mkdir(mode=0o700)
            (root / "ui").mkdir(mode=0o700)
            (root / "ui" / "journal").mkdir(mode=0o700)
            env = private_environment(root)
            (root / "sources").mkdir(mode=0o700)
            pcm.write_stems(root / "sources")  # Existing exact generator, one execution.
            generated = {}
            for bus in pcm.BUSES:
                name = "saved-mix-" + bus + ".wav"
                _, generated[name] = read_original(root / "sources" / name, pcm.WAV_BYTES, root)
            publish(root / "sources.original.json", {
                "schema": "motionwright.tauri-audio-synthetic-originals/1",
                "generator": "tooling/native_saved_mix_av.py::write_stems",
                "sources": generated, "source_classification": "SYNTHETIC_TONES_NOT_SPEECH",
                "automatic_retry": False, "formal_G2": False, "formal_G3": False}, root)
            database = root / "profile" / "data" / "com.seradotcom.motionwright" / "motionwright.sqlite3"
            seed_dir = run_command([args.fixture, "seed-tauri-audio", database, root],
                                   "seed", root, env, 40)
            seed_closure = empty_natural_tree(root, 10)
            validate_process_closure(seed_closure, os.getpid(), natural=True)
            publish(root / "seed-process-closure.original.json", seed_closure, root)
            before_path = root / "evidence" / "tauri-audio-before.private.json"
            before, before_identity = read_json(before_path, MAX_JSON, root)
            validate_before(before, context, root, database, generated)
            seed, seed_identity = read_json(seed_dir / "stdout.original", MAX_JSON, root)
            need(seed.get("schema") == "motionwright.tauri-audio-fixture-prepared/1"
                 and seed.get("before_witness", {}).get("sha256") == before_identity["sha256"]
                 and seed.get("writer_closed") is True
                 and equal_json(seed.get("expected_change"), before["expected_change"]),
                 "ORIGINAL_SEED_RESPONSE_BINDING")
            manifest_path = root / "evidence" / "development-audition-admission.json"
            manifest = {"schema": "motionwright.development-tauri-audition-admission/1",
                **{key: context[key] for key in ("source_sha", "sdk_sha", "run_id", "run_attempt")},
                "private_root": str(root), "before_receipt_sha256": before_identity["sha256"],
                **{key: before[key] for key in
                   ("project_id", "generation", "revision_before", "deliverable_profile_id")}}
            manifest_identity = publish(manifest_path, manifest, root)
            # Only the real desktop child receives custody admission. It grants
            # no IPC, owner, approval or substitute for the normal app lifecycle.
            desktop_env = dict(env, MOTIONWRIGHT_DEVELOPMENT_AUDITION_MANIFEST=str(manifest_path))
            directory = root / "commands" / "desktop"
            directory.mkdir(mode=0o700)
            publish(directory / "intent.original.json", {
                "argv": [str(args.desktop)], "database_path": str(database),
                "profile": {key: desktop_env[key] for key in
                            ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                             "XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP")},
                "manifest": manifest_identity, "automatic_retry": False}, root)
            streams = [(directory / (name + ".original")).open("xb") for name in ("stdout", "stderr")]
            desktop = subprocess.Popen([str(args.desktop)], cwd=args.repo, env=desktop_env,
                stdin=subprocess.DEVNULL, stdout=streams[0], stderr=streams[1],
                start_new_session=True, preexec_fn=resource_limit)
            deadline = time.monotonic() + args.seconds
            binding = wait_window(desktop, root, deadline)
            executable = processes.executing_desktop(binding, args.desktop)
            need({key: executable[key] for key in ("bytes", "sha256")} ==
                 {key: build["artifacts"]["motionwright-desktop"][key] for key in ("bytes", "sha256")},
                 "RUNNING_DESKTOP_NOT_ORIGINAL_BUILD")
            publish(root / "ui" / "intent.original.json", {
                "schema": "motionwright.tauri-audio-accessible-intent/1",
                "supervisor_pid": os.getpid(), "binding": binding,
                "executing_original_binary": executable,
                "before_receipt_sha256": before_identity["sha256"],
                "deadline_monotonic": deadline, "automatic_retry": False}, root)
            remaining = deadline - time.monotonic()
            need(0 < remaining <= 120, "UI_DEADLINE_BEFORE_WORKER")
            run_command([sys.executable, "-B", "-E", "-s", Path(__file__).resolve(),
                         "--repo", args.repo, "--ui-worker", "--directory", root,
                         "--deadline", str(deadline)], "accessible-ui", root, env, remaining + 3)
            ui, ui_identity = read_json(root / "ui" / "observation.original.json", MAX_JSON, root)
            validate_ui_observation(ui, before, binding)
            result["ui_observed"] = True
            # A successful key dispatch is insufficient: observe the real app
            # exit and the complete descendant tree before opening any Store.
            desktop.wait(timeout=15)
            need(desktop.returncode == 0, "NORMAL_DESKTOP_EXIT_FAILED")
            natural = empty_natural_tree(root, 15)
            validate_process_closure(natural, os.getpid(), natural=True)
            publish(root / "writer-process-closure.original.json", natural, root)
            result.update(writer_process_closed=True, normal_writer_close_observed=True,
                          desktop_pid=desktop.pid, desktop_exit_code=desktop.returncode)
            original_pcm = capture_readback(
                root, context, before, before_identity, manifest_identity, binding, ui)
            pcm_identity = publish(root / "original-pcm-observation.private.json", original_pcm, root)
            result["original_wav_verified"] = True
            reader_dir = run_command([args.fixture, "tauri-audio-readback", database, root],
                                     "independent-reader", root, env, 40)
            readback_path = root / "evidence" / "tauri-audio-readback.private.json"
            readback, readback_identity = read_json(readback_path, MAX_JSON, root)
            validate_saved_readback(readback, before, before_identity)
            reader, reader_identity = read_json(reader_dir / "stdout.original", MAX_JSON, root)
            need(reader.get("schema") == "motionwright.tauri-audio-readback-receipt/1"
                 and reader.get("readback_witness", {}).get("sha256") == readback_identity["sha256"]
                 and reader.get("exactly_one_recipe_save") is True
                 and type(reader.get("additional_journal_events")) is int
                 and reader["additional_journal_events"] == 0, "ORIGINAL_READER_RESPONSE_BINDING")
            closure = empty_natural_tree(root, 10)
            validate_process_closure(closure, os.getpid(), natural=True)
            result["independent_readback"] = True
            unchanged_original(before_path, before_identity, root)
            unchanged_original(manifest_path, manifest_identity, root)
            unchanged_original(root / "ui" / "observation.original.json", ui_identity, root)
            unchanged_original(readback_path, readback_identity, root)
            for name, witness in generated.items():
                unchanged_original(root / "sources" / name, witness, root, pcm.WAV_BYTES)
            need(source.source_observation(args.repo, context["source_sha"], SOURCE_PATHS) == own_source,
                 "MOTIONWRIGHT_SOURCE_CHANGED")
            need(source.source_observation(Path(build["sdk_root"]), context["sdk_sha"], SDK_PATHS)
                 == build["sdk_source"], "SDK_SOURCE_CHANGED")
            need(frontend_observation(args.repo) == frontend, "BUILT_FRONTEND_CHANGED")
            for name, binary in (("motionwright-desktop", args.desktop), ("native-render-e2e", args.fixture)):
                need(source.regular_file(binary, 512 * 1024 * 1024) ==
                     {key: build["artifacts"][name][key] for key in ("bytes", "sha256")},
                     "ORIGINAL_EXECUTABLE_CHANGED")
            unchanged_original(args.build_original, build["cargo_original"], None, MAX_BUILD)
            need(namespace(root, root) == root_namespace, "PRIVATE_ROOT_REPLACED")
            result.update(status="PASS", project_id=before["project_id"], generation=before["generation"],
                revision_before=before["revision_before"], revision_after=before["expected_revision"],
                deliverable_profile_id=before["deliverable_profile_id"],
                database_identity=before["database_identity"], cas_root=before["cas_root"],
                originals={"before": before_identity, "seed_response": seed_identity,
                           "ui": ui_identity, "manifest": manifest_identity,
                           "pcm": pcm_identity, "readback": readback_identity,
                           "reader_response": reader_identity},
                verified_pcm_sample_frames=pcm.FRAMES, verified_pcm_samples=pcm.FRAMES * 2,
                additional_journal_events=0, resources=bounded_resources(root))
            # Git/source observers are processes too; take a final current tree.
            closure = empty_natural_tree(root, 5)
            validate_process_closure(closure, os.getpid(), natural=True)
        except BaseException as error:
            primary = error
            result.update(status="FAIL", failure_type=type(error).__name__, failure_code=code(error),
                          failure_traceback="".join(traceback.format_exception(error))[-8192:])
        finally:
            # All failures stop effects. A signal may clean a failed attempt,
            # but never change it to a successful UI, owner or audio result.
            direct_exit = desktop.poll() if desktop is not None else None
            try:
                if primary is not None or closure is None:
                    closure = processes.close_own_children()
                closure = compact_closure(closure)
                # Retain a failed/incomplete cleanup observation too. Its
                # presence never makes it pass the strict closure predicate.
                publish(root / "process-closure.private.json", closure, root)
                validate_process_closure(closure, os.getpid(), natural=result["status"] == "PASS")
            except BaseException as error:
                result.update(status="FAIL", closure_failure_type=type(error).__name__,
                              closure_failure_code=code(error))
                if primary is None:
                    primary = error
            for stream in streams:
                try:
                    stream.close()
                except BaseException as error:
                    result.update(status="FAIL", stream_close_failure_code=code(error))
                    if primary is None:
                        primary = error
            if directory is not None:
                try:
                    completion = {"status": "DIRECT_EXIT_OBSERVED" if direct_exit is not None
                                  else "DIRECT_EXIT_UNKNOWN_OR_REAPED_BY_CLEANUP",
                                  "pid": desktop.pid if desktop is not None else None,
                                  "exit_code": direct_exit,
                                  "automatic_retry": False}
                    for name in ("stdout", "stderr"):
                        path = directory / (name + ".original")
                        if path.exists():
                            completion[name] = source.regular_file(path, MAX_STREAM)
                    publish(directory / "completion.original.json", completion, root)
                except BaseException as error:
                    result.update(status="FAIL", stream_failure_type=type(error).__name__,
                                  stream_failure_code=code(error))
                    if primary is None:
                        primary = error
    publish(root / "result.private.json", result, root)
    return 0 if result["status"] == "PASS" else 1


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--desktop", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--build-original", type=Path)
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--ui-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--directory", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--deadline", type=float, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.ui_worker:
        need(args.directory is not None and args.deadline is not None
             and args.desktop is None and args.fixture is None and args.build_original is None,
             "UI_WORKER_ARGUMENT_SHAPE")
    else:
        need(args.desktop is not None and args.fixture is not None and args.build_original is not None
             and args.directory is None and args.deadline is None and 30 <= args.seconds <= 120,
             "HARNESS_ARGUMENT_SHAPE")
    return args


def main(argv=None):
    try:
        args = parse_args(argv)
        if args.ui_worker:
            return worker(args)
        status = scenario(args)
        print(json.dumps({"status": "PASS" if status == 0 else "FAIL", "formal_G2": False,
                          "formal_G3": False, "automatic_retry": False}, sort_keys=True))
        return status
    except BaseException as error:
        print(json.dumps({"status": "FAIL", "code": code(error), "formal_G2": False,
                          "formal_G3": False, "automatic_retry": False}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
