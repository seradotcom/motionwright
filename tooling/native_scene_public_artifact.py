#!/usr/bin/env python3
"""Curate native scene media and bounded original files of synthetic Manim source cases."""
from __future__ import annotations

import argparse
import hashlib
import io
import math
import os
import stat
import zipfile
import json
from pathlib import Path
import re
import struct
import sys

from cross_app_public_artifact import PublicationError, read_bounded, require, strict_object

SCHEMA = "motionwright.v01-native-scene-public/1"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SCENES = {
    "blender": {
        "media": "motionwright-contribution.glb",
        "evidence": "motionwright-blender-evidence.json",
        "asset_key": "glb",
        "required_count": 24,
        "commands": {
            "driver.blender.collection.create", "driver.blender.collection.link",
            "driver.blender.export.glb", "driver.blender.material.assign",
            "driver.blender.material.create", "driver.blender.object.create",
            "driver.blender.object.transform",
        },
        "max_bytes": 128_000_000,
    },
    "manim": {
        "media": "motionwright-manim-community.mp4",
        "evidence": "motionwright-manim-evidence.json",
        "asset_key": "mp4",
        # Manim async polling can add completed status observations.
        # The E2E caps receipt collection at 256, so require the original
        # operation stages instead of falsely demanding exactly 10 rows.
        "max_receipts": 256,
        "required_completed_commands": {
            "driver.manim-community.render.start",
            "driver.manim-community.render.status",
            "driver.manim-community.render.result",
        },
        "commands": {
            "driver.manim-community.doctor", "driver.manim-community.render.cancel",
            "driver.manim-community.render.result", "driver.manim-community.render.start",
            "driver.manim-community.render.status",
        },
        "max_bytes": 128_000_000,
    },
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verified_private_scene(root: Path, source: str, pin: str, scene: str) -> bytes:
    spec = SCENES[scene]
    require(root.is_dir() and not root.is_symlink(),
            "missing_or_linked_private_evidence_root")
    result = strict_object(read_bounded(root, "result.json", 128_000))
    receipt = strict_object(read_bounded(root, spec["evidence"], 1_000_000))
    asset = result.get(spec["asset_key"])
    original = receipt.get("result")
    project = result.get("project")
    completed = receipt.get("completed_commands")
    count = receipt.get("receipt_count")
    # The native E2E's append-only journal records async polling attempts.
    # Exact rows vary with provider responsiveness; counting 10 precisely
    # rejected an otherwise verified real MP4. Do not relax any authority,
    # source, original stage or artifact digest check.
    manim_receipts_valid = (
        type(count) is int and 3 <= count <= spec["max_receipts"]
        and isinstance(completed, list) and len(completed) >= 3
        and all(isinstance(command, str) and command in spec["commands"]
                for command in completed)
        and len(completed) == len(set(completed))
        and spec["required_completed_commands"] <= set(completed)
        and count >= len(completed)
    ) if scene == "manim" else True
    require(
        result.get(f"native_{scene}_e2e") == "PASS"
        and receipt.get(f"native_{scene}_e2e") == "PASS"
        and result.get("motionwright_sha") == source
        and result.get("semwright_sha") == pin
        and result.get("semwright_version") == "1.0.0"
        and set(result.get("capabilities_checked", [])) == spec["commands"]
        and isinstance(project, dict)
        and all(isinstance(project.get(key), str) and project[key]
                for key in ("project_id", "generation", "scene_id"))
        and type(project.get("revision")) is int and project["revision"] >= 1
        and all(receipt.get(evidence_key) == project[project_key]
                for evidence_key, project_key in (
                    ("project_id", "project_id"), ("generation", "generation"),
                    ("revision", "revision"), ("scene_id", "scene_id")))
        and isinstance(original, dict)
        and original.get("native_driver") == ("driver:" + scene if scene == "blender"
                                               else "driver:manim-community")
        and isinstance(original.get("artifact"), dict)
        and manim_receipts_valid
        and (scene == "manim" or (
            type(count) is int and count == spec["required_count"]
            and isinstance(completed, list) and len(completed) >= 2
        )),
        "native_scene_receipts_not_verified",
    )
    require(isinstance(asset, dict) and
            isinstance(asset.get("sha256"), str) and
            SHA256.fullmatch(asset["sha256"]) is not None and
            type(asset.get("bytes")) is int and 0 < asset["bytes"] <= spec["max_bytes"] and
            original["artifact"].get("sha256") == asset["sha256"] and
            original["artifact"].get("bytes") == asset["bytes"],
            "native_scene_artifact_identity_mismatch")
    blob = read_bounded(root, spec["media"], spec["max_bytes"])
    require(len(blob) == asset["bytes"] and digest(blob) == asset["sha256"],
            "native_scene_artifact_integrity_mismatch")
    if scene == "blender":
        require(len(blob) >= 12 and blob[:4] == b"glTF"
                and struct.unpack("<II", blob[4:12]) == (2, len(blob))
                and type(asset.get("meshes")) is int and asset["meshes"] >= 2
                and type(asset.get("materials")) is int and asset["materials"] >= 2,
                "blender_glb_structure_not_verified")
    else:
        require(len(blob) >= 12 and blob[4:8] == b"ftyp"
                and asset.get("width") == 640 and asset.get("height") == 360
                and asset.get("relative_path", "").endswith(".mp4"),
                "manim_mp4_structure_not_verified")
    return blob


def publish(root: Path, output: Path, source: str, pin: str,
            scene: str, job_status: str) -> dict:
    require(scene in SCENES, "unsupported_native_scene")
    require(bool(SHA40.fullmatch(source)) and bool(SHA40.fullmatch(pin)),
            "native_scene_source_identity_invalid")
    require(job_status in {"success", "failure", "cancelled", "skipped"},
            "native_scene_ci_status_invalid")
    require(not output.exists(), "public_scene_artifact_already_exists")
    src = root.resolve()
    dst = output.resolve()
    require(src != dst and src not in dst.parents and dst not in src.parents,
            "public_scene_overlaps_private_source")
    media = (verified_private_scene(root, source, pin, scene)
             if job_status == "success" else None)
    files = ([{
        "name": SCENES[scene]["media"],
        "sha256": digest(media),
        "bytes": len(media),
    }] if media is not None else [])
    receipt = {
        "schema": SCHEMA,
        "renderer": scene,
        "motionwright_source_sha": source,
        "semwright_source_pin": pin,
        "native_scene_ci_evidence": "PASS" if media is not None else "NOT_VERIFIED",
        "included_files": files,
        "raw_native_job_or_provider_receipts_uploaded": False,
        "owner_connection_or_session_material_uploaded": False,
        "raw_daemon_or_sandbox_logs_uploaded": False,
        "native_effects_replayed_by_publisher": False,
        "installed_owner_acceptance": "NOT_RUN",
        "v01_release_authorized": False,
    }
    output.mkdir(parents=True, exist_ok=False)
    if media is not None:
        (output / SCENES[scene]["media"]).write_bytes(media)
    with (output / "public-evidence.json").open("x", encoding="utf-8") as f:
        json.dump(receipt, f, sort_keys=True, indent=2)
        f.write("\n")
    return receipt



# The source round-trip is a closed synthetic CI case. These helpers are also
# used by the existing wrapper, before its original files can be cleaned up.
SOURCE_PHASES = ("original", "reopen", "edit", "edited-reopen", "edge25")
SOURCE_SCHEMA = "motionwright.manim.source-roundtrip/1"
SOURCE_PUBLIC_SCHEMA = "motionwright.manim.source-public/1"
SOURCE_MIME = "application/vnd.motionwright.manim-source+zip"
SOURCE_MEMBERS = {"plan.json", "scene.py", "render-config.json", "assets.lock.json", "manifest.json"}
SOURCE_MAX_FILE = 128 * 1024 * 1024
SOURCE_MAX_TREE = 512 * 1024 * 1024
SOURCE_MAX_FILES = 4096
SOURCE_MAX_JSON = 4 * 1024 * 1024
SOURCE_TOKEN = r"mw_[0-9a-f]{32}"
SOURCE_DATA = r"(?:original|reopen|edited-reopen|edge25)"


def source_json(data):
    def pairs(rows):
        value = {}
        for key, item in rows:
            require(key not in value, "duplicate_source_evidence_key")
            value[key] = item
        return value
    def bad_constant(_):
        raise PublicationError("nonfinite_source_evidence")
    def finite_float(token):
        number = float(token)
        require(math.isfinite(number), "nonfinite_source_evidence")
        return number
    return json.loads(data, object_pairs_hook=pairs, parse_constant=bad_constant,
                      parse_float=finite_float)


def source_clock(phase):
    require(phase in SOURCE_PHASES, "unknown_manim_source_phase")
    return {"frames": 29, "fps": 25} if phase == "edge25" else {"frames": 24, "fps": 12}


def source_relative(name):
    require(type(name) is str and 0 < len(name) <= 1024
            and "\\" not in name and "\x00" not in name,
            "unsafe_source_case_path")
    parts = name.split("/")
    require(all(part not in {"", ".", ".."} and ":" not in part
                and len(part) <= 255 for part in parts),
            "unsafe_source_case_path")
    return parts


def source_bytes(root, name, limit=SOURCE_MAX_FILE, *, empty=False):
    # Bound the actual read, not only an earlier stat of a mutable file.
    parts = source_relative(name)
    current = Path(root)
    require(current.is_dir() and not current.is_symlink(), "source_case_root_not_plain")
    for part in parts[:-1]:
        current /= part
        mode = current.lstat().st_mode
        require(stat.S_ISDIR(mode) and not stat.S_ISLNK(mode), "source_case_parent_not_plain")
    path = current / parts[-1]
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and not stat.S_ISLNK(before.st_mode)
            and type(limit) is int and 0 <= before.st_size <= limit
            and (empty or before.st_size > 0), "source_case_file_not_bounded")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        require((opened.st_dev, opened.st_ino, opened.st_size) ==
                (before.st_dev, before.st_ino, before.st_size), "source_case_open_changed")
        data = stream.read(before.st_size + 1)
        after_fd = os.fstat(stream.fileno())
    after = path.lstat()
    signature = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    require(len(data) == before.st_size and signature(before) == signature(after_fd)
            == signature(after), "source_case_file_changed")
    return data


