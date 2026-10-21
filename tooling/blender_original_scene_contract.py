"""Read-only contracts for custody of an original Blender Scene.

These helpers validate supplied source bytes and typed SDK getter payloads.
They do not execute a driver, confer authority, or attest native/human acceptance.
The caller retains the original Broker requests, receipts and process bindings.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping, Sequence
from uuid import UUID

PLAN_SCHEMA = "motionwright.blender.original-scene-plan/1"
ROOTS = ("scenes", "collections", "objects", "cameras", "materials")
PREFIX = "motionwright."
RNA_FLOAT_ABS_TOLERANCE = 1e-6
MAX_ENTITIES = 2048
MAX_CUSTODY_BYTES = 512 * 1024 * 1024


class SceneContractError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SceneContractError(message)


def _integer(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _uuid(value: Any) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _name(value: Any) -> bool:
    return (isinstance(value, str) and 0 < len(value.encode("utf-8")) <= 63
            and all(ord(char) >= 32 and ord(char) != 127 for char in value))


def expected_identity_table(plan: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, str]]:
    """Reconstruct expected identities from source fields, never from native names."""
    _require(plan.get("schema") == PLAN_SCHEMA, "wrong source-plan schema")
    _require(_sha(plan.get("project_json_sha256")), "invalid Project source digest")
    contribution = plan.get("contribution")
    camera = plan.get("camera")
    _require(isinstance(contribution, dict) and isinstance(camera, dict), "missing source plan")
    for key in ("project_id", "generation", "scene_id"):
        _require(_uuid(contribution.get(key)), "invalid complete source UUID: " + key)
    _require(_integer(contribution.get("revision")), "invalid source revision")
    objects = contribution.get("objects")
    _require(isinstance(objects, list) and 0 < len(objects) <= 512, "invalid source object count")
    base = {
        PREFIX + "project_id": contribution["project_id"],
        PREFIX + "generation": contribution["generation"],
        PREFIX + "scene_id": contribution["scene_id"],
        PREFIX + "revision": str(contribution["revision"]),
        PREFIX + "project_json_sha256": plan["project_json_sha256"],
    }
    table: dict[tuple[str, str], dict[str, str]] = {}

    def add(root: str, name: Any, role: str, node_id: str | None = None) -> None:
        _require(_name(name), "invalid bounded native entity name")
        key = (root, name)
        _require(key not in table, "ambiguous native entity identity")
        properties = {**base, PREFIX + "role": role}
        if node_id is not None:
            properties[PREFIX + "node_id"] = node_id
        table[key] = properties

    add("scenes", plan.get("native_scene_name"), "native_scene")
    add("collections", contribution.get("collection_name"), "scene_collection")
    add("objects", camera.get("object_name"), "scene_camera")
    add("cameras", camera.get("data_name"), "scene_camera_data")
    node_ids: set[str] = set()
    for obj in objects:
        _require(isinstance(obj, dict) and _uuid(obj.get("node_id")), "invalid source node UUID")
        node_id = obj["node_id"]
        _require(node_id not in node_ids, "duplicate complete source node UUID")
        node_ids.add(node_id)
        add("objects", obj.get("name"), "canvas_object", node_id)
        add("materials", obj.get("material_name"), "canvas_material", node_id)
    identities = plan.get("identities")
    _require(isinstance(identities, list), "missing emitted source identity table")
    actual: dict[tuple[str, str], dict[str, str]] = {}
    for item in identities:
        _require(isinstance(item, dict) and set(item) == {"root", "name", "properties"},
                 "invalid source identity entry")
        _require(isinstance(item["root"], str) and isinstance(item["name"], str)
                 and isinstance(item["properties"], dict), "invalid emitted identity types")
        key = (item["root"], item["name"])
        _require(key not in actual, "duplicate emitted identity entry")
        actual[key] = item["properties"]
    _require(actual == table, "emitted identity table disagrees with canonical source fields")
    return table


def _pages(pages: Any, generation: int, *, reference: str | None = None) -> list[dict[str, Any]]:
    _require(isinstance(pages, list) and 0 < len(pages) <= 16, "missing/bounded getter pages")
    offset = 0
    output: list[dict[str, Any]] = []
    for index, page in enumerate(pages):
        _require(isinstance(page, dict), "invalid getter page")
        _require(type(page.get("generation")) is int and page["generation"] == generation,
                 "native semantic generation changed during readback")
        _require(type(page.get("offset")) is int and page["offset"] == offset,
                 "getter pagination is incomplete or reordered")
        _require(type(page.get("truncated")) is bool, "missing pagination terminal")
        if reference is not None:
            _require(page.get("ref") == reference, "custom getter belongs to another native entity")
        items = page.get("items")
        _require(isinstance(items, list) and len(items) <= 256
                 and all(isinstance(item, dict) for item in items), "invalid getter page items")
        _require(page["truncated"] == (index < len(pages) - 1),
                 "truncated, trailing, or incomplete getter pages")
        _require(not page["truncated"] or bool(items), "nonadvancing getter page")
        output.extend(items)
        offset += len(items)
        _require(offset <= MAX_ENTITIES, "getter inventory exceeds the source profile")
    return output


def check_identity_readback(
    plan: Mapping[str, Any],
    generation: int,
    inventories: Mapping[str, Any],
    custom_lists: Mapping[str, Any],
) -> dict[str, Any]:
    """Check complete root inventories and actual semantic.custom.list outputs.

    Inventory must cover every entity in each of the five roots. Unmarked
    runtime defaults are retained as extras; a marked unexpected entity fails.
    The result is a data binding, not a native execution receipt.
    """
    expected = expected_identity_table(plan)
    _require(_integer(generation, 1), "invalid semantic generation")
    _require(set(inventories) == set(ROOTS), "missing or unexpected native root inventory")
    refs: dict[tuple[str, str], str] = {}
    all_refs: set[str] = set()
    for root in ROOTS:
        for item in _pages(inventories[root], generation):
            name, ref = item.get("name"), item.get("ref")
            _require(isinstance(name, str) and isinstance(ref, str) and bool(ref),
                     "native inventory omitted name or reference")
            _require((root, name) not in refs and ref not in all_refs, "duplicate native entity")
            refs[(root, name)] = ref
            all_refs.add(ref)
    _require(len(all_refs) <= MAX_ENTITIES, "combined native inventory exceeds profile")
    _require(set(custom_lists) == all_refs, "custom-property inventory is not exhaustive")
    _require(set(expected).issubset(refs), "an expected original entity is missing")
    extras = []
    for key, reference in refs.items():
        properties: dict[str, Any] = {}
        all_keys: set[str] = set()
        for item in _pages(custom_lists[reference], generation, reference=reference):
            name = item.get("key")
            _require(isinstance(name, str) and name not in all_keys, "duplicate or invalid custom key")
            all_keys.add(name)
            if name.startswith(PREFIX):
                _require(item.get("status") == "managed", "source identity is not readable")
                properties[name] = item.get("value")
        if key in expected:
            _require(properties == expected[key], "native source identity disagrees: " + "/".join(key))
        else:
            _require(not properties, "unexpected native entity claims source ownership")
            extras.append({"root": key[0], "name": key[1], "ref": reference})
    return {
        "semantic_generation": generation,
        "entities": [{"root": root, "name": name, "ref": refs[(root, name)]}
                     for root, name in expected],
        "unowned_runtime_entities": extras,
    }


def _equal_value(expected: Any, observed: Any) -> bool:
    if isinstance(expected, bool):
        return type(observed) is bool and expected == observed
    if type(expected) is int:
        return type(observed) is int and expected == observed
    if type(expected) is float:
        return (not isinstance(observed, bool) and isinstance(observed, (int, float))
                and math.isfinite(float(expected)) and math.isfinite(float(observed))
                and abs(float(expected) - float(observed)) <= RNA_FLOAT_ABS_TOLERANCE)
    if isinstance(expected, list):
        return (isinstance(observed, list) and len(expected) == len(observed)
                and all(_equal_value(a, b) for a, b in zip(expected, observed)))
    return type(expected) is type(observed) and expected == observed


def check_property_getters(reference: str, expected: Mapping[str, Any], getters: Sequence[Any]) -> None:
    """Check driver.blender.semantic.property.get payloads for one exact ref."""
    _require(isinstance(reference, str) and bool(reference), "missing entity reference")
    _require(isinstance(getters, list) and len(getters) == len(expected), "missing property getters")
    seen: set[str] = set()
    for value in getters:
        _require(isinstance(value, dict), "invalid property getter")
        key = value.get("property")
        _require(key in expected and key not in seen, "unexpected or duplicated property getter")
        seen.add(key)
        _require(value.get("ref") == reference, "property getter belongs to another entity")
        descriptor = value.get("descriptor")
        _require(isinstance(descriptor, dict) and descriptor.get("id") == key
                 and descriptor.get("status") in ("managed", "read_only"),
                 "RNA descriptor does not identify the requested property")
        _require(_equal_value(expected[key], value.get("value")), "native property differs: " + key)


def check_relation_pages(
    pages: Sequence[Any], reference: str, property_name: str, generation: int,
    required_refs: set[str], *, exact: bool,
) -> set[str]:
    """Check retained request args plus semantic.relations result pages.

    The wrapper records args and data from the same original Broker call.
    This pure check does not authenticate that wrapper or its execution source.
    """
    _require(isinstance(pages, list) and bool(pages), "missing relation observations")
    payloads = []
    for call in pages:
        _require(isinstance(call, dict) and call.get("command") == "driver.blender.semantic.relations",
                 "wrong relation command")
        args, data = call.get("args"), call.get("data")
        _require(isinstance(args, dict) and isinstance(data, dict), "missing relation call")
        _require(args.get("ref") == reference and args.get("property") == property_name
                 and data.get("relation") == property_name, "relation binding differs")
        _require(type(args.get("offset", 0)) is int and args.get("offset", 0) == data.get("offset"),
                 "relation offset differs from request")
        payloads.append(data)
    refs: set[str] = set()
    for item in _pages(payloads, generation):
        ref = item.get("ref")
        _require(isinstance(ref, str) and bool(ref) and ref not in refs, "duplicate relation target")
        refs.add(ref)
    _require(refs == required_refs if exact else required_refs.issubset(refs),
             "native relation does not preserve required source membership")
    return refs


def _plain_path(workspace: Path, relative: str, *, must_exist: bool) -> Path:
    _require(isinstance(relative, str) and "\\" not in relative and "\x00" not in relative,
             "source path must be a simple workspace-relative POSIX path")
    rel = PurePosixPath(relative)
    _require(not rel.is_absolute() and rel.parts and all(p not in ("", ".", "..") for p in rel.parts)
             and ":" not in relative and str(rel) == relative, "invalid source path")
    workspace = workspace.absolute()
    for part in (workspace, *workspace.parents):
        info = part.lstat()
        _require(stat.S_ISDIR(info.st_mode) and not part.is_symlink()
                 and not (getattr(info, "st_file_attributes", 0) & 0x400),
                 "workspace ancestry is not a plain directory")
    current = workspace
    for piece in rel.parts[:-1]:
        current /= piece
        info = current.lstat()
        _require(stat.S_ISDIR(info.st_mode) and not current.is_symlink()
                 and not (getattr(info, "st_file_attributes", 0) & 0x400),
                 "source ancestry is not a plain directory")
    target = workspace.joinpath(*rel.parts)
    if must_exist:
        _require(target.exists() or target.is_symlink(), "source artifact is absent")
    return target


def require_new_source_path(workspace: Path, relative: str) -> Path:
    """Preflight only: file.save itself is path-based, not atomic/no-clobber."""
    target = _plain_path(workspace, relative, must_exist=False)
    try:
        target.lstat()
    except FileNotFoundError:
        return target
    raise SceneContractError("source output already exists")


def inspect_source_file(
    workspace: Path, relative: str, *, expected_sha256: str | None = None,
    expected_bytes: int | None = None,
) -> dict[str, Any]:
    """Hash one retained regular file through one descriptor without writing it.

    An isolated owner-controlled workspace is a prerequisite. This helper does
    not turn Blender's path API into an atomic publisher or a filesystem lock.
    """
    if expected_sha256 is not None:
        _require(_sha(expected_sha256), "invalid expected source digest")
    if expected_bytes is not None:
        _require(_integer(expected_bytes, 1), "invalid expected source size")
    target = _plain_path(workspace, relative, must_exist=True)
    before = target.lstat()
    _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
             and not (getattr(before, "st_file_attributes", 0) & 0x400), "source is not a plain single-link file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(target, flags)
    try:
        opened = os.fstat(fd)
        _require((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                 "source changed before open")
        _require(stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1
                 and 0 < opened.st_size <= MAX_CUSTODY_BYTES, "source size/type is outside profile")
        digest = hashlib.sha256()
        count = 0
        while True:
            data = os.read(fd, 1024 * 1024)
            if not data:
                break
            count += len(data)
            _require(count <= MAX_CUSTODY_BYTES, "source grew beyond profile")
            digest.update(data)
        after = os.fstat(fd)
        named_after = target.lstat()
        def signature(info: os.stat_result) -> tuple[int, ...]:
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                    info.st_ctime_ns, info.st_nlink)
        _require(signature(opened) == signature(after) == signature(named_after)
                 and count == opened.st_size, "source changed while its bytes were read")
        actual = digest.hexdigest()
        _require(expected_sha256 is None or actual == expected_sha256, "source digest mismatch")
        _require(expected_bytes is None or count == expected_bytes, "source byte size mismatch")
        return {"relative_path": relative, "sha256": actual, "bytes": count}
    finally:
        os.close(fd)


def check_file_open_response(
    request_path: str, response: Mapping[str, Any], before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> None:
    """Require native file.open response and independent file pins to agree."""
    _require(set(response) == {"changed", "path", "use_scripts"}
             and response.get("changed") is True and response.get("use_scripts") is False
             and response.get("path") == request_path, "file.open did not return the exact scoped contract")
    _require(set(before) == {"relative_path", "sha256", "bytes"}
             and before == after and before.get("relative_path") == request_path
             and _sha(before.get("sha256")) and _integer(before.get("bytes"), 1),
             "reopen source identity changed or is incomplete")


def check_project_source(plan: Mapping[str, Any], source_bytes: bytes) -> None:
    """Bind the plan to retained serde-json-compact Project bytes."""
    expected_identity_table(plan)
    _require(type(source_bytes) is bytes and 0 < len(source_bytes) <= 16 * 1024 * 1024,
             "invalid retained Project byte size")
    _require(hashlib.sha256(source_bytes).hexdigest() == plan["project_json_sha256"],
             "retained Project digest differs from the source plan")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            _require(key not in result, "duplicate JSON member in retained Project")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise SceneContractError("non-finite JSON constant: " + value)

    try:
        source = json.loads(source_bytes, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SceneContractError("invalid retained Project JSON") from error
    _require(isinstance(source, dict), "retained Project is not an object")
    contribution = plan["contribution"]
    for project_key, plan_key in (("id", "project_id"), ("generation", "generation"), ("revision", "revision")):
        _require(type(source.get(project_key)) is type(contribution[plan_key])
                 and source.get(project_key) == contribution[plan_key], "Project authority differs")
    scenes = source.get("scenes")
    _require(isinstance(scenes, list), "retained Project omitted scenes")
    selected = [scene for scene in scenes if isinstance(scene, dict)
                and scene.get("id") == contribution["scene_id"]]
    _require(len(selected) == 1, "source scene membership is absent or ambiguous")
    scene = selected[0]
    _require(scene.get("renderer") == "blender", "retained scene is not a Blender source")
    _require(scene.get("camera") == plan["camera"]["source"], "retained camera source differs")
    nodes = scene.get("nodes")
    _require(isinstance(nodes, list) and all(isinstance(node, dict) for node in nodes),
             "invalid retained Canvas nodes")
    ids = [node.get("id") for node in nodes]
    _require(all(_uuid(node_id) for node_id in ids) and len(set(ids)) == len(ids),
             "retained node identities are absent or duplicated")
    _require(set(ids) == {obj["node_id"] for obj in contribution["objects"]},
             "source plan dropped or invented a Canvas object")


def expected_camera_properties(plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Values for typed property getters, without claiming a rendered match."""
    expected_identity_table(plan)
    camera = plan["camera"]
    source = camera["source"]
    _require(isinstance(source, dict) and set(source) == {
        "center_x", "center_y", "zoom", "rotation_deg", "safe_margin"}, "invalid camera source fields")
    _require(all(not isinstance(value, bool) and isinstance(value, (int, float))
                 and math.isfinite(float(value)) for value in source.values()),
             "invalid finite camera source values")
    _require(source.get("zoom") == 1.0 and source.get("rotation_deg") == 0.0,
             "unsupported original camera profile")
    _require(camera.get("projection") == "ORTHO" and camera.get("sensor_fit") == "VERTICAL"
             and camera.get("resolution") == [1920, 1080] and camera.get("pixel_aspect") == [1.0, 1.0],
             "camera plan does not use the declared static projection")
    return {
        "camera_object": {
            "location": camera["location"], "rotation_euler": camera["rotation_euler"],
            "rotation_mode": "XYZ", "scale": [1.0, 1.0, 1.0], "hide_render": False,
        },
        "camera_data": {
            "type": "ORTHO", "sensor_fit": "VERTICAL", "ortho_scale": camera["ortho_scale"],
            "clip_start": camera["clip_start"], "clip_end": camera["clip_end"],
        },
        "render_settings": {
            "resolution_x": 1920, "resolution_y": 1080, "resolution_percentage": 100,
            "pixel_aspect_x": 1.0, "pixel_aspect_y": 1.0,
        },
    }
