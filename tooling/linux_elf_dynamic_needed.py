"""Bounded, source-bound read-only survey of ELF DT_NEEDED from a Linux AppImage.

Unlike GNU Build ID, DT_NEEDED describes direct dynamic-link declarations.
An observed SONAME candidate is NOT proof of runtime loader resolution:
RUNPATH/RPATH, glibc defaults, dlopen, mounts, host libraries and loader
namespace are not executed or attested. Never grant G2/G3 from this survey.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import stat
import struct
import sys

import candidate_package_inventory as inventory

SCHEMA = "motionwright.linux-appimage-elf-needed-survey/2"
MAX_BINARY = 128 * 1024 * 1024
MAX_ALL_BYTES = 384 * 1024 * 1024
MAX_ELF_MEMBERS = 500
MAX_NEEDED_PER_MEMBER = 512
MAX_DYNAMIC_SIZE = 1024 * 1024
MAX_STRTAB = 2 * 1024 * 1024
MAX_STRING = 8192
MAX_REPORT = 4 * 1024 * 1024
HDR = struct.Struct("<16sHHIQQQIHHHHHH")
PHDR = struct.Struct("<IIQQQQQQ")
DYN = struct.Struct("<qQ")
PT_LOAD, PT_DYNAMIC = 1, 2
DT_NULL, DT_NEEDED, DT_STRTAB, DT_STRSZ = 0, 1, 5, 10
DT_SONAME, DT_RPATH, DT_RUNPATH = 14, 15, 29
SOURCE = re.compile(r"[0-9a-f]{40}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")


def require(ok, code):
    if not ok:
        raise ValueError(code)


def digest(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def dynamic_needed(raw):
    """Strict ELF64 little-endian x86-64 PT_DYNAMIC; no subprocess/ldd."""
    require(isinstance(raw, bytes) and len(raw) <= MAX_BINARY,
            "elf_needed_input_budget")
    require(len(raw) >= HDR.size and raw.startswith(b"\x7fELF"),
            "elf_needed_not_elf")
    (ident, kind, machine, ver, _entry, phoff, _shoff, _flags, ehsize,
     phentsize, phnum, _shentsize, _shnum, _shstrndx) = HDR.unpack_from(raw)
    require(ident[4:7] == bytes((2, 1, 1))
            and kind in (2, 3) and machine == 62 and ver == 1
            and ehsize == HDR.size and phentsize == PHDR.size
            and 0 < phnum <= 256 and phoff >= HDR.size
            and phoff + phnum * PHDR.size <= len(raw),
            "elf_needed_unsupported_header")
    loads, dynamics = [], []
    for index in range(phnum):
        ptype, _flags, off, vaddr, _paddr, fsize, msize, _align = (
            PHDR.unpack_from(raw, phoff + index * PHDR.size))
        if ptype in (PT_LOAD, PT_DYNAMIC):
            require(off <= len(raw) and fsize <= len(raw) - off
                    and fsize <= msize, "elf_needed_segment_out_of_bounds")
        if ptype == PT_LOAD:
            loads.append((vaddr, fsize, off))
        if ptype == PT_DYNAMIC:
            require(fsize <= MAX_DYNAMIC_SIZE and
                    fsize % DYN.size == 0 and fsize >= DYN.size,
                    "elf_needed_dynamic_size")
            dynamics.append((off, fsize))
    require(len(dynamics) <= 1, "elf_needed_ambiguous_dynamic_segments")
    if not dynamics:
        return {"status": "NO_PT_DYNAMIC", "needed": [],
                "soname": None, "rpath": None, "runpath": None}
    off, fsize = dynamics[0]
    tags = {}
    terminated = False
    for index in range(fsize // DYN.size):
        tag, val = DYN.unpack_from(raw, off + index * DYN.size)
        if tag == DT_NULL:
            terminated = True
            break
        if tag in (DT_STRTAB, DT_STRSZ, DT_SONAME, DT_RPATH, DT_RUNPATH):
            require(tag not in tags, "elf_needed_duplicate_single_tag")
            tags[tag] = val
        elif tag == DT_NEEDED:
            values = tags.setdefault(DT_NEEDED, [])
            require(len(values) < MAX_NEEDED_PER_MEMBER,
                    "elf_needed_too_many_dependencies")
            values.append(val)
    require(terminated, "elf_needed_dynamic_without_null")
    needed_refs = tags.get(DT_NEEDED, [])
    string_refs = needed_refs + [
        tags[tag] for tag in (DT_SONAME, DT_RPATH, DT_RUNPATH) if tag in tags]
    if string_refs:
        require(DT_STRTAB in tags and DT_STRSZ in tags
                and 0 < tags[DT_STRSZ] <= MAX_STRTAB,
                "elf_needed_string_table_missing")
        addr, size = tags[DT_STRTAB], tags[DT_STRSZ]
        candidates = [
            file_off + (addr - vaddr)
            for vaddr, fsize, file_off in loads
            if vaddr <= addr and addr - vaddr <= fsize
            and size <= fsize - (addr - vaddr)
        ]
        require(len(candidates) == 1,
                "elf_needed_string_table_ambiguous_or_unmapped")
        table = raw[candidates[0]:candidates[0] + size]
        require(len(table) == size, "elf_needed_string_table_truncated")
        def get(index):
            require(type(index) is int and 0 <= index < len(table),
                    "elf_needed_string_offset")
            end = table.find(b"\x00", index)
            require(end >= 0 and 0 < end - index <= MAX_STRING,
                    "elf_needed_string_unterminated")
            try:
                value = table[index:end].decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise ValueError("elf_needed_non_utf8") from exc
            require(all(ord(char) >= 32 for char in value),
                    "elf_needed_control_character")
            return value
    else:
        def get(index):
            raise ValueError("elf_needed_string_table_missing")
    needed = [get(index) for index in needed_refs]
    require(all("/" not in name and "\\" not in name
                and len(name.encode("utf-8")) <= 255 for name in needed),
            "elf_needed_unsafe_soname")
    require(len(needed) == len(set(needed)), "elf_needed_duplicate_dependency")
    soname = get(tags[DT_SONAME]) if DT_SONAME in tags else None
    require(soname is None or
            ("/" not in soname and "\\" not in soname and
             len(soname.encode("utf-8")) <= 255),
            "elf_needed_unsafe_soname")
    return {
        "status": "PT_DYNAMIC_DECLARATIONS",
        "needed": needed, "soname": soname,
        "rpath": get(tags[DT_RPATH]) if DT_RPATH in tags else None,
        "runpath": get(tags[DT_RUNPATH]) if DT_RUNPATH in tags else None,
    }


def scan_original(root, doc, *, max_bytes=MAX_ALL_BYTES):
    """Rehash each original member against its same-run extracted payload."""
    inventory.validate(doc)
    require(doc["context"]["kind"] == "appimage"
            and doc["context"]["platform"] == "linux"
            and doc["context"]["arch"] == "x86_64"
            and doc["custody"]["status"] == "DIRECT_CAPTURED_EXTRACTION"
            and len(doc["roots"]) == 1
            and doc["roots"][0]["role"] == "payload",
            "elf_needed_original_roster_context")
    members = [r for r in doc["roots"][0]["members"]
               if r.get("kind") == "file" and r.get("native_format") == "ELF"]
    require(1 <= len(members) <= MAX_ELF_MEMBERS,
            "elf_needed_original_roster_limit")
    root = Path(root).resolve(strict=True)
    require(type(max_bytes) is int and 0 < max_bytes <= MAX_ALL_BYTES,
            "elf_needed_original_total_budget")
    records, seen, total = [], set(), 0
    for member in sorted(members, key=lambda item: item["path"]):
        path = member["path"]
        require(isinstance(path, str) and not path.startswith("/")
                and not path.startswith("./")
                and all(part not in ("", ".", "..") for part in path.split("/"))
                and path not in seen,
                "elf_needed_original_duplicate_or_invalid_path")
        seen.add(path)
        expected = {"bytes": member["bytes"], "sha256": member["sha256"]}
        require(type(expected["bytes"]) is int
                and 0 < expected["bytes"] <= MAX_BINARY
                and SHA.fullmatch(expected["sha256"]) is not None,
                "elf_needed_original_member_identity")
        total += expected["bytes"]
        require(total <= max_bytes, "elf_needed_original_total_budget")
        target = root / path
        info = target.lstat()
        require(stat.S_ISREG(info.st_mode),
                "elf_needed_original_nonregular")
        raw = inventory.capture.stable_read(root, target, MAX_BINARY)
        require(digest(raw) == expected,
                "elf_needed_live_payload_different_from_original")
        decoded = dynamic_needed(raw)
        records.append({
            "member": path, "identity": digest(raw),
            "dynamic": decoded,
        })
    return {"scanned": len(records), "total_bytes": total, "entries": records}


DESKTOP_MEMBER = "usr/bin/motionwright-desktop"
SAME_SOURCE_KEYS = (
    "source", "source_tree", "run_id", "run_attempt", "platform", "arch",
    "candidate_receipt", "cargo_index", "artifact_identities_sha256",
    "generated_metadata_sha256",
)


def verify_cross_bundle_desktop(app, deb, scan, deb_payload):
    """Bind the one installed desktop ELF to identical DEB and AppImage bytes.

    This observes only the original files and dynamic declarations. It is not
    an assertion about transitive loader resolution, dlopen or host packages.
    """
    inventory.validate(deb)
    ac, dc = app["context"], deb["context"]
    require(ac["kind"] == "appimage" and dc["kind"] == "deb"
            and all(ac[k] == dc[k] for k in SAME_SOURCE_KEYS)
            and dc["platform"] == "linux" and dc["arch"] == "x86_64"
            and deb["custody"]["status"] == "DIRECT_CAPTURED_EXTRACTION"
            and len(deb["roots"]) == 1
            and deb["roots"][0]["role"] == "payload",
            "elf_needed_deb_appimage_source_mismatch")
    app_members = [
        member for member in app["roots"][0]["members"]
        if member["path"] == DESKTOP_MEMBER]
    deb_members = [
        member for member in deb["roots"][0]["members"]
        if member["path"] == DESKTOP_MEMBER]
    scanned = [
        item for item in scan["entries"]
        if item["member"] == DESKTOP_MEMBER]
    require(len(app_members) == len(deb_members) == len(scanned) == 1,
            "elf_needed_desktop_member_not_unique")
    a, d, observed = app_members[0], deb_members[0], scanned[0]
    require(a["kind"] == d["kind"] == "file"
            and a["native_format"] == d["native_format"] == "ELF"
            and type(a["bytes"]) is int
            and 0 < a["bytes"] <= MAX_BINARY
            and {"bytes": a["bytes"], "sha256": a["sha256"]} ==
                {"bytes": d["bytes"], "sha256": d["sha256"]} ==
                observed["identity"],
            "elf_needed_cross_bundle_desktop_bytes_differ")
    root = Path(deb_payload).resolve(strict=True)
    file = root / DESKTOP_MEMBER
    info = file.lstat()
    require(stat.S_ISREG(info.st_mode), "elf_needed_deb_desktop_nonregular")
    raw = inventory.capture.stable_read(root, file, MAX_BINARY)
    require(digest(raw) == observed["identity"],
            "elf_needed_deb_live_original_different")
    dynamic = dynamic_needed(raw)
    require(dynamic == observed["dynamic"],
            "elf_needed_cross_bundle_dynamic_different")
    return {
        "status": "SAME_ORIGINAL_DESKTOP_ELF_BYTES_AND_DYNAMIC_DECLARATIONS",
        "member": DESKTOP_MEMBER, "original": digest(raw),
        "dynamic": dynamic,
        "claims": {
            "desktop_elf_source_bytes": "EXACT_IN_DEB_AND_APPIMAGE",
            "external_system_libraries_resolved": False,
            "runtime_loader_execution": "NOT_RUN",
            "G2": "NOT_GRANTED", "G3": "STOP",
        },
    }


def candidate_edges(scan):
    """Identify *candidates*, never claim loader resolution or full closure."""
    index = {}
    for record in scan["entries"]:
        dynamic = record["dynamic"]
        basename = record["member"].split("/")[-1]
        for label in {basename, dynamic["soname"]} - {None}:
            index.setdefault(label, []).append(record)
    edges, dispositions = [], {}
    for record in scan["entries"]:
        for name in record["dynamic"]["needed"]:
            matches = index.get(name, [])
            distinct = {m["identity"]["sha256"] for m in matches}
            status = ("NOT_OBSERVED_IN_BUNDLE" if not distinct
                      else "ONE_BYTE_IDENTITY_CANDIDATE" if len(distinct) == 1
                      else "AMBIGUOUS_DIFFERENT_BYTES")
            dispositions[status] = dispositions.get(status, 0) + 1
            edges.append({
                "requester": record["member"], "dt_needed": name,
                "status": status,
                "candidate_members": sorted(m["member"] for m in matches),
                "candidate_sha256": sorted(distinct),
            })
    return {"edges": edges, "counts": dispositions,
            "assertions": {
                "loader_search_path_or_namespace_checked": False,
                "system_library_exclusion_approved": False,
                "dynamic_dlopen_and_plugins_observed": False,
                "native_graph_closure": "NOT_VERIFIED",
                "G2": "NOT_GRANTED", "G3": "STOP",
            }}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--appimage-inventory", type=Path, required=True)
    parser.add_argument("--live-payload", type=Path, required=True)
    parser.add_argument("--deb-inventory", type=Path, required=True)
    parser.add_argument("--deb-live-payload", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    source = args.expected_source
    require(sys.platform == "linux"
            and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_SHA") == source
            and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
            and SOURCE.fullmatch(source) is not None,
            "elf_needed_runner_source")
    repo = Path(__file__).resolve().parents[1]
    require(Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True) == repo,
            "elf_needed_runner_checkout")
    path = args.appimage_inventory.absolute()
    require(path.is_relative_to(repo), "elf_needed_inventory_outside_checkout")
    raw = inventory.capture.stable_read(repo, path, inventory.MAX_REPORT)
    original = inventory.capture.parse_json(raw)
    require(original["context"]["source"] == source
            and str(original["context"]["run_id"]) == os.environ["GITHUB_RUN_ID"]
            and original["context"]["run_attempt"] == 1,
            "elf_needed_original_source_binding")
    runner = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    payload = args.live_payload.resolve(strict=True)
    require(payload.is_relative_to(runner),
            "elf_needed_payload_outside_runner")
    scan = scan_original(payload, original)
    deb_inventory_path = args.deb_inventory.absolute()
    require(deb_inventory_path.is_relative_to(repo),
            "elf_needed_deb_inventory_outside_checkout")
    deb_raw = inventory.capture.stable_read(
        repo, deb_inventory_path, inventory.MAX_REPORT)
    deb_original = inventory.capture.parse_json(deb_raw)
    require(deb_original["context"]["source"] == source
            and str(deb_original["context"]["run_id"]) ==
                os.environ["GITHUB_RUN_ID"]
            and deb_original["context"]["run_attempt"] == 1,
            "elf_needed_original_deb_source_binding")
    deb_payload = args.deb_live_payload.resolve(strict=True)
    require(deb_payload.is_relative_to(runner),
            "elf_needed_deb_payload_outside_runner")
    desktop_pair = verify_cross_bundle_desktop(
        original, deb_original, scan, deb_payload)
    audit = candidate_edges(scan)
    result = {
        "schema": SCHEMA,
        "source": source, "source_tree": original["context"]["source_tree"],
        "run_id": original["context"]["run_id"], "run_attempt": 1,
        "original_appimage_inventory": digest(raw),
        "original_deb_inventory": digest(deb_raw),
        "same_desktop_elf_in_both_bundles": desktop_pair,
        "scanned_elf": scan["scanned"], "original_elf_bytes": scan["total_bytes"],
        "members": scan["entries"], "candidate_linkage": audit,
        "claims": {"system_library_host_resolution": "NOT_OBSERVED",
                   "dynamic_dlopen_sources": "NOT_OBSERVED",
                   "native_linker_graph": "NOT_VERIFIED",
                   "license_review": "NOT_PERFORMED",
                   "release_authorized": False,
                   "G2": "NOT_GRANTED", "G3": "STOP"},
    }
    data = inventory.capture.encoded(result)
    require(len(data) <= MAX_REPORT, "elf_needed_report_budget")
    output = args.output.absolute()
    require(output.is_relative_to(repo) and output.parent.is_dir()
            and not os.path.lexists(output),
            "elf_needed_output_replay_or_redirect")
    inventory.capture.write_new(output, data)
    require(inventory.capture.stable_read(repo, output, MAX_REPORT) == data,
            "elf_needed_output_changed")
    print("linux-elf-dynamic-needed=PASS ELF=" + str(scan["scanned"]) +
          " edges=" + str(len(audit["edges"])) +
          " G2=NOT_GRANTED G3=STOP")


if __name__ == "__main__":
    main()