def source_file_record(root, name, limit=SOURCE_MAX_FILE, *, empty=False):
    content = source_bytes(root, name, limit, empty=empty)
    return {"path": name, "bytes": len(content), "sha256": digest(content)}


def emitted_session_tokens(path):
    # This is exclusion of the tokens really emitted for this disposable case.
    # It is deliberately not described as universal secret detection.
    path = Path(path)
    raw = source_bytes(path.parent, path.name, 16 * 1024)
    tokens = {raw, raw.strip()}
    try:
        parsed = source_json(raw)
    except (ValueError, UnicodeError, PublicationError):
        parsed = None
    def visit(value):
        if type(value) is str and len(value.encode("utf-8")) >= 16:
            tokens.add(value.encode("utf-8"))
        elif type(value) is dict:
            for item in value.values():
                visit(item)
        elif type(value) is list:
            for item in value:
                visit(item)
    visit(parsed)
    return tuple(token for token in tokens if len(token) >= 16)


def source_excludes_emitted_tokens(data, tokens):
    require(all(token not in data for token in tokens), "emitted_session_material_in_case")
    # Additional negative control; never the basis for declaring arbitrary
    # user data safe. All admitted roots below were freshly made by this case.
    require(not any(marker in data for marker in
                    (b"-----BEGIN " + b"PRIVATE KEY-----",
                     b"-----BEGIN " + b"OPENSSH PRIVATE KEY-----")),
            "private_key_material_in_case")


def source_case_kind(name):
    source_relative(name)
    if re.fullmatch(r"data/" + SOURCE_DATA + r"/motionwright\.sqlite3(?:-wal|-shm)?", name):
        return "database"
    if re.fullmatch(r"data/" + SOURCE_DATA + r"/blobs/sha256/[0-9a-f]{2}/[0-9a-f]{64}", name):
        return "cas"
    if re.fullmatch(r"data/" + SOURCE_DATA + r"/blobs/\.staging/[0-9a-f-]{36}\.part", name):
        return "cas_staging"
    if re.fullmatch(r"data/" + SOURCE_DATA + r"/native-manim-(?:original|edited)\.mwmsrc\.zip", name):
        return "carrier"
    if re.fullmatch(r"bundles/(?:original|edited|\.motionwright-export-[0-9a-f-]{36})/manifest\.json", name):
        return "bundle"
    if re.fullmatch(r"bundles/(?:original|edited|\.motionwright-export-[0-9a-f-]{36})/blobs/sha256/[0-9a-f]{2}/[0-9a-f]{64}", name):
        return "cas"
    if re.fullmatch(r"bundles/(?:original|edited|\.motionwright-export-[0-9a-f-]{36})/sha256/[0-9a-f]{2}/[0-9a-f]{64}", name):
        return "cas"
    if re.fullmatch(r"work/" + SOURCE_TOKEN + r"(?:\.py|\.plan\.json)", name):
        return "work"
    if re.fullmatch(r"work/(" + SOURCE_TOKEN + r")_execution/\1(?:\.py|\.objects\.json)", name):
        return "work"
    if re.fullmatch(r"work/" + SOURCE_TOKEN + r"_execution/\.(?:cache|matplotlib|tmp)/.+", name):
        return "runtime_cache"
    if re.fullmatch(r"output/" + SOURCE_TOKEN + r"(?:\.mwmsrc\.zip|\.objects\.json)", name):
        return "output"
    if re.fullmatch(r"output/videos/" + SOURCE_TOKEN + r"/360p(?:12|25)/.+\.(?:mp4|txt)", name):
        return "output"
    raise PublicationError("unapproved_manim_case_file")


