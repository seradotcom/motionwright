"""Inspect the two locked Graph font archives. No extraction or installation.

Source identity and CSS coverage are evidence about bytes, not Pango acceptance.
Only the fixed public package pins below are accepted by the CLI.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import tarfile

MAX_ARCHIVE = 8 * 1024 * 1024
MAX_MEMBER = 16 * 1024 * 1024
MAX_UNPACKED = 32 * 1024 * 1024
MAX_MEMBERS = 1024
MAX_FACES = 32
SDK_PIN = "54d3459a051adbce4cae9f60c0dea8f60c824be6"
PROFILE = "mw-manim-semantic-graph-v1"
PACKAGES = (
    {
        "id": "instrument-sans",
        "package": "@fontsource-variable/instrument-sans",
        "version": "5.3.0",
        "url": "https://registry.npmjs.org/@fontsource-variable/instrument-sans/-/instrument-sans-5.3.0.tgz",
        "integrity": "sha512-u4gKbDBTNFGkg997tfQn3eHOhHuquWUFTRT/rwzuKtrxX5P2ekfs2x+LgBPP4P32+cC+vUwF1Cr+IdRoPQbrGw==",
        "css": "index.css",
        "editorial_family": "Instrument Sans Variable",
        "expected_native_family": "Instrument Sans",
        "css_weight": "400 700",
    },
    {
        "id": "ibm-plex-mono",
        "package": "@fontsource/ibm-plex-mono",
        "version": "5.3.0",
        "url": "https://registry.npmjs.org/@fontsource/ibm-plex-mono/-/ibm-plex-mono-5.3.0.tgz",
        "integrity": "sha512-eTgnZjZEGk1QtD3ZstF+Vclo2HLAni8YMy34/DxllwZvyz1lR/1RF/xTiAquOBO7MvqBx8D2Ig2WCPMVfdZu7Q==",
        "css": "400.css",
        "editorial_family": "IBM Plex Mono",
        "expected_native_family": "IBM Plex Mono",
        "css_weight": "400",
    },
)


class Rejected(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Rejected(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise Rejected("nonfinite_json")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)


def _reparse(metadata):
    return bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def _identity(metadata):
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_size,
        metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_mode,
    )


def read_owned_file(root, path, limit):
    """Read once under a caller-selected owner root; reject links and swaps."""
    root, path = Path(root), Path(path)
    require(root.is_absolute() and path.is_absolute(), "absolute_paths_required")
    root_meta = root.lstat()
    require(stat.S_ISDIR(root_meta.st_mode) and not _reparse(root_meta), "owner_root")
    canonical_root = root.resolve(strict=True)
    relative = path.relative_to(root)
    require(relative.parts and all(p not in (".", "..") for p in relative.parts), "owned_path")
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        metadata = current.lstat()
        require(stat.S_ISDIR(metadata.st_mode) and not _reparse(metadata), "parent_not_regular")
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and not _reparse(before), "input_not_regular")
    require(before.st_nlink == 1 and 0 < before.st_size <= limit, "input_size_or_links")
    require(path.resolve(strict=True).is_relative_to(canonical_root), "input_escaped_root")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        require(_identity(before) == _identity(opened), "input_changed_before_open")
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    require(len(raw) == before.st_size and len(raw) <= limit, "input_changed_size")
    require(_identity(opened) == _identity(after) == _identity(path.lstat()), "input_changed")
    return raw


def _archive_members(archive):
    members = {}
    unpacked = 0
    for index, member in enumerate(archive, 1):
        require(index <= MAX_MEMBERS, "archive_member_budget")
        path = PurePosixPath(member.name)
        raw_name = member.name[:-1] if member.isdir() and member.name.endswith("/") else member.name
        raw_parts = raw_name.split("/")
        require(
            not path.is_absolute() and path.parts and path.parts[0] == "package"
            and all(part not in ("", ".", "..") for part in raw_parts)
            and str(path) == raw_name
            and "\\" not in member.name and "\0" not in member.name,
            "archive_path",
        )
        require(member.name not in members, "duplicate_archive_member")
        require(member.isdir() or member.isfile(), "archive_special_member")
        require(0 <= member.size <= MAX_MEMBER, "archive_member_size")
        unpacked += member.size
        require(unpacked <= MAX_UNPACKED, "archive_unpacked_budget")
        members[member.name] = member
    return members


def _member_bytes(archive, members, name, limit):
    member = members.get(name)
    require(member is not None and member.isfile(), "selected_member_missing")
    require(0 < member.size <= limit, "selected_member_size")
    stream = archive.extractfile(member)
    require(stream is not None, "selected_member_unreadable")
    with stream:
        raw = stream.read(limit + 1)
    require(len(raw) == member.size and len(raw) <= limit, "selected_member_truncated")
    return raw


def _ranges(value):
    ranges = []
    for token in value.split(","):
        match = re.fullmatch(r"U\+([0-9A-Fa-f]{1,6})(?:-([0-9A-Fa-f]{1,6}))?", token.strip())
        require(match is not None, "css_unicode_range")
        start = int(match[1], 16)
        end = int(match[2], 16) if match[2] else start
        require(0 <= start <= end <= 0x10FFFF, "css_unicode_range_bound")
        ranges.append([start, end])
    require(0 < len(ranges) <= 256, "css_unicode_range_budget")
    return ranges


def _css_faces(raw, spec):
    text = raw.decode("utf-8", "strict")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    blocks = re.findall(r"@font-face\s*\{([^{}]*)\}", text)
    require(0 < len(blocks) <= MAX_FACES, "css_face_budget")
    require(not re.sub(r"@font-face\s*\{[^{}]*\}", "", text).strip(), "css_outside_faces")
    faces = []
    for block in blocks:
        props = {}
        for declaration in block.split(";"):
            if not declaration.strip():
                continue
            pair = declaration.split(":", 1)
            require(len(pair) == 2, "css_declaration")
            key, value = pair[0].strip(), pair[1].strip()
            require(key not in props, "css_duplicate_property")
            props[key] = value
        require(
            set(props) <= {"font-family", "font-style", "font-display", "font-weight", "src", "unicode-range"}
            and {"font-family", "font-style", "font-weight", "src", "unicode-range"} <= set(props),
            "css_property_set",
        )
        require(props["font-family"].strip("\"'") == spec["editorial_family"], "css_family")
        require(props["font-style"] == "normal", "css_style")
        require(" ".join(props["font-weight"].split()) == spec["css_weight"], "css_weight")
        sources = []
        for item in props["src"].split(","):
            match = re.fullmatch(
                r"url\(\s*['\"]?(\./files/[A-Za-z0-9._-]+)['\"]?\s*\)\s*"
                r"format\(\s*['\"]([^'\"]+)['\"]\s*\)", item.strip()
            )
            require(match is not None, "css_source")
            reference, format_name = match[1], match[2]
            require(".." not in reference and ":" not in reference, "css_source_escape")
            if reference.endswith(".woff2"):
                require(format_name in ("woff2", "woff2-variations"), "css_woff2_format")
                sources.append("package/" + reference[2:])
            else:
                require(reference.endswith(".woff") and format_name == "woff", "css_other_format")
        require(len(sources) == 1, "css_one_woff2_per_face")
        faces.append({"member": sources[0], "unicode_ranges": _ranges(props["unicode-range"])})
    require(len({face["member"] for face in faces}) == len(faces), "css_duplicate_face")
    return faces


def _woff2_header(raw):
    require(len(raw) >= 48, "woff2_header_truncated")
    values = struct.unpack(">4sIIHHIIHHIIIII", raw[:48])
    signature, flavor, length, tables, reserved, sfnt_size, compressed, major, minor, meta_off, meta_len, meta_orig, priv_off, priv_len = values
    require(signature == b"wOF2" and flavor == 0x00010000, "woff2_not_truetype")
    require(length == len(raw) and reserved == 0 and 1 <= tables <= 256, "woff2_header")
    require(0 < sfnt_size <= MAX_MEMBER and 0 < compressed <= len(raw) - 48, "woff2_bounds")
    for offset, size in ((meta_off, meta_len), (priv_off, priv_len)):
        require((offset == 0 and size == 0) or (offset >= 48 and size > 0 and offset + size <= len(raw)), "woff2_optional_bounds")
    require((meta_off == 0 and meta_orig == 0) or (meta_off > 0 and meta_orig > 0), "woff2_metadata")
    return {
        "container": "woff2", "sfnt_flavor": "truetype",
        "table_count": tables, "sfnt_size": sfnt_size,
        "version": [major, minor], "inspection": "HEADER_ONLY_NOT_NATIVE_OPEN",
    }


def inspect_package(raw, spec, lock_entry):
    """The CLI supplies only PACKAGES; tests may supply synthetic pinned fixtures."""
    require(type(raw) is bytes and 0 < len(raw) <= MAX_ARCHIVE, "archive_size")
    require(
        lock_entry.get("version") == spec["version"]
        and lock_entry.get("resolved") == spec["url"]
        and lock_entry.get("integrity") == spec["integrity"]
        and lock_entry.get("license") == "OFL-1.1",
        "lock_package_pin",
    )
    algorithm, encoded = spec["integrity"].split("-", 1)
    expected = base64.b64decode(encoded, validate=True)
    require(algorithm == "sha512" and len(expected) == 64, "archive_integrity_format")
    require(hashlib.sha512(raw).digest() == expected, "archive_integrity_mismatch")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        members = _archive_members(archive)
        css_name = "package/" + spec["css"]
        css = _member_bytes(archive, members, css_name, 256 * 1024)
        license_bytes = _member_bytes(archive, members, "package/LICENSE", 64 * 1024)
        metadata_bytes = _member_bytes(archive, members, "package/metadata.json", 64 * 1024)
        license_text = license_bytes.decode("utf-8", "strict")
        require("SIL OPEN FONT LICENSE" in license_text and "Version 1.1" in license_text, "license_text")
        metadata = strict_json(metadata_bytes)
        require(metadata.get("family") == spec["expected_native_family"], "package_family_metadata")
        fonts = []
        for face in _css_faces(css, spec):
            font = _member_bytes(archive, members, face["member"], MAX_MEMBER)
            fonts.append({
                "member": face["member"],
                "relative_path": spec["id"] + "/" + face["member"][8:],
                "sha256": sha(font), "size_bytes": len(font),
                "unicode_ranges": face["unicode_ranges"],
                "header": _woff2_header(font),
            })
    return {
        "package": spec["package"], "version": spec["version"],
        "archive": {"sha256": sha(raw), "size_bytes": len(raw), "integrity": spec["integrity"], "url": spec["url"]},
        "editorial_family": spec["editorial_family"],
        "expected_native_family": spec["expected_native_family"],
        "weight": 400, "style": "normal", "fallback": "forbidden",
        "metadata": {"member": "package/metadata.json", "sha256": sha(metadata_bytes), "size_bytes": len(metadata_bytes)},
        "license": {"member": "package/LICENSE", "sha256": sha(license_bytes), "size_bytes": len(license_bytes), "spdx": "OFL-1.1"},
        "stylesheet": {"member": css_name, "sha256": sha(css), "size_bytes": len(css)},
        "font_files": fonts,
    }


def inspect_archives(lock_bytes, archives):
    lock = strict_json(lock_bytes)
    require(lock.get("lockfileVersion") == 3 and type(lock.get("packages")) is dict, "dependency_lock")
    require(set(archives) == {spec["id"] for spec in PACKAGES}, "archive_set")
    packages = []
    for spec in PACKAGES:
        entry = lock["packages"].get("node_modules/" + spec["package"])
        require(type(entry) is dict, "lock_font_package_missing")
        packages.append(inspect_package(archives[spec["id"]], spec, entry))
    return {
        "schema": "motionwright.manim.bundled-font-intake/1",
        "profile_id": PROFILE, "status": "SOURCE_INTAKE_ONLY",
        "source_provenance": {
            "profile_product_sdk_pin_reference": SDK_PIN,
            "proposal_reviewed_sdk_head": "def9c35e38432704cbb45d9ba2c3683a8dd373ba",
            "reviewed_lock_path": "integrations/motion-canvas/runtime/package-lock.json",
            "input_lock_sha256": sha(lock_bytes),
            "input_lock_tree_binding": "CALLER_MUST_VERIFY_EXACT_SOURCE_TREE",
        },
        "packages": packages,
        "native_font_open_observed": False, "pango_layout_observed": False,
        "native_render_performed": False, "profile_admitted": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--instrument-archive", required=True)
    parser.add_argument("--mono-archive", required=True)
    args = parser.parse_args()
    result = inspect_archives(
        read_owned_file(args.root, args.lock, 2 * 1024 * 1024),
        {
            "instrument-sans": read_owned_file(args.root, args.instrument_archive, MAX_ARCHIVE),
            "ibm-plex-mono": read_owned_file(args.root, args.mono_archive, MAX_ARCHIVE),
        },
    )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
