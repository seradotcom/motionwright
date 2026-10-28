#!/usr/bin/env python3
"""Stage the existing Graph compiler's original runtime closure before Host launch.

No native library, subprocess, archive, download, font converter or Graph is
created here. The owner must independently bind this checkout to its selected
Git source. The real compiled runner rechecks its own embedded pins on use.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

from manim_bundled_font_intake import MAX_MEMBER, Rejected, require, sha, strict_json
from mlt_official_runtime_intake import IntakeError, capture_regular

COMPILER = "crates/manim-profile/src/semantic_graph_source.rs"
FONT_ORIGINALS = "crates/manim-profile/testdata/graph-fonts"
PROFILE_DIRECTORY = "graph-profile-v1"
CLOSURE_NAME = "source-closure.json"
# Same bound and ordered module inventory as semantic_graph_helper_loader.py.
MAX_HELPER_BYTES = 262144
HELPERS = (
    "manim_bundled_font_intake", "manim_graph_font_probe",
    "manim_graph_vector_text", "manim_graph_svg_import",
    "manim_graph_text_layout", "manim_graph_runtime",
)


def _literal_body(text, start, end):
    require(text.count(start) == 1 and text.count(end) == 1,
            "graph_runtime_compiler_literal_boundary")
    left = text.index(start) + len(start)
    right = text.index(end, left)
    return text[left:right]


def compiler_pin_specs(raw):
    """Read only the two closed literal clauses, never evaluate Rust or Python."""
    text = raw.decode("utf-8", "strict")
    assets = _literal_body(
        text, "const BUILTIN_ASSETS: [GraphBuiltinAssetPin; 9] = [",
        "];\n\npub fn graph_builtin_asset_pins()",
    )
    pattern = re.compile(
        r'\s*GraphBuiltinAssetPin\s*\{\s*relative_path:\s*"([^"\\]+)",\s*'
        r'sha256:\s*"([0-9a-f]{64})",\s*size_bytes:\s*([0-9]+),\s*\},'
    )
    position, pins = 0, []
    while position < len(assets.rstrip()):
        match = pattern.match(assets, position)
        require(match is not None, "graph_runtime_asset_literal")
        relative, digest, count = match.groups()
        path = PurePosixPath(relative)
        require(not path.is_absolute() and str(path) == relative
                and all(part not in ("", ".", "..") for part in relative.split("/"))
                and path.parts[0] in ("instrument-sans", "ibm-plex-mono")
                and (path.name == "LICENSE" or path.suffix == ".woff2")
                and "\\" not in relative and "\0" not in relative,
                "graph_runtime_asset_path")
        require(0 < int(count) <= MAX_MEMBER, "graph_runtime_asset_bound")
        pins.append({"relative_path": relative, "sha256": digest, "size_bytes": int(count)})
        position = match.end()
    require(len(pins) == 9 and len({pin["relative_path"] for pin in pins}) == 9
            and {pin["relative_path"] for pin in pins if pin["relative_path"].endswith("/LICENSE")}
                == {"instrument-sans/LICENSE", "ibm-plex-mono/LICENSE"},
            "graph_runtime_asset_inventory")

    helpers = _literal_body(
        text, "fn helper_sources() -> [(&'static str, &'static [u8]); 6] {\n    [",
        "    ]\n}\n\npub(crate) fn graph_helper_pins()",
    )
    pattern = re.compile(
        r'\s*\(\s*"([a-z_]+)",\s*include_bytes!\("([^"\\]+)"\),\s*\),'
    )
    position, modules = 0, []
    while position < len(helpers.rstrip()):
        match = pattern.match(helpers, position)
        require(match is not None, "graph_runtime_helper_literal")
        module, relative = match.groups()
        require(relative == f"../../../tooling/{module}.py", "graph_runtime_helper_include")
        modules.append(module)
        position = match.end()
    require(tuple(modules) == HELPERS, "graph_runtime_helper_inventory")
    return pins, modules


def _directory(path):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path,
            "graph_runtime_absolute_canonical_directory")
    metadata = path.lstat()
    require(stat.S_ISDIR(metadata.st_mode)
            and not bool(getattr(metadata, "st_file_attributes", 0) & 0x400),
            "graph_runtime_directory_identity")
    return path


def _directory_identity(metadata):
    return metadata.st_dev, metadata.st_ino, metadata.st_mode


def _read_original(root, path, limit):
    """Reuse the custodian's nonblocking no-follow reader and the font intake bounds.

    The nonblocking open also rejects a FIFO substituted after the namespace
    check, without waiting for a writer. No helper bytes are imported here.
    """
    root, path = _directory(root), Path(path)
    relative = path.relative_to(root)
    require(path.is_absolute() and relative.parts
            and all(part not in (".", "..") for part in relative.parts),
            "graph_runtime_owned_original_path")
    parents, current = [], root
    for part in relative.parts[:-1]:
        current = _directory(current / part)
        parents.append((current, _directory_identity(current.lstat())))
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
            and not bool(getattr(before, "st_file_attributes", 0) & 0x400)
            and 0 < before.st_size <= limit, "graph_runtime_original_regular_bound")
    try:
        raw, captured = capture_regular(path, limit)
    except IntakeError as error:
        raise Rejected("graph_runtime_original_capture: " + str(error)) from error
    opened = captured["before"]
    require((before.st_dev, before.st_ino, before.st_size, before.st_mode,
             before.st_mtime_ns, before.st_ctime_ns)
            == (opened["device"], opened["inode"], opened["size"], opened["mode"],
                opened["mtime_ns"], opened["ctime_ns"])
            and path.lstat().st_nlink == 1
            and all(_directory_identity(_directory(parent).lstat()) == identity
                    for parent, identity in parents), "graph_runtime_original_identity_changed")
    return raw


def _open_directory(path):
    require(os.name == "posix" and hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW"),
            "graph_runtime_requires_owner_directory_handles")
    path = _directory(path)
    before = path.lstat()
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        require(_directory_identity(before) == _directory_identity(os.fstat(descriptor))
                == _directory_identity(path.lstat()), "graph_runtime_directory_changed")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_child(parent, name):
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=parent)
    require(stat.S_ISDIR(os.fstat(descriptor).st_mode), "graph_runtime_child_directory")
    return descriptor


def _write_new(parent, name, raw):
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=parent)
    try:
        require(stat.S_ISREG(os.fstat(descriptor).st_mode), "graph_runtime_output_regular")
        view = memoryview(raw)
        while view:
            count = os.write(descriptor, view)
            require(0 < count <= len(view), "graph_runtime_short_write")
            view = view[count:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _encode(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("ascii")


def _inputs(repo):
    compiler = _read_original(repo, repo / COMPILER, MAX_MEMBER)
    pins, modules = compiler_pin_specs(compiler)
    source_lock = _read_original(repo, repo / "SOURCE_LOCK.json", MAX_MEMBER)
    require(type(strict_json(source_lock)) is dict, "graph_runtime_source_lock")
    originals = [(COMPILER, compiler), ("SOURCE_LOCK.json", source_lock)]
    entries, rows = {}, []
    for pin in pins:
        source = FONT_ORIGINALS + "/" + pin["relative_path"]
        raw = _read_original(repo, repo / source, pin["size_bytes"])
        require(len(raw) == pin["size_bytes"] and sha(raw) == pin["sha256"],
                "graph_runtime_original_font_pin")
        relative = "fonts/" + pin["relative_path"]
        originals.append((source, raw))
        entries[relative] = raw
        rows.append({"source_relative_path": source, "runtime_relative_path": PROFILE_DIRECTORY + "/" + relative,
                     "sha256": pin["sha256"], "size_bytes": pin["size_bytes"],
                     "pin_origin": COMPILER + "::BUILTIN_ASSETS"})
    for module in modules:
        source = "tooling/" + module + ".py"
        raw = _read_original(repo, repo / source, MAX_HELPER_BYTES)
        relative = "helpers/" + module + ".py"
        originals.append((source, raw))
        entries[relative] = raw
        rows.append({"source_relative_path": source, "runtime_relative_path": PROFILE_DIRECTORY + "/" + relative,
                     "sha256": sha(raw), "size_bytes": len(raw),
                     "pin_origin": COMPILER + "::helper_sources/include_bytes!"})
    return originals, entries, rows


def _unchanged(repo, originals):
    for relative, raw in originals:
        require(_read_original(repo, repo / relative, len(raw)) == raw,
                "graph_runtime_original_changed")


def _expected_directories(entries):
    directories = {""}
    for relative in entries:
        parent = PurePosixPath(relative).parent
        while str(parent) != ".":
            directories.add(str(parent))
            parent = parent.parent
    return sorted(directories, key=lambda item: (len(PurePosixPath(item).parts), item))


def _verify_profile(profile, entries):
    """Bound traversal by the fixed closure; never descend into an unknown path."""
    directories = _expected_directories(entries)
    allowed = set(entries) | set(directories)
    for relative in directories:
        path = profile / relative
        descriptor = _open_directory(path)
        try:
            expected = {PurePosixPath(name).name for name in allowed if name
                        and str(PurePosixPath(name).parent) == (relative or ".")}
            with os.scandir(descriptor) as observed:
                names = set()
                for row in observed:
                    require(row.name in expected and row.name not in names,
                            "graph_runtime_unexpected_existing_member")
                    names.add(row.name)
                require(names == expected, "graph_runtime_incomplete_existing_closure")
        finally:
            os.close(descriptor)
    for relative, raw in entries.items():
        require(_read_original(profile, profile / relative, len(raw)) == raw,
                "graph_runtime_existing_bytes_differ")


def _fresh_profile(runtime_descriptor, entries, *, before_completion):
    # This mkdir is the only reservation. Any failure retains every partial file;
    # a later caller takes verification-only and cannot complete this attempt.
    os.mkdir(PROFILE_DIRECTORY, mode=0o700, dir_fd=runtime_descriptor)
    handles = {"": _open_child(runtime_descriptor, PROFILE_DIRECTORY)}
    try:
        for relative in _expected_directories(entries)[1:]:
            path = PurePosixPath(relative)
            parent = str(path.parent)
            parent = "" if parent == "." else parent
            os.mkdir(path.name, mode=0o700, dir_fd=handles[parent])
            handles[relative] = _open_child(handles[parent], path.name)
        for relative, raw in entries.items():
            if relative == CLOSURE_NAME:
                continue
            path = PurePosixPath(relative)
            _write_new(handles[str(path.parent)], path.name, raw)
        before_completion()
        _write_new(handles[""], CLOSURE_NAME, entries[CLOSURE_NAME])
        for descriptor in reversed(list(handles.values())):
            os.fsync(descriptor)
    finally:
        for descriptor in reversed(list(handles.values())):
            os.close(descriptor)


def prepare_graph_runtime(repo_root, runtime_root, receipt_path):
    """Copy the 9+6 originals or verify an exact complete prior copy, without overwrite.

    Owner-only setup API, before read-only Host mounts are created. Neither the
    paths nor this receipt are accepted by an operation as authority or Ready.
    All three arguments are absolute existing owner locations (receipt is a
    filename in an existing private directory). Runtime/PyAV provisioning is an
    independent existing prerequisite and is left untouched.
    """
    repo, runtime = _directory(repo_root), _directory(runtime_root)
    receipt_path = Path(receipt_path)
    require(receipt_path.is_absolute() and receipt_path.name not in ("", ".", ".."),
            "graph_runtime_receipt_path")
    receipt_parent = _directory(receipt_path.parent)
    require(not receipt_path.is_relative_to(runtime / PROFILE_DIRECTORY),
            "graph_runtime_external_receipt_required")
    originals, entries, rows = _inputs(repo)
    receipt = {
        "schema": "motionwright.manim.graph-runtime-source-intake/1",
        "status": "SOURCE_INTAKE_ONLY", "profile_id": "mw-manim-semantic-graph-v1",
        "source_root": str(repo), "runtime_root": str(runtime),
        "source_tree_binding": "OWNER_MUST_VERIFY_CURRENT_GIT_SOURCE",
        "compiler_source": {"relative_path": COMPILER, "sha256": sha(originals[0][1]),
                            "size_bytes": len(originals[0][1])},
        "source_lock": {"relative_path": "SOURCE_LOCK.json", "sha256": sha(originals[1][1]),
                        "size_bytes": len(originals[1][1])},
        "files": rows, "files_count": len(rows), "total_original_bytes": sum(map(len, entries.values())),
        "source_closure_sha256": sha(_encode(rows)),
        "existing_destination_policy": "VERIFY_EXACT_COMPLETE_OR_REJECT_WITHOUT_WRITES",
        "compiled_runner_pin_agreement_verified": False,
        "native_font_open_observed": False, "pango_layout_observed": False,
        "native_render_performed": False, "profile_admitted": False,
    }
    raw_receipt = _encode(receipt)
    entries[CLOSURE_NAME] = raw_receipt
    profile = runtime / PROFILE_DIRECTORY
    if os.path.lexists(receipt_path):
        require(os.path.lexists(profile)
                and _read_original(receipt_parent, receipt_path, len(raw_receipt)) == raw_receipt,
                "graph_runtime_existing_receipt_differs")
    _unchanged(repo, originals)
    with ExitStack() as handles:
        runtime_descriptor = _open_directory(runtime)
        handles.callback(os.close, runtime_descriptor)
        receipt_descriptor = _open_directory(receipt_parent)
        handles.callback(os.close, receipt_descriptor)
        if os.path.lexists(profile):
            _verify_profile(profile, entries)
        else:
            _fresh_profile(runtime_descriptor, entries, before_completion=lambda: _unchanged(repo, originals))
        _verify_profile(profile, entries)
        _unchanged(repo, originals)
        require(_directory_identity(os.fstat(runtime_descriptor)) == _directory_identity(runtime.lstat())
                and _directory_identity(os.fstat(receipt_descriptor)) == _directory_identity(receipt_parent.lstat()),
                "graph_runtime_owner_directory_changed")
        if not os.path.lexists(receipt_path):
            _write_new(receipt_descriptor, receipt_path.name, raw_receipt)
            os.fsync(receipt_descriptor)
        require(_read_original(receipt_parent, receipt_path, len(raw_receipt)) == raw_receipt,
                "graph_runtime_receipt_changed")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    receipt = prepare_graph_runtime(args.repo_root, args.runtime_root, args.receipt)
    print(json.dumps({"schema": receipt["schema"], "status": receipt["status"],
                      "source_closure_sha256": receipt["source_closure_sha256"]}, sort_keys=True))


if __name__ == "__main__":
    main()
