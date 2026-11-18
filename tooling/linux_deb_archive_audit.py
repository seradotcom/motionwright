"""Authenticate original Debian ar/control/data and extracted-inventory identity.

This is a source-bound archive metadata and payload check, not verification
of native linkage, licensing, the AppImage runtime, installation, or release.
No package contents are extracted, installed or executed.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

import candidate_package_inventory as inventory

SCHEMA = "motionwright.linux-deb-original-control-audit/1"
MAX_ARCHIVE = 128 * 1024 * 1024
MAX_TAR_MEMBERS = 4096
MAX_CONTROL_TAR = 128 * 1024
MAX_CONTROL_FILE = 64 * 1024
MAX_PAYLOAD_MEMBER = 512 * 1024 * 1024
MAX_PAYLOAD_BYTES = 512 * 1024 * 1024
AR_MEMBERS = ("debian-binary", "control.tar.gz", "data.tar.gz")
MD5 = re.compile(r"[0-9a-f]{32}")
DEB_FILENAME = re.compile(r"Motionwright_([0-9][A-Za-z0-9.+~:-]*)_amd64[.]deb")
PACKAGE_FIELDS = ("Package", "Version", "Architecture", "Description")
REQUIRED_RUNTIME = frozenset({"libwebkit2gtk-4.1-0", "libgtk-3-0"})
RUNTIME_RELATION = re.compile(
    r"(?P<package>[a-z0-9][a-z0-9+.-]+)"
    r"(?::(?P<arch>[a-z0-9][a-z0-9-]*))?"
    r"(?:[ \t]*\([ \t]*(?:<<|<=|=|>=|>>)[ \t]*"
    r"(?P<version>[0-9][A-Za-z0-9.+:~\-]*)[ \t]*\))?"
)


def need(condition, code):
    if not condition:
        raise ValueError(code)


def checksum(raw):
    return hashlib.sha256(raw).hexdigest()


def original_ar_parts(raw):
    """Only this exact Debian 2.0 ar profile is admitted, no GNU long names."""
    need(type(raw) is bytes and 8 + 3 * 60 < len(raw) <= MAX_ARCHIVE,
         "deb_archive_byte_budget")
    need(raw[:8] == b"!<arch>\n", "deb_ar_magic")
    offset, parts, headers = 8, {}, {}
    for required in AR_MEMBERS:
        need(offset + 60 <= len(raw), "deb_ar_header_truncated")
        header = raw[offset:offset + 60]
        offset += 60
        need(header[58:60] == bytes((96, 10)), "deb_ar_header_magic")
        try:
            name = header[:16].decode("ascii").strip().rstrip("/")
            number = header[48:58].decode("ascii").strip()
            size = int(number)
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError("deb_ar_header_invalid") from error
        need(name == required and str(size) == number and 0 <= size <= MAX_ARCHIVE
             and offset + size <= len(raw), "deb_ar_member_roster_or_size")
        body = raw[offset:offset + size]
        parts[name] = body
        headers[name] = {"bytes": size, "sha256": checksum(body),
                         "header_sha256": checksum(header)}
        offset += size
        if size % 2:
            need(offset < len(raw) and raw[offset:offset+1] == b"\n",
                 "deb_ar_member_padding")
            offset += 1
    need(offset == len(raw) and parts["debian-binary"] == b"2.0\n",
         "deb_ar_extra_member_or_version")
    return parts, headers


def tar_member_name(member, *, container):
    name = member.name
    need(isinstance(name, str) and name not in ("", ".", "./")
         and not name.startswith("./") and not name.endswith("/"),
         "deb_noncanonical_" + container + "_member")
    inventory.relative(name)
    need(not member.pax_headers and not member.sparse,
         "deb_tar_extended_or_sparse_member")
    need(member.isfile() or member.isdir() or member.issym(),
         "deb_unsupported_" + container + "_member")
    if member.isdir():
        need(member.size == 0, "deb_directory_with_data")
    if member.issym():
        need(member.size == 0 and isinstance(member.linkname, str)
             and 0 < len(member.linkname) <= 4096 and "\0" not in member.linkname,
             "deb_symlink_invalid")
    return name


def control_parts(blob):
    need(type(blob) is bytes and 0 < len(blob) <= MAX_CONTROL_TAR,
         "deb_control_compressed_budget")
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
            result = {}
            for row in archive:
                name = tar_member_name(row, container="control")
                need(row.isfile() and name in ("control", "md5sums")
                     and name not in result
                     and 0 < row.size <= MAX_CONTROL_FILE,
                     "deb_control_member_not_closed")
                reader = archive.extractfile(row)
                need(reader is not None, "deb_control_unreadable")
                raw = reader.read(MAX_CONTROL_FILE + 1)
                need(len(raw) == row.size, "deb_control_member_bytes")
                result[name] = raw
    except (tarfile.TarError, EOFError, OSError) as error:
        raise ValueError("deb_control_tar_invalid") from error
    need(set(result) == {"control", "md5sums"}, "deb_control_file_roster")
    return result


def required_runtime_relations(value):
    """Require both runtime packages in every alternative of a mandatory group.

    This is the emitted amd64 binary-control profile, not a general source
    control parser or proof of linkage. Architecture/build restrictions and
    folded source-only relationships are not admitted.
    """
    need(isinstance(value, str) and 0 < len(value) <= MAX_CONTROL_FILE
         and "\n" not in value and "\r" not in value,
         "deb_control_runtime_relation_format")
    groups = value.split(",")
    need(0 < len(groups) <= 256, "deb_control_runtime_relation_budget")
    mandatory = set()
    for group in groups:
        alternatives = group.split("|")
        need(0 < len(alternatives) <= 32, "deb_control_runtime_relation_budget")
        packages = set()
        for alternative in alternatives:
            match = RUNTIME_RELATION.fullmatch(alternative.strip(" \t"))
            need(match is not None, "deb_control_runtime_relation_format")
            version = match["version"]
            if version is not None:
                if ":" in version:
                    epoch, version = version.split(":", 1)
                    need(re.fullmatch(r"[0-9]+", epoch) is not None,
                         "deb_control_runtime_relation_version")
                upstream, separator, revision = version.rpartition("-")
                if separator:
                    need(re.fullmatch(r"[A-Za-z0-9+.~]+", revision) is not None,
                         "deb_control_runtime_relation_version")
                else:
                    upstream = version
                need(re.fullmatch(r"[0-9][A-Za-z0-9.+:~\-]*", upstream) is not None,
                     "deb_control_runtime_relation_version")
            packages.add((match["package"], match["arch"] or "amd64"))
        if len(packages) == 1:
            package, arch = next(iter(packages))
            if arch == "amd64":
                mandatory.add(package)
    need(REQUIRED_RUNTIME <= mandatory, "deb_control_required_runtime_relations")


def control_fields(raw, package_filename):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_CONTROL_FILE
         and raw.endswith(b"\n") and b"\r" not in raw and b"\x00" not in raw,
         "deb_control_text_budget")
    try:
        rows = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("deb_control_text_encoding") from error
    result, active = {}, None
    for line in rows:
        if line.startswith(" "):
            need(active is not None, "deb_control_orphan_continuation")
            result[active] += "\n" + line
        else:
            need(": " in line and line[0].isalpha(), "deb_control_invalid_field")
            key, value = line.split(": ", 1)
            need(re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", key) is not None
                 and key.lower() not in {k.lower() for k in result}
                 and value.strip(), "deb_control_duplicate_or_empty_field")
            result[key] = value
            active = key
    for key in PACKAGE_FIELDS:
        need(key in result, "deb_control_missing_" + key.lower())
    match = DEB_FILENAME.fullmatch(package_filename)
    need(match is not None, "deb_filename_profile")
    need(result["Package"] == "motionwright" and result["Version"] == match[1]
         and result["Architecture"] == "amd64", "deb_control_candidate_identity")
    need("Depends" in result, "deb_control_required_runtime_relations")
    required_runtime_relations(result["Depends"])
    return result


def parsed_md5sums(raw):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_CONTROL_FILE
         and raw.endswith(b"\n") and b"\r" not in raw and b"\0" not in raw,
         "deb_md5sums_text_budget")
    result = {}
    for line in raw.splitlines():
        need(len(line) >= 35 and line[32:34] == b"  ",
             "deb_md5sums_line_format")
        try:
            value = line[:32].decode("ascii")
            path = line[34:].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("deb_md5sums_encoding") from error
        need(MD5.fullmatch(value) is not None and path.startswith("usr/")
             and path not in result, "deb_md5sums_duplicate_or_path")
        inventory.relative(path)
        result[path] = value
    need(result and len(result) <= MAX_TAR_MEMBERS, "deb_md5sums_roster")
    return result


def audit_deb_bytes(deb_raw, original_inventory, expected_source):
    inventory.validate(original_inventory)
    ctx = original_inventory["context"]
    need(ctx["kind"] == "deb" and ctx["platform"] == "linux"
         and ctx["arch"] == "x86_64" and ctx["run_attempt"] == 1
         and ctx["source"] == expected_source
         and original_inventory["custody"]["status"] == "DIRECT_CAPTURED_EXTRACTION",
         "deb_original_inventory_context")
    need(len(deb_raw) == ctx["package"]["bytes"]
         and checksum(deb_raw) == ctx["package"]["sha256"],
         "deb_original_package_bytes_changed")
    need(len(original_inventory["roots"]) == 1
         and original_inventory["roots"][0]["role"] == "payload",
         "deb_inventory_root_changed")
    entries = {r["path"]: r for r in original_inventory["roots"][0]["members"]}
    need(set(entries) >= {"DEBIAN", "DEBIAN/control", "DEBIAN/md5sums"}
         and entries["DEBIAN"]["kind"] == "directory"
         and {p for p in entries if p == "DEBIAN" or p.startswith("DEBIAN/")}
             == {"DEBIAN", "DEBIAN/control", "DEBIAN/md5sums"},
         "deb_unexpected_control_namespace")
    ar_parts, ar_headers = original_ar_parts(deb_raw)
    metadata = control_parts(ar_parts["control.tar.gz"])
    for name, content in metadata.items():
        recorded = entries["DEBIAN/" + name]
        need(recorded["kind"] == "file"
             and recorded["bytes"] == len(content)
             and recorded["sha256"] == checksum(content),
             "deb_original_control_member_changed:" + name)
    fields = control_fields(metadata["control"], ctx["package"]["filename"])
    md5_expected = parsed_md5sums(metadata["md5sums"])
    expected = {key: val for key, val in entries.items()
                if key != "DEBIAN" and not key.startswith("DEBIAN/")}
    expected_files = {p: r for p, r in expected.items() if r["kind"] == "file"}
    need(set(expected_files) == set(md5_expected), "deb_md5sums_not_complete")
    observed, total_files, total_dirs, total_symlinks, bytes_total = set(), 0, 0, 0, 0
    try:
        with tarfile.open(fileobj=io.BytesIO(ar_parts["data.tar.gz"]),
                          mode="r|gz") as archive:
            for item in archive:
                path = tar_member_name(item, container="data")
                need(path in expected and path not in observed,
                     "deb_data_tar_extra_or_duplicate")
                observed.add(path)
                recorded = expected[path]
                if item.isfile():
                    need(recorded["kind"] == "file"
                         and item.size == recorded["bytes"]
                         and 0 <= item.size <= MAX_PAYLOAD_MEMBER,
                         "deb_data_file_type_or_size_changed")
                    reader = archive.extractfile(item)
                    need(reader is not None, "deb_data_file_unreadable")
                    whole = hashlib.sha256()
                    md5 = hashlib.md5(usedforsecurity=False)
                    counted = 0
                    while True:
                        part = reader.read(1024 * 1024)
                        if not part:
                            break
                        counted += len(part)
                        need(counted <= item.size
                             and bytes_total + counted <= MAX_PAYLOAD_BYTES,
                             "deb_data_file_budget")
                        whole.update(part)
                        md5.update(part)
                    need(counted == item.size
                         and whole.hexdigest() == recorded["sha256"]
                         and md5.hexdigest() == md5_expected[path],
                         "deb_data_original_bytes_or_md5_mismatch")
                    total_files += 1
                    bytes_total += counted
                elif item.isdir():
                    need(recorded["kind"] == "directory", "deb_data_directory_changed")
                    total_dirs += 1
                else:
                    need(item.issym() and recorded["kind"] == "symlink"
                         and item.linkname == recorded["target"],
                         "deb_data_symlink_literal_changed")
                    total_symlinks += 1
                need(len(observed) <= MAX_TAR_MEMBERS,
                     "deb_data_member_roster_budget")
    except (tarfile.TarError, EOFError, OSError) as error:
        raise ValueError("deb_data_tar_invalid") from error
    need(set(expected) == observed and total_files == len(md5_expected),
         "deb_data_tar_namespace_not_inventory")
    return {
        "schema": SCHEMA,
        "status": "ORIGINAL_DEB_AR_CONTROL_DATA_AND_MD5SUMS_EXACT",
        "context": {
            "run_id": ctx["run_id"], "run_attempt": ctx["run_attempt"],
            "source": ctx["source"], "source_tree": ctx["source_tree"],
            "package": ctx["package"], "candidate_receipt": ctx["candidate_receipt"],
        },
        "ar_members": ar_headers,
        "control": {
            "bytes": len(metadata["control"]),
            "sha256": checksum(metadata["control"]),
            "package": fields["Package"], "version": fields["Version"],
            "arch": fields["Architecture"],
            "depends_value_sha256": checksum(fields["Depends"].encode("utf-8")),
        },
        "md5sums": {
            "bytes": len(metadata["md5sums"]),
            "sha256": checksum(metadata["md5sums"]),
            "all_data_regular_members": total_files,
            "recorded_entries": len(md5_expected),
            "status": "EACH_ORIGINAL_DATA_FILE_MD5_AND_SHA256_EXACT",
        },
        "data_tar": {
            "regular_files": total_files, "directories": total_dirs,
            "symlinks": total_symlinks, "total_regular_bytes": bytes_total,
            "member_roster_complete_vs_extracted_inventory": True,
        },
        "gaps": {
            "Tauri_generator_provenance": "NOT_ATTESTED",
            "AppImage_additional_members": "NOT_CHECKED",
            "native_linker_graph": "NOT_VERIFIED",
            "native_and_transitive_licenses": "NOT_REVIEWED",
            "redistribution_permission": "NOT_INFERRED",
            "formal_G2": "NOT_GRANTED", "formal_G3": "NOT_GRANTED",
            "human_acceptance": False, "release_authorized": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deb", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--expected-inventory-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    need(re.fullmatch(r"[0-9a-f]{40}", args.expected_source) is not None,
         "deb_expected_source_format")
    need(re.fullmatch(r"[0-9a-f]{64}", args.expected_inventory_sha256) is not None,
         "deb_expected_inventory_sha")
    inventory_path = args.inventory.absolute()
    raw_inventory = inventory.capture.stable_read(
        Path(inventory_path.anchor), inventory_path, inventory.MAX_REPORT)
    need(0 < len(raw_inventory) <= inventory.MAX_REPORT
         and checksum(raw_inventory) == args.expected_inventory_sha256,
         "deb_inventory_original_sha_changed")
    parsed = inventory.capture.parse_json(raw_inventory)
    deb_path = args.deb.absolute()
    raw_deb = inventory.capture.stable_read(
        Path(deb_path.anchor), deb_path, MAX_ARCHIVE)
    result = audit_deb_bytes(raw_deb, parsed, args.expected_source)
    need(args.output.parent.is_dir() and not args.output.exists(),
         "deb_output_preexisting_or_missing_parent")
    inventory.capture.write_new(args.output, inventory.capture.encoded(result))
    output_path = args.output.absolute()
    readback = inventory.capture.stable_read(
        Path(output_path.anchor), output_path, inventory.MAX_REPORT)
    need(inventory.capture.parse_json(readback) == result, "deb_output_readback_failed")
    print("deb-original-control-audit=PASS files=" +
          str(result["data_tar"]["regular_files"]) +
          " md5sums=" + str(result["md5sums"]["recorded_entries"]) +
          " G3=NOT_GRANTED", flush=True)


if __name__ == "__main__":
    main()
