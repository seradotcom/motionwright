"""Observe the bundled SQLite static output of one captured desktop build.

This closed profile hashes actual OUT_DIR bytes and their Rust archive in the
same capture. It does not prove final linker incorporation, review native
licensing, execute a compiler, or authorize distribution. The binaries remain
inside the captured target directory; only this observation is exported.
"""
from __future__ import annotations

from pathlib import Path
import os
import re

SCHEMA = "motionwright.sqlite-native-build-output/1"
MAX_ARCHIVE = 64 * 1024 * 1024
REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"
SQLITE_ID = REGISTRY + "#libsqlite3-sys@0.39.1"
CC_ID = REGISTRY + "#cc@1.6.0"
TARGETS = {
    "x86_64-pc-windows-msvc": ("target/windows-captured", ("libsqlite3.a", "sqlite3.lib")),
    "x86_64-unknown-linux-gnu": ("target/linux-captured", ("libsqlite3.a",)),
    "aarch64-apple-darwin": ("target/macos-captured", ("libsqlite3.a",)),
}
AUTHENTICATED_SOURCES = {
    SQLITE_ID: (
        "14a5f01b87b92705d07b433c06f96cbc764637d81a0bffb5734ce17cf0f9a2b3",
        {
            "build.rs": (35413, "769b31370a41aa722e1d5a530012432c985bb142eab34d095481ffd1a3436e1b"),
            "sqlite3/sqlite3.c": (9582521, "0c657666d819f261cc4007526ad57e1669de9949df7a8ea847aa284a2bf0ee52"),
            "sqlite3/sqlite3.h": (695729, "d6c84557261893439b341d2e6c35187ae3ea24fbfd843ad94616f72d89242b8c"),
        },
    ),
    CC_ID: (
        "f74872d07caf508b30a21f6836e7d7016a2eaf7d9ff4f48deaa58cd8a0407630",
        {"src/lib.rs": (218849, "0dfd1f0e2cd7c0a8e990fcd8a63aaaa95ffeb5d694a6c508c6d8fb384bc69704")},
    ),
}
INPUT_LIMITS = {
    "source-before.json": 16 * 1024 * 1024,
    "source-after.json": 16 * 1024 * 1024,
    "metadata.stdout": 32 * 1024 * 1024,
    "metadata.completion.json": 1024 * 1024,
    "build.stdout": 128 * 1024 * 1024,
    "build.completion.json": 1024 * 1024,
    "cargo-components.json": 32 * 1024 * 1024,
    "cargo-artifacts.json": 32 * 1024 * 1024,
    "cargo-license-sources.json": 64 * 1024 * 1024,
}


def require(condition, code):
    if not condition:
        raise ValueError("sqlite_native_" + code)


def one(rows, predicate, code):
    require(type(rows) is list and len(rows) <= 50000
            and all(type(row) is dict for row in rows), code)
    selected = [row for row in rows if predicate(row)]
    require(len(selected) == 1, code)
    return selected[0]


def absolute_path(value):
    require(type(value) is str and 0 < len(value) <= 4096
            and not any(ord(char) < 32 for char in value)
            and ".." not in value.replace("\\", "/").split("/"), "path_invalid")
    path = Path(value)
    require(path.is_absolute(), "path_invalid")
    return path


def source_references(notices):
    """Use the original crate authentication, not a wrapper license inference."""
    require(notices.get("issues") == [], "source_issues")
    result = []
    for package_id, (archive_sha, files) in AUTHENTICATED_SOURCES.items():
        row = one(notices.get("components"), lambda row: row.get("id") == package_id,
                  "source_component_not_unique")
        proof = row.get("source_authentication", {})
        require(row.get("issues") == [] and row.get("source") == REGISTRY
                and proof.get("status") == "VERIFIED"
                and proof.get("kind") == "registry_archive"
                and proof.get("scope") == "all_regular_package_files_against_original_crate"
                and proof.get("archive_sha256") == archive_sha,
                "source_profile_mismatch")
        root = proof.get("source_root")
        require(type(root) is str and root.startswith("registry/src/")
                and root.endswith("/" + package_id.split("#")[1].replace("@", "-"))
                and ".." not in root.split("/"), "source_root_invalid")
        selected = []
        for relative, (size, digest) in files.items():
            item = one(proof.get("files"),
                       lambda item: item.get("source_path") == root + "/" + relative,
                       "source_file_not_unique")
            require(item.get("bytes") == size and item.get("sha256") == digest,
                    "source_bytes_mismatch")
            selected.append({"path": relative, "bytes": size, "sha256": digest})
        result.append({"package_id": package_id, "archive_sha256": archive_sha,
                       "files": selected})
    return result


