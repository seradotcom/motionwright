"""Versioned, explicit native evidence selection; never discovers an owner tree.

The producer owns the fixed role/field policy. This collector only reads its
closed spec, preserves exact selected binary originals, and gives projected
JSON a different identity. It never dispatches, retries, encrypts, uploads,
opens SQLite or infers physical/human acceptance. Linux openat handles are the
physical custody boundary; other platforms fail closed for capture.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile

SPEC_SCHEMA = "motionwright.native.evidence-selection/2"
MANIFEST_SCHEMA = "motionwright.native.selected-evidence/2"
PROJECTION_SCHEMA = "motionwright.native.json-projection/2"
SPEC_SCHEMA_V3 = "motionwright.native.evidence-selection/3"
MANIFEST_SCHEMA_V3 = "motionwright.native.selected-evidence/3"
DIAGNOSTIC_CONTRACT_SCHEMA = "motionwright.native.diagnostic-contract/1"
DIAGNOSTIC_KINDS = frozenset(("diagnostic-original", "diagnostic-json-original"))
DIAGNOSTIC_MAXIMUM = {"stdout": 262144, "stderr": 262144,
                      "stdin": 65536, "typed-terminal": 65536,
                      "film-command-original": 4 * 1024 * 1024}
MAX_FILE = 256 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_FILES = 4096
MAX_JSON = 8 * 1024 * 1024
# These four /2 snapshots are JSON data, not process streams or terminals.
# Their source-owned Object envelopes remain closed. Only their explicit leaf
# rules may retain escaped text or a receipt payload without projecting it.
JSON_SNAPSHOT_DEPTH = 32
JSON_SNAPSHOT_NODES = 65536
JSON_SNAPSHOT_STRING_BYTES = MAX_JSON
JSON_SNAPSHOT_POLICIES = {
    "hyperframes-original-render-failure": ("original", "original-render.failure.json"),
    "hyperframes-original-render-failure-custody": ("original", "original-render.failure-custody.json"),
    "hyperframes-edited-render-failure": ("edited", "edited-render.failure.json"),
    "hyperframes-edited-render-failure-custody": ("edited", "edited-render.failure-custody.json"),
}
STATES = frozenset(("INCLUDED", "EXCLUDED_BY_SCOPE", "REQUIRED_MISSING",
                    "CAPTURE_FAILED", "CHANGED"))
LAYERS = frozenset(("readback", "persistence", "attempt-custody", "origin"))
PHASES = frozenset(("original", "edited", "comparison", "single"))
SHA = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
LABEL = re.compile(r"[a-z][a-z0-9_.-]{0,95}\Z")
PART = re.compile(r"[A-Za-z0-9_.+-]{1,160}\Z")
# These names are rejected before lstat/open/hash, including required entries.
# A producer cannot turn a missing required role into an intentional exclusion.
EXCLUDED_COMPONENTS = frozenset((
    "runtime", "config", "home", "state", "scratch", "bin", "logs",
    ".ssh", ".aws", ".gnupg", ".config", ".cache",
))
EXCLUDED_NAMES = frozenset((
    "motionwright.session", "observer-diagnostic.json", "environ", "cmdline",
    "maps", "stdout", "stderr", "traceback", "connection.json",
    "connection-config.json", "native-connection.json",
))
EXCLUDED_SUFFIXES = (".log", ".session", ".stdout", ".stderr", ".traceback")
LOST_SCOPE = (
    "whole-owner-tree", "raw-diagnostics", "raw-environment",
    "broker-grants-and-journal-replay", "atomic-sqlite-snapshot",
)
LOST_SCOPE_V3 = tuple("unselected-raw-diagnostics" if x == "raw-diagnostics" else x
                      for x in LOST_SCOPE)
DIAGNOSTIC_MANIFEST_FIELDS = frozenset((
    "diagnostic_contract", "diagnostic_contract_sha256", "diagnostic_roster_complete",
    "diagnostic_eof_claimed", "diagnostic_history_complete_claimed",
    "diagnostic_payloads_redacted",
))

class SelectionError(ValueError):
    """A closed code, never a raw source exception or file payload."""
    def __init__(self, code):
        if not LABEL.fullmatch(code):
            code = "selection-error"
        self.code = code
        super().__init__(code)

def require(value, code):
    if not value:
        raise SelectionError(code)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")

def strict(data):
    require(type(data) is bytes and 0 < len(data) <= MAX_JSON, "json-budget")
    def pairs(entries):
        result = {}
        for key, value in entries:
            require(key not in result, "duplicate-json-key")
            result[key] = value
        return result
    def constant(_):
        raise SelectionError("nonfinite-json")
    try:
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise SelectionError("invalid-json") from error
    require(type(value) is dict, "json-object-required")
    return value

@dataclass(frozen=True)
class Field:
    rule: object
    required: bool = True

@dataclass(frozen=True)
class Object:
    fields: dict

@dataclass(frozen=True)
class Array:
    item: object
    maximum: int
    minimum: int = 0

@dataclass(frozen=True)
class String:
    maximum: int = 256
    pattern: str | None = None
    values: tuple | None = None

@dataclass(frozen=True)
class Number:
    minimum: float | None = None
    maximum: float | None = None
    integer: bool = False

@dataclass(frozen=True)
class Nullable:
    item: object

@dataclass(frozen=True)
class Literal:
    value: object

@dataclass(frozen=True)
class JsonText:
    """Escaped JSON text, only inside one of the four scoped snapshots."""
    maximum: int = MAX_JSON

@dataclass(frozen=True)
class JsonValue:
    """A bounded receipt payload; never an open envelope or an authority."""
    pass

def project(value, rule, *, exact=False, depth=0):
    """Closed fields only. No wildcard object keys or arbitrary JSON leaves."""
    require(depth < 64, "projection-depth")
    if isinstance(rule, Object):
        require(type(value) is dict, "projection-object")
        require(all(type(k) is str and isinstance(v, Field)
                    for k, v in rule.fields.items()), "policy-fields")
        if exact:
            require(set(value) <= set(rule.fields), "original-json-extra-field")
        result = {}
        for key, field in rule.fields.items():
            if key not in value:
                require(not field.required, "projection-required-field")
                continue
            result[key] = project(value[key], field.rule, exact=exact, depth=depth+1)
        return result
    if isinstance(rule, Array):
        require(type(value) is list and rule.minimum <= len(value) <= rule.maximum,
                "projection-array")
        return [project(x, rule.item, exact=exact, depth=depth+1) for x in value]
    if isinstance(rule, Nullable):
        return None if value is None else project(value, rule.item, exact=exact, depth=depth+1)
    if isinstance(rule, Literal):
        require(type(value) is type(rule.value) and value == rule.value, "projection-literal")
        return value
    if rule is bool:
        require(type(value) is bool, "projection-boolean")
        return value
    if isinstance(rule, String):
        require(type(value) is str and len(value.encode("utf-8")) <= rule.maximum
                and not any(ord(x) < 32 or ord(x) == 127 for x in value), "projection-string")
        if rule.pattern is not None:
            require(re.fullmatch(rule.pattern, value) is not None, "projection-string-pattern")
        if rule.values is not None:
            require(value in rule.values, "projection-string-enum")
        return value
    if isinstance(rule, Number):
        require(type(value) in ((int,) if rule.integer else (int, float))
                and math.isfinite(value), "projection-number")
        require(rule.minimum is None or value >= rule.minimum, "projection-number-min")
        require(rule.maximum is None or value <= rule.maximum, "projection-number-max")
        return value
    raise SelectionError("unregistered-projection-rule")

def _json_leaf_rules(rule, *, field=None, depth=0):
    """Inspect the source policy; a payload wildcard cannot replace a struct."""
    require(depth < 64, "json-snapshot-policy-depth")
    if isinstance(rule, JsonText):
        require(type(rule.maximum) is int and 0 <= rule.maximum <= MAX_JSON,
                "json-snapshot-text-policy")
        return True
    if isinstance(rule, JsonValue):
        require(field == "payload", "json-snapshot-value-field")
        return True
    if isinstance(rule, Object):
        require(all(type(k) is str and isinstance(v, Field)
                    for k, v in rule.fields.items()), "policy-fields")
        # Do not short-circuit: every leaf in the closed policy is checked.
        found = [_json_leaf_rules(v.rule, field=k, depth=depth+1)
                 for k, v in rule.fields.items()]
        return any(found)
    if isinstance(rule, Array):
        return _json_leaf_rules(rule.item, depth=depth+1)
    if isinstance(rule, Nullable):
        return _json_leaf_rules(rule.item, field=field, depth=depth+1)
    return False

def _json_snapshot_member(item, *, legacy):
    """Bind opt-in rules to one exact file, phase, root, kind and schema."""
    policy = item.get("policy")
    named = type(policy) is str and policy in JSON_SNAPSHOT_POLICIES
    located = item.get("root") == "records" and any(
        item.get("relative_path") == pair[1] for pair in JSON_SNAPSHOT_POLICIES.values())
    if not named and not located:
        return False
    require(named and legacy, "json-snapshot-policy-scope")
    phase, path = JSON_SNAPSHOT_POLICIES[policy]
    role = "render-failure-custody" if path.endswith(".failure-custody.json") else "render-failure"
    require(item.get("root") == "records" and item.get("relative_path") == path
            and item.get("phase") == phase and item.get("layer") == "attempt-custody"
            and item.get("role") == role and item.get("required") is False
            and item.get("kind") == "json-original", "json-snapshot-member-scope")
    return True

def _checked_json_policy(item, rule, *, legacy):
    scoped = _json_snapshot_member(item, legacy=legacy)
    leaves = _json_leaf_rules(rule)
    require(not leaves or scoped, "json-snapshot-leaf-not-opted-in")
    require(not scoped or isinstance(rule, Object), "json-snapshot-closed-envelope")
    return scoped

def _bounded_json_snapshot(value):
    """One budget for the complete file, shared across all payloads and text.

    Nodes include object keys and values. UTF-8 bytes include every key/string;
    recursion uses at most the fixed document depth, never an input-sized stack.
    """
    nodes, string_bytes = 0, 0
    def visit(current, depth):
        nonlocal nodes, string_bytes
        nodes += 1
        require(depth <= JSON_SNAPSHOT_DEPTH and nodes <= JSON_SNAPSHOT_NODES,
                "json-snapshot-structure-budget")
        if type(current) is str:
            try:
                string_bytes += len(current.encode("utf-8", "strict"))
            except UnicodeError as error:
                raise SelectionError("json-snapshot-text-encoding") from error
            require(string_bytes <= JSON_SNAPSHOT_STRING_BYTES,
                    "json-snapshot-string-budget")
        elif type(current) is dict:
            for key, child in current.items():
                require(type(key) is str, "json-snapshot-key")
                visit(key, depth+1)
                visit(child, depth+1)
        elif type(current) is list:
            for child in current:
                visit(child, depth+1)
        elif type(current) is float:
            require(math.isfinite(current), "json-snapshot-nonfinite")
        else:
            require(current is None or type(current) in (bool, int), "json-snapshot-value")
    visit(value, 0)

def _json_snapshot_tokens(raw):
    """Bound depth and JSON nodes before the decoder allocates an object tree.

    This constant-state UTF-8 lexer is a resource guard, not a second decoder.
    The unchanged strict decoder still rejects malformed grammar/duplicates;
    the subsequent whole-file walk checks decoded strings and finite values.
    """
    require(type(raw) is bytes and 0 < len(raw) <= MAX_JSON, "json-budget")
    try:
        raw.decode("utf-8", "strict")
    except UnicodeError as error:
        raise SelectionError("json-snapshot-text-encoding") from error
    depth, nodes = 0, 0
    quoted, escaped, primitive = False, False, False
    # json.loads(bytes) permits a UTF-8 BOM. Preserve it without counting a
    # false scalar node; UTF-16/32 NULs are rejected as lexical controls below.
    start = 3 if raw.startswith(b"\xef\xbb\xbf") else 0
    primitive_bytes = b"-+.0123456789eEtruefalsn"
    for byte in memoryview(raw)[start:]:
        if quoted:
            require(byte >= 32, "json-snapshot-lexical-control")
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
            continue
        if byte in b" \t\r\n,:":
            primitive = False
            continue
        if byte in b"}]":
            require(depth > 0, "json-snapshot-lexical-depth")
            depth -= 1
            primitive = False
            continue
        if primitive and byte not in b'"{[':
            require(byte in primitive_bytes, "json-snapshot-lexical-token")
            continue
        require(depth <= JSON_SNAPSHOT_DEPTH, "json-snapshot-structure-budget")
        nodes += 1
        require(nodes <= JSON_SNAPSHOT_NODES, "json-snapshot-structure-budget")
        primitive = False
        if byte == 34:
            quoted = True
        elif byte in b"{[":
            depth += 1
        else:
            require(byte in b"-0123456789tfn", "json-snapshot-lexical-token")
            primitive = True
    require(not quoted and not escaped and depth == 0, "json-snapshot-lexical-incomplete")

def _validate_json_snapshot(value, rule, *, field=None, depth=0):
    """Validate closed typed envelopes; return no rewritten representation."""
    require(depth < 64, "projection-depth")
    if isinstance(rule, JsonText):
        require(type(value) is str and len(value.encode("utf-8")) <= rule.maximum,
                "json-snapshot-text")
    elif isinstance(rule, JsonValue):
        require(field == "payload", "json-snapshot-value-field")
        # The single whole-file walk has already enforced finite JSON budgets.
    elif isinstance(rule, Object):
        require(type(value) is dict, "projection-object")
        require(set(value) <= set(rule.fields), "original-json-extra-field")
        for key, spec in rule.fields.items():
            if key not in value:
                require(not spec.required, "projection-required-field")
                continue
            _validate_json_snapshot(value[key], spec.rule, field=key, depth=depth+1)
    elif isinstance(rule, Array):
        require(type(value) is list and rule.minimum <= len(value) <= rule.maximum,
                "projection-array")
        for child in value:
            _validate_json_snapshot(child, rule.item, depth=depth+1)
    elif isinstance(rule, Nullable):
        if value is not None:
            _validate_json_snapshot(value, rule.item, field=field, depth=depth+1)
    else:
        project(value, rule, exact=True, depth=depth)

def _project_selected_json(value, item, rule, *, legacy, exact):
    if _checked_json_policy(item, rule, legacy=legacy):
        _bounded_json_snapshot(value)
        _validate_json_snapshot(value, rule)
        return value
    return project(value, rule, exact=exact)

def _decode_selected_json(raw, item, rule, *, legacy, exact):
    if _checked_json_policy(item, rule, legacy=legacy):
        _json_snapshot_tokens(raw)
    return _project_selected_json(strict(raw), item, rule, legacy=legacy, exact=exact)

def member_path(raw):
    require(type(raw) is str and 0 < len(raw) <= 1024 and "\\" not in raw,
            "member-path")
    parts = raw.split("/")
    require(all(p not in ("", ".", "..") and PART.fullmatch(p) for p in parts)
            and not PurePosixPath(raw).is_absolute(), "member-path")
    return tuple(parts)

def excluded(parts):
    lowered = tuple(p.casefold() for p in parts)
    return any(p in EXCLUDED_COMPONENTS or p in EXCLUDED_NAMES
               or p.endswith(EXCLUDED_SUFFIXES) for p in lowered)

def _film_command_entry(root, path, diagnostic_contract):
    if (type(diagnostic_contract) is not dict
            or type(diagnostic_contract.get("context")) is not dict
            or diagnostic_contract["context"].get("scenario") != "film180-g2"):
        return None
    from native_film180_command_custody import entry_for
    return entry_for(root, path, diagnostic_contract)


def _diagnostic_path_allowed(item, diagnostic_contract):
    entry = _film_command_entry(item.get("root"), item.get("relative_path"), diagnostic_contract)
    return entry is not None and all(item.get(key) == entry[key]
        for key in ("phase", "layer", "role"))


def context(value):
    expected = {"source_sha", "semwright_sha", "run_id", "run_attempt", "scenario"}
    require(type(value) is dict and set(value) == expected, "context-fields")
    require(all(type(value[k]) is str and GIT_SHA.fullmatch(value[k])
                for k in ("source_sha", "semwright_sha")), "context-source")
    require(all(type(value[k]) is str and re.fullmatch(r"[1-9][0-9]{0,19}", value[k])
                for k in ("run_id", "run_attempt")), "context-run")
    require(value["run_attempt"] == "1", "native-attempt-not-replayed")
    require(type(value["scenario"]) is str and LABEL.fullmatch(value["scenario"]),
            "context-scenario")
    return value

def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)

class RootHandles:
    """Anchored no-follow openat; excludes are resolved before this is called."""
    def __init__(self, roots):
        require(os.name == "posix" and hasattr(os, "O_NOFOLLOW")
                and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd,
                "capture-needs-linux-openat")
        self.fds, self.initial, self.paths = {}, {}, {}
        self.diagnostic_contract = None
        try:
            for name, raw in roots.items():
                require(type(raw) is str and raw.startswith("/") and "//" not in raw,
                        "root-absolute")
                parts = raw.split("/")[1:]
                require(parts and all(x not in ("", ".", "..") for x in parts),
                        "root-canonical")
                fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
                try:
                    for part in parts:
                        nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                      dir_fd=fd)
                        os.close(fd)
                        fd = nxt
                    info = os.fstat(fd)
                    require(info.st_uid == os.geteuid(), "root-owner")
                    # Source checkout is an explicit separate read-only root;
                    # selected private data roots must already be private.
                    if name != "source":
                        require(stat.S_IMODE(info.st_mode) & 0o077 == 0, "root-private-mode")
                    require(all((info.st_dev, info.st_ino) !=
                                (x.st_dev, x.st_ino) for x in self.initial.values()),
                            "aliased-roots")
                    require(all(not (raw.startswith(other + "/") or other.startswith(raw + "/"))
                                for other in self.paths.values()), "overlapping-roots")
                    self.fds[name], self.initial[name], self.paths[name] = fd, info, raw
                    fd = -1
                finally:
                    if fd >= 0:
                        os.close(fd)
        except BaseException:
            self.close()
            raise

    def close(self):
        for fd in self.fds.values():
            os.close(fd)
        self.fds.clear()

    def parent(self, root, parts):
        fd = os.dup(self.fds[root])
        observed = []
        try:
            observed.append((fd, identity(os.fstat(fd))))
            for part in parts[:-1]:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=fd)
                fd = nxt
                observed.append((fd, identity(os.fstat(fd))))
            return observed
        except BaseException:
            for handle, _ in observed:
                os.close(handle)
            raise

    @staticmethod
    def close_parents(parents):
        for fd, _ in parents:
            os.close(fd)

    def admit_diagnostic_contract(self, contract):
        # This only enables the closed Film filenames for private read custody.
        # It confers no native/Host write authority and cannot enable neighbors.
        require(self.diagnostic_contract is None, "diagnostic-reader-repeat")
        self.diagnostic_contract = _checked_diagnostic_contract(contract, contract["context"])

    def read(self, root, parts, maximum):
        entry = _film_command_entry(root, "/".join(parts), self.diagnostic_contract)
        require(not excluded(parts) or entry is not None, "excluded-before-open")
        if entry is not None:
            maximum = min(maximum, entry["maximum"])
        parents = self.parent(root, parts)
        fd = -1
        try:
            leaf = parts[-1]
            before_name = os.stat(leaf, dir_fd=parents[-1][0], follow_symlinks=False)
            require(stat.S_ISREG(before_name.st_mode) and before_name.st_nlink == 1,
                    "member-regular-single-link")
            require(0 <= before_name.st_size <= maximum, "member-byte-budget")
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=parents[-1][0])
            before = os.fstat(fd)
            require(identity(before_name) == identity(before), "member-changed-before-open")
            data = bytearray()
            while True:
                block = os.read(fd, min(65536, maximum + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
                require(len(data) <= maximum, "member-byte-budget")
            after = os.fstat(fd)
            after_name = os.stat(leaf, dir_fd=parents[-1][0], follow_symlinks=False)
            require(identity(before) == identity(after) == identity(after_name)
                    and len(data) == before.st_size, "member-changed-during-read")
            require(all(identity(os.fstat(p)) == mark for p, mark in parents),
                    "parent-changed-during-read")
            return bytes(data), identity(before)
        finally:
            if fd >= 0:
                os.close(fd)
            self.close_parents(parents)

    def stat_member(self, root, parts):
        require(not excluded(parts) or _film_command_entry(root, "/".join(parts),
                self.diagnostic_contract) is not None, "excluded-before-stat")
        parents = self.parent(root, parts)
        try:
            mark = os.stat(parts[-1], dir_fd=parents[-1][0], follow_symlinks=False)
            require(stat.S_ISREG(mark.st_mode) and mark.st_nlink == 1,
                    "member-regular-single-link")
            return identity(mark)
        finally:
            self.close_parents(parents)

    def stable_roots(self):
        return all(identity(os.fstat(fd)) == identity(self.initial[name])
                   for name, fd in self.fds.items())

def _checked_diagnostic_contract(contract, expected_context, *, policies=None,
                                 role_contract=None, roots=None):
    """Freeze an external, source-owned roster; never infer it from a TAR."""
    require(type(contract) is dict, "diagnostic-contract-required")
    frozen = strict(canonical(contract))
    require(set(frozen) == {"schema", "context", "entries"}
            and frozen["schema"] == DIAGNOSTIC_CONTRACT_SCHEMA,
            "diagnostic-contract-fields")
    context(frozen["context"])
    require(frozen["context"] == expected_context, "diagnostic-context-mismatch")
    film = frozen["context"]["scenario"] == "film180-g2"
    if film:
        from native_film180_command_custody import contract as film_contract
        require(frozen == film_contract(expected_context), "film-command-fixed-contract")
    entries = frozen["entries"]
    require(type(entries) is list and 1 <= len(entries) <= MAX_FILES,
            "diagnostic-contract-entries")
    fields = {"root", "relative_path", "phase", "layer", "role", "type", "maximum", "policy"}
    paths = set()
    for entry in entries:
        require(type(entry) is dict and set(entry) == fields, "diagnostic-entry-fields")
        require(type(entry["root"]) is str and LABEL.fullmatch(entry["root"])
                and entry["root"] != "source", "diagnostic-entry-root")
        if roots is not None:
            require(entry["root"] in roots or film, "diagnostic-entry-root")
        require(not excluded(member_path(entry["relative_path"])) or film,
                "diagnostic-entry-excluded")
        require(type(entry["phase"]) is str and entry["phase"] in PHASES
                and type(entry["layer"]) is str and entry["layer"] == "attempt-custody"
                and type(entry["role"]) is str and LABEL.fullmatch(entry["role"]),
                "diagnostic-entry-scope")
        key = (entry["phase"], entry["layer"], entry["role"])
        if role_contract is not None:
            require(key in role_contract, "diagnostic-entry-role")
        require(type(entry["type"]) is str and entry["type"] in DIAGNOSTIC_MAXIMUM,
                "diagnostic-entry-type")
        require(entry["type"] != "film-command-original" or film, "film-command-scenario")
        require(type(entry["maximum"]) is int
                and 0 < entry["maximum"] <= DIAGNOSTIC_MAXIMUM[entry["type"]],
                "diagnostic-entry-maximum")
        if entry["type"] == "typed-terminal":
            require(type(entry["policy"]) is str and LABEL.fullmatch(entry["policy"]),
                    "diagnostic-json-policy")
            if policies is not None:
                require(entry["policy"] in policies
                        and isinstance(policies[entry["policy"]], Object),
                        "diagnostic-json-policy")
        else:
            require(entry["policy"] is None, "diagnostic-stream-policy")
        path = (entry["root"], entry["relative_path"])
        require(path not in paths, "diagnostic-entry-alias")
        paths.add(path)
    return frozen


def _diagnostic_selection(rows, guards, contract):
    """Every reserved path is selected with its fixed type or guarded absent."""
    entries = {(e["root"], e["relative_path"]): e for e in contract["entries"]}
    covered = set()
    for row in rows:
        path = (row["root"], row["relative_path"])
        diagnostic = type(row.get("kind")) is str and row["kind"] in DIAGNOSTIC_KINDS
        require(path in entries or not diagnostic, "diagnostic-unregistered-member")
        if path not in entries:
            require("diagnostic_type" not in row, "diagnostic-type-on-ordinary-member")
            continue
        require(path not in covered, "diagnostic-selection-alias")
        covered.add(path)
        entry = entries[path]
        kind = ("diagnostic-json-original" if entry["type"] == "typed-terminal"
                else "diagnostic-original")
        require(row.get("kind") == kind and row.get("diagnostic_type") == entry["type"],
                "diagnostic-kind-mismatch")
        require(all(row.get(k) == entry[k] for k in ("phase", "layer", "role", "policy")),
                "diagnostic-member-scope")
        require(row.get("required") is True, "diagnostic-selected-made-optional")
        unobserved = row.get("expected_sha256") is None and row.get("bytes") is None
        require(unobserved or (type(row.get("bytes")) is int
                and 0 <= row["bytes"] <= entry["maximum"]
                and type(row.get("expected_sha256")) is str
                and SHA.fullmatch(row["expected_sha256"])), "diagnostic-member-budget")
        if row.get("state") == "INCLUDED":
            require(not unobserved and type(row.get("retained_bytes")) is int
                    and 0 <= row["retained_bytes"] <= entry["maximum"],
                    "diagnostic-retained-budget")
            require(row.get("member") == "/".join(("selected", entry["phase"], entry["layer"],
                    entry["root"], entry["relative_path"])), "diagnostic-tar-path")
    for guard in guards:
        path = (guard["root"], guard["relative_path"])
        if path not in entries:
            continue
        require(path not in covered, "diagnostic-selection-alias")
        covered.add(path)
        require(all(guard.get(k) == entries[path][k] for k in ("phase", "layer", "role")),
                "diagnostic-guard-scope")
    require(covered == set(entries), "diagnostic-roster-incomplete")


def _diagnostic_complete(rows, guards, contract, stable):
    paths = {(e["root"], e["relative_path"]) for e in contract["entries"]}
    return bool(stable and all(row["state"] == "INCLUDED" for row in rows
                if (row["root"], row["relative_path"]) in paths)
                and all(guard["absent_before"] is True and guard["absent_after"] is True
                        for guard in guards
                        if (guard["root"], guard["relative_path"]) in paths))


def _checked_spec(spec, policies, role_contract, diagnostic_contract=None):
    # Copy/freeze before any path resolution, stat or read. The caller cannot
    # insert a late selection while capture is in progress.
    frozen = strict(canonical(spec))
    keys = {"schema", "context", "roots", "members", "exclusions", "source_bindings", "origin"}
    require(set(frozen) in (keys, keys | {"absence_guards"}), "spec-fields")
    frozen.setdefault("absence_guards", [])
    require(frozen["schema"] in (SPEC_SCHEMA, SPEC_SCHEMA_V3), "spec-schema")
    v3 = frozen["schema"] == SPEC_SCHEMA_V3
    require(v3 or diagnostic_contract is None, "diagnostic-contract-on-legacy")
    context(frozen["context"])
    if v3:
        diagnostic_contract = _checked_diagnostic_contract(diagnostic_contract, frozen["context"],
            policies=policies, role_contract=role_contract)
    require(type(frozen["roots"]) is dict and 1 <= len(frozen["roots"]) <= 16
            and all(type(k) is str and LABEL.fullmatch(k) for k in frozen["roots"]),
            "spec-roots")
    require("source" in frozen["roots"], "source-root-required")
    members = frozen["members"]
    require(type(members) is list and 1 <= len(members) <= MAX_FILES, "spec-members")
    require(type(role_contract) is dict and role_contract
            and any(v[0] > 0 for v in role_contract.values()), "role-contract-required")
    seen, destinations, role_counts = set(), set(), {}
    json_snapshots = set()
    member_keys = {"root", "relative_path", "role", "layer", "phase", "kind",
                   "required", "expected_sha256", "bytes", "policy"}
    for item in members:
        require(type(item) is dict, "member-fields")
        diagnostic = v3 and type(item.get("kind")) is str and item["kind"] in DIAGNOSTIC_KINDS
        require(set(item) == (member_keys | {"diagnostic_type"} if diagnostic else member_keys),
                "member-fields")
        require(type(item["root"]) is str and item["root"] != "source"
                and (item["root"] in frozen["roots"] or
                    (diagnostic and _diagnostic_path_allowed(item, diagnostic_contract))), "member-root")
        parts = member_path(item["relative_path"])
        require(type(item["layer"]) is str and item["layer"] in LAYERS
                and type(item["phase"]) is str and item["phase"] in PHASES, "member-scope")
        require(type(item["role"]) is str and LABEL.fullmatch(item["role"]), "member-role")
        require(type(item["required"]) is bool, "member-required")
        require(type(item["kind"]) is str and
                (item["kind"] in ("original", "json-original", "json-projection")
                 or diagnostic), "member-kind")
        if _json_snapshot_member(item, legacy=not v3):
            require(item["policy"] not in json_snapshots, "json-snapshot-duplicate-policy")
            json_snapshots.add(item["policy"])
        unobserved = item["expected_sha256"] is None and item["bytes"] is None
        require(unobserved or (type(item["expected_sha256"]) is str
                and SHA.fullmatch(item["expected_sha256"])), "member-expected-digest")
        require(unobserved or (type(item["bytes"]) is int and 0 <= item["bytes"] <= MAX_FILE),
                "member-expected-size")
        key = (item["root"], item["relative_path"])
        destination = (item["phase"], item["layer"], item["role"], item["relative_path"])
        require(key not in seen and destination not in destinations, "duplicate-selection")
        seen.add(key); destinations.add(destination)
        contract_key = (item["phase"], item["layer"], item["role"])
        require(contract_key in role_contract, "unregistered-role")
        if item["kind"] == "original":
            require(item["policy"] is None and not parts[-1].casefold().endswith(".json"),
                    "original-json-needs-closed-policy")
        elif item["kind"] == "diagnostic-original":
            require(item["policy"] is None, "diagnostic-stream-policy")
        else:
            require(type(item["policy"]) is str and item["policy"] in policies,
                    "unregistered-json-policy")
            _checked_json_policy(item, policies[item["policy"]], legacy=not v3)
        require(not excluded(parts) or (diagnostic and
                _diagnostic_path_allowed(item, diagnostic_contract)), "required-role-in-excluded-scope")
        role_counts[contract_key] = role_counts.get(contract_key, 0) + 1
    for key, bounds in role_contract.items():
        require(type(key) is tuple and len(key) == 3
                and type(key[0]) is str and key[0] in PHASES
                and type(key[1]) is str and key[1] in LAYERS
                and type(key[2]) is str and LABEL.fullmatch(key[2]), "role-contract-key")
        require(type(bounds) is tuple and len(bounds) == 2
                and 0 <= bounds[0] <= bounds[1] <= MAX_FILES, "role-contract-range")
        require(bounds[0] <= role_counts.get(key, 0) <= bounds[1], "role-count-incomplete")
        if bounds[0] > 0:
            require(all(x["required"] for x in members if
                        (x["phase"], x["layer"], x["role"]) == key),
                    "required-role-made-optional")
    ex = frozen["exclusions"]
    require(type(ex) is list and len(ex) <= 128, "exclusion-budget")
    for row in ex:
        require(type(row) is dict and set(row) == {"root", "relative_path", "reason"}
                and row["root"] in frozen["roots"] and row["reason"] == "outside-reviewed-scope",
                "exclusion-fields")
        require(excluded(member_path(row["relative_path"])), "exclusion-not-policy")
        require((row["root"], row["relative_path"]) not in seen,
                "exclusion-masks-required-member")
    guards = frozen["absence_guards"]
    require(type(guards) is list and len(guards) <= 128, "absence-guard-budget")
    guard_keys = set()
    for row in guards:
        require(type(row) is dict and set(row) == {"root", "relative_path", "phase", "layer", "role"},
                "absence-guard-fields")
        require(type(row["root"]) is str and row["root"] in frozen["roots"]
                and row["root"] != "source", "absence-guard-root")
        require(not excluded(member_path(row["relative_path"])) or
                _diagnostic_path_allowed(row, diagnostic_contract), "absence-guard-excluded")
        require(type(row["phase"]) is str and row["phase"] in PHASES
                and type(row["layer"]) is str and row["layer"] in LAYERS
                and type(row["role"]) is str, "absence-guard-scope")
        key = (row["phase"], row["layer"], row["role"])
        require(key in role_contract and role_contract[key][0] == 0, "absence-guard-role")
        path_key = (row["root"], row["relative_path"])
        require(path_key not in seen and path_key not in guard_keys, "absence-guard-alias")
        guard_keys.add(path_key)
    if v3:
        diagnostic_contract = _checked_diagnostic_contract(diagnostic_contract, frozen["context"],
            policies=policies, role_contract=role_contract, roots=frozen["roots"])
        _diagnostic_selection(members, guards, diagnostic_contract)
    bindings = frozen["source_bindings"]
    require(type(bindings) is list and 1 <= len(bindings) <= 128, "source-bindings")
    bind_names = set()
    for row in bindings:
        require(type(row) is dict and set(row) == {"path", "sha256"}, "source-binding-fields")
        member_path(row["path"])
        require(not excluded(member_path(row["path"])), "source-binding-excluded")
        require(type(row["sha256"]) is str and SHA.fullmatch(row["sha256"])
                and row["path"] not in bind_names, "source-binding-digest")
        bind_names.add(row["path"])
    require("tooling/native_evidence_projection.py" in bind_names, "collector-source-binding")
    if v3 and frozen["context"]["scenario"] == "film180-g2":
        require("tooling/native_film180_command_custody.py" in bind_names,
                "film-command-source-binding")
    origin = frozen["origin"]
    require(type(origin) is dict and set(origin) == {"kind", "fixture_id", "producer_paths",
                "private_data", "upload_authorization"}, "origin-fields")
    require(origin["kind"] in ("generated-first-party-fixture", "public-licensed-fixture")
            and type(origin["fixture_id"]) is str and LABEL.fullmatch(origin["fixture_id"])
            and origin["private_data"] is True
            and origin["upload_authorization"] == "NOT_GRANTED_BY_COLLECTOR", "origin-scope")
    require(type(origin["producer_paths"]) is list and origin["producer_paths"]
            and all(x in bind_names for x in origin["producer_paths"]), "origin-source-closure")
    return frozen

def _verify_sources(spec, handles):
    """Compare both committed blob and actual bytes; Git status is never trust."""
    source = spec["roots"]["source"]
    ref = spec["context"]["source_sha"]
    for item in spec["source_bindings"]:
        p = item["path"]
        run = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c",
                              "core.untrackedCache=false", "show", ref + ":" + p],
                             cwd=source, capture_output=True, timeout=30)
        require(run.returncode == 0 and sha(run.stdout) == item["sha256"],
                "source-git-binding")
        raw, _ = handles.read("source", member_path(p), MAX_FILE)
        require(raw == run.stdout and sha(raw) == item["sha256"],
                "physical-source-differs-from-git")

def verify_source_bindings(source_root, source_sha, source_bindings):
    """Pre-effect code identity check using the same anchored reader as sealing."""
    require(type(source_sha) is str and GIT_SHA.fullmatch(source_sha), "source-preflight-commit")
    require(type(source_bindings) is list and 1 <= len(source_bindings) <= 128,
            "source-preflight-bindings")
    names = set()
    for row in source_bindings:
        require(type(row) is dict and set(row) == {"path", "sha256"}, "source-binding-fields")
        require(not excluded(member_path(row["path"])) and row["path"] not in names,
                "source-preflight-path")
        require(type(row["sha256"]) is str and SHA.fullmatch(row["sha256"]),
                "source-binding-digest")
        names.add(row["path"])
    require("tooling/native_evidence_projection.py" in names, "collector-source-binding")
    spec = {"roots": {"source": str(source_root)}, "context": {"source_sha": source_sha},
            "source_bindings": json.loads(canonical(source_bindings))}
    with _HandlesContext(spec["roots"]) as handles:
        _verify_sources(spec, handles)
        require(handles.stable_roots(), "source-preflight-root-changed")
    return {"source_sha": source_sha, "source_bytes_bound": True,
            "source_bindings": spec["source_bindings"], "native_effects": 0}


def _contract_rows(contract):
    return [{"phase": k[0], "layer": k[1], "role": k[2],
             "minimum": v[0], "maximum": v[1]} for k, v in sorted(contract.items())]

def _layers(rows, contract, stable):
    required = {(k[0], k[1]) for k, bounds in contract.items() if bounds[0] > 0}
    required.update((x["phase"], x["layer"]) for x in rows)
    return [{"phase": phase, "layer": layer, "complete":
             stable and all(x["state"] == "INCLUDED" for x in rows
                            if x["phase"] == phase and x["layer"] == layer)}
            for phase, layer in sorted(required)]

def capture_selected_originals(spec, *, policies, role_contract, diagnostic_contract=None):
    """Return selected TAR bytes and /2 or explicit /3 manifest; never upload.

    A /3 diagnostic contract is supplied by the source/run-admitted caller.
    Retaining opaque streams does not prove EOF, sanitization or producer exit.
    """
    diagnostic_contract = (None if diagnostic_contract is None
                           else strict(canonical(diagnostic_contract)))
    spec = _checked_spec(spec, policies, role_contract, diagnostic_contract)
    v3 = spec["schema"] == SPEC_SCHEMA_V3
    rows, payloads, initial = [], {}, {}
    stable, source_bound = False, False
    with _HandlesContext(spec["roots"]) as handles:
        if diagnostic_contract is not None and spec["context"]["scenario"] == "film180-g2":
            handles.admit_diagnostic_contract(diagnostic_contract)
        _verify_sources(spec, handles)
        source_bound = True
        # Freeze only selected namespace entries before reading any payload.
        inode_owners = {}
        for item in spec["members"]:
            key = (item["root"], item["relative_path"])
            if item["root"] not in spec["roots"]:
                # The whole original observation root was never available.
                # Do not probe a substitute path or invent an absence witness.
                initial[key] = "CAPTURE_FAILED"
                continue
            try:
                mark = handles.stat_member(item["root"], member_path(item["relative_path"]))
                require(mark[:2] not in inode_owners, "aliased-selected-member")
                inode_owners[mark[:2]] = key
                initial[key] = mark
            except FileNotFoundError:
                initial[key] = "REQUIRED_MISSING" if item["required"] else "OPTIONAL_MISSING"
            except (OSError, SelectionError):
                initial[key] = "CAPTURE_FAILED"
        absence_guards = []
        for guard in spec["absence_guards"]:
            absent = False
            try:
                handles.stat_member(guard["root"], member_path(guard["relative_path"]))
            except FileNotFoundError:
                absent = True
            except (OSError, SelectionError):
                pass
            absence_guards.append({**guard, "absent_before": absent,
                                   "absent_after": False, "opened": False, "hashed": False})
        total = 0
        for item in spec["members"]:
            row = {k: item[k] for k in ("root", "relative_path", "role", "layer", "phase",
                                        "kind", "required", "expected_sha256", "bytes", "policy")}
            if item["kind"] in DIAGNOSTIC_KINDS:
                row["diagnostic_type"] = item["diagnostic_type"]
            key = (item["root"], item["relative_path"])
            mark = initial[key]
            if type(mark) is str or item["expected_sha256"] is None:
                row["state"] = mark if type(mark) is str and mark != "OPTIONAL_MISSING" else "CAPTURE_FAILED"
                row["code"] = "selected-object-not-captured"
                rows.append(row)
                continue
            try:
                maximum = MAX_JSON if _json_snapshot_member(item, legacy=not v3) else MAX_FILE
                raw, observed = handles.read(item["root"], member_path(item["relative_path"]),
                                             min(maximum, item["bytes"]))
                require(observed == mark, "selected-object-changed")
                require(len(raw) == item["bytes"] and sha(raw) == item["expected_sha256"],
                        "selected-digest-or-size-changed")
                data = raw
                if item["kind"] not in ("original", "diagnostic-original"):
                    projected = _decode_selected_json(raw, item, policies[item["policy"]],
                        legacy=not v3,
                        exact=item["kind"] in ("json-original", "diagnostic-json-original"))
                    if item["kind"] == "json-projection":
                        data = canonical({"schema": PROJECTION_SCHEMA, "policy": item["policy"],
                            "source_sha256": sha(raw), "source_bytes": len(raw),
                            "context": spec["context"], "phase": item["phase"],
                            "role": item["role"], "original_bytes_retained_locally": True,
                            "full_original_json_claimed": False, "value": projected})
                name = "/".join(("selected", item["phase"], item["layer"], item["root"],
                                  item["relative_path"]))
                if item["kind"] == "json-projection":
                    name += ".projection.json"
                require(name not in payloads, "tar-name-collision")
                total += len(data)
                require(total <= MAX_TOTAL, "capture-total-budget")
                payloads[name] = data
                row.update(state="INCLUDED", member=name, retained_sha256=sha(data),
                           retained_bytes=len(data), original_bytes=item["kind"] != "json-projection")
            except FileNotFoundError:
                row.update(state="CHANGED", code="selected-object-disappeared")
            except SelectionError as error:
                state = "CHANGED" if any(x in error.code for x in ("changed", "digest", "differs")) else "CAPTURE_FAILED"
                row.update(state=state, code=error.code)
            except (OSError, ValueError, TypeError, OverflowError):
                row.update(state="CAPTURE_FAILED", code="selected-capture-failed")
            rows.append(row)
        stable = handles.stable_roots()
        for item in spec["members"]:
            key = (item["root"], item["relative_path"])
            if type(initial[key]) is str:
                if initial[key] not in ("REQUIRED_MISSING", "OPTIONAL_MISSING"):
                    continue
                # Absence is a namespace observation, not permission to accept a
                # file created later in a nested directory. Never read it here.
                try:
                    handles.stat_member(item["root"], member_path(item["relative_path"]))
                except FileNotFoundError:
                    pass
                except (OSError, SelectionError):
                    stable = False
                else:
                    stable = False
                    for row in rows:
                        if (row["root"], row["relative_path"]) == key:
                            row.update(state="CHANGED", code="selected-object-appeared-after-selection")
                continue
            try:
                unchanged = (handles.stat_member(item["root"], member_path(item["relative_path"]))
                             == initial[key])
                if not unchanged:
                    for row in rows:
                        if (row["root"], row["relative_path"]) == key and row["state"] == "INCLUDED":
                            row.update(state="CHANGED", code="selected-object-changed-after-read")
                stable = unchanged and stable
            except (OSError, SelectionError):
                for row in rows:
                    if (row["root"], row["relative_path"]) == key and row["state"] == "INCLUDED":
                        row.update(state="CHANGED", code="selected-object-changed-after-read")
                stable = False
        for guard in absence_guards:
            try:
                handles.stat_member(guard["root"], member_path(guard["relative_path"]))
            except FileNotFoundError:
                guard["absent_after"] = True
            except (OSError, SelectionError):
                pass
            stable = stable and guard["absent_before"] and guard["absent_after"]
        # Check actual collector/producer bytes again after all reads.
        try:
            _verify_sources(spec, handles)
        except (SelectionError, OSError, subprocess.SubprocessError):
            stable, source_bound = False, False
    layer_rows = _layers(rows, role_contract, stable and source_bound)
    manifest = {
        "schema": MANIFEST_SCHEMA_V3 if v3 else MANIFEST_SCHEMA, "context": spec["context"],
        "selection_sha256": sha(canonical({"spec": spec, "diagnostic_contract": diagnostic_contract}
                                          if v3 else spec)), "origin": spec["origin"],
        "source_bindings": spec["source_bindings"], "source_bytes_bound": source_bound,
        "absence_guards": absence_guards,
        "role_contract": _contract_rows(role_contract), "members": rows,
        "exclusions": [{**r, "state": "EXCLUDED_BY_SCOPE",
                        "opened": False, "hashed": False, "enumerated": False}
                       for r in spec["exclusions"]],
        "layers": layer_rows, "selected_namespace_stable": stable,
        "required_layers_complete": all(x["complete"] for x in layer_rows),
        "complete_owner_state_claimed": False, "atomic_sqlite_snapshot_claimed": False,
        "full_original_json_claimed": False,
        "raw_diagnostics_included": False, "raw_environment_included": False,
        "automatic_retry": False, "native_effects_invoked_by_collector": 0,
        "lost_scope": list(LOST_SCOPE), "physical_acceptance": False,
        "human_acceptance": False, "release_authorized": False,
        "upload_authorization": "NOT_GRANTED_BY_COLLECTOR",
    }
    if v3:
        manifest.update(
            diagnostic_contract=diagnostic_contract,
            diagnostic_contract_sha256=sha(canonical(diagnostic_contract)),
            diagnostic_roster_complete=_diagnostic_complete(rows, absence_guards,
                diagnostic_contract, stable and source_bound),
            # A post-read CHANGED row can still have original bytes in the TAR.
            # Presence is independent of admissibility/completeness.
            raw_diagnostics_included=any(r["kind"] in DIAGNOSTIC_KINDS and r.get("member") in payloads
                                         for r in rows),
            diagnostic_eof_claimed=False, diagnostic_history_complete_claimed=False,
            diagnostic_payloads_redacted=False, lost_scope=list(LOST_SCOPE_V3))
    if v3 and spec["context"]["scenario"] == "film180-g2":
        from native_film180_command_custody import summarize
        manifest["film_command_originals"] = summarize(rows, absence_guards, payloads, diagnostic_contract)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, data in sorted(payloads.items()):
            require(len(name.encode("utf-8")) <= 255, "tar-path-budget")
            info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o600
            info.uid = info.gid = 0; info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
        data = canonical(manifest)
        info = tarfile.TarInfo("selected-evidence-manifest.json")
        info.size = len(data); info.mode = 0o600; info.uid = info.gid = 0; info.mtime = 0
        archive.addfile(info, io.BytesIO(data))
    return stream.getvalue(), manifest

class _HandlesContext:
    def __init__(self, roots): self.handles = RootHandles(roots)
    def __enter__(self): return self.handles
    def __exit__(self, *_): self.handles.close()

def validate_selected_manifest(manifest, *, expected_context, role_contract,
                               expected_diagnostic_contract=None, require_complete=True):
    """Validate complete custody, or explicitly permit incomplete /3 selection.

    Partial intake preserves required roles and their failed/missing rows. It
    still requires source binding, a stable selected namespace and bilateral
    absence observations. It never grants render, recovery or acceptance credit.
    Independent source/run-owned contracts remain mandatory for /3.
    """
    context(expected_context)
    require(type(require_complete) is bool, "selected-completeness-mode")
    require(type(manifest) is dict and manifest.get("schema") in (MANIFEST_SCHEMA, MANIFEST_SCHEMA_V3),
            "selected-manifest-schema")
    v3 = manifest["schema"] == MANIFEST_SCHEMA_V3
    require(v3 or require_complete, "partial-custody-on-legacy")
    diagnostic_contract = None
    if v3:
        diagnostic_contract = _checked_diagnostic_contract(expected_diagnostic_contract,
            expected_context, role_contract=role_contract)
        actual_contract = _checked_diagnostic_contract(manifest.get("diagnostic_contract"),
            expected_context, role_contract=role_contract)
        require(canonical(actual_contract) == canonical(diagnostic_contract)
                and manifest.get("diagnostic_contract_sha256") == sha(canonical(diagnostic_contract)),
                "selected-diagnostic-contract-mismatch")
    else:
        require(expected_diagnostic_contract is None
                and not (DIAGNOSTIC_MANIFEST_FIELDS & set(manifest)), "diagnostic-contract-on-legacy")
    require(manifest.get("context") == expected_context, "selected-context-mismatch")
    require(manifest.get("role_contract") == _contract_rows(role_contract),
            "selected-role-contract-mismatch")
    require(manifest.get("source_bytes_bound") is True
            and manifest.get("selected_namespace_stable") is True,
            "selected-layer-incomplete")
    require(all(manifest.get(k) is False for k in (
        "complete_owner_state_claimed", "atomic_sqlite_snapshot_claimed",
        "full_original_json_claimed",
        "raw_environment_included", "automatic_retry", "physical_acceptance",
        "human_acceptance", "release_authorized")), "selected-scope-escalation")
    require(manifest.get("native_effects_invoked_by_collector") == 0
            and manifest.get("upload_authorization") == "NOT_GRANTED_BY_COLLECTOR"
            and manifest.get("lost_scope") == list(LOST_SCOPE_V3 if v3 else LOST_SCOPE),
            "selected-scope-mismatch")
    if not v3:
        require(manifest.get("raw_diagnostics_included") is False, "selected-scope-escalation")
    rows = manifest.get("members")
    require(type(rows) is list and 1 <= len(rows) <= MAX_FILES, "selected-member-list")
    counts, names = {}, set()
    json_snapshots = set()
    for row in rows:
        require(type(row) is dict and type(row.get("state")) is str
                and row["state"] in STATES, "selected-member-state")
        require(type(row.get("kind")) is str
                and (row["kind"] in ("original", "json-original", "json-projection")
                     or (v3 and row["kind"] in DIAGNOSTIC_KINDS)), "selected-member-kind")
        if not v3:
            require(row.get("kind") not in DIAGNOSTIC_KINDS and "diagnostic_type" not in row,
                    "diagnostic-member-on-legacy")
        require(type(row.get("phase")) is str and row["phase"] in PHASES
                and type(row.get("layer")) is str and row["layer"] in LAYERS
                and type(row.get("role")) is str, "selected-member-role")
        k = (row["phase"], row["layer"], row["role"])
        require(k in role_contract and type(row.get("required")) is bool, "selected-member-role")
        counts[k] = counts.get(k, 0) + 1
        require(not require_complete or not row["required"] or row["state"] == "INCLUDED",
                "selected-required-missing")
        require(not excluded(member_path(row.get("relative_path"))) or
                (row["kind"] in DIAGNOSTIC_KINDS and _diagnostic_path_allowed(row, diagnostic_contract)),
                "selected-excluded-member")
        require(type(row.get("root")) is str and LABEL.fullmatch(row["root"])
                and row["root"] != "source", "selected-member-root")
        if _json_snapshot_member(row, legacy=not v3):
            require(row["policy"] not in json_snapshots, "json-snapshot-duplicate-policy")
            json_snapshots.add(row["policy"])
            if row["state"] == "INCLUDED":
                require(type(row.get("bytes")) is int and 0 < row["bytes"] <= MAX_JSON
                        and type(row.get("retained_bytes")) is int
                        and 0 < row["retained_bytes"] <= MAX_JSON, "json-snapshot-file-budget")
        if not require_complete and row["state"] != "INCLUDED":
            require(row["state"] in ("REQUIRED_MISSING", "CAPTURE_FAILED", "CHANGED"),
                    "selected-partial-member-state")
            require(not ({"member", "retained_sha256", "retained_bytes", "original_bytes"} & set(row)),
                    "selected-partial-unadmitted-payload")
            unobserved = row.get("expected_sha256") is None and row.get("bytes") is None
            require(unobserved or (type(row.get("expected_sha256")) is str
                    and SHA.fullmatch(row["expected_sha256"])
                    and type(row.get("bytes")) is int and 0 <= row["bytes"] <= MAX_FILE),
                    "selected-partial-expected-identity")
            require(type(row.get("code")) is str and LABEL.fullmatch(row["code"]),
                    "selected-partial-failure-code")
        if row["state"] == "INCLUDED":
            member_path(row.get("member"))
            require(row["member"] not in names and SHA.fullmatch(row.get("retained_sha256", ""))
                    and type(row.get("retained_bytes")) is int and row["retained_bytes"] >= 0,
                    "selected-member-identity")
            names.add(row["member"])
            require(row.get("original_bytes") is (row.get("kind") != "json-projection"),
                    "selected-json-identity")
    for key, bounds in role_contract.items():
        require(bounds[0] <= counts.get(key, 0) <= bounds[1], "selected-role-missing")
        if bounds[0]:
            require(all(r["required"] for r in rows
                        if (r["phase"], r["layer"], r["role"]) == key),
                    "selected-required-made-optional")
    computed_layers = _layers(rows, role_contract, True)
    require(manifest.get("layers") == computed_layers,
            "selected-layer-contract")
    complete = all(layer["complete"] for layer in computed_layers)
    require(manifest.get("required_layers_complete") is complete,
            "selected-global-completeness-mismatch")
    require(not require_complete or complete, "selected-layer-incomplete")
    guards = manifest.get("absence_guards")
    require(type(guards) is list and len(guards) <= 128, "selected-absence-guards")
    guard_names = set()
    selected_names = {(x["root"], x["relative_path"]) for x in rows}
    for row in guards:
        require(type(row) is dict and set(row) == {"root", "relative_path", "phase", "layer",
                "role", "absent_before", "absent_after", "opened", "hashed"}, "selected-absence-fields")
        require(type(row["phase"]) is str and row["phase"] in PHASES
                and type(row["layer"]) is str and row["layer"] in LAYERS
                and type(row["role"]) is str and type(row["root"]) is str,
                "selected-absence-role")
        key = (row["phase"], row["layer"], row["role"])
        require(key in role_contract and role_contract[key][0] == 0, "selected-absence-role")
        name = (row["root"], row["relative_path"])
        require(name not in selected_names and name not in guard_names, "selected-absence-alias")
        guard_names.add(name)
        require((not excluded(member_path(row["relative_path"])) or
                 _diagnostic_path_allowed(row, diagnostic_contract))
                and row["absent_before"] is True and row["absent_after"] is True
                and row["opened"] is False and row["hashed"] is False, "selected-absence-changed")
    if v3:
        _diagnostic_selection(rows, guards, diagnostic_contract)
        included = any(r["kind"] in DIAGNOSTIC_KINDS and r["state"] == "INCLUDED" for r in rows)
        require(manifest.get("raw_diagnostics_included") is included
                and manifest.get("diagnostic_roster_complete") is
                    _diagnostic_complete(rows, guards, diagnostic_contract, True),
                "selected-diagnostic-completeness")
        require(all(manifest.get(k) is False for k in ("diagnostic_eof_claimed",
                "diagnostic_history_complete_claimed", "diagnostic_payloads_redacted")),
                "selected-diagnostic-scope-escalation")
    if v3 and expected_context["scenario"] == "film180-g2":
        from native_film180_command_custody import SUMMARY_SCHEMA
        summary = manifest.get("film_command_originals")
        require(type(summary) is dict and summary.get("schema") == SUMMARY_SCHEMA
                and summary.get("context") == expected_context, "film-command-summary-binding")
    else:
        require("film_command_originals" not in manifest, "film-command-summary-on-other-scope")
    for row in manifest.get("exclusions", []):
        require(row.get("state") == "EXCLUDED_BY_SCOPE"
                and excluded(member_path(row.get("relative_path")))
                and all(row.get(x) is False for x in ("opened", "hashed", "enumerated")),
                "selected-exclusion-contract")
    return manifest

def _selected_tar_framing(raw):
    """Admit only physical regular headers and one local PAX path extension.

    tarfile consumes auxiliary headers before getmembers(); checking only the
    effective pax_headers would miss empty global or GNU long-name headers.
    This bounded framing walk leaves PAX parsing and checksums to tarfile.
    """
    offset, pending, extensions = 0, None, []
    require(len(raw) % 512 == 0, "selected-tar-framing")
    while offset + 512 <= len(raw):
        header = raw[offset:offset + 512]
        if header == b"\0" * 512:
            require(pending is None and len(raw) - offset >= 1024
                    and not any(raw[offset:]), "selected-tar-footer")
            return extensions
        try:
            info = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
        except (tarfile.HeaderError, UnicodeError, ValueError):
            raise SelectionError("selected-tar-header") from None
        require(info.type in (tarfile.REGTYPE, tarfile.XHDTYPE)
                and not info.linkname, "selected-tar-physical-kind")
        if info.type == tarfile.XHDTYPE:
            require(pending is None and info.name == "././@PaxHeader"
                    and 0 < info.size <= 2048, "selected-tar-local-pax")
            pending = raw[offset + 512:offset + 512 + info.size]
        else:
            require(0 <= info.size <= MAX_FILE, "selected-tar-member-size")
            extensions.append(pending)
            require(len(extensions) <= MAX_FILES + 1, "selected-tar-count")
            pending = None
        payload_end = offset + 512 + info.size
        next_offset = offset + 512 + ((info.size + 511) // 512) * 512
        require(next_offset <= len(raw), "selected-tar-truncated")
        require(not any(raw[payload_end:next_offset]), "selected-tar-padding")
        offset = next_offset
    raise SelectionError("selected-tar-footer")


def inspect_selected_tar(raw, *, expected_context, role_contract, policies,
                         expected_diagnostic_contract=None, require_complete=True):
    """Read exact selected bytes; partial /3 custody requires an explicit opt-in.

    An independently admitted diagnostic roster is always required for /3.
    Passing require_complete=False preserves incomplete flags and failed rows;
    it does not weaken payload identities, policies or namespace/source guards.
    """
    require(type(require_complete) is bool, "selected-completeness-mode")
    expected_diagnostic_contract = (None if expected_diagnostic_contract is None
        else strict(canonical(expected_diagnostic_contract)))
    require(type(raw) is bytes and 0 < len(raw) <= MAX_TOTAL + 16 * 1024 * 1024,
            "selected-tar-budget")
    extensions = _selected_tar_framing(raw)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        infos = archive.getmembers()
        require(not archive.pax_headers and len(infos) == len(extensions),
                "selected-tar-auxiliary-headers")
        require(1 <= len(infos) <= MAX_FILES + 1, "selected-tar-count")
        names = [x.name for x in infos]
        require(len(names) == len(set(names)) and "selected-evidence-manifest.json" in names,
                "selected-tar-members")
        for info, pax_payload in zip(infos, extensions):
            extended = pax_payload is not None
            member_path(info.name)
            require(len(info.name.encode("utf-8")) <= 255, "tar-path-budget")
            require(info.type == tarfile.REGTYPE and not info.linkname
                    and not info.sparse and 0 <= info.size <= MAX_FILE
                    and info.pax_headers == ({"path": info.name} if extended else {}),
                    "selected-tar-member-kind")
            if extended:
                # tarfile folds duplicate PAX keys and ignores unparsed tails.
                # Bind the complete physical record, including its canonical
                # ASCII length, to the single admitted effective path.
                expected_record = (str(len(pax_payload)).encode("ascii")
                    + b" path=" + info.name.encode("ascii") + b"\n")
                require(pax_payload == expected_record, "selected-tar-pax-record")
        manifest_info = archive.getmember("selected-evidence-manifest.json")
        require(manifest_info.size <= MAX_JSON, "selected-manifest-budget")
        manifest = strict(archive.extractfile(manifest_info).read())
        validate_selected_manifest(manifest, expected_context=expected_context,
                                   role_contract=role_contract,
                                   expected_diagnostic_contract=expected_diagnostic_contract,
                                   require_complete=require_complete)
        if manifest["schema"] == MANIFEST_SCHEMA_V3:
            _checked_diagnostic_contract(expected_diagnostic_contract, expected_context,
                policies=policies, role_contract=role_contract)
        expected = {r["member"]: r for r in manifest["members"] if r["state"] == "INCLUDED"}
        require(set(names) == set(expected) | {"selected-evidence-manifest.json"},
                "selected-tar-unlisted-member")
        original_buffers = {}
        for name, row in expected.items():
            info = archive.getmember(name)
            require(info.size == row["retained_bytes"], "selected-tar-size")
            data = archive.extractfile(info).read()
            original_buffers[name] = data
            require(sha(data) == row["retained_sha256"], "selected-tar-digest")
            require(row["kind"] in ("original", "json-original", "json-projection")
                    or (manifest["schema"] == MANIFEST_SCHEMA_V3 and row["kind"] in DIAGNOSTIC_KINDS),
                    "selected-tar-member-kind")
            if row["kind"] != "json-projection":
                require(sha(data) == row["expected_sha256"] and len(data) == row["bytes"],
                        "selected-original-identity")
            if row["kind"] in ("json-original", "diagnostic-json-original"):
                require(row["policy"] in policies, "selected-policy-missing")
                _decode_selected_json(data, row, policies[row["policy"]],
                    legacy=manifest["schema"] == MANIFEST_SCHEMA, exact=True)
            if row["kind"] == "json-projection":
                p = strict(data)
                require(set(p) == {"schema", "policy", "source_sha256", "source_bytes",
                         "context", "phase", "role", "original_bytes_retained_locally",
                         "full_original_json_claimed", "value"}, "selected-projection-fields")
                require(row["policy"] in policies, "selected-policy-missing")
                _project_selected_json(p.get("value"), row, policies[row["policy"]],
                    legacy=manifest["schema"] == MANIFEST_SCHEMA, exact=True)
                require(p.get("schema") == PROJECTION_SCHEMA
                        and p.get("source_sha256") == row["expected_sha256"]
                        and p.get("source_bytes") == row["bytes"]
                        and p.get("context") == expected_context
                        and p.get("phase") == row["phase"] and p.get("role") == row["role"]
                        and p.get("policy") == row["policy"]
                        and p.get("original_bytes_retained_locally") is True
                        and p.get("full_original_json_claimed") is False,
                        "selected-projection-binding")
        if manifest["schema"] == MANIFEST_SCHEMA_V3 and expected_context["scenario"] == "film180-g2":
            from native_film180_command_custody import summarize
            require(manifest["film_command_originals"] == summarize(manifest["members"],
                manifest["absence_guards"], original_buffers, expected_diagnostic_contract),
                "film-command-summary-originals")
    return manifest