def source_case_directory(name):
    source_relative(name)
    if name in {"data", "bundles", "work", "output"}:
        return
    if re.fullmatch(r"data/" + SOURCE_DATA + r"(?:/blobs(?:/(?:\.staging|sha256(?:/[0-9a-f]{2})?))?)?", name):
        return
    # Project portability exports content-addressed bytes under blobs/sha256.
    # This is a closed parent chain, not permission for arbitrary bundle files.
    if re.fullmatch(r"bundles/(?:original|edited|\.motionwright-export-[0-9a-f-]{36})(?:/blobs(?:/sha256(?:/[0-9a-f]{2})?)?)?", name):
        return
    if re.fullmatch(r"bundles/(?:original|edited|\.motionwright-export-[0-9a-f-]{36})(?:/sha256(?:/[0-9a-f]{2})?)?", name):
        return
    if re.fullmatch(r"work/" + SOURCE_TOKEN + r"_execution(?:/\.(?:cache|matplotlib|tmp)(?:/.+)?)?", name):
        return
    # The pinned runtime creates this empty directory even for movie output.
    # Files beneath it remain outside the closed source custody contract.
    if re.fullmatch(r"output/images(?:/" + SOURCE_TOKEN + r")?", name):
        return
    if re.fullmatch(r"output/videos(?:/" + SOURCE_TOKEN + r"(?:/360p(?:12|25)(?:/partial_movie_files(?:/MotionwrightScene)?)?)?)?", name):
        return
    # Diagnostics expose only a closed structural category, never a filename
    # or native session path. Unexpected trees remain strictly rejected.
    nodes = name.split("/")
    category = nodes[0] if nodes[0] in {"data", "bundles", "work", "output"} else "other"
    known = {"images", "videos", "Tex", "texts", "logs", "sections",
             "partial_movie_files", "original", "reopen", "edited-reopen",
             "edge25", "blobs", "sha256", ".staging", "MotionwrightScene",
             "__pycache__", ".cache", ".matplotlib", ".tmp"}
    branch = nodes[1] if len(nodes) > 1 and nodes[1] in known else "other"
    leaf = nodes[-1] if nodes[-1] in known else (
        "source-token" if re.fullmatch(SOURCE_TOKEN, nodes[-1]) else (
            "execution-token" if re.fullmatch(SOURCE_TOKEN + r"_execution", nodes[-1])
            else "other"
        )
    )
    raise PublicationError(
        f"unapproved_manim_case_directory:{category}:{branch}:{leaf}:{len(nodes)}"
    )


def source_tree_inventory(root):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), "source_tree_root_not_plain")
    files, directories = [], []
    pending = [(root, "")]
    total = 0
    while pending:
        directory, prefix = pending.pop()
        entries = sorted(directory.iterdir(), key=lambda value: value.name)
        require(len(entries) <= SOURCE_MAX_FILES, "source_directory_entry_budget")
        for path in entries:
            name = prefix + path.name
            source_relative(name)
            require(len(name.split("/")) <= 16, "source_tree_depth")
            meta = path.lstat()
            require(not stat.S_ISLNK(meta.st_mode), "linked_source_case_entry")
            if stat.S_ISDIR(meta.st_mode):
                source_case_directory(name)
                directories.append(name)
                require(len(directories) <= SOURCE_MAX_FILES, "source_directory_budget")
                pending.append((path, name + "/"))
            else:
                require(stat.S_ISREG(meta.st_mode), "nonregular_source_case_entry")
                kind = source_case_kind(name)
                row = source_file_record(root, name, empty=True)
                if kind == "cas":
                    parts = name.split("/")
                    require(parts[-2] == parts[-1][:2] and row["sha256"] == parts[-1],
                            "source_cas_identity_mismatch")
                files.append({**row, "kind": kind})
                total += row["bytes"]
                require(len(files) <= SOURCE_MAX_FILES and total <= SOURCE_MAX_TREE,
                        "source_case_total_budget")
    return {"directories": sorted(directories), "files": sorted(files, key=lambda row: row["path"]),
            "total_bytes": total}


