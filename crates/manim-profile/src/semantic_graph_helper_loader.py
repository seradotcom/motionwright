# Fixed compiler-owned loader. _DATA is a JSON string, never Python syntax.
# Both the original Host job and an extracted native source project use the same
# checked module bytes. Imported archives are never executed by the importer.
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import types

from manim import Scene

_GRAPH_DATA = json.loads(_DATA)


def _graph_link_or_reparse(info):
    return (stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)))


def _graph_read_fixed(root, relative, expected_size, expected_sha256):
    relative = Path(relative)
    if (not root.is_absolute() or relative.is_absolute()
            or ".." in relative.parts or type(expected_size) is not int
            or not 0 < expected_size <= 262144):
        raise ValueError("graph_helper_path_or_budget")
    current = root
    root_stat = current.lstat()
    if not stat.S_ISDIR(root_stat.st_mode) or _graph_link_or_reparse(root_stat):
        raise ValueError("graph_runtime_root_not_owned_directory")
    for component in relative.parts[:-1]:
        current = current / component
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or _graph_link_or_reparse(info):
            raise ValueError("graph_helper_parent_not_owned_directory")
    path = root / relative
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or _graph_link_or_reparse(before)
            or before.st_nlink != 1 or before.st_size != expected_size):
        raise ValueError("graph_helper_not_regular_original")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
                before.st_dev, before.st_ino, before.st_size):
            raise ValueError("graph_helper_open_identity_changed")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            raw = source.read(expected_size + 1)
        after = os.fstat(descriptor)
        final = path.lstat()
        signature = lambda row: (
            row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns,
            row.st_ctime_ns, row.st_nlink, row.st_mode,
        )
        if (signature(opened) != signature(after) or signature(after) != signature(final)
                or len(raw) != expected_size
                or hashlib.sha256(raw).hexdigest() != expected_sha256):
            raise ValueError("graph_helper_original_bytes_changed")
    finally:
        os.close(descriptor)
    return path, raw


def _graph_load_helpers(root):
    allowed = (
        "manim_bundled_font_intake", "manim_graph_font_probe",
        "manim_graph_vector_text", "manim_graph_svg_import",
        "manim_graph_text_layout", "manim_graph_runtime",
    )
    pins = _GRAPH_DATA["helper_pins"]
    if (len(pins) != len(allowed)
            or tuple(pin["module"] for pin in pins) != allowed
            or any(name in sys.modules for name in allowed)):
        raise ValueError("graph_helper_preload_or_inventory")
    originals = []
    # Validate the COMPLETE closure before executing any trusted helper.
    for name, pin in zip(allowed, pins, strict=True):
        relative = f"graph-profile-v1/helpers/{name}.py"
        if pin["relative_path"] != relative:
            raise ValueError("graph_helper_fixed_path_mismatch")
        path, raw = _graph_read_fixed(
            root, relative, pin["size_bytes"], pin["sha256"])
        originals.append((name, path, raw, pin))
    observations = []
    for name, path, raw, pin in originals:
        module = types.ModuleType(name)
        module.__file__, module.__package__ = str(path), ""
        sys.modules[name] = module
        # The bytes above are the compiler-pinned helper implementation.
        # No request, imported Python file, user module name or expression
        # reaches this compile/exec boundary. No .pyc or ambient import wins.
        exec(compile(raw, str(path), "exec"), module.__dict__)
        observations.append({
            "module": name, "relative_path": pin["relative_path"],
            "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
        })
    return sys.modules["manim_graph_runtime"], observations
