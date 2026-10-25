"""Derive a bounded Cargo package inventory from captured build observations.

Pure stdlib: this module does not read paths, execute Cargo, or authenticate an
input capture. The caller must bind metadata (--locked --format-version=1
--filter-platform TARGET, with matching feature flags), build stdout and source
identity to the same invocation. JSON duplicate-key rejection belongs to that
caller because an already parsed dict cannot retain duplicate keys.

Roles are graph purposes, not an assertion that all package bytes were linked
into the executable. Cargo JSON does not identify the compilation triple of
every unit; a shared host/target library remains explicitly ambiguous.
"""
from __future__ import annotations

from collections import deque
import hashlib
import json
import math
from pathlib import PurePosixPath, PureWindowsPath
import re

MAX_PACKAGES = 8192
MAX_MESSAGES = 50000
MAX_JSON_NODES = 1000000
MAX_TOTAL_TEXT = 64 * 1024 * 1024
MAX_TEXT = 1024 * 1024
MAX_DEPTH = 32
LIBRARY_KINDS = frozenset({"lib", "rlib", "dylib", "cdylib", "staticlib"})
DEVELOPMENT_KINDS = frozenset({"test", "bench", "example"})


class InventoryError(ValueError):
    """A stable refusal code without copying arbitrary input into diagnostics."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise InventoryError(code)


def _json_bounds(value: object) -> None:
    stack = [(value, 0)]
    nodes = 0
    text_bytes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        _require(nodes <= MAX_JSON_NODES and depth <= MAX_DEPTH,
                 "input_bounds_exceeded")
        if type(item) is dict:
            _require(len(item) <= 20000, "input_bounds_exceeded")
            for key, child in item.items():
                _require(type(key) is str, "input_json_type_invalid")
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
        elif type(item) is list:
            _require(len(item) <= MAX_MESSAGES, "input_bounds_exceeded")
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            _require(len(item) <= MAX_TEXT, "input_bounds_exceeded")
            try:
                text_bytes += len(item.encode("utf-8"))
            except UnicodeEncodeError as error:
                raise InventoryError("input_text_invalid") from error
            _require(text_bytes <= MAX_TOTAL_TEXT, "input_bounds_exceeded")
        elif type(item) is float:
            _require(math.isfinite(item), "input_json_type_invalid")
        else:
            _require(item is None or type(item) in (bool, int),
                     "input_json_type_invalid")


def _object(value: object, code: str) -> dict:
    _require(type(value) is dict, code)
    return value


def _keys(value: dict, required: tuple[str, ...], code: str) -> None:
    _require(all(key in value for key in required), code)


def _text(value: object, code: str, *, empty: bool = False,
          maximum: int = 32768) -> str:
    _require(type(value) is str and len(value) <= maximum
             and "\x00" not in value and (empty or bool(value.strip())), code)
    return value


def _texts(value: object, code: str, *, maximum: int = 8192,
           nonempty: bool = False, unique: bool = True) -> list[str]:
    _require(type(value) is list and len(value) <= maximum
             and (not nonempty or bool(value)), code)
    result = [_text(item, code) for item in value]
    if unique:
        _require(len(result) == len(set(result)), code)
    return result


def _path(value: object, code: str) -> str:
    text = _text(value, code)
    _require("\r" not in text and "\n" not in text, code)
    windows = PureWindowsPath(text)
    posix = PurePosixPath(text)
    _require(windows.is_absolute() or posix.is_absolute(), code)
    parts = windows.parts if windows.is_absolute() else posix.parts
    _require(".." not in parts, code)
    return text


def _path_key(value: str) -> str:
    # Separator normalization only. Do not assume case-insensitive directories,
    # follow symlinks, infer host/target context, or read an arbitrary filename.
    return value.replace("\\", "/")


def _target(value: object) -> dict:
    value = _object(value, "target_invalid")
    _keys(value, ("name", "kind", "crate_types", "src_path"), "target_missing_field")
    result = {
        "name": _text(value["name"], "target_invalid", maximum=512),
        "kind": sorted(_texts(value["kind"], "target_invalid", maximum=16,
                              nonempty=True)),
        "crate_types": sorted(_texts(value["crate_types"], "target_invalid",
                                     maximum=16, nonempty=True)),
        "src_path": _path(value["src_path"], "target_invalid"),
    }
    if "edition" in value:
        result["edition"] = _text(value["edition"], "target_invalid", maximum=16)
    _target_category(result)
    return result


def _target_key(target: dict) -> tuple:
    return (target["name"], tuple(target["kind"]), tuple(target["crate_types"]),
            _path_key(target["src_path"]), target.get("edition"))


def _target_category(target: dict) -> str:
    kinds = set(target["kind"])
    if kinds == {"custom-build"}:
        return "build-script"
    if kinds == {"proc-macro"}:
        _require(set(target["crate_types"]) == {"proc-macro"},
                 "proc_macro_target_inconsistent")
        return "proc-macro"
    if kinds == {"bin"}:
        return "bin"
    if kinds and kinds <= LIBRARY_KINDS:
        _require("proc-macro" not in target["crate_types"],
                 "proc_macro_target_inconsistent")
        return "library"
    if len(kinds) == 1 and kinds <= DEVELOPMENT_KINDS:
        return "development"
    raise InventoryError("target_kind_unsupported")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("ascii")).hexdigest()


def _metadata(metadata: dict, root_package_name: str) -> tuple:
    metadata = _object(metadata, "metadata_invalid")
    _keys(metadata, ("version", "packages", "workspace_members", "resolve"),
          "metadata_missing_field")
    _require(type(metadata["version"]) is int and metadata["version"] == 1,
             "metadata_version_invalid")
    raw_packages = metadata["packages"]
    _require(type(raw_packages) is list
             and 0 < len(raw_packages) <= MAX_PACKAGES, "package_count_invalid")
    packages = {}
    for raw in raw_packages:
        raw = _object(raw, "package_invalid")
        _keys(raw, ("id", "name", "version", "source", "manifest_path",
                    "license", "license_file", "targets"), "package_missing_field")
        package_id = _text(raw["id"], "package_identity_invalid")
        _require(package_id not in packages, "duplicate_package_id")
        source = raw["source"]
        license_expression = raw["license"]
        license_file = raw["license_file"]
        for value in (source, license_expression, license_file):
            if value is not None:
                _text(value, "package_identity_invalid")
        _require(type(raw["targets"]) is list and 0 < len(raw["targets"]) <= 128,
                 "package_targets_invalid")
        targets = [_target(target) for target in raw["targets"]]
        target_keys = [_target_key(target) for target in targets]
        _require(len(target_keys) == len(set(target_keys)), "duplicate_target_identity")
        packages[package_id] = {
            "id": package_id,
            "name": _text(raw["name"], "package_identity_invalid", maximum=512),
            "version": _text(raw["version"], "package_identity_invalid", maximum=512),
            "source": source,
            "manifest_path": _path(raw["manifest_path"], "package_identity_invalid"),
            "license": license_expression,
            "license_file": license_file,
            "targets": targets,
            "target_keys": set(target_keys),
            "has_proc_macro": any(_target_category(t) == "proc-macro" for t in targets),
            "has_build_script": any(_target_category(t) == "build-script" for t in targets),
        }
    members = _texts(metadata["workspace_members"], "workspace_members_invalid",
                     maximum=MAX_PACKAGES, nonempty=True)
    _require(all(member in packages for member in members),
             "workspace_package_missing")
    roots = [member for member in members
             if packages[member]["name"] == root_package_name]
    _require(len(roots) == 1, "root_package_not_unique")
    root_id = roots[0]
    _require(any(_target_category(t) == "bin" for t in packages[root_id]["targets"]),
             "root_binary_target_missing")

    resolve = _object(metadata["resolve"], "resolve_missing")
    _keys(resolve, ("nodes", "root"), "resolve_missing_field")
    _require(resolve["root"] is None or resolve["root"] == root_id,
             "resolve_root_mismatch")
    raw_nodes = resolve["nodes"]
    _require(type(raw_nodes) is list and 0 < len(raw_nodes) <= MAX_PACKAGES,
             "resolve_nodes_invalid")
    nodes = {}
    for raw in raw_nodes:
        raw = _object(raw, "resolve_node_invalid")
        _keys(raw, ("id", "dependencies", "deps", "features"),
              "resolve_node_missing_field")
        node_id = _text(raw["id"], "resolve_node_invalid")
        _require(node_id in packages, "resolve_package_missing")
        _require(node_id not in nodes, "duplicate_resolve_id")
        dependencies = _texts(raw["dependencies"], "resolve_dependencies_invalid",
                              maximum=MAX_PACKAGES)
        features = _texts(raw["features"], "resolve_features_invalid")
        _require(type(raw["deps"]) is list and len(raw["deps"]) <= MAX_PACKAGES,
                 "resolve_dependencies_invalid")
        edges = []
        seen_edges = set()
        for dep in raw["deps"]:
            dep = _object(dep, "resolve_dependency_invalid")
            _keys(dep, ("name", "pkg", "dep_kinds"), "resolve_dependency_missing_field")
            name = _text(dep["name"], "resolve_dependency_invalid", maximum=512)
            dep_id = _text(dep["pkg"], "resolve_dependency_invalid")
            _require(dep_id in packages, "resolve_package_missing")
            edge_key = (name, dep_id)
            _require(edge_key not in seen_edges, "duplicate_dependency_identity")
            seen_edges.add(edge_key)
            kinds = dep["dep_kinds"]
            _require(type(kinds) is list and 0 < len(kinds) <= 128,
                     "dependency_kinds_invalid")
            seen_kinds = set()
            for descriptor in kinds:
                descriptor = _object(descriptor, "dependency_kind_invalid")
                _keys(descriptor, ("kind", "target"), "dependency_kind_missing_field")
                kind = descriptor["kind"]
                _require(kind is None or (type(kind) is str
                         and kind in ("normal", "build", "dev")),
                         "dependency_kind_invalid")
                kind = "normal" if kind is None else kind
                predicate = descriptor["target"]
                if predicate is not None:
                    _text(predicate, "dependency_target_invalid")
                identity = (kind, predicate)
                _require(identity not in seen_kinds, "duplicate_dependency_kind")
                seen_kinds.add(identity)
                _require(dep_id != node_id or kind == "dev", "resolve_cycle")
                edges.append({"name": name, "package_id": dep_id,
                              "kind": kind, "target_predicate": predicate})
        _require(set(dependencies) == {edge["package_id"] for edge in edges},
                 "resolve_dependencies_disagree")
        nodes[node_id] = {"edges": edges, "features": set(features)}
    _require(root_id in nodes, "resolve_root_missing")
    _require(all(edge["package_id"] in nodes for node in nodes.values()
                 for edge in node["edges"]), "resolve_node_missing")
    return packages, nodes, root_id


def _contexts(packages: dict, nodes: dict, root_id: str) -> dict:
    contexts = {}
    queue = deque([(root_id, "runtime")])
    while queue:
        package_id, context = queue.popleft()
        current = contexts.setdefault(package_id, set())
        if context in current:
            continue
        current.add(context)
        for edge in nodes[package_id]["edges"]:
            if edge["kind"] == "dev":
                continue
            child = edge["package_id"]
            if edge["kind"] == "build":
                next_context = "build"
            elif context == "runtime" and packages[child]["has_proc_macro"]:
                next_context = "proc-macro-support"
            else:
                next_context = context
            queue.append((child, next_context))
    # Cargo's admitted non-dev package graph must not contain a cycle. Dev
    # cycles can occur in normal metadata and are deliberately not traversed.
    indegree = dict.fromkeys(contexts, 0)
    outgoing = {package_id: set() for package_id in contexts}
    for package_id in contexts:
        for edge in nodes[package_id]["edges"]:
            child = edge["package_id"]
            if edge["kind"] != "dev" and child in contexts:
                outgoing[package_id].add(child)
        for child in outgoing[package_id]:
            indegree[child] += 1
    ready = deque(pid for pid, degree in indegree.items() if degree == 0)
    visited = 0
    while ready:
        package_id = ready.popleft()
        visited += 1
        for child in outgoing[package_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    _require(visited == len(contexts), "resolve_cycle")
    return contexts



def _feature_program(feature_map, seeds, aliases):
    enabled, forwarded, seen, pending = set(), {}, set(), list(seeds)
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        _require(name in feature_map, "artifact_feature_not_declared")
        seen.add(name)
        for token in feature_map[name]:
            if token.startswith("dep:"):
                alias = token[4:]
                _require(alias in aliases, "feature_dependency_alias_unknown")
                enabled.add(alias)
            elif "/" in token:
                alias, child_feature = token.split("/", 1)
                weak = alias.endswith("?")
                if weak:
                    alias = alias[:-1]
                _require(alias in aliases and bool(child_feature)
                         and "/" not in child_feature,
                         "feature_dependency_reference_invalid")
                if not weak:
                    enabled.add(alias)
                forwarded.setdefault(alias, set()).add(child_feature)
            else:
                _require(token in feature_map, "local_feature_reference_unknown")
                pending.append(token)
    return enabled, forwarded


def _dependency_source_key(source):
    if source is None:
        return None
    _text(source, "dependency_source_invalid")
    return source.split("#", 1)[0] if source.startswith("git+") else source


def _feature_declarations(metadata):
    declarations, feature_maps = {}, {}
    for raw in metadata["packages"]:
        package_id = raw["id"]
        _keys(raw, ("dependencies", "features"), "feature_metadata_missing_field")
        feature_map = _object(raw["features"], "feature_map_invalid")
        _require(len(feature_map) <= 4096, "feature_map_invalid")
        feature_maps[package_id] = {
            _text(name, "feature_name_invalid", maximum=512):
            _texts(tokens, "feature_program_invalid", maximum=4096, unique=False)
            for name, tokens in feature_map.items()
        }
        _require(type(raw["dependencies"]) is list
                 and len(raw["dependencies"]) <= MAX_PACKAGES,
                 "declared_dependencies_invalid")
        declared = []
        for dep in raw["dependencies"]:
            dep = _object(dep, "declared_dependency_invalid")
            _keys(dep, ("name", "source", "kind", "rename", "optional",
                        "uses_default_features", "features", "target"),
                  "declared_dependency_missing_field")
            name = _text(dep["name"], "declared_dependency_invalid", maximum=512)
            rename = dep["rename"]
            if rename is not None:
                _text(rename, "dependency_rename_invalid", maximum=512)
            kind = dep["kind"]
            _require(kind is None or (type(kind) is str
                     and kind in ("normal", "build", "dev")), "dependency_kind_invalid")
            predicate = dep["target"]
            if predicate is not None:
                _text(predicate, "dependency_target_invalid")
            for key in ("optional", "uses_default_features"):
                _require(type(dep[key]) is bool, "declared_dependency_invalid")
            declared.append({
                "name": name, "alias": rename if rename is not None else name,
                "rename": rename, "kind": "normal" if kind is None else kind,
                "target_predicate": predicate,
                "declared_source": dep["source"],
                "source_key": _dependency_source_key(dep["source"]),
                "optional": dep["optional"],
                "uses_default_features": dep["uses_default_features"],
                "features": set(_texts(dep["features"],
                                       "dependency_features_invalid", unique=False)),
            })
        declarations[package_id] = declared
    return declarations, feature_maps


def _matching_feature_declaration(edge, child, declarations):
    matches = []
    for declaration in declarations:
        if (declaration["name"] != child["name"]
                or declaration["kind"] != edge["kind"]
                or declaration["target_predicate"] != edge["target_predicate"]):
            continue
        if declaration["rename"] is not None:
            names = {declaration["alias"].replace("-", "_")}
        else:
            names = {declaration["name"].replace("-", "_")}
            names.update(t["name"].replace("-", "_") for t in child["targets"]
                         if _target_category(t) in ("library", "proc-macro"))
        if edge["name"].replace("-", "_") in names:
            matches.append(declaration)
    _require(len(matches) == 1, "resolve_declaration_not_unique")
    return matches[0]


def _observed_feature_graph(metadata, packages, nodes, root_id, messages):
    declarations, feature_maps = _feature_declarations(metadata)
    vectors = {}
    for message in messages[:-1]:
        if message["reason"] != "compiler-artifact":
            continue
        package_id = _text(message.get("package_id"), "message_package_id_invalid")
        _require(package_id in packages and package_id in nodes,
                 "observed_package_identity_unknown")
        target = _target(message.get("target"))
        _require(_target_key(target) in packages[package_id]["target_keys"],
                 "observed_target_identity_mismatch")
        features = tuple(sorted(_texts(message.get("features"), "artifact_features_invalid")))
        _require(set(features) <= nodes[package_id]["features"],
                 "artifact_features_outside_metadata")
        if _target_category(target) in ("library", "proc-macro", "bin"):
            vectors.setdefault(package_id, set()).add(features)
    programs = {}
    for package_id, feature_sets in vectors.items():
        aliases = {d["alias"] for d in declarations[package_id]}
        programs[package_id] = [
            (features, *_feature_program(feature_maps[package_id], features, aliases))
            for features in sorted(feature_sets)
        ]
    filtered = {pid: {**node, "edges": []} for pid, node in nodes.items()}
    conditional, evidence, source_resolutions = {}, [], []
    for parent_id, node in nodes.items():
        matched = []
        parent_programs = programs.get(parent_id, [])
        for edge in node["edges"]:
            if edge["kind"] == "dev":
                continue
            child_id = edge["package_id"]
            declaration = _matching_feature_declaration(
                edge, packages[child_id], declarations[parent_id])
            matched.append(declaration)
            alias = declaration["alias"]
            decisions = [not declaration["optional"] or alias in enabled
                         for _, enabled, _ in parent_programs]
            active = not declaration["optional"] or any(decisions)
            source_resolutions.append({
                "parent_id": parent_id, "package_id": child_id,
                "dependency_alias": alias, "kind": edge["kind"],
                "target_predicate": edge["target_predicate"],
                "declared_source": declaration["declared_source"],
                "effective_source": packages[child_id]["source"],
                "resolution_origin": "CARGO_RESOLVE_GRAPH",
                "source_authentication": "CALLER_REQUIRED",
                "emission_evidence": "RESOLVED_EDGE_ONLY",
                "active_in_observed_feature_graph": active,
            })
            if declaration["optional"]:
                evidence.append({
                    "parent_id": parent_id, "package_id": child_id,
                    "dependency_alias": alias, "kind": edge["kind"],
                    "target_predicate": edge["target_predicate"],
                    "observed_feature_sets": [list(p[0]) for p in parent_programs],
                    "active_in_observed_feature_sets": decisions,
                    "status": ("ADMITTED" if active else
                               "INACTIVE_IN_OBSERVED_UNITS" if parent_programs else
                               "NO_PRIMARY_UNIT_OBSERVED"),
                })
            if not active:
                continue
            index = len(filtered[parent_id]["edges"])
            filtered[parent_id]["edges"].append(edge)
            conditional[(parent_id, index)] = declaration["optional"] and not all(decisions)
            child_vectors = vectors.get(child_id, set())
            if child_vectors and parent_programs:
                for _, enabled, forwarded in parent_programs:
                    if declaration["optional"] and alias not in enabled:
                        continue
                    requested = declaration["features"] | forwarded.get(alias, set())
                    if (declaration["uses_default_features"]
                            and "default" in feature_maps[child_id]):
                        requested = requested | {"default"}
                    _require(requested <= set(feature_maps[child_id]),
                             "requested_dependency_feature_not_declared")
                    _require(any(requested <= set(v) for v in child_vectors),
                             "dependency_feature_vector_not_observed")
        for declaration in declarations[parent_id]:
            if declaration["kind"] == "dev" or declaration["target_predicate"] is not None:
                continue
            active = not declaration["optional"] or any(
                declaration["alias"] in enabled for _, enabled, _ in parent_programs)
            _require(not active or declaration in matched,
                     "active_dependency_missing_from_resolve")
    possible = _contexts(packages, filtered, root_id)
    certain_nodes = {pid: {**node, "edges": [
        edge for index, edge in enumerate(node["edges"])
        if not conditional.get((pid, index), False)
        or len(possible.get(pid, set())) == 1
    ]} for pid, node in filtered.items()}
    certain = _contexts(packages, certain_nodes, root_id)
    for record in evidence:
        parent_contexts = possible.get(record["parent_id"], set())
        record["parent_possible_contexts"] = sorted(parent_contexts)
        record["context_assignment"] = (
            "NOT_DETERMINABLE_FROM_CARGO_JSON"
            if record["status"] == "ADMITTED" and len(parent_contexts) > 1
            and not all(record["active_in_observed_feature_sets"])
            else "CONSISTENT_WITH_OBSERVED_FEATURE_SETS")
    return possible, certain, evidence, source_resolutions


def _context_roles(contexts):
    roles = set()
    if "runtime" in contexts:
        roles.update(("runtime", "target"))
    if "build" in contexts:
        roles.update(("build", "host"))
    if "proc-macro-support" in contexts:
        roles.add("host")
    return roles


def _annotate_feature_context(unit, category, certain_contexts):
    candidates = set(unit["roles"])
    unit["possible_roles"] = sorted(candidates)
    if category == "library":
        mixed_sides = {"host", "target"} <= candidates
        confirmed = set() if mixed_sides else _context_roles(certain_contexts)
        unit["roles"] = sorted(candidates & confirmed)
    elif category == "proc-macro" and "build" not in certain_contexts:
        unit["roles"] = sorted(candidates - {"build"})
    sides = set(unit["roles"]) & {"host", "target"}
    unit["execution_context"] = (next(iter(sides)) if len(sides) == 1
                                 else "NOT_DETERMINABLE_FROM_CARGO_JSON")
    unit["feature_context_assignment"] = (
        "CONFIRMED_CONTEXT" if len(sides) == 1
        else "NOT_DETERMINABLE_FROM_CARGO_JSON")


def _profile(value: object) -> dict:
    value = _object(value, "profile_invalid")
    _keys(value, ("opt_level", "debuginfo", "debug_assertions",
                  "overflow_checks", "test"), "profile_missing_field")
    _text(value["opt_level"], "profile_invalid", maximum=16)
    debug = value["debuginfo"]
    _require(debug is None or type(debug) in (str, int), "profile_invalid")
    for field in ("debug_assertions", "overflow_checks", "test"):
        _require(type(value[field]) is bool, "profile_invalid")
    _require(value["test"] is False, "development_unit_forbidden")
    return json.loads(_canonical(value))


def _unit_roles(package: dict, contexts: set[str], category: str,
                root_id: str) -> tuple[list[str], str]:
    roles = set()
    if category == "build-script":
        roles.update(("host", "build"))
    elif category == "proc-macro":
        roles.update(("host", "proc-macro"))
        if "build" in contexts:
            roles.add("build")
    elif category == "bin":
        _require(package["id"] == root_id, "foreign_binary_unit")
        roles.update(("target", "runtime"))
    elif category == "library":
        if "runtime" in contexts:
            roles.update(("target", "runtime"))
        if "build" in contexts:
            roles.update(("host", "build"))
        if "proc-macro-support" in contexts:
            roles.add("host")
    else:
        raise InventoryError("development_unit_forbidden")
    _require(bool(roles), "unit_role_missing")
    sides = roles & {"host", "target"}
    context = (next(iter(sides)) if len(sides) == 1
               else "NOT_DETERMINABLE_FROM_CARGO_JSON")
    return sorted(roles), context


def derive_components(metadata: dict, messages: list[dict], *,
                      root_package_name: str, target_triple: str) -> dict:
    """Return only observed packages; refuse missing runtime observations.

    An observed runtime package means that its library/root binary artifact
    appeared in this successful build stream. It does not prove that every
    module survived linking, or prove a unit's exact compilation triple when
    Cargo emits no such field. Build-script output cannot stand in for that
    runtime artifact. Unobserved host graph entries are reported separately,
    never manufactured as components.
    """
    _require(type(messages) is list and 0 < len(messages) <= MAX_MESSAGES,
             "messages_invalid")
    _json_bounds([metadata, messages])
    _text(root_package_name, "root_package_name_invalid", maximum=512)
    _require(type(target_triple) is str
             and re.fullmatch(r"[A-Za-z0-9_.+-]{1,128}", target_triple) is not None,
             "target_triple_invalid")
    packages, nodes, root_id = _metadata(metadata, root_package_name)
    for message in messages:
        _object(message, "message_invalid")
        _text(message.get("reason"), "message_reason_invalid", maximum=64)
    terminals = [i for i, message in enumerate(messages)
                 if message["reason"] == "build-finished"]
    _require(len(terminals) == 1 and terminals[0] == len(messages) - 1,
             "build_finished_not_unique_last")
    _require(type(messages[-1].get("success")) is bool, "build_success_invalid")
    _require(messages[-1]["success"], "build_failed")
    contexts, certain_contexts, feature_admission, source_resolutions = _observed_feature_graph(
        metadata, packages, nodes, root_id, messages)

    by_package = {}
    unit_keys = set()
    artifact_owners = {}
    script_keys = set()
    runtime_observed = set()
    root_binaries = []
    native_links = []
    compiler_unit_count = 0
    for index, message in enumerate(messages[:-1]):
        reason = message["reason"]
        _require(reason in ("compiler-artifact", "compiler-message",
                            "build-script-executed"), "message_reason_unsupported")
        package_id = _text(message.get("package_id"), "message_package_id_invalid")
        _require(package_id in packages, "observed_package_identity_unknown")
        _require(package_id in contexts, "observed_package_outside_admitted_graph")
        package = packages[package_id]
        if reason == "compiler-message":
            target = _target(message.get("target"))
            _require(_target_key(target) in package["target_keys"],
                     "observed_target_identity_mismatch")
            # Diagnostics are never counted as compiled units or components.
            continue
        if reason == "build-script-executed":
            _require(package["has_build_script"], "build_script_target_missing")
            _keys(message, ("linked_libs", "linked_paths", "cfgs", "env", "out_dir"),
                  "build_script_missing_field")
            linked_libs = _texts(message["linked_libs"], "native_links_invalid",
                                 maximum=4096, unique=False)
            linked_paths = _texts(message["linked_paths"], "native_links_invalid",
                                  maximum=4096, unique=False)
            cfgs = _texts(message["cfgs"], "build_script_cfgs_invalid",
                          maximum=4096, unique=False)
            _require(type(message["env"]) is list and len(message["env"]) <= 4096,
                     "build_script_env_invalid")
            for pair in message["env"]:
                _require(type(pair) is list and len(pair) == 2,
                         "build_script_env_invalid")
                _text(pair[0], "build_script_env_invalid")
                _text(pair[1], "build_script_env_invalid", empty=True)
            out_dir = _path(message["out_dir"], "build_script_out_dir_invalid")
            key = (package_id, _path_key(out_dir))
            _require(key not in script_keys, "duplicate_build_script_observation")
            script_keys.add(key)
            observation = {
                "message_index": index, "package_id": package_id,
                "linked_libs": list(linked_libs), "linked_paths": list(linked_paths),
                "cfgs": list(cfgs), "out_dir": out_dir,
                "evidence": "CARGO_BUILD_SCRIPT_OUTPUT",
                "native_library_identity": "NOT_VERIFIED",
                "native_library_license": "NOT_VERIFIED",
                "compilation_context": "NOT_DETERMINABLE_FROM_CARGO_JSON",
            }
            native_links.append(observation)
            by_package.setdefault(package_id, []).append({
                "kind": reason, "message_index": index,
                "roles": ["build", "host"], "features": None,
                "execution_context": "host",
                "target_compilation_context": "NOT_DETERMINABLE_FROM_CARGO_JSON",
                "native_link_observation_index": len(native_links) - 1,
            })
            continue

        _keys(message, ("manifest_path", "target", "profile", "features",
                        "filenames", "executable", "fresh"), "artifact_missing_field")
        manifest = _path(message["manifest_path"], "artifact_manifest_invalid")
        _require(_path_key(manifest) == _path_key(package["manifest_path"]),
                 "artifact_manifest_identity_mismatch")
        target = _target(message["target"])
        _require(_target_key(target) in package["target_keys"],
                 "observed_target_identity_mismatch")
        category = _target_category(target)
        _require(category != "development", "development_unit_forbidden")
        profile = _profile(message["profile"])
        features = sorted(_texts(message["features"], "artifact_features_invalid"))
        _require(set(features) <= nodes[package_id]["features"],
                 "artifact_features_outside_metadata")
        filenames = _texts(message["filenames"], "artifact_filenames_invalid",
                           maximum=128, nonempty=True)
        filenames = [_path(filename, "artifact_filenames_invalid") for filename in filenames]
        filename_keys = [_path_key(filename) for filename in filenames]
        _require(len(filename_keys) == len(set(filename_keys)),
                 "artifact_filenames_invalid")
        executable = message["executable"]
        if executable is not None:
            executable = _path(executable, "artifact_executable_invalid")
            _require(_path_key(executable) in filename_keys,
                     "executable_not_in_artifact_filenames")
        _require(type(message["fresh"]) is bool, "artifact_fresh_invalid")
        roles, execution_context = _unit_roles(
            package, contexts[package_id], category, root_id)
        identity = {
            "package_id": package_id, "target": target, "profile": profile,
            "features": features, "filenames": list(filenames), "executable": executable,
        }
        unit_id = _digest(identity)
        _require(unit_id not in unit_keys, "duplicate_compiler_unit")
        unit_keys.add(unit_id)
        for filename in filename_keys:
            _require(filename not in artifact_owners,
                     "artifact_filename_identity_collision")
            artifact_owners[filename] = unit_id
        unit = {
            **identity, "kind": reason, "unit_id": unit_id, "message_index": index,
            "fresh": message["fresh"], "roles": roles,
            "execution_context": execution_context,
        }
        _annotate_feature_context(
            unit, category, certain_contexts.get(package_id, set()))
        by_package.setdefault(package_id, []).append(unit)
        compiler_unit_count += 1
        if category in ("library", "bin") and "runtime" in contexts[package_id]:
            runtime_observed.add(package_id)
        if package_id == root_id and category == "bin":
            _require(executable is not None, "root_binary_executable_missing")
            root_binaries.append({"unit_id": unit_id, "name": target["name"],
                                  "executable": executable})

    _require(bool(root_binaries), "root_binary_not_observed")
    runtime_expected = {pid for pid, purpose in contexts.items() if "runtime" in purpose}
    _require(runtime_expected <= runtime_observed, "runtime_package_not_observed")
    components = []
    for package_id in sorted(by_package):
        package = packages[package_id]
        units = by_package[package_id]
        roles = sorted(_context_roles(certain_contexts.get(package_id, set()))
                       | {role for unit in units for role in unit["roles"]})
        possible_roles = sorted(_context_roles(contexts[package_id])
                                | {role for unit in units
                                   for role in unit.get("possible_roles", unit["roles"])})
        components.append({
            key: package[key] for key in
            ("id", "name", "version", "source", "manifest_path", "license", "license_file")
        } | {
            "roles": roles, "possible_roles": possible_roles,
            "graph_contexts": sorted(certain_contexts.get(package_id, set())),
            "possible_graph_contexts": sorted(contexts[package_id]),
            "observed_units": units, "license_evidence": "METADATA_DECLARATION_ONLY",
        })
    declared_development_targets = [
        {"package_id": pid, "name": target["name"], "kind": target["kind"]}
        for pid in sorted(contexts) for target in packages[pid]["targets"]
        if _target_category(target) == "development"
    ]
    warnings = [
        "Cargo metadata and lock membership do not prove emitted package contents.",
        "Exact per-unit compilation triples are not present in this Cargo JSON contract.",
        "The caller must authenticate same-source, same-feature, target-filtered captures.",
        "Cargo-resolved source replacements do not authenticate patch configuration or source bytes.",
        "Native linked libraries need separate identity and license evidence.",
        "Optional edges use observed unit features; workspace resolve features are only an upper bound.",
        "possible_roles do not assign an ambiguous feature vector to a particular context.",
        "cfg admission relies on authenticated target-filtered metadata, not on output paths.",
    ]
    if declared_development_targets:
        warnings.append(
            "Metadata declares examples/tests/benches that were not counted as build units.")
    return {
        "schema": "motionwright.cargo-build-inventory/1",
        "root_package_id": root_id, "root_package_name": root_package_name,
        "target_triple": target_triple,
        "target_triple_evidence": "CALLER_CAPTURE_CONTRACT_NOT_PER_UNIT_JSON",
        "build_finished_success": True,
        "root_binary_observations": root_binaries,
        "components": components,
        "compiler_unit_count": compiler_unit_count,
        "native_link_observations": native_links,
        "runtime_package_observations": "COMPLETE_FOR_POSSIBLE_RUNTIME_CLOSURE",
        "feature_admission": feature_admission,
        "source_resolutions": source_resolutions,
        "unit_context_evidence": "EXPLICIT_TARGET_KIND_OR_UNAMBIGUOUS_GRAPH_CONTEXT",
        "unobserved_host_graph_packages": [
            {"id": pid, "graph_contexts": sorted(contexts[pid]), "status": "NOT_OBSERVED"}
            for pid in sorted(contexts) if pid not in by_package
        ],
        "metadata_only_development_targets": declared_development_targets,
        "full_package_inventory": "NOT_VERIFIED",
        "final_linked_code_inventory": "NOT_VERIFIED",
        "license_rights": "NOT_ASSESSED",
        "warnings": warnings,
    }