def original_message(messages, unit, reason):
    index = unit.get("message_index")
    require(type(index) is int and 0 <= index < len(messages),
            "message_index_invalid")
    message = messages[index]
    require(message.get("reason") == reason
            and message.get("package_id") == SQLITE_ID, "message_scope_mismatch")
    return message


def compiler_unit(capture, messages, unit):
    message = original_message(messages, unit, "compiler-artifact")
    inventory = capture._load_sibling("cargo_build_inventory")
    identity = {
        "package_id": message["package_id"],
        "target": inventory._target(message.get("target")),
        "profile": inventory._profile(message.get("profile")),
        "features": sorted(message.get("features", [])),
        "filenames": list(message.get("filenames", [])),
        "executable": message.get("executable"),
    }
    require(unit.get("fresh") is False and message.get("fresh") is False
            and unit.get("feature_context_assignment") == "CONFIRMED_CONTEXT"
            and all(unit.get(key) == value for key, value in identity.items())
            and unit.get("unit_id") == inventory._digest(identity),
            "compiler_unit_mismatch")
    require("bundled" in unit["features"] and "cc" in unit["features"]
            and not any("sqlcipher" in feature or feature in {"loadable_extension", "in_gecko"}
                        for feature in unit["features"]),
            "bundled_profile_required")
    return message


