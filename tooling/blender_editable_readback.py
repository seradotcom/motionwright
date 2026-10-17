#!/usr/bin/env python3
"""Independent, source-only readback of Motionwright's static Blender Collection.

Run with Blender 4.5, in a fresh process owned by the native test runner::

    blender --background --factory-startup --disable-autoexec --python \
      tooling/blender_editable_readback.py -- --workspace ROOT \
      --evidence evidence.json --evidence-sha256 SHA --output readback.json

The only .blend operation is ``bpy.data.libraries.load(..., link=False)``.
No source is authored, saved, rendered, or evaluated through a driver. No
Semwright implementation is imported. Output is a newly created JSON file.

The evidence digest binds this observation to its exact input bytes. An input
receipt is not authority: the caller must separately verify the original
ProductionCoordinator journal. This oracle does not certify that journal,
rendering, audio, a full Scene, canonical edit import, or external rights.

``python blender_editable_readback.py --self-test --workspace ROOT`` tests the
verifier with explicitly synthetic data and ordinary files; it never imports
bpy and is not native production evidence.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import unittest
import uuid


SOURCE_SCHEMA = "motionwright-blender-collection-source/1"
EVIDENCE_SCHEMA = "motionwright-blender-editable-evidence/1"
OUTPUT_SCHEMA = "motionwright-blender-collection-readback/1"
CONTRACT_PIN = "54d3459a051adbce4cae9f60c0dea8f60c824be6"
MAX_BLEND_BYTES = 32 * 1024 * 1024
MAX_EVIDENCE_BYTES = 16 * 1024 * 1024
MAX_ENTITIES = 64
FLOAT_TOLERANCE = 1.0e-5
ISLAND = "sw_authoring_island"
ENTITY = "sw_authoring_entity"
MATERIAL = "sw_authoring_material"
MARKER = "sw_authoring_observed_v1"
TEXTURE_FIELDS = (
    "base_color_texture", "roughness_texture", "metallic_texture",
    "normal_texture", "emission_texture", "opacity_texture",
)
LIMITATIONS = [
    "collection_library_only", "static_mesh_subset_only",
    "normalized_color_channels_without_raster_colorimetry",
    "display_names_are_bounded_native_aliases",
    "scene_camera_world_render_settings_not_persisted",
    "text_fonts_animation_audio_not_in_profile",
    "independent_blend_topology_readback_required",
    "native_transform_edit_not_imported_into_project",
    "external_asset_rights_and_nle_roundtrip_not_evaluated",
]
CREATE_STAGES = [
    "create-inspect", "create-plan", "create-apply", "create-verify",
    "create-readback", "create-persist",
]
EDIT_STAGES = [
    "edit-inspect", "edit-plan", "edit-apply", "edit-verify",
    "edit-readback", "edit-persist",
]
PHASE_STAGES = {
    "source_persisted": CREATE_STAGES,
    "source_reopened": CREATE_STAGES + ["source-reopen"],
    "transform_persisted": CREATE_STAGES + ["source-reopen"] + EDIT_STAGES,
    "edited_source_reopened": CREATE_STAGES + ["source-reopen"] + EDIT_STAGES + ["edited-reopen"],
}


class OracleError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def require(condition, message, code="source_mismatch"):
    if not condition:
        raise OracleError(code, message)


def exact_keys(value, keys, label):
    require(isinstance(value, dict) and set(value) == set(keys),
            label + " has missing or unsupported fields", "invalid_evidence")


def sha256_text(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def local_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is not None


def bounded_text(value, maximum=256):
    return isinstance(value, str) and 0 < len(value.encode("utf-8")) <= maximum and "\x00" not in value


def native_display_name(value):
    # Match the pinned Entity.name contract: <=80 UTF-8 bytes and no Rust
    # char::is_control (Unicode General Category Cc, U+0000..001F/007F..009F).
    return bounded_text(value, 80) and all(not (ord(char) < 32 or 0x7F <= ord(char) <= 0x9F) for char in value)


def unsigned(value, maximum=(1 << 64) - 1):
    return type(value) is int and 0 <= value <= maximum


def canonical_uuid(value):
    require(isinstance(value, str), "logical UUID must be a string", "invalid_evidence")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise OracleError("invalid_evidence", "logical UUID is malformed") from error
    require(str(parsed) == value, "logical UUID is not canonical", "invalid_evidence")
    return parsed.hex


def number(value, maximum=1_000_000.0):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) <= maximum


def finite_vector(value, size=3, maximum=1_000_000.0):
    return isinstance(value, list) and len(value) == size and all(number(v, maximum) for v in value)


def close(a, b):
    return number(a) and number(b) and abs(a - b) <= FLOAT_TOLERANCE * max(1.0, abs(a), abs(b))


def vector_close(a, b):
    return finite_vector(a, len(b)) and all(close(x, y) for x, y in zip(a, b))


def same_json(a, b):
    """JSON semantic equality with booleans distinct from numeric 0 and 1."""
    if type(a) in (int, float) and type(b) in (int, float):
        return math.isfinite(a) and math.isfinite(b) and a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same_json(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same_json(x, y) for x, y in zip(a, b))
    return a == b


def projection_value(value):
    # The pinned source-projection-v4 wire contract normalizes exact signed
    # zeros only. It does not round source floats or apply the comparison tolerance.
    if isinstance(value, float):
        return 0.0 if value == 0.0 else value
    if isinstance(value, (tuple, list)):
        return [projection_value(v) for v in value]
    if isinstance(value, dict):
        return {k: projection_value(v) for k, v in value.items()}
    return value


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def source_projection_digest(value):
    encoded = json_bytes(projection_value(value))
    require(len(encoded) <= MAX_EVIDENCE_BYTES, "source projection exceeds budget")
    return hashlib.sha256(encoded).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON field", "invalid_evidence")
        result[key] = value
    return result


def parse_json(data):
    def reject_constant(_value):
        raise OracleError("invalid_evidence", "non-finite JSON number")

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise OracleError("invalid_evidence", "evidence is not bounded UTF-8 JSON") from error
    pending = [(value, 0)]
    visited = 0
    while pending:
        row, depth = pending.pop()
        visited += 1
        require(depth <= 64 and visited <= 1_000_000,
                "JSON structure exceeds budget", "invalid_evidence")
        if isinstance(row, dict):
            pending.extend((child, depth + 1) for child in row.values())
        elif isinstance(row, list):
            pending.extend((child, depth + 1) for child in row)
        elif isinstance(row, float):
            require(math.isfinite(row), "non-finite JSON number", "invalid_evidence")
    return value


def validate_relative(value, suffix=None, flat=False):
    require(bounded_text(value, 240) and "\\" not in value and ":" not in value,
            "path is not a bounded relative POSIX name", "unsafe_path")
    parts = value.split("/")
    require(all(part and part not in (".", "..") for part in parts)
            and not value.startswith("/"), "path traversal or empty component", "unsafe_path")
    require(not flat or len(parts) == 1, "artifact must use a generated local filename", "unsafe_path")
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{stem}{n}" for stem in ("COM", "LPT") for n in range(1, 10)}
    for part in parts:
        require(not part.endswith((".", " "))
                and all(ord(ch) >= 32 and ch not in '<>"|?*' for ch in part)
                and part.split(".")[0].upper() not in reserved,
                "path has a platform-ambiguous component", "unsafe_path")
    require(suffix is None or value.endswith(suffix), "unexpected file extension", "unsafe_path")
    return parts


def _safe_stat(path, directory=False):
    try:
        result = os.lstat(path)
    except OSError as error:
        raise OracleError("unsafe_path", "physical workspace entry is unavailable") from error
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    require(not stat.S_ISLNK(result.st_mode)
            and not (getattr(result, "st_file_attributes", 0) & reparse),
            "symlink or reparse point is outside the physical-file boundary", "unsafe_path")
    require(stat.S_ISDIR(result.st_mode) if directory else stat.S_ISREG(result.st_mode),
            "unexpected physical entry type", "unsafe_path")
    if not directory:
        require(result.st_nlink == 1, "artifact has multiple physical links", "unsafe_path")
    return result


def physical_root(value):
    root = Path(value)
    require(root.is_absolute() and not any(part in (".", "..") for part in root.parts),
            "workspace root must be absolute and normalized", "unsafe_path")
    current = Path(root.anchor)
    _safe_stat(current, directory=True)
    for component in root.parts[1:]:
        current = current / component
        _safe_stat(current, directory=True)
    require(root.resolve(strict=True) == root, "workspace root has an alias", "unsafe_path")
    return root


def physical_path(root, relative, existing=True):
    parts = validate_relative(relative)
    physical_root(str(root))
    current = root
    for component in parts[:-1]:
        current = current / component
        _safe_stat(current, directory=True)
    result = current / parts[-1]
    if existing:
        _safe_stat(result)
    else:
        require(not os.path.lexists(result), "output already exists", "output_conflict")
    return result


def _stat_identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


class PinnedFile:
    """Keeps a descriptor and verifies path identity and bytes before/after use.

    Blender's library API takes a pathname, not this descriptor. These checks
    detect observed changes; they do not promise an atomic path-to-fd handoff
    against a concurrently hostile workspace. The runner owns that boundary.
    """

    def __init__(self, root, relative, expected_sha, maximum, expected_size=None):
        require(sha256_text(expected_sha), "expected file digest is malformed", "invalid_evidence")
        self.root, self.relative = root, relative
        self.path = physical_path(root, relative)
        before = _safe_stat(self.path)
        require(0 < before.st_size <= maximum, "file byte budget is invalid", "file_size_mismatch")
        if expected_size is not None:
            require(unsigned(expected_size) and before.st_size == expected_size,
                    "file size differs despite its declared digest", "file_size_mismatch")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        try:
            self.fd = os.open(self.path, flags)
        except OSError as error:
            raise OracleError("unsafe_path", "physical input could not be opened") from error
        self.closed = False
        try:
            self.identity = _stat_identity(os.fstat(self.fd))
            require(self.identity == _stat_identity(before), "file changed during open", "file_changed")
            self.maximum, self.expected_sha = maximum, expected_sha
            self.data = self._read_descriptor()
            self.size = len(self.data)
            require(self.size == before.st_size, "file changed during read", "file_changed")
            require(hashlib.sha256(self.data).hexdigest() == expected_sha,
                    "physical file digest differs", "file_digest_mismatch")
            self.verify_unchanged()
        except BaseException:
            self.close()
            raise

    def _read_descriptor(self):
        os.lseek(self.fd, 0, os.SEEK_SET)
        chunks, total = [], 0
        while True:
            block = os.read(self.fd, min(1024 * 1024, self.maximum + 1 - total))
            if not block:
                break
            total += len(block)
            require(total <= self.maximum, "file grew beyond byte budget", "file_changed")
            chunks.append(block)
        return b"".join(chunks)

    def verify_unchanged(self):
        path = physical_path(self.root, self.relative)
        require(_stat_identity(_safe_stat(path)) == self.identity
                and _stat_identity(os.fstat(self.fd)) == self.identity,
                "physical file identity or metadata changed", "file_changed")
        require(hashlib.sha256(self._read_descriptor()).hexdigest() == self.expected_sha,
                "physical file bytes changed", "file_changed")
        require(_stat_identity(_safe_stat(path)) == self.identity
                and _stat_identity(os.fstat(self.fd)) == self.identity,
                "physical file changed during verification", "file_changed")

    def close(self):
        if not self.closed:
            os.close(self.fd)
            self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def write_new_json(root, relative, value):
    validate_relative(relative, ".json")
    path = physical_path(root, relative, existing=False)
    body = json_bytes(value) + b"\n"
    require(len(body) <= MAX_EVIDENCE_BYTES, "output exceeds byte budget", "output_budget")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise OracleError("output_write_failed", "new result file could not be completed") from error
    digest = hashlib.sha256(body).hexdigest()
    with PinnedFile(root, relative, digest, MAX_EVIDENCE_BYTES, len(body)):
        pass
    return {"relative_path": relative, "sha256": digest, "bytes": len(body)}


def validate_transform(value):
    exact_keys(value, ["translation", "rotation", "scale"], "transform")
    require(all(finite_vector(value[key]) for key in value), "transform exceeds finite bounds", "invalid_evidence")
    require(all(abs(v) >= 0.0001 for v in value["scale"]), "transform has singular scale", "invalid_evidence")


def expected_spec(contribution, edits):
    exact_keys(contribution, ["project_id", "generation", "revision", "scene_id", "collection_name", "objects"], "canonical contribution")
    for key in ("project_id", "generation", "scene_id"):
        canonical_uuid(contribution[key])
    require(unsigned(contribution["revision"]) and bounded_text(contribution["collection_name"], 128),
            "canonical contribution stamp or name is malformed", "invalid_evidence")
    objects = contribution["objects"]
    require(isinstance(objects, list) and 1 <= len(objects) <= MAX_ENTITIES,
            "source requires 1..64 entities", "invalid_evidence")
    require(isinstance(edits, list) and len(edits) <= 1, "native edit budget exceeded", "invalid_evidence")
    edit = None
    if edits:
        edit = edits[0]
        exact_keys(edit, ["node_id", "transform"], "native edit")
        canonical_uuid(edit["node_id"])
        validate_transform(edit["transform"])
    entities, materials, identities = [], [], set()
    for obj in objects:
        exact_keys(obj, ["node_id", "name", "material_name", "color_rgba", "primitive", "location", "rotation", "scale"], "canonical object")
        node_id = canonical_uuid(obj["node_id"])
        require(node_id not in identities, "duplicate logical identity", "invalid_evidence")
        identities.add(node_id)
        require(native_display_name(obj["name"]) and bounded_text(obj["material_name"], 128),
                "canonical display name is malformed", "invalid_evidence")
        color = obj["color_rgba"]
        require(finite_vector(color, 4, 1.0) and all(v >= 0 for v in color),
                "material channels are outside 0..1", "invalid_evidence")
        transform = {"translation": obj["location"], "rotation": obj["rotation"], "scale": obj["scale"]}
        validate_transform(transform)
        if edit and edit["node_id"] == obj["node_id"]:
            require(not same_json(transform, edit["transform"]),
                    "declared edit is a semantic no-op", "invalid_evidence")
            transform = copy.deepcopy(edit["transform"])
        if obj["primitive"] == "plane":
            shape = {"kind": "mesh", "vertices": [[-1.0, -1.0, 0.0], [1.0, -1.0, 0.0], [1.0, 1.0, 0.0], [-1.0, 1.0, 0.0]], "faces": [[0, 1, 2, 3]], "uv": None}
        else:
            require(obj["primitive"] == "cylinder", "source shape is outside the static subset", "invalid_evidence")
            shape = {"kind": "cylinder", "radius": 1.0, "depth": 2.0, "segments": 32}
        material_id = "mat_" + node_id
        material = {"id": material_id, "base_color": color, "roughness": 0.45,
                    "metallic": 0.0, "opacity": color[3], "emission_color": [0.0, 0.0, 0.0, 1.0],
                    "emission_strength": 0.0, **{field: None for field in TEXTURE_FIELDS}}
        materials.append(material)
        entities.append({"id": "node_" + node_id, "name": obj["name"], "shape": shape,
                         "transform": transform, "materials": [material_id], "modifiers": [],
                         "shade_smooth": False, "attributes": []})
    if edit:
        require(canonical_uuid(edit["node_id"]) in identities, "edit target is absent", "invalid_evidence")
    return {"version": 1, "collection": "scene_" + canonical_uuid(contribution["scene_id"]),
            "meters_per_unit": 1.0, "textures": [], "materials": materials,
            "entities": entities, "relations": [], "animation": None}


def validate_source(source):
    exact_keys(source, ["schema", "source_contract_semwright_pin", "project_resource",
                        "canonical_contribution", "native_edits", "source_sha256", "spec"], "source")
    require(source["schema"] == SOURCE_SCHEMA and source["source_contract_semwright_pin"] == CONTRACT_PIN,
            "unsupported source schema or API contract pin", "invalid_evidence")
    require(bounded_text(source["project_resource"]) and sha256_text(source["source_sha256"]),
            "source identity or declared digest is malformed", "invalid_evidence")
    spec = expected_spec(source["canonical_contribution"], source["native_edits"])
    require(same_json(source["spec"], spec), "source spec differs from the bounded canonical projection", "invalid_evidence")
    # source_sha256's serde_json preimage is verified by the Rust consumer. This
    # independent Python oracle binds that declaration through the CLI input SHA.
    return spec


def native_name(island, identity):
    require(local_id(island) and local_id(identity), "invalid native local identity", "invalid_evidence")
    require(len(identity) <= 40, "identity would be truncated by the pinned native naming contract", "invalid_evidence")
    return "SW_" + island[:12] + "_" + identity


def shader_ports(node):
    require(isinstance(node.get("inputs"), list) and len(node["inputs"]) <= 64, "shader input budget differs")
    ports = {}
    for entry in node["inputs"]:
        require(isinstance(entry, list) and len(entry) == 2 and bounded_text(entry[0], 128)
                and entry[0] not in ports, "shader input identity is ambiguous")
        ports[entry[0]] = entry[1]
    return ports


def verify_material_row(row, material, island):
    require(isinstance(row, dict), "material readback is missing")
    require(row.get("id") == material["id"] and row.get("name") == native_name(island, material["id"])
            and row.get("library") is False, "material identity or locality differs")
    require(vector_close(row.get("color"), material["base_color"])
            and vector_close(row.get("base_color"), material["base_color"])
            and vector_close(row.get("emission_color"), material["emission_color"]),
            "material source colors differ")
    for key in ("roughness", "metallic", "opacity", "emission_strength"):
        require(close(row.get(key), material[key]), "material " + key + " differs")
    require(row.get("texture_bindings") == [] and row.get("links") == [["Principled BSDF", "BSDF", "Material Output", "Surface"]],
            "material graph contains an unsupported dependency or link")
    nodes = row.get("nodes")
    require(isinstance(nodes, list) and len(nodes) == 2, "material must contain exactly two native nodes")
    by_name = {n.get("name"): n for n in nodes if isinstance(n, dict)}
    require(set(by_name) == {"Principled BSDF", "Material Output"}
            and by_name["Principled BSDF"].get("type") == "ShaderNodeBsdfPrincipled"
            and by_name["Material Output"].get("type") == "ShaderNodeOutputMaterial",
            "material shader identities differ")
    ports = shader_ports(by_name["Principled BSDF"])
    shader_ports(by_name["Material Output"])
    for socket, value in (("Base Color", material["base_color"]), ("Emission Color", material["emission_color"])):
        require(vector_close(ports.get(socket), value), "effective shader " + socket + " differs")
    for socket, field in (("Alpha", "opacity"), ("Roughness", "roughness"), ("Metallic", "metallic"), ("Emission Strength", "emission_strength")):
        require(close(ports.get(socket), material[field]), "effective shader " + socket + " differs")


def verify_snapshot(snapshot, spec):
    exact_keys(snapshot, ["native_session", "island", "fingerprint", "drift", "total", "items", "source_only", "exhaustive"], "snapshot")
    island = snapshot["island"]
    require(local_id(island) and bounded_text(snapshot["native_session"])
            and sha256_text(snapshot["fingerprint"]) and snapshot["drift"] is False
            and snapshot["source_only"] is True and snapshot["exhaustive"] is True,
            "snapshot is incomplete, stale or outside the source profile")
    require(type(snapshot["total"]) is int and snapshot["total"] == len(spec["entities"])
            and isinstance(snapshot["items"], list) and len(snapshot["items"]) == snapshot["total"],
            "snapshot entity count differs")
    entities = {item["id"]: item for item in spec["entities"]}
    materials = {item["id"]: item for item in spec["materials"]}
    seen = set()
    for row in snapshot["items"]:
        require(isinstance(row, dict) and row.get("entity") in entities and row["entity"] not in seen,
                "snapshot has duplicate or unexpected entity identity")
        seen.add(row["entity"])
        entity = entities[row["entity"]]
        require(row.get("name") == native_name(island, entity["id"])
                and row.get("data_name") == native_name(island, entity["id"])
                and row.get("native_island") == island and row.get("type") == "MESH"
                and row.get("collections") == [native_name(island, spec["collection"])],
                "snapshot native names or Collection identity differ")
        for key in ("library", "data_library", "hidden_render", "hidden_viewport"):
            require(row.get(key) is False, "snapshot has a foreign or hidden entity")
        require(row.get("parent") is None and row.get("parent_name") is None
                and row.get("parent_type") == "OBJECT" and row.get("parent_bone") == ""
                and same_json(row.get("parent_inverse"), [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]])
                and type(row.get("drivers")) is int and row["drivers"] == 0 and row.get("action") is None,
                "snapshot has a parent, animation or driver outside the static subset")
        for key in ("modifiers", "constraints", "actions", "nla_tracks", "attributes"):
            require(row.get(key) == [], "snapshot contains unsupported " + key)
        for key in ("translation", "rotation", "scale"):
            require(vector_close(row.get(key), entity["transform"][key]), "snapshot transform differs")
        vertices, faces = (4, 1) if entity["shape"]["kind"] == "mesh" else (64, 34)
        for key, expected in (("vertices", vertices), ("polygons", faces), ("uv_layers", 0), ("smooth_polygons", 0), ("data_users", 1)):
            require(type(row.get(key)) is int and row[key] == expected, "snapshot mesh summary differs")
        require(sha256_text(row.get("geometry_digest")) and sha256_text(row.get("normal_digest"))
                and row.get("normal_method") == "source-polygon-normal-v1", "snapshot source digest is malformed")
        require(isinstance(row.get("materials"), list) and len(row["materials"]) == 1,
                "snapshot material slots differ")
        verify_material_row(row["materials"][0], materials[entity["materials"][0]], island)
    require(list(row["entity"] for row in snapshot["items"]) == sorted(seen), "snapshot ordering differs from the source projection")
    return snapshot


def verify_reopen(before, after):
    require(before["native_session"] != after["native_session"], "reopen reused the prior native session")
    for key in ("island", "fingerprint", "total", "items"):
        require(same_json(before[key], after[key]), "reopen changed source " + key)


def verify_delta(before, after, node_id):
    require(before["native_session"] == after["native_session"] and before["island"] == after["island"]
            and before["total"] == after["total"] and before["fingerprint"] != after["fingerprint"],
            "edit is a no-op or changed native identity")
    target = "node_" + canonical_uuid(node_id)
    originals = {row["entity"]: row for row in before["items"]}
    require(set(originals) == {row["entity"] for row in after["items"]}, "edit changed entity membership")
    changed = False
    for row in after["items"]:
        previous = originals[row["entity"]]
        restored = copy.deepcopy(row)
        if row["entity"] == target:
            changed = any(not same_json(row[key], previous[key]) for key in ("translation", "rotation", "scale"))
            for key in ("translation", "rotation", "scale"):
                restored[key] = copy.deepcopy(previous[key])
        require(same_json(restored, previous), "edit changed source outside the requested TRS")
    require(changed, "edit has no native parameter delta")


def _get(value, *keys):
    for key in keys:
        require(isinstance(value, dict) and key in value, "input stage is missing a required field", "invalid_evidence")
        value = value[key]
    return value


def verify_artifact(artifact, snapshot, spec):
    exact_keys(artifact, ["relative_path", "sha256", "bytes", "island", "source_fingerprint", "inventory", "method"], "artifact")
    validate_relative(artifact["relative_path"], ".blend", flat=True)
    require(sha256_text(artifact["sha256"]) and unsigned(artifact["bytes"], MAX_BLEND_BYTES)
            and artifact["bytes"] > 0 and artifact["island"] == snapshot["island"]
            and artifact["source_fingerprint"] == snapshot["fingerprint"]
            and artifact["method"] == "libraries-write-indirect-closure",
            "artifact identity, fingerprint, byte count or method differs")
    exact_keys(artifact["inventory"], ["collection", "objects"], "artifact inventory")
    require(artifact["inventory"]["collection"] == native_name(snapshot["island"], spec["collection"])
            and isinstance(artifact["inventory"]["objects"], list)
            and sorted(artifact["inventory"]["objects"]) == sorted(row["name"] for row in snapshot["items"]),
            "artifact inventory is incomplete or ambiguous")


def _persist_matches(data, artifact):
    require(isinstance(data, dict) and data.get("fresh_process_verified") is False,
            "persist falsely certifies fresh-process reopen")
    for key in artifact:
        require(same_json(artifact[key], data.get("path" if key == "relative_path" else key)),
                "artifact is not bound to its input persist stage")


def validate_evidence(evidence):
    exact_keys(evidence, ["schema", "phase", "source", "snapshot", "artifact", "stages",
                          "source_parameter_readback", "fresh_process_reopened", "native_transform_edit_persisted",
                          "independent_file_readback_verified", "raster_verified", "audio_verified",
                          "full_scene_verified", "canonical_edit_imported", "limitations"], "evidence")
    require(evidence["schema"] == EVIDENCE_SCHEMA and evidence["phase"] in PHASE_STAGES,
            "unsupported evidence schema or phase", "invalid_evidence")
    spec = validate_source(evidence["source"])
    original_spec = expected_spec(evidence["source"]["canonical_contribution"], [])
    edited = evidence["phase"] in ("transform_persisted", "edited_source_reopened")
    reopened = evidence["phase"] in ("source_reopened", "edited_source_reopened")
    require(bool(evidence["source"]["native_edits"]) == edited
            and evidence["source_parameter_readback"] is True
            and evidence["fresh_process_reopened"] is reopened
            and evidence["native_transform_edit_persisted"] is edited,
            "phase and source coverage disagree", "invalid_evidence")
    for key in ("independent_file_readback_verified", "raster_verified", "audio_verified", "full_scene_verified", "canonical_edit_imported"):
        require(evidence[key] is False, "input contains unsupported or self-certified coverage", "invalid_evidence")
    require(evidence["limitations"] == LIMITATIONS, "input omitted source profile limitations", "invalid_evidence")
    verify_snapshot(evidence["snapshot"], spec)
    verify_artifact(evidence["artifact"], evidence["snapshot"], spec)
    stages = evidence["stages"]
    require(isinstance(stages, list) and len(stages) == len(PHASE_STAGES[evidence["phase"]])
            and [row.get("stage") for row in stages if isinstance(row, dict)] == PHASE_STAGES[evidence["phase"]],
            "input stage chain is incomplete or duplicated", "invalid_evidence")
    data, stage_by_name, requests = {}, {}, set()
    stamp = evidence["source"]["canonical_contribution"]
    for proof in stages:
        exact_keys(proof, ["stage", "command", "request_id", "request_sha256", "connection_identity",
                           "provider_generation", "descriptor_sha256", "response"], "stage")
        suffix = proof["stage"].rsplit("-", 1)[1]
        operation = {"readback": "inspect", "reopen": "reopen"}.get(suffix, suffix)
        command = "driver.blender.composition." + operation
        require(proof["command"] == command and bounded_text(proof["request_id"], 128)
                and proof["request_id"].endswith("-" + proof["stage"])
                and proof["request_id"] not in requests
                and all(sha256_text(proof[key]) for key in ("request_sha256", "connection_identity", "descriptor_sha256"))
                and unsigned(proof["provider_generation"]), "stage custody declaration is malformed", "invalid_evidence")
        requests.add(proof["request_id"])
        response = proof["response"]
        result, receipt = _get(response, "result"), _get(response, "receipt")
        execution, provenance = _get(result, "execution"), _get(result, "execution", "provenance")
        require(response.get("replayed") is False and result.get("provenance_checked") is True
                and execution.get("backend") == "driver:blender" and execution.get("dry_run") is False
                and execution.get("policy_decision") == "allow"
                and provenance.get("provider") == "driver:blender" and provenance.get("source") == "driver"
                and provenance.get("execution_provider") == "driver:blender"
                and provenance.get("untrusted_metadata") is False
                and same_json(provenance.get("provider_generation"), proof["provider_generation"])
                and provenance.get("descriptor_sha256") == proof["descriptor_sha256"],
                "input stage does not claim a fresh trusted command")
        require(receipt.get("stage") == "completed"
                and all(same_json(receipt.get(key), proof[key]) for key in ("command", "request_id", "request_sha256"))
                and all(same_json(receipt.get(key), stamp[key]) for key in ("project_id", "generation", "revision"))
                and same_json(_get(receipt, "payload", "result"), result)
                and bounded_text(_get(receipt, "payload", "broker_request_id")),
                "input receipt is not internally bound to its stage and Project stamp")
        data[proof["stage"]] = _get(result, "data")
        stage_by_name[proof["stage"]] = proof
    for group in (CREATE_STAGES, (["source-reopen"] + EDIT_STAGES if edited else [])):
        if group:
            custody = {(stage_by_name[name]["connection_identity"], stage_by_name[name]["provider_generation"]) for name in group}
            require(len(custody) == 1, "input changed provider or connection within one native attempt")
    initial = data["create-inspect"]
    require(initial.get("island") is None and initial.get("source_only") is True
            and initial.get("exhaustive") is True and initial.get("drift") is False
            and bounded_text(initial.get("native_session")) and sha256_text(initial.get("fingerprint"))
            and unsigned(initial.get("total"), 512) and isinstance(initial.get("items"), list)
            and len(initial["items"]) == initial["total"], "initial source inspection is incomplete")
    require(same_json(_get(data["create-plan"], "plan", "body", "intent"), {"kind": "create", "spec": original_spec}),
            "create plan differs from canonical source")
    plan_ref = _get(data["create-plan"], "plan_ref")
    require(local_id(plan_ref) and _get(data["create-apply"], "plan_ref") == plan_ref,
            "create apply references another plan")
    original = verify_snapshot(_get(data["create-apply"], "snapshot"), original_spec)
    require(original["native_session"] == initial["native_session"]
            and same_json(original, data["create-readback"])
            and _get(data["create-apply"], "island") == original["island"], "created source changed within its attempt")
    original_persist = data["create-persist"]
    previous_artifact = {key: original_persist.get("path" if key == "relative_path" else key)
                         for key in ("relative_path", "sha256", "bytes", "island", "source_fingerprint", "inventory", "method")}
    verify_artifact(previous_artifact, original, original_spec)
    _persist_matches(original_persist, previous_artifact)
    latest = original
    if "source-reopen" in data:
        latest = verify_snapshot(data["source-reopen"], original_spec)
        verify_reopen(original, latest)
    if edited:
        require(same_json(data["edit-inspect"], latest), "source changed before the requested edit")
        edit = evidence["source"]["native_edits"][0]
        expected_intent = {"kind": "transform", "island": latest["island"],
                           "entity": "node_" + canonical_uuid(edit["node_id"]),
                           "transform": edit["transform"], "meters_per_unit": 1.0,
                           "expected_fingerprint": latest["fingerprint"]}
        require(same_json(_get(data["edit-plan"], "plan", "body", "intent"), expected_intent), "edit plan differs from its declared delta")
        require(local_id(_get(data["edit-plan"], "plan_ref"))
                and _get(data["edit-plan"], "plan_ref") == _get(data["edit-apply"], "plan_ref"), "edit apply references another plan")
        changed = verify_snapshot(_get(data["edit-apply"], "snapshot"), spec)
        verify_delta(latest, changed, edit["node_id"])
        require(same_json(changed, data["edit-readback"])
                and _get(data["edit-apply"], "island") == changed["island"], "edited source drifted before persist")
        latest = changed
        _persist_matches(data["edit-persist"], evidence["artifact"])
        require(evidence["artifact"]["relative_path"] != previous_artifact["relative_path"]
                and evidence["artifact"]["sha256"] != previous_artifact["sha256"], "edit overwrote or reused the original artifact")
    else:
        _persist_matches(original_persist, evidence["artifact"])
    if "edited-reopen" in data:
        again = verify_snapshot(data["edited-reopen"], spec)
        verify_reopen(latest, again)
        require(again["native_session"] != original["native_session"], "final reopen reused the original native session")
        latest = again
    require(same_json(evidence["snapshot"], latest), "final snapshot differs from the declared phase")
    persist_name = "edit-persist" if edited else "create-persist"
    request = stage_by_name[persist_name]["request_id"][:-(len(persist_name) + 1)]
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request) is not None, "persist request prefix is malformed")
    expected_path = "blender-editable-{}-{}-{}.blend".format(
        canonical_uuid(stamp["scene_id"]), evidence["source"]["source_sha256"][:16],
        hashlib.sha256(request.encode("utf-8")).hexdigest()[:16])
    require(evidence["artifact"]["relative_path"] == expected_path, "artifact filename is not bound to source and attempt")
    return spec


def analytic_geometry(shape):
    """Bounded independent reference geometry, in native local coordinates."""
    if shape["kind"] == "mesh":
        return copy.deepcopy(shape["vertices"]), copy.deepcopy(shape["faces"])
    count, radius, half_depth = shape["segments"], shape["radius"], shape["depth"] / 2.0
    require(count == 32 and radius == 1.0 and half_depth == 1.0, "unsupported analytic cylinder")
    ring = [(math.cos(2.0 * math.pi * index / count) * radius,
             math.sin(2.0 * math.pi * index / count) * radius) for index in range(count)]
    vertices = [[x, y, z] for z in (-half_depth, half_depth) for x, y in ring]
    faces = [list(range(count - 1, -1, -1)), list(range(count, 2 * count))]
    faces.extend([[index, (index + 1) % count, (index + 1) % count + count, index + count] for index in range(count)])
    return vertices, faces


def edge_set(faces):
    return {tuple(sorted((face[index], face[(index + 1) % len(face)])))
            for face in faces for index in range(len(face))}


def polygon_normal(vertices, face):
    # Newell's formula independently checks outward winding, including ngons.
    total = [0.0, 0.0, 0.0]
    for index, point_id in enumerate(face):
        a, b = vertices[point_id], vertices[face[(index + 1) % len(face)]]
        total[0] += (a[1] - b[1]) * (a[2] + b[2])
        total[1] += (a[2] - b[2]) * (a[0] + b[0])
        total[2] += (a[0] - b[0]) * (a[1] + b[1])
    length = math.sqrt(sum(component * component for component in total))
    require(length > 1.0e-10, "source polygon is degenerate")
    return [component / length for component in total]


def verify_geometry(observed, shape):
    vertices, faces = analytic_geometry(shape)
    require(isinstance(observed["vertices"], list) and len(observed["vertices"]) == len(vertices)
            and all(vector_close(actual, expected) for actual, expected in zip(observed["vertices"], vertices)),
            "physical mesh vertices differ from the analytic primitive")
    require(observed["faces"] == faces, "physical mesh topology or winding differs")
    expected_edges = edge_set(faces)
    edges = observed["edges"]
    require(len(edges) == len(expected_edges) and len({tuple(sorted(edge)) for edge in edges}) == len(edges)
            and {tuple(sorted(edge)) for edge in edges} == expected_edges, "physical mesh has missing, duplicate or loose edges")
    require(observed["loops"] == [index for face in faces for index in face], "physical mesh corner topology differs")
    require(len(observed["normals"]) == len(faces)
            and all(vector_close(normal, polygon_normal(vertices, face)) for normal, face in zip(observed["normals"], faces)),
            "physical polygon normals are not analytic outward source normals")
    require(observed["material_indices"] == [0] * len(faces)
            and all(value is False for value in observed["smooth"])
            and len(observed["smooth"]) == len(faces), "physical mesh material assignment or flat normals differ")
    return {"vertices": len(vertices), "edges": len(expected_edges), "polygons": len(faces),
            "corners": sum(len(face) for face in faces),
            "analytic_shape": "quad" if shape["kind"] == "mesh" else "cylinder_32",
            "vertices_verified": True, "faces_and_winding_verified": True,
            "normals_verified": True, "loose_edges_absent": True}


def _no_animation(data, label):
    require(getattr(data, "animation_data", None) is None, label + " has animation or drivers outside the static subset")


def _local(data, label):
    require(data.library is None and data.override_library is None, label + " has a linked or overridden dependency")
    _no_animation(data, label)


def _custom_keys(data, expected, label):
    require(set(data.keys()) == set(expected), label + " contains unsupported custom source properties")


def read_material(material, expected, island):
    _local(material, "material")
    _custom_keys(material, [ISLAND, MATERIAL], "material")
    require(material.get(ISLAND) == island and material.get(MATERIAL) == expected["id"]
            and material.name == native_name(island, expected["id"]), "physical material identity differs")
    require(material.use_nodes and material.node_tree is not None, "physical material is not a node material")
    tree = material.node_tree
    _local(tree, "material node tree")
    require(len(tree.nodes) == 2 and len(tree.links) == 1, "physical material graph is outside the two-node subset")
    shader = tree.nodes.get("Principled BSDF")
    output = tree.nodes.get("Material Output")
    require(shader is not None and shader.bl_idname == "ShaderNodeBsdfPrincipled"
            and output is not None and output.bl_idname == "ShaderNodeOutputMaterial"
            and not shader.mute and not output.mute and output.is_active_output,
            "physical material nodes are missing, muted or inactive")
    require(output.target == "ALL", "material output targets another renderer")
    link = list(tree.links)[0]
    require(link.is_valid and not link.is_muted and link.from_node == shader and link.to_node == output
            and link.from_socket.identifier == "BSDF" and link.to_socket.identifier == "Surface",
            "physical material link does not connect Principled BSDF to Surface")
    ports = {}
    for socket in ("Base Color", "Alpha", "Roughness", "Metallic", "Emission Color", "Emission Strength"):
        value = shader.inputs.get(socket)
        require(value is not None and not value.is_linked, "effective material input is missing or externally linked")
        ports[socket] = value.default_value
    row = {"id": material.get(MATERIAL), "name": material.name, "color": list(material.diffuse_color),
           "roughness": material.roughness, "metallic": material.metallic, "library": False,
           "base_color": list(ports["Base Color"]), "opacity": float(ports["Alpha"]),
           "emission_color": list(ports["Emission Color"]), "emission_strength": float(ports["Emission Strength"]),
           "nodes": [], "links": [["Principled BSDF", "BSDF", "Material Output", "Surface"]], "texture_bindings": []}
    for node in tree.nodes:
        inputs = []
        for socket in node.inputs:
            if hasattr(socket, "default_value"):
                value = socket.default_value
                if isinstance(value, (float, int, bool)):
                    inputs.append([socket.identifier, value])
                elif hasattr(value, "__len__") and not isinstance(value, str) and len(value) <= 4:
                    inputs.append([socket.identifier, list(value)])
        row["nodes"].append({"name": node.name, "type": node.bl_idname, "inputs": inputs})
    verify_material_row(row, expected, island)
    return row


def read_object(obj, entity, material, collection, island):
    _local(obj, "object")
    _custom_keys(obj, [ISLAND, ENTITY, "sw_display_name"], "object")
    require(obj.type == "MESH" and obj.get(ISLAND) == island and obj.get(ENTITY) == entity["id"]
            and obj.name == native_name(island, entity["id"])
            and obj.get("sw_display_name") == entity["name"], "physical object lost logical or display identity")
    require(obj.parent is None and obj.parent_type == "OBJECT" and obj.parent_bone == ""
            and obj.rotation_mode == "XYZ" and not obj.is_instancer and obj.instance_type == "NONE"
            and obj.instance_collection is None and not obj.hide_render and not obj.hide_viewport,
            "physical object has unsupported parent, instancing, rotation or visibility state")
    require(list(obj.users_collection) == [collection] and not obj.modifiers and not obj.constraints
            and not obj.vertex_groups, "physical object has external membership or evaluated dependencies")
    require(vector_close(list(obj.delta_location), [0.0, 0.0, 0.0])
            and vector_close(list(obj.delta_rotation_euler), [0.0, 0.0, 0.0])
            and vector_close(list(obj.delta_rotation_quaternion), [1.0, 0.0, 0.0, 0.0])
            and vector_close(list(obj.delta_scale), [1.0, 1.0, 1.0]), "physical object has undeclared delta transforms")
    mesh = obj.data
    _local(mesh, "mesh")
    _custom_keys(mesh, [ISLAND], "mesh")
    require(mesh.get(ISLAND) == island and mesh.name == native_name(island, entity["id"])
            and mesh.users == 1 and mesh.shape_keys is None, "physical mesh has shared, animated or foreign data")
    require(len(mesh.vertices) <= 64 and len(mesh.polygons) <= 34 and len(mesh.loops) <= 192
            and len(mesh.edges) <= 96 and len(mesh.uv_layers) == 0 and len(mesh.color_attributes) == 0
            and not mesh.has_custom_normals, "physical mesh exceeds the static source profile")
    builtins = {"position", ".edge_verts", ".corner_vert", ".corner_edge", "material_index",
                "sharp_face", "sharp_edge", ".select_vert", ".select_edge", ".select_poly"}
    require(all(attribute.name in builtins for attribute in mesh.attributes), "physical mesh contains undeclared attributes")
    require(all(not vertex.groups for vertex in mesh.vertices), "physical mesh contains vertex weights")
    geometry = {"vertices": [list(vertex.co) for vertex in mesh.vertices],
                "faces": [list(face.vertices) for face in mesh.polygons],
                "normals": [list(face.normal) for face in mesh.polygons],
                "edges": [list(edge.vertices) for edge in mesh.edges],
                "loops": [corner.vertex_index for corner in mesh.loops],
                "material_indices": [face.material_index for face in mesh.polygons],
                "smooth": [face.use_smooth for face in mesh.polygons]}
    topology = verify_geometry(geometry, entity["shape"])
    require(len(mesh.materials) == 1 and mesh.materials[0] is not None
            and len(obj.material_slots) == 1 and obj.material_slots[0].link == "DATA"
            and obj.material_slots[0].material == mesh.materials[0], "physical material slot is missing or overridden")
    material_row = read_material(mesh.materials[0], material, island)
    geometry_digest = source_projection_digest({
        "vertices": geometry["vertices"],
        "faces": [[face, material_id, smooth] for face, material_id, smooth in zip(geometry["faces"], geometry["material_indices"], geometry["smooth"])],
        "uv": [], "weights": [[] for _ in geometry["vertices"]], "groups": [], "attributes": [],
    })
    row = {"entity": obj.get(ENTITY), "name": obj.name, "type": obj.type, "native_island": obj.get(ISLAND),
           "library": False, "translation": list(obj.location), "rotation": list(obj.rotation_euler), "scale": list(obj.scale),
           "parent": None, "parent_name": None, "parent_type": obj.parent_type, "parent_bone": obj.parent_bone,
           "parent_inverse": [list(matrix_row) for matrix_row in obj.matrix_parent_inverse],
           "collections": sorted(item.name for item in obj.users_collection), "hidden_render": obj.hide_render,
           "hidden_viewport": obj.hide_viewport, "modifiers": [], "constraints": [], "action": None,
           "actions": [], "nla_tracks": [], "drivers": 0, "data_name": mesh.name, "data_library": False,
           "geometry_digest": geometry_digest, "normal_digest": source_projection_digest(geometry["normals"]),
           "normal_method": "source-polygon-normal-v1", "smooth_polygons": 0, "attributes": [],
           "vertices": len(mesh.vertices), "polygons": len(mesh.polygons), "uv_layers": 0,
           "data_users": mesh.users, "materials": [material_row]}
    return row, {"entity": entity["id"], "native_name": obj.name, "display_name_verified": True,
                 "material_id": material["id"], "material_source_and_effective_sockets_verified": True,
                 "geometry_digest": geometry_digest, "normal_digest": row["normal_digest"], **topology}


def read_blend(bpy, pinned, evidence, spec):
    require(tuple(bpy.app.version[:2]) == (4, 5), "oracle requires Blender 4.5", "unsupported_runtime")
    require(bpy.app.background and all(flag in sys.argv for flag in ("--background", "--factory-startup", "--disable-autoexec"))
            and bpy.context.preferences.filepaths.use_scripts_auto_execute is False,
            "oracle requires a fresh background process with auto-execution disabled", "unsafe_runtime")
    scene = bpy.context.scene
    context = {"scene_units": scene.unit_settings.scale_length, "fps": scene.render.fps,
               "fps_base": scene.render.fps_base, "frame": scene.frame_current}
    require(same_json(context, {"scene_units": 1.0, "fps": 24, "fps_base": 1.0, "frame": 1}),
            "factory source fingerprint context differs; the oracle will not change it", "unsupported_source_context")
    island = evidence["artifact"]["island"]
    name = native_name(island, spec["collection"])
    require(not any(item.get(ISLAND) == island for item in bpy.data.collections)
            and bpy.data.collections.get(name) is None, "native island existed before independent readback", "unsafe_runtime")
    expected_inventory = {
        "collections": [name], "objects": sorted(native_name(island, entity["id"]) for entity in spec["entities"]),
        "meshes": sorted(native_name(island, entity["id"]) for entity in spec["entities"]),
        "materials": sorted(native_name(island, material["id"]) for material in spec["materials"]),
    }
    for root, names in expected_inventory.items():
        require(all(getattr(bpy.data, root).get(value) is None for value in names), "native source name collides with process state")
    unsupported_roots = (
        "actions", "armatures", "brushes", "cache_files", "cameras", "curves", "fonts", "grease_pencils",
        "hair_curves", "images", "lattices", "lights", "linestyles", "masks", "metaballs", "movieclips",
        "node_groups", "paint_curves", "palettes", "particles", "pointclouds", "scenes", "sounds",
        "speakers", "texts", "textures", "volumes", "worlds",
    )
    pinned.verify_unchanged()
    try:
        with bpy.data.libraries.load(str(pinned.path), link=False) as (available, requested):
            for root, names in expected_inventory.items():
                actual = list(getattr(available, root))
                require(len(actual) <= MAX_ENTITIES and sorted(actual) == names,
                        "physical .blend inventory has extra, missing or ambiguous " + root)
            for root in unsupported_roots:
                require(not getattr(available, root, []), "physical .blend includes an unsupported datablock class: " + root)
            requested.collections = [name]
    except OracleError:
        raise
    except Exception as error:
        raise OracleError("blend_load_failed", "Blender did not accept the exact Collection library input") from error
    pinned.verify_unchanged()
    collection = bpy.data.collections.get(name)
    require(collection is not None and collection.get(ISLAND) == island
            and collection.get("sw_display_name") == spec["collection"], "physical Collection identity differs")
    _local(collection, "Collection")
    _custom_keys(collection, [ISLAND, MARKER, "sw_display_name"], "Collection")
    require(not collection.children and not collection.hide_render and not collection.hide_viewport
            and vector_close(list(collection.instance_offset), [0.0, 0.0, 0.0]), "physical Collection has unreported hierarchy or visibility state")
    objects = sorted(collection.all_objects, key=lambda obj: (str(obj.get(ENTITY, "")), obj.name))
    require(len(objects) == len(spec["entities"]) and len(collection.objects) == len(objects), "physical Collection is not a flat complete entity set")
    entities = {entity["id"]: entity for entity in spec["entities"]}
    materials = {material["id"]: material for material in spec["materials"]}
    require({obj.get(ENTITY) for obj in objects} == set(entities), "physical Collection entity identities differ")
    rows, checks = [], []
    for obj in objects:
        entity = entities[obj.get(ENTITY)]
        row, check = read_object(obj, entity, materials[entity["materials"][0]], collection, island)
        rows.append(row)
        checks.append(check)
    require(same_json(rows, evidence["snapshot"]["items"]), "physical source readback differs from the complete input snapshot")
    fingerprint = source_projection_digest({"schema": "blender-source-projection-v4", "items": rows, **context})
    require(fingerprint == evidence["snapshot"]["fingerprint"] == evidence["artifact"]["source_fingerprint"]
            and collection.get(MARKER) == fingerprint, "physical source fingerprint or persisted marker differs")
    require(sorted(obj.name for obj in objects) == sorted(evidence["artifact"]["inventory"]["objects"]), "physical object inventory differs from persist declaration")
    # The source file remains pinned after all reads, not merely after library load.
    pinned.verify_unchanged()
    build_hash = bpy.app.build_hash
    return {"runtime": {"blender_version": bpy.app.version_string,
                         "blender_build_hash": build_hash.decode("ascii", "replace") if isinstance(build_hash, bytes) else str(build_hash),
                         "process_id": os.getpid(), "background": True, "auto_execution_disabled": True},
            "source_fingerprint": fingerprint, "source_fingerprint_context": context,
            "source_fingerprint_context_origin": "independently_read_factory_scene_not_persisted_scene",
            "inventory": evidence["artifact"]["inventory"], "entities": checks,
            "collection_display_name_verified": True, "closed_source_dependencies_verified": True,
            "source_rows_exactly_preserved": True, "file_bytes_unchanged": True}


def run_oracle(args):
    root = physical_root(args.workspace)
    validate_relative(args.evidence, ".json")
    validate_relative(args.output, ".json")
    require(args.evidence != args.output, "input and output must differ", "output_conflict")
    physical_path(root, args.output, existing=False)
    report = {"schema": OUTPUT_SCHEMA, "status": "failed", "source_only": True,
              "independent_file_readback_verified": False, "original_execution_authority_verified": False,
              "input_receipts_are_authority": False, "source_sha256_recomputed": False,
              "raster_verified": False, "audio_verified": False, "full_scene_verified": False,
              "canonical_edit_imported": False, "external_rights_verified": False,
              "source_contract_semwright_pin": CONTRACT_PIN,
              "source_contract_pin_is_runtime_attestation": False,
              "input_evidence_sha256": args.evidence_sha256,
              "limitations": [
                  "source_collection_only_no_render_or_audio", "factory_context_required_for_source_fingerprint",
                  "serde_source_digest_declaration_is_input_bound_and_verified_by_consumer",
                  "original_journal_and_authority_require_independent_consumer_verification",
                  "historical_before_file_is_not_loaded_by_this_observation",
                  "native_edit_execution_and_canonical_import_require_separate_evidence",
                  "physical_path_checks_do_not_supply_atomic_descriptor_handoff_to_blender",
              ]}
    code = 0
    try:
        with PinnedFile(root, args.evidence, args.evidence_sha256, MAX_EVIDENCE_BYTES) as input_file:
            evidence = parse_json(input_file.data)
            spec = validate_evidence(evidence)
            artifact = evidence["artifact"]
            with PinnedFile(root, artifact["relative_path"], artifact["sha256"], MAX_BLEND_BYTES, artifact["bytes"]) as blend:
                # Deliberately deferred: --self-test never imports or mocks bpy.
                try:
                    import bpy
                except ImportError as error:
                    raise OracleError("unsupported_runtime", "native readback must run inside Blender 4.5") from error
                observed = read_blend(bpy, blend, evidence, spec)
                input_file.verify_unchanged()
                blend.verify_unchanged()
            source = evidence["source"]
            stamp = source["canonical_contribution"]
            report.update(observed)
            report.update({"status": "pass", "independent_file_readback_verified": True,
                           "fresh_process_collection_readback_verified": True,
                           "input_stage_chain_coherent": True,
                           "declared_source_sha256": source["source_sha256"],
                           "source_sha256_binding": "exact_input_evidence_bytes",
                           "artifact": {key: artifact[key] for key in ("relative_path", "sha256", "bytes")},
                           "project_stamp": {key: stamp[key] for key in ("project_id", "generation", "revision", "scene_id")},
                           "phase": evidence["phase"], "logical_identity_and_source_parameters_verified": True,
                           "canonical_to_file_transform_delta_verified": bool(source["native_edits"]),
                           "historical_before_file_readback_verified": False,
                           "native_edit_execution_verified": False,
                           "declared_native_edits": source["native_edits"],
                           "input_evidence_bytes": input_file.size})
    except OracleError as error:
        report["error"] = {"code": error.code, "message": str(error)}
        code = 2
    except Exception as error:
        # No raw exception text: it could contain private paths or provider input.
        report["error"] = {"code": "oracle_runtime_error", "exception_type": type(error).__name__}
        code = 3
    result = write_new_json(root, args.output, report)
    print(json.dumps({"status": report["status"], "artifact": result}, sort_keys=True))
    return code


def synthetic_source(edited=False):
    objects = [
        {"node_id": "01234567-89ab-4000-8000-000000000001", "name": "Rectángulo editable",
         "material_name": "legacy-rect-material", "color_rgba": [0.2, 0.4, 0.8, 0.5], "primitive": "plane",
         "location": [1.0, 2.0, 0.0], "rotation": [0.0, 0.0, 0.25], "scale": [2.0, 3.0, 1.0]},
        {"node_id": "01234567-89ab-4000-8000-000000000002", "name": "Cylinder editable",
         "material_name": "legacy-circle-material", "color_rgba": [0.8, 0.3, 0.1, 1.0], "primitive": "cylinder",
         "location": [-1.0, 1.0, 0.01], "rotation": [0.0, 0.0, 0.0], "scale": [1.5, 1.5, 0.005]},
    ]
    contribution = {"project_id": "aaaaaaaa-0000-4000-8000-000000000000", "generation": "bbbbbbbb-0000-4000-8000-000000000000",
                    "revision": 12, "scene_id": "cccccccc-0000-4000-8000-000000000000", "collection_name": "source-scene", "objects": objects}
    edits = [{"node_id": objects[0]["node_id"], "transform": {"translation": [3.0, 2.0, 0.0], "rotation": objects[0]["rotation"], "scale": objects[0]["scale"]}}] if edited else []
    return {"schema": SOURCE_SCHEMA, "source_contract_semwright_pin": CONTRACT_PIN,
            "project_resource": "synthetic-project-resource", "canonical_contribution": contribution,
            "native_edits": edits, "source_sha256": "a" * 64, "spec": expected_spec(contribution, edits)}


def synthetic_geometry(shape):
    vertices, faces = analytic_geometry(shape)
    return {"vertices": vertices, "faces": faces, "edges": [list(edge) for edge in sorted(edge_set(faces))],
            "loops": [index for face in faces for index in face],
            "normals": [polygon_normal(vertices, face) for face in faces],
            "material_indices": [0] * len(faces), "smooth": [False] * len(faces)}


def synthetic_material(material, island):
    ports = [["Base Color", material["base_color"]], ["Alpha", material["opacity"]],
             ["Roughness", material["roughness"]], ["Metallic", material["metallic"]],
             ["Emission Color", material["emission_color"]], ["Emission Strength", material["emission_strength"]]]
    return {"id": material["id"], "name": native_name(island, material["id"]), "color": material["base_color"],
            "roughness": material["roughness"], "metallic": material["metallic"], "library": False,
            "base_color": material["base_color"], "opacity": material["opacity"],
            "emission_color": material["emission_color"], "emission_strength": material["emission_strength"],
            "texture_bindings": [], "links": [["Principled BSDF", "BSDF", "Material Output", "Surface"]],
            "nodes": [{"name": "Principled BSDF", "type": "ShaderNodeBsdfPrincipled", "inputs": ports},
                      {"name": "Material Output", "type": "ShaderNodeOutputMaterial", "inputs": []}]}


def synthetic_snapshot(source, session="synthetic_session_a", island="synthetic_island"):
    """JSON-only test input. It is never presented as output from Blender."""
    spec = source["spec"]
    materials = {material["id"]: material for material in spec["materials"]}
    rows = []
    for entity in sorted(spec["entities"], key=lambda value: value["id"]):
        geometry = synthetic_geometry(entity["shape"])
        mesh_projection = {
            "vertices": geometry["vertices"],
            "faces": [[face, 0, False] for face in geometry["faces"]],
            "uv": [], "weights": [[] for _ in geometry["vertices"]], "groups": [], "attributes": [],
        }
        rows.append({
            "entity": entity["id"], "name": native_name(island, entity["id"]), "type": "MESH",
            "native_island": island, "library": False, **copy.deepcopy(entity["transform"]),
            "parent": None, "parent_name": None, "parent_type": "OBJECT", "parent_bone": "",
            "parent_inverse": [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]],
            "collections": [native_name(island, spec["collection"])], "hidden_render": False,
            "hidden_viewport": False, "modifiers": [], "constraints": [], "action": None,
            "actions": [], "nla_tracks": [], "drivers": 0, "data_name": native_name(island, entity["id"]),
            "data_library": False, "geometry_digest": source_projection_digest(mesh_projection),
            "normal_digest": source_projection_digest(geometry["normals"]), "normal_method": "source-polygon-normal-v1",
            "smooth_polygons": 0, "attributes": [], "vertices": len(geometry["vertices"]),
            "polygons": len(geometry["faces"]), "uv_layers": 0, "data_users": 1,
            "materials": [synthetic_material(materials[entity["materials"][0]], island)],
        })
    fingerprint = source_projection_digest({"schema": "blender-source-projection-v4", "items": rows,
                                            "scene_units": 1.0, "fps": 24, "fps_base": 1.0, "frame": 1})
    return {"native_session": session, "island": island, "fingerprint": fingerprint, "drift": False,
            "total": len(rows), "items": rows, "source_only": True, "exhaustive": True}


def synthetic_evidence(phase):
    """Fabricated test declarations; never dispatches or impersonates native APIs."""
    edited = phase in ("transform_persisted", "edited_source_reopened")
    original_source = synthetic_source()
    source = synthetic_source(edited)
    if edited:
        source["source_sha256"] = "b" * 64
    original = synthetic_snapshot(original_source)
    reopened = synthetic_snapshot(original_source, "synthetic_session_b")
    changed = synthetic_snapshot(source, "synthetic_session_b")
    again = synthetic_snapshot(source, "synthetic_session_c")

    def artifact(src, snapshot, prefix, digest):
        return {"relative_path": "blender-editable-{}-{}-{}.blend".format(
                    canonical_uuid(src["canonical_contribution"]["scene_id"]), src["source_sha256"][:16],
                    hashlib.sha256(prefix.encode()).hexdigest()[:16]),
                "sha256": digest, "bytes": 128, "island": snapshot["island"],
                "source_fingerprint": snapshot["fingerprint"],
                "inventory": {"collection": snapshot["items"][0]["collections"][0],
                              "objects": [row["name"] for row in snapshot["items"]]},
                "method": "libraries-write-indirect-closure"}

    old_artifact = artifact(original_source, original, "syntheticcreate", "c" * 64)
    new_artifact = artifact(source, changed, "syntheticedit", "d" * 64)

    def persist(value):
        return {("path" if key == "relative_path" else key): child for key, child in value.items()} | {"fresh_process_verified": False}

    fake_report = {"test_scope": "synthetic_only_report_not_interpreted_as_verification"}
    payloads = {
        "create-inspect": {"native_session": original["native_session"], "island": None,
                           "fingerprint": "f" * 64, "drift": False, "total": 0, "items": [],
                           "source_only": True, "exhaustive": True},
        "create-plan": {"plan_ref": "synthetic_create_plan", "plan": {"body": {"intent": {"kind": "create", "spec": original_source["spec"]}}, "digest": "e" * 64}},
        "create-apply": {"plan_ref": "synthetic_create_plan", "island": original["island"], "snapshot": original, "report": fake_report},
        "create-verify": fake_report, "create-readback": original, "create-persist": persist(old_artifact),
        "source-reopen": reopened, "edit-inspect": reopened,
        "edit-apply": {"plan_ref": "synthetic_edit_plan", "island": changed["island"], "snapshot": changed, "report": fake_report},
        "edit-verify": fake_report, "edit-readback": changed, "edit-persist": persist(new_artifact),
        "edited-reopen": again,
    }
    if edited:
        edit = source["native_edits"][0]
        payloads["edit-plan"] = {"plan_ref": "synthetic_edit_plan", "plan": {"body": {"intent": {
            "kind": "transform", "island": reopened["island"], "entity": "node_" + canonical_uuid(edit["node_id"]),
            "transform": edit["transform"], "meters_per_unit": 1.0, "expected_fingerprint": reopened["fingerprint"],
        }}, "digest": "e" * 64}}
    stages = []
    stamp = source["canonical_contribution"]
    for stage in PHASE_STAGES[phase]:
        prefix = "syntheticcreate" if stage in CREATE_STAGES else "syntheticedit" if stage in EDIT_STAGES else "syntheticreopen"
        suffix = stage.rsplit("-", 1)[1]
        command = "driver.blender.composition." + {"readback": "inspect"}.get(suffix, suffix)
        generation = 1 if stage in CREATE_STAGES else 2 if stage != "edited-reopen" else 3
        proof = {"stage": stage, "command": command, "request_id": prefix + "-" + stage,
                 "request_sha256": hashlib.sha256(stage.encode()).hexdigest(),
                 "connection_identity": str(generation) * 64, "provider_generation": generation,
                 "descriptor_sha256": "e" * 64}
        result = {"data": copy.deepcopy(payloads[stage]), "provenance_checked": True,
                  "execution": {"backend": "driver:blender", "dry_run": False, "policy_decision": "allow",
                                "provenance": {"provider": "driver:blender", "source": "driver",
                                               "execution_provider": "driver:blender", "untrusted_metadata": False,
                                               "provider_generation": generation, "descriptor_sha256": proof["descriptor_sha256"]}}}
        receipt = {key: proof[key] for key in ("command", "request_id", "request_sha256")}
        receipt.update({key: stamp[key] for key in ("project_id", "generation", "revision")})
        receipt.update({"stage": "completed", "payload": {"result": copy.deepcopy(result), "broker_request_id": "synthetic_not_an_authority_receipt"}})
        proof["response"] = {"replayed": False, "result": result, "receipt": receipt}
        stages.append(proof)
    snapshot = {"source_persisted": original, "source_reopened": reopened,
                "transform_persisted": changed, "edited_source_reopened": again}[phase]
    return {"schema": EVIDENCE_SCHEMA, "phase": phase, "source": source, "snapshot": snapshot,
            "artifact": new_artifact if edited else old_artifact, "stages": stages,
            "source_parameter_readback": True, "fresh_process_reopened": phase in ("source_reopened", "edited_source_reopened"),
            "native_transform_edit_persisted": edited, "independent_file_readback_verified": False,
            "raster_verified": False, "audio_verified": False, "full_scene_verified": False,
            "canonical_edit_imported": False, "limitations": LIMITATIONS.copy()}


class OracleSelfTests(unittest.TestCase):
    """Synthetic verifier tests. No class or function here impersonates bpy."""

    workspace = None

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="blender-oracle-selftest-", dir=self.workspace)
        self.root = physical_root(str(Path(self.temporary.name)))

    def tearDown(self):
        self.temporary.cleanup()

    def rejected(self, action, code=None):
        with self.assertRaises(OracleError) as failure:
            action()
        if code:
            self.assertEqual(failure.exception.code, code)

    def test_no_bpy_import(self):
        self.assertNotIn("bpy", sys.modules)

    def test_source_preserves_full_logical_ids(self):
        source = synthetic_source()
        spec = validate_source(source)
        self.assertEqual(len({entity["id"] for entity in spec["entities"]}), 2)
        for obj, entity in zip(source["canonical_contribution"]["objects"], spec["entities"]):
            self.assertTrue(entity["id"].endswith(uuid.UUID(obj["node_id"]).hex))

    def test_source_rejects_duplicate_identity(self):
        source = synthetic_source()
        source["canonical_contribution"]["objects"][1]["node_id"] = source["canonical_contribution"]["objects"][0]["node_id"]
        self.rejected(lambda: validate_source(source))

    def test_source_names_match_the_pinned_eighty_byte_contract(self):
        original = synthetic_source()
        for name in ("x" * 81, "\u00e9" * 41, "bad\nname", "bad\x85name", ""):
            changed = copy.deepcopy(original["canonical_contribution"])
            changed["objects"][0]["name"] = name
            self.rejected(lambda: expected_spec(changed, []))
        for name in ("x" * 80, "\u00e9" * 40):
            changed = copy.deepcopy(original["canonical_contribution"])
            changed["objects"][0]["name"] = name
            self.assertEqual(expected_spec(changed, [])["entities"][0]["name"], name)

    def test_source_rejects_projection_tampering(self):
        source = synthetic_source()
        source["spec"]["entities"][0]["transform"]["translation"] = [8.0, 9.0, 0.0]
        self.rejected(lambda: validate_source(source))

    def test_source_rejects_unknown_texture_or_field(self):
        source = synthetic_source()
        source["spec"]["materials"][0]["base_color_texture"] = {"path": "external.png"}
        self.rejected(lambda: validate_source(source))
        source = synthetic_source()
        source["spec"]["camera"] = "not-covered"
        self.rejected(lambda: validate_source(source))

    def test_source_rejects_noop_edit(self):
        source = synthetic_source(True)
        obj = source["canonical_contribution"]["objects"][0]
        source["native_edits"][0]["transform"] = {"translation": obj["location"], "rotation": obj["rotation"], "scale": obj["scale"]}
        self.rejected(lambda: validate_source(source))

    def test_source_rejects_nonfinite_and_bool_coordinates(self):
        for value in (float("nan"), float("inf"), True):
            source = synthetic_source()
            source["canonical_contribution"]["objects"][0]["location"][0] = value
            self.rejected(lambda: validate_source(source))

    def test_quad_and_cylinder_analytic_checks(self):
        for entity in synthetic_source()["spec"]["entities"]:
            result = verify_geometry(synthetic_geometry(entity["shape"]), entity["shape"])
            self.assertTrue(result["normals_verified"])

    def test_geometry_rejects_wrong_vertex_with_same_counts(self):
        shape = synthetic_source()["spec"]["entities"][0]["shape"]
        geometry = synthetic_geometry(shape)
        geometry["vertices"][0][0] += 0.25
        self.rejected(lambda: verify_geometry(geometry, shape))

    def test_geometry_rejects_reversed_winding_and_wrong_normal(self):
        shape = synthetic_source()["spec"]["entities"][1]["shape"]
        geometry = synthetic_geometry(shape)
        geometry["faces"][0].reverse()
        self.rejected(lambda: verify_geometry(geometry, shape))
        geometry = synthetic_geometry(shape)
        geometry["normals"][0] = [0.0, 0.0, 1.0]
        self.rejected(lambda: verify_geometry(geometry, shape))

    def test_geometry_rejects_loose_edge_and_wrong_slot(self):
        shape = synthetic_source()["spec"]["entities"][0]["shape"]
        geometry = synthetic_geometry(shape)
        geometry["edges"].append([0, 2])
        self.rejected(lambda: verify_geometry(geometry, shape))
        geometry = synthetic_geometry(shape)
        geometry["material_indices"][0] = 1
        self.rejected(lambda: verify_geometry(geometry, shape))

    def test_material_rejects_base_color_alpha_only_fix(self):
        expected = synthetic_source()["spec"]["materials"][0]
        row = synthetic_material(expected, "island_test")
        verify_material_row(row, expected, "island_test")
        row["nodes"][0]["inputs"][1][1] = 1.0
        self.rejected(lambda: verify_material_row(row, expected, "island_test"))

    def test_material_rejects_wrong_effective_roughness_or_link(self):
        expected = synthetic_source()["spec"]["materials"][0]
        row = synthetic_material(expected, "island_test")
        row["nodes"][0]["inputs"][2][1] = 0.9
        self.rejected(lambda: verify_material_row(row, expected, "island_test"))
        row = synthetic_material(expected, "island_test")
        row["links"] = []
        self.rejected(lambda: verify_material_row(row, expected, "island_test"))

    def test_delta_rejects_noop_and_unrelated_source_change(self):
        node_id = synthetic_source()["canonical_contribution"]["objects"][0]["node_id"]
        row = {"entity": "node_" + canonical_uuid(node_id), "translation": [0.0, 0.0, 0.0],
               "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0], "geometry_digest": "c" * 64}
        before = {"native_session": "s", "island": "i", "total": 1, "fingerprint": "a" * 64, "items": [row]}
        after = copy.deepcopy(before)
        after["fingerprint"] = "b" * 64
        self.rejected(lambda: verify_delta(before, after, node_id))
        after["items"][0]["translation"][0] = 1.0
        verify_delta(before, after, node_id)
        after["items"][0]["geometry_digest"] = "d" * 64
        self.rejected(lambda: verify_delta(before, after, node_id))

    def test_reopen_rejects_same_process_or_semantic_loss(self):
        before = {"native_session": "before", "island": "i", "fingerprint": "a" * 64, "total": 1, "items": [{"value": 1}]}
        after = copy.deepcopy(before)
        self.rejected(lambda: verify_reopen(before, after))
        after["native_session"] = "after"
        verify_reopen(before, after)
        after["items"][0]["value"] = 2
        self.rejected(lambda: verify_reopen(before, after))

    def test_paths_reject_traversal_and_platform_aliases(self):
        for value in ("../x.blend", "/x.blend", "a//x.blend", "a/./x.blend", "a\\x.blend", "E:x.blend", "x.blend:stream", "CON.json", "x. /y.json"):
            self.rejected(lambda value=value: validate_relative(value), "unsafe_path")

    def test_same_digest_wrong_declared_bytes_is_rejected(self):
        data = b"synthetic ordinary bytes; not a blend"
        (self.root / "fixture.bin").write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        with PinnedFile(self.root, "fixture.bin", digest, 1024, len(data)):
            pass
        self.rejected(lambda: PinnedFile(self.root, "fixture.bin", digest, 1024, len(data) + 1), "file_size_mismatch")

    def test_same_size_wrong_digest_is_rejected(self):
        (self.root / "fixture.bin").write_bytes(b"abcd")
        self.rejected(lambda: PinnedFile(self.root, "fixture.bin", hashlib.sha256(b"abce").hexdigest(), 1024, 4), "file_digest_mismatch")

    def test_hard_link_is_rejected(self):
        path = self.root / "file.bin"
        path.write_bytes(b"abc")
        try:
            os.link(path, self.root / "alias.bin")
        except OSError as error:
            self.skipTest("hard links unsupported: " + type(error).__name__)
        self.rejected(lambda: PinnedFile(self.root, "file.bin", hashlib.sha256(b"abc").hexdigest(), 1024), "unsafe_path")

    def test_symlink_file_and_parent_are_rejected(self):
        path = self.root / "file.bin"
        path.write_bytes(b"abc")
        directory = self.root / "real"
        directory.mkdir()
        (directory / "file.bin").write_bytes(b"abc")
        try:
            (self.root / "alias.bin").symlink_to(path)
            (self.root / "alias-dir").symlink_to(directory, target_is_directory=True)
        except OSError as error:
            self.skipTest("symlink unsupported: " + type(error).__name__)
        for relative in ("alias.bin", "alias-dir/file.bin"):
            self.rejected(lambda relative=relative: PinnedFile(self.root, relative, hashlib.sha256(b"abc").hexdigest(), 1024), "unsafe_path")

    def test_changed_or_replaced_input_is_rejected(self):
        path = self.root / "file.bin"
        path.write_bytes(b"abc")
        with PinnedFile(self.root, "file.bin", hashlib.sha256(b"abc").hexdigest(), 1024) as pinned:
            path.write_bytes(b"abd")
            self.rejected(pinned.verify_unchanged, "file_changed")
        path.write_bytes(b"abc")
        with PinnedFile(self.root, "file.bin", hashlib.sha256(b"abc").hexdigest(), 1024) as pinned:
            replacement = self.root / "replacement.bin"
            replacement.write_bytes(b"abc")
            try:
                os.replace(replacement, path)
            except OSError as error:
                self.skipTest("open-file replacement unsupported: " + type(error).__name__)
            self.rejected(pinned.verify_unchanged, "file_changed")

    def test_json_rejects_duplicate_keys_nonfinite_and_excessive_depth(self):
        for data in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b"[" * 70 + b"0" + b"]" * 70):
            self.rejected(lambda data=data: parse_json(data), "invalid_evidence")

    def test_output_is_new_and_hashed(self):
        result = write_new_json(self.root, "output.json", {"synthetic": True})
        self.assertEqual(result["sha256"], hashlib.sha256((self.root / "output.json").read_bytes()).hexdigest())
        self.rejected(lambda: write_new_json(self.root, "output.json", {"synthetic": False}), "output_conflict")

    def test_projection_normalizes_signed_zero_only(self):
        self.assertEqual(source_projection_digest({"x": -0.0}), source_projection_digest({"x": 0.0}))
        self.assertNotEqual(source_projection_digest({"x": 1.0}), source_projection_digest({"x": 1.0 + 1.0e-10}))

    def test_four_synthetic_phase_shapes_are_coherent_not_authoritative(self):
        for phase in PHASE_STAGES:
            evidence = parse_json(json_bytes(synthetic_evidence(phase)))
            self.assertEqual(validate_evidence(evidence), evidence["source"]["spec"])

    def test_evidence_rejects_missing_duplicate_and_reordered_stages(self):
        for kind in ("missing", "duplicate", "reordered"):
            evidence = synthetic_evidence("source_persisted")
            if kind == "missing":
                evidence["stages"].pop()
            elif kind == "duplicate":
                evidence["stages"].append(copy.deepcopy(evidence["stages"][-1]))
            else:
                evidence["stages"][0], evidence["stages"][1] = evidence["stages"][1], evidence["stages"][0]
            self.rejected(lambda: validate_evidence(evidence), "invalid_evidence")

    def test_evidence_rejects_self_certified_native_or_media_coverage(self):
        for field in ("independent_file_readback_verified", "raster_verified", "audio_verified", "full_scene_verified", "canonical_edit_imported"):
            evidence = synthetic_evidence("source_persisted")
            evidence[field] = True
            self.rejected(lambda: validate_evidence(evidence), "invalid_evidence")

    def test_evidence_rejects_unknown_receipt_or_provider_change(self):
        evidence = synthetic_evidence("source_persisted")
        evidence["stages"][2]["response"]["receipt"]["stage"] = "outcome_unknown"
        self.rejected(lambda: validate_evidence(evidence))
        evidence = synthetic_evidence("source_persisted")
        proof = evidence["stages"][3]
        proof["provider_generation"] = 8
        proof["response"]["result"]["execution"]["provenance"]["provider_generation"] = 8
        proof["response"]["receipt"]["payload"]["result"] = copy.deepcopy(proof["response"]["result"])
        self.rejected(lambda: validate_evidence(evidence))

    def test_evidence_rejects_detached_persist_and_false_native_delta(self):
        evidence = synthetic_evidence("source_persisted")
        evidence["artifact"]["bytes"] += 1
        self.rejected(lambda: validate_evidence(evidence))
        evidence = synthetic_evidence("transform_persisted")
        before = next(row for row in evidence["stages"] if row["stage"] == "source-reopen")["response"]["result"]["data"]
        evidence["snapshot"]["fingerprint"] = before["fingerprint"]
        self.rejected(lambda: validate_evidence(evidence))

    def test_invalid_input_produces_failed_report_without_importing_bpy(self):
        import contextlib
        import io
        body = b'{"schema":"synthetic_invalid_input"}\n'
        (self.root / "evidence.json").write_bytes(body)
        args = argparse.Namespace(workspace=str(self.root), evidence="evidence.json",
                                  evidence_sha256=hashlib.sha256(body).hexdigest(), output="failed.json")
        with contextlib.redirect_stdout(io.StringIO()):
            code = run_oracle(args)
        self.assertEqual(code, 2)
        report = parse_json((self.root / "failed.json").read_bytes())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error"]["code"], "invalid_evidence")
        self.assertFalse(report["independent_file_readback_verified"])
        self.assertFalse(report["original_execution_authority_verified"])
        self.assertFalse(report["raster_verified"])
        self.assertNotIn("bpy", sys.modules)


def main(argv=None):
    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace")
    parser.add_argument("--evidence")
    parser.add_argument("--evidence-sha256")
    parser.add_argument("--output")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        require(not any((args.evidence, args.evidence_sha256, args.output)), "self-test accepts no production input or output", "invalid_arguments")
        root = physical_root(args.workspace or str(Path(__file__).absolute().parent))
        OracleSelfTests.workspace = str(root)
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(OracleSelfTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        print(json.dumps({"schema": "motionwright-blender-oracle-selftest/1",
                          "scope": "synthetic_verifier_tests_only_not_native_readback",
                          "tests_run": result.testsRun, "failures": len(result.failures),
                          "errors": len(result.errors), "skipped": len(result.skipped),
                          "bpy_imported": "bpy" in sys.modules,
                          "native_source_verified": False, "status": "pass" if result.wasSuccessful() else "failed"}, sort_keys=True))
        return 0 if result.wasSuccessful() else 1
    if not all((args.workspace, args.evidence, args.evidence_sha256, args.output)):
        parser.error("native readback requires --workspace, --evidence, --evidence-sha256 and --output")
    return run_oracle(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OracleError as failure:
        print(json.dumps({"status": "failed", "error": {"code": failure.code, "message": str(failure)}}, sort_keys=True), file=sys.stderr)
        raise SystemExit(2)
