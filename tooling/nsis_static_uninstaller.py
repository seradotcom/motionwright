"""Derive an uninstaller reference from original NSIS bytes without running setup.

Only the reader executes. The reconstructed executable remains in private
runner storage; exported observations contain identities and command streams.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import struct

SCHEMA = "motionwright.nsis-static-uninstaller/1"
MEMBER = "mw-owned-new-uninstaller.exe"
MAX_LIST = 8 * 1024 * 1024
MAX_SELF = 16 * 1024 * 1024
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
READER_URL = "https://github.com/ip7z/7zip/releases/download/26.05/7z2605-x64.exe"
READER_ARCHIVE = {"bytes": 1663506,
    "sha256": "fd99e86f57d8f429ef5ed045bf98315af962e2309b0f1ba87a147a44c71647d0"}
READER_FILES = {
    "7z.exe": {"bytes": 577536, "sha256": "94205cfc054dd3bbeb978905bc619a65b3f04e9e73bb2717841110b6f46d962e"},
    "7z.dll": {"bytes": 1908736, "sha256": "78bf6b668d75801e3aa180b4ccb16e6d8395fba80dec8a065055bb6f8dfabb82"},
    "License.txt": {"bytes": 6031, "sha256": "519ac0a4bded9c18ea02e0afb71f663d8c47373bd9facd3ac96a79f51d77765d"},
}
ORIGINALS = tuple(
    f"{label}.{suffix}"
    for label in ("reader", "listing", "extract")
    for suffix in ("request.json", "completion.json", "stdout", "stderr")
    if (label, suffix) != ("extract", "stdout")
)


def require(value, code):
    if not value:
        raise ValueError(code)


def digest(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def validate_context(value):
    require(isinstance(value, dict) and set(value) == {
        "source", "source_tree", "run_id", "run_attempt",
        "base_inventory_sha256", "setup"}, "nsis_static_context_fields")
    require(isinstance(value["source"], str) and SHA40.fullmatch(value["source"])
            and isinstance(value["source_tree"], str) and SHA40.fullmatch(value["source_tree"])
            and isinstance(value["run_id"], str) and re.fullmatch(r"[1-9][0-9]*", value["run_id"])
            and type(value["run_attempt"]) is int and value["run_attempt"] == 1
            and isinstance(value["base_inventory_sha256"], str)
            and SHA64.fullmatch(value["base_inventory_sha256"]), "nsis_static_context_identity")
    setup = value["setup"]
    require(isinstance(setup, dict) and set(setup) == {"filename", "bytes", "sha256"}
            and isinstance(setup["filename"], str)
            and re.fullmatch(r"[A-Za-z0-9_.-]{1,200}-setup\.exe", setup["filename"])
            and type(setup["bytes"]) is int and 0 < setup["bytes"] <= 512 * 1024 * 1024
            and isinstance(setup["sha256"], str) and SHA64.fullmatch(setup["sha256"]),
            "nsis_static_setup_identity")
    return value


def select_member(raw, setup_bytes):
    require(0 < len(raw) <= MAX_LIST, "nsis_static_listing_bound")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as error:
        raise ValueError("nsis_static_listing_encoding") from error
    require("\0" not in text and not re.search(
        r"(?i)\b(?:BadCmd|ERRORS?|WARNINGS?|unsupported)\b", text),
        "nsis_static_listing_diagnostic")
    lines = text.splitlines()
    separators = [i for i, line in enumerate(lines) if line == "----------"]
    require(len(separators) == 1, "nsis_static_listing_structure")
    before = lines[:separators[0]]
    require(before.count("Type = Nsis") == 1
            and before.count("Physical Size = " + str(setup_bytes)) == 1,
            "nsis_static_archive_profile")
    blocks, block = [], {}
    for line in [*lines[separators[0] + 1:], ""]:
        if not line:
            if block:
                blocks.append(block); block = {}
            continue
        require(" = " in line, "nsis_static_listing_property")
        key, value = line.split(" = ", 1)
        require(key and key not in block, "nsis_static_listing_duplicate_property")
        block[key] = value
    require(0 < len(blocks) <= 4096, "nsis_static_listing_member_bound")
    names, selected = set(), []
    for block in blocks:
        name = block.get("Path")
        require(isinstance(name, str) and 0 < len(name) <= 1024
                and not any(ord(char) < 32 for char in name)
                and name.casefold() not in names, "nsis_static_member_ambiguous")
        names.add(name.casefold())
        if name.replace("\\", "/").rsplit("/", 1)[-1].casefold() != MEMBER.casefold():
            continue
        require(name.replace("\\", "/").rsplit("/", 1)[-1] == MEMBER
                and all(part not in {"", ".", ".."} for part in name.replace("\\", "/").split("/"))
                and block.get("Folder", "-") == "-"
                and "Size" in block
                and re.fullmatch(r"(?:[1-9][0-9]*)?", block["Size"]),
                "nsis_static_member_invalid")
        # Solid NSIS members can have no listed size until decompression.
        # The actual process stream remains bounded independently of this hint.
        size = int(block["Size"]) if block["Size"] else None
        require(size is None or 64 <= size <= MAX_SELF, "nsis_static_member_bound")
        selected.append({"path": name, "listed_bytes": size})
    require(len(selected) == 1, "nsis_static_member_missing_or_repeated")
    return selected[0]


def inspect_pe(raw):
    require(64 <= len(raw) <= MAX_SELF and raw[:2] == b"MZ", "nsis_static_member_not_pe")
    offset = struct.unpack_from("<I", raw, 60)[0]
    require(64 <= offset <= min(len(raw) - 24, 1024 * 1024)
            and raw[offset:offset + 4] == b"PE\0\0", "nsis_static_member_not_pe")
    machine, sections = struct.unpack_from("<HH", raw, offset + 4)
    optional_size, flags = struct.unpack_from("<HH", raw, offset + 20)
    require(machine in (0x14C, 0x8664) and 0 < sections <= 96
            and flags & 2 and not flags & 0x2000
            and optional_size >= 96 and offset + 24 + optional_size <= len(raw),
            "nsis_static_member_pe_profile")
    magic = struct.unpack_from("<H", raw, offset + 24)[0]
    require((machine, magic) in ((0x14C, 0x10B), (0x8664, 0x20B)),
            "nsis_static_member_pe_profile")
    return {"format": "PE", "machine": hex(machine), "sections": sections}


def reader_identity(capture, reader, *, pinned=True):
    reader = reader.resolve(strict=True)
    require(reader.name.casefold() == "7z.exe", "nsis_static_reader_name")
    result = {}
    for role, path in (("executable", reader), ("library", reader.with_name("7z.dll"))):
        observed = capture.stable_hash(path.parent, path.name, limit=128 * 1024 * 1024)
        require(observed["bytes"] > 0, "nsis_static_reader_empty")
        require(not pinned or observed == READER_FILES[path.name.lower()], "nsis_static_reader_pin")
        result[role] = {"path": str(path), **observed}
    return result


def commands(reader, setup, member=None):
    base = str(reader)
    result = {
        "reader": [base, "i", "-sccUTF-8"],
        "listing": [base, "l", "-slt", "-sccUTF-8", "--", str(setup)],
    }
    if member is not None:
        result["extract"] = [base, "x", "-so", "-spd", "-sccUTF-8", "--", str(setup), member]
    return result


def source_identity(capture, setup, context):
    require(setup.name == context["setup"]["filename"], "nsis_static_setup_name")
    observed = capture.stable_hash(setup.parent, setup.name, limit=512 * 1024 * 1024)
    require(observed == {key: context["setup"][key] for key in ("bytes", "sha256")},
            "nsis_static_setup_changed")
    return observed


def private_layout(repo, private_root, private_payload, context):
    private_root = private_root.resolve(strict=True)
    repository = repo.resolve(strict=True)
    require(not private_root.is_relative_to(repository)
            and not repository.is_relative_to(private_root)
            and private_payload.is_absolute()
            and private_payload == private_root / ("motionwright-uninstaller-" + context["run_id"] + "-1"),
            "nsis_static_private_destination")
    return private_root


def observe(*, capture, repo, setup, reader, observations, private_root, private_payload, context):
    """Create exactly one static reference before the caller starts installation."""
    validate_context(context)
    private_root = private_layout(repo, private_root, private_payload, context)
    setup = capture.admitted(repo, setup)
    source_identity(capture, setup, context)
    before_reader = reader_identity(capture, reader)
    reader = Path(before_reader["executable"]["path"])
    require(not observations.exists() and not private_payload.exists(),
            "nsis_static_original_destination_exists")
    capture.new_directory(repo, observations)
    capture.new_directory(private_root, private_payload)
    argv = commands(reader, setup)
    for label in ("reader", "listing"):
        capture.run_captured(argv[label], repo, observations, label,
                             stream_limit=MAX_LIST, timeout=45)
        require(not capture.stable_read(observations, label + ".stderr", MAX_LIST).strip(),
                "nsis_static_reader_diagnostic")
    listing = capture.stable_read(observations, "listing.stdout", MAX_LIST)
    member = select_member(listing, context["setup"]["bytes"])
    argv = commands(reader, setup, member["path"])
    try:
        capture.run_captured(argv["extract"], repo, private_payload, "extract",
                             stream_limit=MAX_SELF, timeout=90)
    finally:
        # stdout is an executable, so it never enters the exported observations.
        for suffix in ("request.json", "completion.json", "stderr"):
            path = private_payload / ("extract." + suffix)
            if path.is_file():
                raw = capture.stable_read(private_payload, path.name, MAX_SELF)
                capture.write_new(observations / path.name, raw)
    require(not capture.stable_read(private_payload, "extract.stderr", MAX_SELF).strip(),
            "nsis_static_reader_diagnostic")
    raw = capture.stable_read(private_payload, "extract.stdout", MAX_SELF)
    require(member["listed_bytes"] is None or len(raw) == member["listed_bytes"],
            "nsis_static_extracted_size_mismatch")
    pe = inspect_pe(raw)
    source_identity(capture, setup, context)
    require(reader_identity(capture, reader) == before_reader, "nsis_static_reader_changed")
    record = {
        "schema": SCHEMA, "context": context, "reader": before_reader, "member": member,
        "identity": digest(raw), "inspection": pe,
        "origin": "ORIGINAL_SETUP_STATIC_RECONSTRUCTION", "setup_executed": False,
        "originals": {name: capture.stable_hash(observations, name, limit=MAX_SELF)
                      for name in ORIGINALS},
        "payload_exported": False, "full_package_inventory": "NOT_VERIFIED",
    }
    reference = capture.encoded(record)
    capture.write_new(observations / "reference.json", reference)
    return verify(capture=capture, repo=repo, setup=setup, observations=observations,
                  private_root=private_root, private_payload=private_payload, context=context,
                  expected_reference_sha256=digest(reference)["sha256"])


def verify(*, capture, repo, setup, observations, private_root, private_payload, context,
           expected_reference_sha256):
    """Revalidate the sealed static reference; never read the installed file."""
    validate_context(context)
    private_layout(repo, private_root, private_payload, context)
    require(isinstance(expected_reference_sha256, str)
            and SHA64.fullmatch(expected_reference_sha256), "nsis_static_reference_seal_required")
    raw = capture.stable_read(observations, "reference.json", 1024 * 1024)
    require(digest(raw)["sha256"] == expected_reference_sha256, "nsis_static_reference_seal_changed")
    record = capture.parse_json(raw)
    require(isinstance(record, dict) and set(record) == {
        "schema", "context", "reader", "member", "identity", "inspection", "origin",
        "setup_executed", "originals", "payload_exported", "full_package_inventory"},
        "nsis_static_reference_fields")
    require(record["schema"] == SCHEMA and record["context"] == context
            and record["origin"] == "ORIGINAL_SETUP_STATIC_RECONSTRUCTION"
            and record["setup_executed"] is False and record["payload_exported"] is False
            and record["full_package_inventory"] == "NOT_VERIFIED",
            "nsis_static_reference_context")
    require({p.name for p in observations.iterdir()} == {*ORIGINALS, "reference.json"},
            "nsis_static_original_roster")
    source_identity(capture, setup, context)
    reader = Path(record["reader"]["executable"]["path"])
    require(reader_identity(capture, reader) == record["reader"], "nsis_static_reader_changed")
    require(record["originals"] == {
        name: capture.stable_hash(observations, name, limit=MAX_SELF) for name in ORIGINALS},
        "nsis_static_original_changed")
    member = select_member(capture.stable_read(observations, "listing.stdout", MAX_LIST),
                           context["setup"]["bytes"])
    require(member == record["member"], "nsis_static_reference_member")
    payload = capture.stable_read(private_payload, "extract.stdout", MAX_SELF)
    require(digest(payload) == record["identity"]
            and (member["listed_bytes"] is None or len(payload) == member["listed_bytes"])
            and inspect_pe(payload) == record["inspection"], "nsis_static_reference_bytes")
    argv = commands(reader, setup, member["path"])
    for label, command in argv.items():
        request = capture.parse_json(capture.stable_read(observations, label + ".request.json"))
        completed = capture.parse_json(capture.stable_read(observations, label + ".completion.json"))
        expected = {"command": command, "cwd": str(repo), "label": label, "executed_once": True}
        require(request == expected and all(completed.get(k) == v for k, v in expected.items())
                and type(completed.get("returncode")) is int and completed["returncode"] == 0
                and completed.get("errors") == [], "nsis_static_process_not_completed")
        stdout = payload if label == "extract" else capture.stable_read(observations, label + ".stdout", MAX_LIST)
        stderr = capture.stable_read(observations, label + ".stderr", MAX_SELF)
        require(not stderr.strip()
                and completed.get("stdout_sha256") == digest(stdout)["sha256"]
                and completed.get("stderr_sha256") == digest(stderr)["sha256"],
                "nsis_static_process_stream_mismatch")
    require(capture.stable_read(observations, "reference.json", 1024 * 1024) == raw,
            "nsis_static_reference_changed")
    return {
        "expected_role": "INSTALLER_GENERATED_SELF",
        "reference_status": "ORIGINAL_SETUP_STATIC_RECONSTRUCTION",
        "expected_identity": record["identity"],
        "setup_sha256": context["setup"]["sha256"],
        "reference_sha256": digest(raw)["sha256"],
    }


def resolve_bootstrap(capture, *, program_files, path_executable):
    """Prefer the installed tool directory; a PATH shim lacks its own library."""
    candidates = []
    for value in (program_files, path_executable):
        require(value is None or (isinstance(value, str) and value
                                  and Path(value).is_absolute()),
                "nsis_static_bootstrap_path")
    if program_files is not None:
        candidates.append(Path(program_files) / "7-Zip" / "7z.exe")
    if path_executable is not None:
        candidates.append(Path(path_executable))
    for candidate in candidates:
        try:
            identity = reader_identity(capture, candidate, pinned=False)
        except FileNotFoundError:
            continue
        return Path(identity["executable"]["path"])
    raise ValueError("nsis_static_bootstrap_not_found")


def provision_reader(*, capture, directory, bootstrap):
    """Extract three hash-pinned portable tool files; never execute its installer."""
    import urllib.parse
    import urllib.request

    class Redirects(urllib.request.HTTPRedirectHandler):
        count = 0

        def redirect_request(self, request, fp, code, message, headers, new_url):
            self.count += 1
            target = urllib.parse.urlsplit(new_url)
            require(self.count <= 3 and target.scheme == "https"
                    and target.hostname in {"github.com", "release-assets.githubusercontent.com"}
                    and target.port in (None, 443) and target.username is None
                    and target.password is None, "nsis_reader_download_redirect")
            return super().redirect_request(request, fp, code, message, headers, new_url)

    require(directory.is_absolute() and not directory.exists(), "nsis_reader_intake_exists")
    bootstrap_before = reader_identity(capture, bootstrap, pinned=False)
    bootstrap = Path(bootstrap_before["executable"]["path"])
    capture.new_directory(directory.parent, directory)
    with urllib.request.build_opener(Redirects()).open(READER_URL, timeout=30) as response:
        require(response.status == 200, "nsis_reader_download_status")
        raw = response.read(READER_ARCHIVE["bytes"] + 1)
    require(digest(raw) == READER_ARCHIVE, "nsis_reader_archive_pin")
    archive = directory / "7z2605-x64.exe"
    capture.write_new(archive, raw)
    for name, expected in READER_FILES.items():
        label = "tool-" + name.replace(".", "-")
        capture.run_captured(
            [str(bootstrap), "x", "-so", "-spd", "-sccUTF-8", "--", str(archive), name],
            directory, directory, label, stream_limit=4 * 1024 * 1024, timeout=45)
        require(not capture.stable_read(directory, label + ".stderr").strip(),
                "nsis_reader_intake_diagnostic")
        body = capture.stable_read(directory, label + ".stdout", 4 * 1024 * 1024)
        require(digest(body) == expected, "nsis_reader_portable_pin")
        capture.write_new(directory / name, body)
    require(reader_identity(capture, bootstrap, pinned=False) == bootstrap_before,
            "nsis_reader_bootstrap_changed")
    require(capture.stable_hash(directory, archive.name) == READER_ARCHIVE,
            "nsis_reader_archive_changed")
    portable = directory / "7z.exe"
    identity = reader_identity(capture, portable)
    capture.write_new(directory / "intake.json", capture.encoded({
        "schema": "motionwright.nsis-static-reader-intake/1",
        "origin": READER_URL, "archive": READER_ARCHIVE, "portable_files": READER_FILES,
        "reader": identity, "bootstrap": bootstrap_before, "installer_executed": False,
    }))
    return portable