def observe(capture, *, repo, state, expected_source, expected_tree, expected_target):
    """Read only named originals and selected live files within the target root."""
    require(expected_target in TARGETS
            and re.fullmatch(r"[0-9a-f]{40}", expected_source or "") is not None
            and re.fullmatch(r"[0-9a-f]{40}", expected_tree or "") is not None,
            "scope_invalid")
    repo = Path(repo).resolve(strict=True)
    state = capture.admitted(repo, state, directory=True)
    raw_inputs = {name: capture.stable_read(state, name, limit)
                  for name, limit in INPUT_LIMITS.items()}
    identities = {name: {"bytes": len(raw), "sha256": capture.sha(raw)}
                  for name, raw in raw_inputs.items()}
    values = {name: capture.parse_json(raw) for name, raw in raw_inputs.items()
              if name != "build.stdout"}
    for name in ("source-before.json", "source-after.json"):
        require(values[name].get("source") == expected_source
                and values[name].get("tree") == expected_tree, "source_scope_mismatch")
    require(raw_inputs["source-before.json"] == raw_inputs["source-after.json"],
            "source_changed")
    for label in ("metadata", "build"):
        completion = values[label + ".completion.json"]
        require(completion.get("returncode") == 0
                and completion.get("errors") == []
                and completion.get("executed_once") is True
                and completion.get("label") == label
                and absolute_path(completion.get("cwd")) == repo
                and completion.get("stdout_sha256") == identities[label + ".stdout"]["sha256"],
                "completion_mismatch")
    metadata = values["metadata.stdout"]
    inventory = values["cargo-components.json"]
    artifacts = values["cargo-artifacts.json"]
    require(inventory.get("build_finished_success") is True
            and inventory.get("target_triple") == expected_target
            and inventory.get("root_package_name") == "motionwright-desktop"
            and absolute_path(metadata.get("workspace_root")) == repo,
            "inventory_scope_mismatch")
    target_relative, names = TARGETS[expected_target]
    target_root = capture.admitted(repo, absolute_path(metadata.get("target_directory")),
                                   directory=True)
    require(target_root.relative_to(repo).as_posix() == target_relative,
            "target_root_mismatch")
    source_proof = source_references(values["cargo-license-sources.json"])
    component = one(inventory.get("components"),
                    lambda row: row.get("id") == SQLITE_ID, "component_not_unique")
    require(component.get("name") == "libsqlite3-sys" and component.get("version") == "0.39.1"
            and component.get("source") == REGISTRY, "component_profile_mismatch")
    unit = one(component.get("observed_units"),
               lambda row: row.get("kind") == "compiler-artifact"
               and row.get("execution_context") == "target", "runtime_unit_not_unique")
    require(unit.get("roles") == ["runtime", "target"]
            and unit.get("target", {}).get("name") == "libsqlite3_sys"
            and unit["target"].get("kind") == ["lib"]
            and unit["target"].get("crate_types") == ["lib"]
            and unit.get("profile", {}).get("test") is False,
            "runtime_unit_profile_mismatch")
    messages = capture.cargo_messages(raw_inputs["build.stdout"])
    compiler_unit(capture, messages, unit)
    script_unit = one(component["observed_units"],
                      lambda row: row.get("kind") == "compiler-artifact"
                      and row.get("target", {}).get("kind") == ["custom-build"],
                      "build_unit_not_unique")
    compiler_unit(capture, messages, script_unit)
    require(script_unit.get("execution_context") == "host"
            and script_unit.get("features") == unit["features"], "build_unit_profile_mismatch")
    script = one(inventory.get("native_link_observations"),
                 lambda row: row.get("package_id") == SQLITE_ID, "out_dir_not_unique")
    script_message = original_message(messages, script, "build-script-executed")
    require(all(script.get(key) == script_message.get(key)
                for key in ("out_dir", "linked_libs", "linked_paths", "cfgs"))
            and script.get("linked_libs") == ["static=sqlite3"], "static_link_declaration_mismatch")
    emitted = one(component["observed_units"],
                  lambda row: row.get("kind") == "build-script-executed",
                  "build_execution_not_unique")
    require(emitted.get("message_index") == script["message_index"],
            "build_execution_mismatch")
    out_dir = capture.admitted(target_root, absolute_path(script.get("out_dir")), directory=True)
    relative_out = out_dir.relative_to(target_root).as_posix()
    require(re.fullmatch(re.escape(expected_target)
                         + r"/release/build/libsqlite3-sys-[0-9a-f]{16}/out", relative_out) is not None,
            "out_dir_scope_mismatch")
    own_search_path = "native=" + str(out_dir)
    require(script["linked_paths"].count(own_search_path) == 1,
            "own_native_search_path_missing")
    # Other native search directories (e.g. MSVC ATL) convey no read authority.
    expected_names = set(names)
    observed_names = set()
    with os.scandir(out_dir) as entries:
        count = 0
        for entry in entries:
            count += 1
            require(count <= 256, "out_dir_entry_budget")
            if entry.name.casefold() in {name.casefold() for name in names}:
                require(entry.name in expected_names, "archive_case_alias")
                observed_names.add(entry.name)
    require(observed_names == expected_names, "archive_missing")
    archives = []
    for name in names:
        path = out_dir / name
        raw = capture.stable_read(target_root, path, MAX_ARCHIVE)
        require(8 < len(raw) <= MAX_ARCHIVE and raw.startswith(b"!<arch>\n"),
                "archive_format_invalid")
        archives.append({"path": path.relative_to(target_root).as_posix(),
                         "bytes": len(raw), "sha256": capture.sha(raw)})
    require(len({(item["bytes"], item["sha256"]) for item in archives}) == 1,
            "msvc_archive_alias_mismatch")
    rlibs = [absolute_path(name) for name in unit.get("filenames", []) if name.endswith(".rlib")]
    require(len(rlibs) == 1, "rlib_not_unique")
    rlib = capture.admitted(target_root, rlibs[0])
    relative_rlib = rlib.relative_to(target_root).as_posix()
    require(re.fullmatch(re.escape(expected_target)
                         + r"/release/deps/liblibsqlite3_sys-[0-9a-f]{16}\.rlib", relative_rlib) is not None,
            "rlib_scope_mismatch")
    roster = capture._binding_roster(artifacts)
    rlib_identity = capture.stable_hash(target_root, rlib, MAX_ARCHIVE)
    require(roster.get(relative_rlib) == {"path": relative_rlib, **rlib_identity},
            "rlib_identity_mismatch")
    return {
        "schema": SCHEMA, "status": "OBSERVED_BUILD_OUTPUT_BYTES",
        "source": expected_source, "source_tree": expected_tree, "target": expected_target,
        "root_package": "motionwright-desktop", "target_root": target_relative,
        "component_id": SQLITE_ID, "original_inputs": identities,
        "authenticated_source_references": source_proof,
        "build_script": {"compiler_unit_id": script_unit["unit_id"],
                         "compiler_message_index": script_unit["message_index"],
                         "execution_message_index": script["message_index"],
                         "out_dir": relative_out, "linked_libs": script["linked_libs"]},
        "runtime_unit": {"unit_id": unit["unit_id"], "message_index": unit["message_index"],
                         "features": unit["features"], "path": relative_rlib, **rlib_identity},
        "archives": archives,
        "target_context_evidence": "TARGET_LAYOUT_AND_CONFIRMED_RUNTIME_UNIT",
        "final_linker_incorporation": "NOT_VERIFIED",
        "native_license_review": "NOT_VERIFIED",
        "full_package_inventory": "NOT_VERIFIED",
        "redistribution_permission": "NOT_INFERRED",
        "execution_boundary": "Isolated CI build; repeated byte reads are not an atomic filesystem snapshot.",
    }


def verify(capture, raw, *, expected_sha256, repo, state, expected_source,
           expected_tree, expected_target):
    """Require the sealed digest before parsing, then reobserve the live output."""
    require(type(raw) is bytes and 0 < len(raw) <= 1024 * 1024
            and type(expected_sha256) is str
            and re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is not None
            and capture.sha(raw) == expected_sha256, "record_digest_mismatch")
    prior = capture.parse_json(raw)
    current = observe(capture, repo=repo, state=state, expected_source=expected_source,
                      expected_tree=expected_tree, expected_target=expected_target)
    require(prior == current, "observation_changed")
    return current