def copy_source_snapshot(roots, output, tokens):
    # roots is created by the wrapper, never read from provider JSON.
    require(set(roots) == {"data", "bundles", "work", "output"}, "source_snapshot_root_set")
    output = Path(output)
    require(not output.exists(), "source_snapshot_already_exists")
    output.mkdir(parents=True, mode=0o700)
    count, total, directory_count = 0, 0, 0
    for name, root in roots.items():
        root = Path(root)
        require(root.is_dir() and not root.is_symlink(), "source_snapshot_input_root")
        target = output / name
        target.mkdir(mode=0o700)
        pending = [(root, target)]
        while pending:
            before_root, after_root = pending.pop()
            for source in sorted(before_root.iterdir(), key=lambda value: value.name):
                destination = after_root / source.name
                relative = destination.relative_to(output).as_posix()
                source_relative(relative)
                source_excludes_emitted_tokens(relative.encode("utf-8"), tokens)
                metadata = source.lstat()
                require(not stat.S_ISLNK(metadata.st_mode), "linked_source_snapshot")
                if stat.S_ISDIR(metadata.st_mode):
                    source_case_directory(relative)
                    require(len(relative.split("/")) <= 16, "source_snapshot_depth")
                    directory_count += 1
                    require(directory_count <= SOURCE_MAX_FILES, "source_snapshot_directory_budget")
                    destination.mkdir(mode=0o700)
                    pending.append((source, destination))
                else:
                    require(stat.S_ISREG(metadata.st_mode), "nonregular_source_snapshot")
                    kind = source_case_kind(relative)
                    data = source_bytes(source.parent, source.name, empty=True)
                    source_excludes_emitted_tokens(data, tokens)
                    if kind == "cas":
                        require(source.name == digest(data) and source.parent.name == source.name[:2],
                                "source_snapshot_cas_mismatch")
                    with destination.open("xb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    destination.chmod(0o600)
                    require(source_bytes(source.parent, source.name, empty=True) == data
                            and source_bytes(destination.parent, destination.name, empty=True) == data,
                            "source_snapshot_changed_while_copying")
                    count += 1
                    total += len(data)
                    require(count <= SOURCE_MAX_FILES and total <= SOURCE_MAX_TREE,
                            "source_snapshot_total_budget")
    inventory = source_tree_inventory(output)
    # The caller compares this complete inventory with the live roots again.
    return inventory


def snapshot_live_inventory(roots):
    rows, directories, total = [], [], 0
    for prefix, root in roots.items():
        require(Path(root).is_dir() and not Path(root).is_symlink(), "source_live_root_not_plain")
        pending = [(Path(root), prefix)]
        directories.append(prefix)
        while pending:
            directory, name = pending.pop()
            for path in sorted(directory.iterdir(), key=lambda value: value.name):
                relative = name + "/" + path.name
                source_relative(relative)
                require(len(relative.split("/")) <= 16, "source_live_depth")
                mode = path.lstat().st_mode
                require(not stat.S_ISLNK(mode), "linked_source_live_entry")
                if stat.S_ISDIR(mode):
                    source_case_directory(relative)
                    directories.append(relative)
                    require(len(directories) <= SOURCE_MAX_FILES, "source_live_directory_budget")
                    pending.append((path, relative))
                else:
                    require(stat.S_ISREG(mode), "nonregular_source_live_entry")
                    kind = source_case_kind(relative)
                    row = source_file_record(path.parent, path.name, empty=True)
                    row["path"] = relative
                    if kind == "cas":
                        require(row["sha256"] == path.name and path.parent.name == path.name[:2],
                                "source_live_cas_mismatch")
                    rows.append({**row, "kind": kind})
                    total += row["bytes"]
                    require(len(rows) <= SOURCE_MAX_FILES and total <= SOURCE_MAX_TREE,
                            "source_live_total_budget")
    return {"directories": sorted(directories), "files": sorted(rows, key=lambda row: row["path"]),
            "total_bytes": total}


SOURCE_EVIDENCE_PATHS = (
    "SOURCE_LOCK.json", "Cargo.toml", "Cargo.lock",
    "crates/native/examples/native-manim-e2e.rs",
    "crates/native/src/production.rs", "crates/native/src/production/manim_source.rs",
    "crates/native/src/multi_renderer.rs", "crates/native/tests/manim_source_contract.rs",
    "crates/manim-profile/Cargo.toml", "crates/manim-profile/src/lib.rs",
    "crates/driver-manim-community/src/main.rs",
    "crates/driver-manim-community/src/bin/runtime_runner.rs",
    "tooling/manim_native_readback.py", "tooling/native_manim_e2e.py",
    "tooling/native_scene_public_artifact.py", "tooling/tests/test_native_scene_public_artifact.py",
    ".github/workflows/native-manim-e2e.yml",
)
SOURCE_TOOLS = {"example", "driver", "runner", "semwright", "semwrightd",
                "sandbox", "python", "ffmpeg", "ffprobe"}


def source_uint(value, maximum, reason, minimum=0):
    require(type(value) is int and minimum <= value <= maximum, reason)
    return value


def source_hash(value):
    require(type(value) is str and SHA256.fullmatch(value) is not None, "source_digest_type")
    return value


def source_closed(value, fields, reason):
    require(type(value) is dict and set(value) == set(fields), reason)
    return value


def source_identity_shape(identity, source, pin):
    source_closed(identity, {"C", "T", "semwright_sha", "semwright_version", "manim_version",
                             "sources", "binaries", "runtime"}, "source_identity_fields")
    require(identity["C"] == source and identity["semwright_sha"] == pin
            and type(identity["T"]) is str and SHA40.fullmatch(identity["T"]) is not None
            and identity["semwright_version"] == "1.0.0"
            and identity["manim_version"] == "0.21.0", "source_identity_binding")
    rows = identity["sources"]
    require(type(rows) is list and len(rows) == len(SOURCE_EVIDENCE_PATHS)
            and [item.get("path") for item in rows] == list(SOURCE_EVIDENCE_PATHS),
            "source_identity_file_roster")
    for item in rows:
        source_closed(item, {"path", "bytes", "sha256"}, "source_identity_file_fields")
        source_uint(item["bytes"], SOURCE_MAX_JSON, "source_identity_file_bytes", 1)
        source_hash(item["sha256"])
    source_closed(identity["binaries"], SOURCE_TOOLS, "source_tool_roster")
    for item in identity["binaries"].values():
        source_closed(item, {"bytes", "sha256"}, "source_tool_fields")
        source_uint(item["bytes"], 256 * 1024 * 1024, "source_tool_bytes", 1)
        source_hash(item["sha256"])
    runtime = source_closed(identity["runtime"], {"files", "bytes", "inventory_sha256"},
                            "source_runtime_fields")
    source_uint(runtime["files"], 50000, "source_runtime_files", 1)
    source_uint(runtime["bytes"], 1024 * 1024 * 1024, "source_runtime_bytes", 1)
    source_hash(runtime["inventory_sha256"])
    return identity



def source_reopened_project(before, after):
    """Compare the complete saved Project under the existing portable import contract."""
    require(type(before) is dict and type(after) is dict and set(before) == set(after)
            and {"id", "generation", "revision", "updated_at"} <= set(before),
            "source_reopened_project_fields")
    for project in (before, after):
        require(type(project["generation"]) is str
                and re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", project["generation"]),
                "source_reopened_generation_type")
        require(type(project["updated_at"]) is str and 0 < len(project["updated_at"]) <= 128,
                "source_reopened_updated_at_type")
        source_uint(project["revision"], 2**64 - 1, "source_reopened_revision")
    require(before["generation"] != after["generation"], "source_reopened_generation_not_rotated")
    preserved = lambda project: {key: value for key, value in project.items()
                                 if key not in {"generation", "updated_at"}}
    require(preserved(before) == preserved(after), "source_reopened_project_content_changed")
    def observation(project):
        data = json.dumps(project, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return {"generation": project["generation"], "updated_at": project["updated_at"],
                "project_document_sha256": digest(data)}
    return {"scope": "ALL_PROJECT_FIELDS_EXCEPT_IMPORT_GENERATION_AND_UPDATED_AT",
            "before": observation(before), "after": observation(after),
            "wall_clock_monotonicity_claimed": False}

def source_process_projection(value, stdout_limit):
    source_closed(value, {"exit_code", "elapsed_seconds", "stdout_bytes", "stderr_bytes",
                          "stderr_sha256", "whole_process_completed"}, "source_reader_process_fields")
    require(type(value["exit_code"]) is int and value["exit_code"] == 0
            and value["whole_process_completed"] is True, "source_reader_process_failed")
    source_uint(value["stdout_bytes"], stdout_limit, "source_reader_stdout")
    source_uint(value["stderr_bytes"], 65536, "source_reader_stderr")
    require(type(value["elapsed_seconds"]) in {int, float}
            and math.isfinite(value["elapsed_seconds"]) and 0 <= value["elapsed_seconds"] <= 180,
            "source_reader_elapsed")
    source_hash(value["stderr_sha256"])
    return dict(value)


def source_readback_projection(phase, producer_bytes, origin, prepared, getters, video, report_bytes,
                               identity):
    # Reuse the existing reader's pure carrier/getter parser. It does not invoke
    # a compiler, provider, decoder or inspect(); that happened once in the wrapper.
    import manim_native_readback as reader
    producer = source_json(producer_bytes)
    report = source_json(report_bytes)
    try:
        expected_source = reader.inspect_sources(producer, prepared, getters, phase, origin_bundle=origin)
    except (reader.ReadbackError, KeyError, TypeError, ValueError, zipfile.BadZipFile) as error:
        raise PublicationError("source_carrier_or_getter_binding") from error
    source_closed(report, {"schema", "status", "phase", "classification", "producer_record_sha256",
                          "video_sha256", "video_bytes", "source", "media", "decoder_identity",
                          "probe_process", "decode_process", "decoded_rgb24_bytes", "decoded_rgb24_sha256",
                          "frames", "images", "thresholds", "losses", "human_acceptance",
                          "external_rights_certified", "native_job_retried"}, "source_readback_fields")
    clock = source_clock(phase)
    count, fps = clock["frames"], clock["fps"]
    require(report["schema"] == "motionwright.manim.static-source-readback/1"
            and report["status"] == "PASS" and report["phase"] == phase
            and report["classification"] == "REAL_DECODED_NATIVE_FIXTURE_CONTENT_READBACK_NOT_RELEASE_ACCEPTANCE"
            and report["producer_record_sha256"] == digest(producer_bytes)
            and report["video_sha256"] == digest(video)
            and type(report["video_bytes"]) is int and report["video_bytes"] == len(video)
            and report["source"] == expected_source
            and expected_source["origin_carrier_bytes_independently_read"] is True
            and expected_source["origin_binding_evidence"] == "PHYSICAL_ORIGIN_BYTES_AND_REBIND_CHECKED"
            and report["human_acceptance"] is False and report["external_rights_certified"] is False
            and report["native_job_retried"] is False, "source_readback_not_bound")
    require(len(video) >= 12 and video[4:8] == b"ftyp"
            and producer["request_id"] == "native-manim-source-v2-" + phase
            and producer["result"].get("native_driver") == "driver:manim-community"
            and producer["result"].get("runtime_contract") == "Manim Community 0.21.0"
            and producer["result"]["artifact"]["sha256"] == digest(video)
            and type(producer["result"]["artifact"]["bytes"]) is int
            and producer["result"]["artifact"]["bytes"] == len(video), "source_original_video_binding")
    media = source_closed(report["media"], {"frame_count", "frame_rate", "time_base", "first_pts",
                         "end_exclusive", "audio_stream_count", "codec", "pixel_format",
                         "all_frame_timestamps_verified"}, "source_media_fields")
    require(type(media["frame_count"]) is int and media["frame_count"] == count
            and media["frame_rate"] == str(fps) + "/1" and media["first_pts"] == "0/1"
            and media["end_exclusive"] == ("29/25" if phase == "edge25" else "2")
            and type(media["audio_stream_count"]) is int and media["audio_stream_count"] == 0
            and media["codec"] == "h264" and media["pixel_format"] == "yuv420p"
            and media["all_frame_timestamps_verified"] is True
            and type(media["time_base"]) is str and re.fullmatch(r"[1-9][0-9]{0,12}/[1-9][0-9]{0,12}", media["time_base"]),
            "source_media_clock")
    thresholds = {"rgb_channel_error": reader.MAX_CHANNEL_ERROR, "boundary_margin": reader.BOUNDARY_MARGIN,
                  "min_shape_core_fraction": reader.MIN_CORE_FRACTION,
                  "min_background_fraction": reader.MIN_BACKGROUND_FRACTION, "bbox_edge_error": reader.BBOX_EDGE_ERROR}
    require(report["thresholds"] == thresholds
            and all(type(report["thresholds"][key]) is type(value) for key, value in thresholds.items()),
            "source_reader_thresholds")
    expected_bytes = 640 * 360 * 3 * count
    require(type(report["decoded_rgb24_bytes"]) is int and report["decoded_rgb24_bytes"] == expected_bytes,
            "source_full_decode_byte_count")
    source_hash(report["decoded_rgb24_sha256"])
    frames = report["frames"]
    require(type(frames) is list and len(frames) == count, "source_frame_measurement_count")
    minima = [1.0] * 4
    left = 110 if phase in {"edit", "edited-reopen"} else 80
    expected_boxes = [[460, 40, 560, 90], [left, 60, left + 200, 180], [250, 130, 330, 210]]
    for index, frame in enumerate(frames):
        source_closed(frame, {"index", "matching_core_fractions", "observed_visible_bounds",
                              "stray_saturated_pixels", "rgb24_sha256"}, "source_frame_fields")
        require(type(frame["index"]) is int and frame["index"] == index, "source_frame_index")
        source_hash(frame["rgb24_sha256"])
        fractions = frame["matching_core_fractions"]
        require(type(fractions) is list and len(fractions) == 4, "source_fraction_roster")
        for i, fraction in enumerate(fractions):
            require(type(fraction) in {int, float} and math.isfinite(fraction)
                    and (reader.MIN_BACKGROUND_FRACTION if i == 0 else reader.MIN_CORE_FRACTION) <= fraction <= 1,
                    "source_frame_content_fraction")
            minima[i] = min(minima[i], fraction)
        bounds = frame["observed_visible_bounds"]
        require(type(bounds) is list and len(bounds) == 3, "source_bounds_roster")
        for actual, expected in zip(bounds, expected_boxes):
            require(type(actual) is list and len(actual) == 4
                    and all(type(a) is int and abs(a - b) <= reader.BBOX_EDGE_ERROR
                            for a, b in zip(actual, expected)), "source_frame_geometry_bounds")
        stray = frame["stray_saturated_pixels"]
        require(type(stray) is list and len(stray) == 3
                and all(type(value) is int and value == 0 for value in stray), "source_frame_stray_pixels")
    source_closed(report["decoder_identity"], {"ffmpeg", "ffprobe"}, "source_decoder_roster")
    decoders = {}
    for name, entry in report["decoder_identity"].items():
        source_closed(entry, {"sha256", "version", "process"}, "source_decoder_fields")
        require(entry["sha256"] == identity["binaries"][name]["sha256"]
                and type(entry["version"]) is str and 0 < len(entry["version"]) <= 2048
                and entry["version"].startswith(name + " version "), "source_decoder_identity")
        decoders[name] = {"sha256": entry["sha256"], "version_sha256": digest(entry["version"].encode()),
                          "process": source_process_projection(entry["process"], 65536)}
    processes = {"probe": source_process_projection(report["probe_process"], 8 * 1024 * 1024),
                 "decode": source_process_projection(report["decode_process"], expected_bytes + 640 * 360 * 3)}
    require(processes["decode"]["stdout_bytes"] == expected_bytes, "source_decode_process_byte_count")
    require(report["losses"] == ["static shapes only", "opaque black matte", "no audio or captions",
                                "binary64 geometry projection",
                                "static identical-frame permutations are not observable animation evidence"],
            "source_reader_scope_changed")
    images = report["images"]
    require(type(images) is list and len(images) == 2, "source_review_image_count")
    for row, index in zip(images, (0, count - 1)):
        source_closed(row, {"name", "bytes", "sha256"}, "source_review_image_fields")
        require(row["name"] == f"manim-{phase}-frame-{index:04d}.png", "source_review_image_name")
        source_uint(row["bytes"], 2 * 1024 * 1024, "source_review_image_bytes", 1)
        source_hash(row["sha256"])
    return {"phase": phase, "readback": "PASS", "frames_checked": count,
            "frame_rate": fps, "dimensions": [640, 360], "audio_streams": 0,
            "original_carrier_sha256": digest(origin), "prepared_carrier_sha256": digest(prepared),
            "getter_sha256": digest(getters), "mp4_sha256": digest(video), "mp4_bytes": len(video),
            "producer_sha256": digest(producer_bytes), "full_readback_sha256": digest(report_bytes),
            "all_original_carrier_bytes_and_rebind_checked": True,
            "minimum_matching_core_fractions": minima, "stray_saturated_pixels": [0, 0, 0],
            "decoded_rgb24_bytes": expected_bytes, "decoded_rgb24_sha256": report["decoded_rgb24_sha256"],
            "decoder_identity": decoders, "processes": processes,
            "source_projection_executed_by_publisher": False,
            "video_decoded_by_publisher": False, "human_acceptance": False}


def source_command_projection(root):
    commands = root / "commands"
    if not commands.exists():
        return []
    require(commands.is_dir() and not commands.is_symlink(), "source_command_root")
    output = []
    labels = {"capability-catalog", "seed-original", "export-original", "import-reopen",
              "edit-source", "export-edited", "import-edited-reopen", "seed-edge25"}
    labels |= {"realize-" + phase for phase in SOURCE_PHASES}
    labels |= {"readback-" + phase for phase in SOURCE_PHASES}
    directories = sorted(commands.iterdir())
    require(len(directories) <= 256, "source_command_budget")
    for directory in directories:
        require(directory.is_dir() and not directory.is_symlink()
                and re.fullmatch(r"[0-9]{4}", directory.name), "source_command_directory")
        if not (directory / "command.json").exists():
            output.append({"ordinal": int(directory.name), "state": "NOT_VERIFIED",
                           "reason": "original_command_receipt_absent"})
            continue
        raw = source_bytes(directory, "command.json", 65536)
        row = source_json(raw)
        require(row.get("schema") == "motionwright.manim.command/1"
                and row.get("label") in labels
                and row.get("state") in {"NOT_STARTED", "STARTED", "UNKNOWN", "EXITED"}
                and type(row.get("mutation")) is bool
                and row.get("native_mutation_retried") is False, "source_command_record")
        require(row.get("exit_code") is None or type(row.get("exit_code")) is int,
                "source_command_exit_type")
        require(row.get("cleanup_exit_code") is None or type(row.get("cleanup_exit_code")) is int,
                "source_command_cleanup_exit_type")
        source_hash(row["argv_sha256"])
        streams = row.get("streams", {})
        require(type(streams) is dict and set(streams) <= {"stdout", "stderr"}, "source_command_streams")
        projected = {}
        for name, record in streams.items():
            source_closed(record, {"bytes_retained", "sha256", "complete"}, "source_command_stream_fields")
            source_uint(record["bytes_retained"], 4 * 1024 * 1024 + 1, "source_command_stream_bytes")
            source_hash(record["sha256"])
            require(type(record["complete"]) is bool, "source_command_stream_complete_type")
            data = source_bytes(directory, name + ".bin", 4 * 1024 * 1024 + 1, empty=True)
            require(len(data) == record["bytes_retained"] and digest(data) == record["sha256"],
                    "source_command_stream_changed")
            projected[name] = dict(record)
        output.append({"ordinal": int(directory.name), "label": row["label"], "state": row["state"],
                       "mutation": row["mutation"], "exit_code": row["exit_code"],
                       "cleanup_exit_code": row.get("cleanup_exit_code"),
                       "streams": projected, "argv_sha256": row["argv_sha256"],
                       "original_receipt_sha256": digest(raw),
                       "finalization_verified": row.get("finalization_errors") == [],
                       "native_mutation_retried": False})
    return output


def collect_source_publication(root, source, pin, success):
    raw = source_bytes(root, "source-roundtrip.json", SOURCE_MAX_JSON)
    attempt = source_json(raw)
    require(attempt.get("schema") == SOURCE_SCHEMA and attempt.get("motionwright_sha") == source
            and attempt.get("semwright_sha") == pin
            and attempt.get("native_job_retried") is False
            and attempt.get("human_acceptance") is False
            and attempt.get("original_runner_files_deleted") is False, "source_attempt_identity")
    identity = source_identity_shape(attempt["inputs_before"], source, pin)
    if attempt.get("inputs_after") is not None:
        source_identity_shape(attempt["inputs_after"], source, pin)
    phases = attempt.get("phases")
    require(type(phases) is list and len(phases) == 5
            and [row.get("phase") for row in phases] == list(SOURCE_PHASES), "source_phase_roster")
    session = root / "attempt" / "runtime" / "motionwright.session"
    has_invocation = any(row.get("native_invocations", 0) != 0 for row in phases)
    require(session.exists() or not has_invocation, "original_case_session_absent")
    tokens = emitted_session_tokens(session) if session.exists() else ()
    commands = source_command_projection(root)
    if success:
        require(attempt.get("status") == "PASS" and attempt.get("finalization_errors") == []
                and attempt["inputs_before"] == attempt.get("inputs_after"), "source_attempt_not_complete")
        require(commands and all(row.get("state") == "EXITED" and type(row.get("exit_code")) is int
                and row["exit_code"] == 0 and row.get("finalization_verified") is True
                and set(row["streams"]) == {"stdout", "stderr"}
                and all(item["complete"] is True for item in row["streams"].values())
                for row in commands), "source_command_not_completed")
    members, summary, snapshots, saved_projects = [], [], [], []
    allowed_files = {"origin": "origin.mwmsrc.zip", "prepared": "prepared.mwmsrc.zip",
                     "getter": "native.objects.json", "video": "native.mp4",
                     "producer": "producer.json", "readback": "readback.json"}
    def add_file(path, name, expected):
        data = source_bytes(path.parent, path.name, SOURCE_MAX_FILE, empty=True)
        source_excludes_emitted_tokens(name.encode("utf-8"), tokens)
        source_excludes_emitted_tokens(data, tokens)
        require(len(data) == expected["bytes"] and digest(data) == expected["sha256"],
                "source_public_member_changed")
        members.append({"source": path, "name": name, "bytes": len(data), "sha256": digest(data)})
    for row in phases:
        phase = row["phase"]
        source_uint(row.get("native_invocations"), 1, "source_native_invocation_count")
        require(row.get("native_outcome") in {"NOT_STARTED", "UNKNOWN", "FAILED_KNOWN", "COMPLETED"}
                and row.get("reader_status") in {"NOT_RUN", "NOT_VERIFIED", "PASS"}
                and row.get("custody_status") in {"NOT_RUN", "NOT_VERIFIED", "RETAINED_ON_RUNNER"},
                "source_phase_disposition")
        directory = root / "phases" / phase
        files = row.get("files")
        require(type(files) is dict and set(files) <= set(allowed_files), "source_phase_file_roles")
        contents = {}
        for role, record in files.items():
            source_closed(record, {"path", "bytes", "sha256"}, "source_phase_file_fields")
            require(record["path"] == allowed_files[role], "source_phase_filename")
            source_uint(record["bytes"], SOURCE_MAX_FILE, "source_phase_file_bytes", 1)
            source_hash(record["sha256"])
            data = source_bytes(directory, record["path"], SOURCE_MAX_JSON if role in {"producer", "readback"} else SOURCE_MAX_FILE)
            require(len(data) == record["bytes"] and digest(data) == record["sha256"], "source_phase_file_changed")
            contents[role] = data
            if role not in {"producer", "readback"}:
                add_file(directory / record["path"], "phases/" + phase + "/" + record["path"], record)
        item = {"phase": phase, "native_invocations": row["native_invocations"],
                "native_outcome": row["native_outcome"], "reader_status": row["reader_status"],
                "original_custody": row["custody_status"],
                "original_journal_status": ("VERIFIED_FROM_SEPARATE_DATABASE_COPY"
                    if row.get("original_journal_status") == "VERIFIED_FROM_SEPARATE_DATABASE_COPY" else "NOT_VERIFIED")}
        if success:
            require(row["native_invocations"] == 1 and row["native_outcome"] == "COMPLETED"
                    and row["reader_status"] == "PASS" and row["custody_status"] == "RETAINED_ON_RUNNER"
                    and row.get("original_journal_status") == "VERIFIED_FROM_SEPARATE_DATABASE_COPY"
                    and set(files) == set(allowed_files)
                    and row.get("inputs_before") == identity == row.get("inputs_after"),
                    "source_phase_not_complete")
            for label in ("realize-" + phase, "readback-" + phase):
                require(sum(command.get("label") == label for command in commands) == 1,
                        "source_phase_command_not_single")
            item["readback"] = source_readback_projection(phase, contents["producer"], contents["origin"],
                contents["prepared"], contents["getter"], contents["video"], contents["readback"], identity)
            producer = source_json(contents["producer"])
            saved_projects.append(producer["project"])
            stamp = row.get("project")
            source_closed(stamp, {"id", "generation", "revision", "scene_id", "deliverable_id"},
                          "source_saved_stamp_fields")
            source_uint(stamp["revision"], 2**64 - 1, "source_saved_revision")
            current = producer["source_origin"]["current_binding"]
            require((stamp["id"], stamp["generation"], stamp["revision"], stamp["scene_id"], stamp["deliverable_id"])
                    == (current["project_id"], current["generation"], current["revision"],
                        current["scene_id"], current["deliverable_id"]), "source_saved_stamp_binding")
            source_uint(row.get("original_receipts"), 1024, "source_original_receipt_count", 6)
            for image in source_json(contents["readback"])["images"]:
                data = source_bytes(directory / "frames", image["name"], 2 * 1024 * 1024)
                require(data.startswith(b"\x89PNG\r\n\x1a\n"), "source_review_image_signature")
                add_file(directory / "frames" / image["name"], "phases/" + phase + "/frames/" + image["name"],
                         {"bytes": image["bytes"], "sha256": image["sha256"]})
        if row["custody_status"] == "RETAINED_ON_RUNNER":
            snapshot = root / "custody" / phase
            inventory = source_tree_inventory(snapshot)
            require(inventory == row.get("custody_inventory"), "original_custody_inventory_changed")
            snapshots.append((snapshot, inventory))
            for record in inventory["files"]:
                add_file(snapshot / record["path"], "custody/" + phase + "/" + record["path"], record)
            item["custody_files"] = len(inventory["files"])
            item["custody_bytes"] = inventory["total_bytes"]
        summary.append(item)
    require(len(members) <= SOURCE_MAX_FILES and sum(row["bytes"] for row in members) <= SOURCE_MAX_TREE
            and len({row["name"] for row in members}) == len(members), "source_public_case_budget")
    if success:
        # These joins compare the physically retained selected carriers. The
        # first original remains in CAS after editing and importing its successor.
        digests = [row["readback"]["original_carrier_sha256"] for row in summary]
        require(digests[0] == digests[1] and digests[2] == digests[3] and digests[2] != digests[0],
                "source_original_edit_reopen_chain")
        stamps = [row["project"] for row in phases]
        require(len({row["id"] for row in stamps[:4]}) == 1
                and stamps[0]["generation"] != stamps[1]["generation"]
                and stamps[1]["generation"] == stamps[2]["generation"] != stamps[3]["generation"]
                and type(stamps[1]["revision"]) is int and type(stamps[2]["revision"]) is int
                and stamps[2]["revision"] > stamps[1]["revision"], "source_saved_context_chain")
        for previous, current in ((0, 1), (2, 3)):
            preserved = source_reopened_project(saved_projects[previous], saved_projects[current])
            require(phases[current].get("project_preservation") == preserved,
                    "source_reopened_project_observation_changed")
            summary[current]["project_preservation"] = preserved
        for phase in ("edit", "edited-reopen"):
            database_name = "reopen" if phase == "edit" else "edited-reopen"
            path = root / "custody" / phase / "data" / database_name / "blobs" / "sha256" / digests[0][:2] / digests[0]
            require(digest(source_bytes(path.parent, path.name, 2 * 1024 * 1024)) == digests[0],
                    "source_original_carrier_not_preserved_after_edit")
    return {"attempt_sha256": digest(raw), "identity": identity, "phases": summary,
            "commands": commands, "members": members, "snapshots": snapshots, "tokens": tokens}


def publish_source_roundtrip(root, output, source, pin, job_status):
    require(SHA40.fullmatch(source) is not None and SHA40.fullmatch(pin) is not None,
            "source_public_identity")
    require(job_status in {"success", "failure", "cancelled", "skipped"}, "source_public_job_status")
    require(not output.exists(), "public_scene_artifact_already_exists")
    src, dst = root.resolve(), output.resolve()
    require(src != dst and src not in dst.parents and dst not in src.parents, "public_scene_overlaps_private_source")
    report = {"schema": SOURCE_PUBLIC_SCHEMA, "renderer": "manim",
              "motionwright_source_sha": source, "semwright_source_pin": pin,
              "native_scene_ci_evidence": "NOT_VERIFIED", "included_files": [],
              "synthetic_fixture_only": True, "synthetic_database_files_included": False,
              "owner_connection_or_session_material_uploaded": False,
              "raw_daemon_or_sandbox_logs_uploaded": False, "raw_command_argv_or_streams_uploaded": False,
              "native_effects_replayed_by_publisher": False, "video_decoded_by_publisher": False,
              "installed_owner_acceptance": "NOT_RUN", "human_acceptance": False, "v01_release_authorized": False,
              "session_material_exclusion_scope": "ACTUAL_EMITTED_CASE_TOKENS_NOT_UNIVERSAL_SECRET_DETECTION",
              "original_custody": "NOT_VERIFIED"}
    output.mkdir(parents=True, exist_ok=False)
    destination = output / "public-evidence.json"
    def save():
        with destination.open("w", encoding="utf-8") as stream:
            json.dump(report, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
    save()
    if not (root / "source-roundtrip.json").exists():
        require(job_status != "success", "source_attempt_missing_after_success")
        return report
    collected = collect_source_publication(root, source, pin, job_status == "success")
    members = collected["members"]
    # This archive contains only approved original files of the synthetic case.
    # Its SQLite journals are explicit; they are not generic user database grants.
    if members:
        archive_path = output / "synthetic-manim-originals.zip"
        with archive_path.open("xb") as file:
            with zipfile.ZipFile(file, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for row in members:
                    data = source_bytes(row["source"].parent, row["source"].name, SOURCE_MAX_FILE, empty=True)
                    source_excludes_emitted_tokens(data, collected["tokens"])
                    require(len(data) == row["bytes"] and digest(data) == row["sha256"], "source_archive_input_changed")
                    archive.writestr(row["name"], data)
            file.flush()
            os.fsync(file.fileno())
        data = source_bytes(output, archive_path.name, SOURCE_MAX_TREE + 16 * 1024 * 1024)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            require(archive.namelist() == [row["name"] for row in members], "source_archive_member_roster")
            for actual, row in zip(archive.infolist(), members):
                require(actual.file_size == row["bytes"] and digest(archive.read(actual)) == row["sha256"],
                        "source_archive_member_hash")
            require(archive.testzip() is None, "source_archive_crc")
        for snapshot, inventory in collected["snapshots"]:
            require(source_tree_inventory(snapshot) == inventory, "source_snapshot_changed_during_publication")
        report["included_files"] = [{"name": archive_path.name, "bytes": len(data), "sha256": digest(data)}]
        report["archive_members"] = [{key: row[key] for key in ("name", "bytes", "sha256")} for row in members]
        report["synthetic_database_files_included"] = any("/motionwright.sqlite3" in row["name"] for row in members)
        report["original_custody"] = "VERIFIED_APPROVED_SYNTHETIC_FILES_ARCHIVED"
    report.update(source_identity=collected["identity"], phases=collected["phases"],
                  command_observations=collected["commands"],
                  original_attempt_sha256=collected["attempt_sha256"])
    if job_status == "success":
        report["native_scene_ci_evidence"] = "PASS"
    save()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=SCENES, required=True)
    parser.add_argument("--manim-source-roundtrip", action="store_true")
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--job-status", required=True)
    args = parser.parse_args(argv)
    try:
        source_lock = strict_object(
            (Path(__file__).resolve().parents[1] / "SOURCE_LOCK.json").read_bytes()
        )
        pin = source_lock["dependencies"]["semwright"]["revision"]
        if args.manim_source_roundtrip:
            require(args.scene == "manim", "source_roundtrip_requires_manim")
            receipt = publish_source_roundtrip(args.evidence_root, args.output_root,
                                               args.source_sha, pin, args.job_status)
        else:
            receipt = publish(args.evidence_root, args.output_root,
                              args.source_sha, pin, args.scene, args.job_status)
    except (PublicationError, OSError, KeyError, TypeError, ValueError, zipfile.BadZipFile) as exc:
        reason = exc.code if isinstance(exc, PublicationError) else "native_scene_publication_refused"
        print("native-scene-public-artifact=refused reason=" + reason, file=sys.stderr)
        return 2
    print("native-scene-public-artifact=" + receipt["native_scene_ci_evidence"] +
          " files=" + str(len(receipt["included_files"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
