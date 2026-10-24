"""Authenticate observed NPM source bytes against the source-bound lock and archive.

No package code is run and no network request or extraction occurs here.
A valid SRI declaration alone is not an authenticated source observation.
"""
from __future__ import annotations
import base64, binascii, hashlib, io, json, posixpath, re, tarfile, zlib
from urllib.parse import urlsplit

class ArchiveError(ValueError):
    pass

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ArchiveError("duplicate_json_key")
        result[key] = value
    return result

def json_object(raw):
    try:
        value = json.loads(raw, object_pairs_hook=object_pairs)
    except (ValueError, UnicodeError) as error:
        raise ArchiveError("invalid_json") from error
    if not isinstance(value, dict):
        raise ArchiveError("json_object_required")
    return value

def relative_path(value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ArchiveError("invalid_member_path")
    if value.startswith("/") or "\\" in value or ":" in value or any(ord(c) < 32 for c in value):
        raise ArchiveError("invalid_member_path")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ArchiveError("invalid_member_path")
    return value

def locked_package(lock_bytes, lock_path):
    lock = json_object(lock_bytes)
    if lock.get("lockfileVersion") not in (2, 3):
        raise ArchiveError("unsupported_lock_version")
    relative_path(lock_path)
    if not re.fullmatch(r"(?:node_modules/(?:@[^/]+/)?[^/]+/)*node_modules/(?:@[^/]+/)?[^/]+", lock_path):
        raise ArchiveError("invalid_lock_package_path")
    entry = lock.get("packages", {}).get(lock_path)
    if not isinstance(entry, dict) or entry.get("link"):
        raise ArchiveError("locked_registry_package_required")
    if not all(isinstance(entry.get(k), str) and entry[k] for k in ("version", "resolved", "integrity")):
        raise ArchiveError("locked_archive_identity_required")
    url = urlsplit(entry["resolved"])
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
        raise ArchiveError("unsupported_archive_url")
    name = entry.get("name")
    if name is None:
        name = lock_path.rsplit("node_modules/", 1)[1]
    if not isinstance(name, str) or not re.fullmatch(r"(?:@[^/]+/)?[^/]+", name):
        raise ArchiveError("invalid_package_name")
    return entry, name

def verify_sri(raw, declaration):
    if not isinstance(declaration, str) or not declaration or len(declaration) > 8192:
        raise ArchiveError("invalid_sri")
    algorithms = {"sha256": 256, "sha384": 384, "sha512": 512}
    values = []
    for token in declaration.split():
        match = re.fullmatch(r"(sha256|sha384|sha512)-([A-Za-z0-9+/]+={0,2})", token)
        if not match:
            raise ArchiveError("unsupported_sri")
        algorithm, encoded = match.groups()
        try:
            value = base64.b64decode(encoded, validate=True)
        except binascii.Error as error:
            raise ArchiveError("invalid_sri") from error
        if len(value) != algorithms[algorithm] // 8 or base64.b64encode(value).decode() != encoded:
            raise ArchiveError("invalid_sri")
        values.append((algorithm, value))
    strongest = max(algorithms[algorithm] for algorithm, _ in values)
    selected = [(a, v) for a, v in values if algorithms[a] == strongest]
    if not any(hashlib.new(a, raw).digest() == v for a, v in selected):
        raise ArchiveError("archive_sri_mismatch")
    return selected[0][0]

def archive_members(raw, *, max_compressed=32*1024*1024, max_expanded=128*1024*1024,
                    max_member=64*1024*1024, max_entries=100000):
    if not isinstance(raw, bytes) or len(raw) > max_compressed:
        raise ArchiveError("compressed_archive_budget")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        body = decoder.decompress(raw, max_expanded + 1)
    except zlib.error as error:
        raise ArchiveError("invalid_gzip") from error
    if len(body) > max_expanded or decoder.unconsumed_tail:
        raise ArchiveError("expanded_archive_budget")
    if not decoder.eof or decoder.unused_data:
        raise ArchiveError("incomplete_or_concatenated_gzip")
    members, directories, names, package_raw, count = {}, set(), set(), None, 0
    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:") as archive:
            for member in archive:
                count += 1
                if count > max_entries:
                    raise ArchiveError("archive_entry_budget")
                name = member.name.rstrip("/") if member.isdir() else member.name
                relative_path(name)
                if name != "package" and not name.startswith("package/"):
                    raise ArchiveError("package_prefix_required")
                key = name.casefold()
                if key in names:
                    raise ArchiveError("duplicate_or_case_colliding_member")
                names.add(key)
                if member.isdir():
                    directories.add(name)
                    continue
                if not member.isfile():
                    raise ArchiveError("non_regular_archive_member")
                if member.size < 0 or member.size > max_member:
                    raise ArchiveError("archive_member_budget")
                if name == "package":
                    raise ArchiveError("package_prefix_required")
                path = name[len("package/"):]
                relative_path(path)
                data = archive.extractfile(member).read(max_member + 1)
                if len(data) != member.size:
                    raise ArchiveError("truncated_archive_member")
                if path == "package.json":
                    package_raw = data
                members[path] = {"bytes": len(data), "sha256": digest(data)}
            if any(body[archive.offset:]):
                raise ArchiveError("trailing_tar_content")
    except (tarfile.TarError, EOFError, OSError) as error:
        raise ArchiveError("invalid_tar") from error
    if package_raw is None:
        raise ArchiveError("archive_package_json_missing")
    for path in members:
        prefix = "package/" + path
        while "/" in prefix:
            prefix = prefix.rsplit("/", 1)[0]
            if prefix != "package" and prefix[len("package/"):] in members:
                raise ArchiveError("file_directory_collision")
    return members, package_raw

def authenticate_npm_sources(lock_bytes, lock_path, archive, observations):
    """Bind original installed modules/notices to an actually checked locked tarball.

    observations is a nonempty list of {path, bytes, sha256} records relative to
    the package. It must include package.json. It normally includes every raw
    contributing module and every original license/notice emitted by the plugin.
    """
    entry, name = locked_package(lock_bytes, lock_path)
    algorithm = verify_sri(archive, entry["integrity"])
    members, package_raw = archive_members(archive)
    package = json_object(package_raw)
    if package.get("name") != name or package.get("version") != entry["version"]:
        raise ArchiveError("archive_package_identity_mismatch")
    if not isinstance(observations, list) or not observations or len(observations) > 100000:
        raise ArchiveError("source_observations_required")
    observed, casefolded = {}, set()
    for row in observations:
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}:
            raise ArchiveError("invalid_source_observation")
        path = relative_path(row["path"])
        if path in observed or path.casefold() in casefolded:
            raise ArchiveError("duplicate_source_observation")
        if type(row["bytes"]) is not int or row["bytes"] < 0 or not re.fullmatch(r"[0-9a-f]{64}", str(row["sha256"])):
            raise ArchiveError("invalid_source_observation")
        if members.get(path) != {"bytes": row["bytes"], "sha256": row["sha256"]}:
            raise ArchiveError("observed_source_not_in_locked_archive:" + path)
        observed[path] = row
        casefolded.add(path.casefold())
    if "package.json" not in observed:
        raise ArchiveError("observed_package_json_required")
    return {"schema": "motionwright.npm-source-authentication/1", "lock_path": lock_path,
            "name": name, "version": entry["version"], "resolved": entry["resolved"],
            "integrity": entry["integrity"], "sri_algorithm": algorithm,
            "package_lock_sha256": digest(lock_bytes), "archive_sha256": digest(archive),
            "archive_bytes": len(archive), "archive_regular_files": len(members),
            "package_json_sha256": digest(package_raw),
            "verified_observations": [observed[path] for path in sorted(observed)],
            "status": "LOCKED_ARCHIVE_AND_OBSERVED_SOURCE_BYTES_VERIFIED",
            "coverage": "Only the supplied observed modules and original notice files; not transitive CSS/assets/native/installer closure",
            "redistribution_permission": "NOT_INFERRED"}
