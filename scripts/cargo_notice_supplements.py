"""Offline verification of explicitly reviewed supplemental Cargo notice material.

The caller authenticates the crate and the tracked record/material bytes. This
module proves raw Git-object linkage and classifies every packaged member. It
does not fetch, execute commands, write files, choose a license or grant rights.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tomllib
from urllib.parse import urlsplit

MAX_RECORD = 64 * 1024
MAX_OBJECTS = 512
MAX_TREE = 4 * 1024 * 1024
MAX_BLOB = 2 * 1024 * 1024
MAX_PROOF = 16 * 1024 * 1024
MAX_MEMBERS = 20000
MAX_MEMBER = 32 * 1024 * 1024
MAX_SOURCE = 128 * 1024 * 1024
MAX_NOTICES = 8
MAX_TEXT = 32 * 1024 * 1024
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
PART = re.compile(r"[A-Za-z0-9_.+-]+")
NOTICE_BASENAME = re.compile(
    r"^(?:LICENSE|LICENCE|COPYING|NOTICE|COPYRIGHT)(?:[._-].*)?$", re.IGNORECASE)
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    prefix + str(number) for prefix in ("COM", "LPT") for number in range(1, 10)}
REPOSITORIES = frozenset("https://github.com/" + name for name in (
    "dropbox/rust-alloc-no-stdlib", "Stranger6667/jsonschema", "servo/stylo",
    "tauri-apps/tauri", "Nugine/simd", "wravery/webview2-rs"))
REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"
MPL_URL = "https://mozilla.org/MPL/2.0/"
MPL_TEXT_URL = "https://www.mozilla.org/media/MPL/2.0/index.txt"
MPL_HEADER = (
    b"/* This Source Code Form is subject to the terms of the Mozilla Public\n"
    b" * License, v. 2.0. If a copy of the MPL was not distributed with this\n"
    b" * file, You can obtain one at https://mozilla.org/MPL/2.0/. */\n")
# This is an observed, closed exception, not a normalization fallback.
LF_CRLF_IDENTITY = {
    "id": REGISTRY + "#webview2-com-macros@0.8.1", "license": "MIT",
    "name": "webview2-com-macros", "version": "0.8.1", "source": REGISTRY,
    "crate_sha256": "67a921c1b6914c367b2b823cd4cde6f96beec77d30a939c8199bb377cf9b9b54",
    "repository": "https://github.com/wravery/webview2-rs",
    "commit": "dffa41a8a46d3f5565eefbff2de57d38d399f158",
    "package_path": "crates/callback-macros",
}
LF_CRLF_MEMBERS = {
    "README.md": {
        "git_bytes": 425, "git_sha256": "dcffb41c431986c4458c0204413c2bcec8ebf81a6a45774888a5e71d50c3417a",
        "git_oid": "2097555df5987d2347f4a4bded90004883203689",
        "bytes": 429, "sha256": "94f2342f8b689e65aed07e90f52acf08a5705b8aeae57c68db9ea51610d68cf2"},
    "src/lib.rs": {
        "git_bytes": 8178, "git_sha256": "0e04b45fa54d548e31b5dbfe295f2eac28ea308bf0339d2357f0c0be1b9daf36",
        "git_oid": "49ea6a8b1e58aa7c1136f113099e0f3e9ef1fbcb",
        "bytes": 8428, "sha256": "3e0fb6feaaea5811a304205b8eaba424a5763947143fc27357f0106db3681ed3"},
}


class SupplementError(ValueError):
    """A static refusal code, without untrusted source text."""


@dataclass(frozen=True)
class VerifiedSupplement:
    source_proof: dict
    notices: tuple[dict, ...]
    texts: dict[str, bytes]


def _need(condition, code):
    if not condition:
        raise SupplementError("cargo_supplement_" + code)


def _object(value, keys, code="record_invalid"):
    _need(isinstance(value, dict) and set(value) == set(keys), code)
    return value


def _integer(value, maximum, minimum=0):
    return type(value) is int and minimum <= value <= maximum


def _path(value):
    _need(isinstance(value, str) and 0 < len(value) <= 512, "path_invalid")
    parts = value.split("/")
    _need(len(parts) <= 32 and all(
        PART.fullmatch(part) and part not in {".", ".."} and not part.endswith(".")
        and part.split(".", 1)[0].upper() not in RESERVED for part in parts), "path_invalid")
    return tuple(parts)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _oid(kind, raw):
    return hashlib.sha1(kind.encode("ascii") + b" " + str(len(raw)).encode("ascii")
                        + b"\0" + raw, usedforsecurity=False).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _json(raw):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)


def _toml(raw):
    _need(len(raw) <= MAX_BLOB, "manifest_budget")
    return tomllib.loads(raw.decode("utf-8"))


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, getattr(info, "st_file_attributes", 0))


def _redirect(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


class _Reader:
    """Same retained-descriptor boundary as source_lock_admission, with empty blobs allowed."""
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.total = 0

    def inspect(self, parts):
        path, observations = self.root, []
        for index, part in enumerate((None, *parts)):
            if part is not None:
                path = path / part
            info = path.lstat()
            leaf = index == len(parts)
            _need(not _redirect(info), "file_redirect")
            _need(stat.S_ISREG(info.st_mode) if leaf else stat.S_ISDIR(info.st_mode),
                  "file_not_regular")
            if leaf:
                _need(info.st_nlink == 1, "file_alias")
            observations.append(_identity(info))
        return path, observations

    def read(self, relative, maximum):
        path, before = self.inspect(_path(relative))
        _need(_integer(before[-1][4], maximum), "file_budget")
        self.total += before[-1][4]
        _need(self.total <= MAX_PROOF, "proof_budget")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            first = os.fstat(stream.fileno())
            opened = _identity(first)
            wanted = before[-1]
            if os.name == "nt":
                # Path creation time and descriptor change time are different domains.
                opened, wanted = opened[:6] + opened[7:], wanted[:6] + wanted[7:]
            _need(not _redirect(first) and stat.S_ISREG(first.st_mode)
                  and opened == wanted, "file_changed")
            raw = stream.read(maximum + 1)
            last = os.fstat(stream.fileno())
        _, after = self.inspect(_path(relative))
        _need(before == after and _identity(first) == _identity(last)
              and len(raw) == first.st_size and len(raw) <= maximum, "file_changed")
        return raw

    def reference(self, row, maximum):
        _object(row, {"path", "bytes", "sha256"}, "reference_invalid")
        _need(_integer(row["bytes"], maximum) and isinstance(row["sha256"], str)
              and HEX64.fullmatch(row["sha256"]), "reference_invalid")
        raw = self.read(row["path"], maximum)
        _need(len(raw) == row["bytes"] and _sha(raw) == row["sha256"], "reference_changed")
        return raw


class _Git:
    def __init__(self, reader, records, commit):
        _need(isinstance(records, list) and 1 <= len(records) <= MAX_OBJECTS, "object_budget")
        self.reader, self.records, self.cache, self.trees = reader, {}, {}, {}
        paths = {"record.json"}
        for row in records:
            _object(row, {"type", "oid", "path", "bytes", "sha256"}, "object_invalid")
            _need(row["type"] in {"commit", "tree", "blob"}
                  and isinstance(row["oid"], str) and HEX40.fullmatch(row["oid"])
                  and row["oid"] not in self.records, "object_invalid")
            path = "/".join(_path(row["path"])).casefold()
            _need(path.startswith("objects/") and path not in paths, "object_path_alias")
            paths.add(path)
            self.records[row["oid"]] = row
        for oid, row in self.records.items():
            self.get(oid, row["type"])
        raw = self.get(commit, "commit")
        _need(b"\0" not in raw and b"\n\n" in raw, "commit_malformed")
        headers = raw.split(b"\n\n", 1)[0].split(b"\n")
        roots = [line for line in headers if line.startswith(b"tree ")]
        _need(len(roots) == 1 and headers[0] == roots[0], "commit_tree_ambiguous")
        self.root = roots[0][5:].decode("ascii")
        _need(HEX40.fullmatch(self.root) is not None, "commit_tree_invalid")

    def get(self, oid, kind):
        key = (oid, kind)
        if key not in self.cache:
            row = self.records.get(oid)
            _need(row is not None and row["type"] == kind, "object_missing_or_wrong_type")
            maximum = MAX_TREE if kind == "tree" else MAX_BLOB if kind == "blob" else 256 * 1024
            data = self.reader.reference({field: row[field] for field in ("path", "bytes", "sha256")},
                                         maximum)
            _need(_oid(kind, data) == oid, "object_oid_mismatch")
            self.cache[key] = data
        return self.cache[key]

    def tree(self, oid):
        if oid not in self.trees:
            raw, offset, result, aliases, previous = self.get(oid, "tree"), 0, {}, set(), None
            while offset < len(raw):
                _need(len(result) < MAX_MEMBERS, "tree_entry_budget")
                space = raw.find(b" ", offset)
                nul = raw.find(b"\0", space + 1) if space >= 0 else -1
                _need(space > offset and nul > space + 1 and nul + 21 <= len(raw), "tree_malformed")
                mode, name = raw[offset:space], raw[space + 1:nul]
                _need(mode in {b"40000", b"100644", b"100755", b"120000", b"160000"}
                      and b"/" not in name and name not in {b".", b".."}, "tree_malformed")
                decoded = name.decode("utf-8")
                alias = decoded.casefold()
                sort_key = name + (b"/" if mode == b"40000" else b"")
                _need(decoded not in result and alias not in aliases
                      and (previous is None or previous < sort_key), "tree_alias_or_order")
                result[decoded] = (mode, raw[nul + 1:nul + 21].hex())
                aliases.add(alias)
                previous, offset = sort_key, nul + 21
            _need(offset == len(raw), "tree_malformed")
            self.trees[oid] = result
        return self.trees[oid]

    def entry(self, relative):
        parts, current = _path(relative), self.root
        for index, part in enumerate(parts):
            entry = self.tree(current).get(part)
            if entry is None:
                return None
            if index == len(parts) - 1:
                return entry
            _need(entry[0] == b"40000", "git_path_redirect")
            current = entry[1]
        raise SupplementError("cargo_supplement_path_invalid")

    def blob_id(self, relative):
        entry = self.entry(relative)
        _need(entry is not None and entry[0] in {b"100644", b"100755"}, "git_blob_path_invalid")
        return entry[1]

    def blob(self, relative):
        return self.get(self.blob_id(relative), "blob")


def _lf_to_crlf(original, packaged):
    _need(b"\r" not in original and b"\0" not in original, "transform_source_invalid")
    original.decode("utf-8")
    _need(original.replace(b"\n", b"\r\n") == packaged, "transform_not_exact")


def _transform(record, member, oid, original, packaged):
    identity = {key: record["component"][key] for key in ("id", "name", "version", "source", "license", "crate_sha256")}
    identity.update({key: record[key] for key in ("repository", "commit", "package_path")})
    _need(identity == LF_CRLF_IDENTITY, "transform_scope_unapproved")
    expected = LF_CRLF_MEMBERS.get(member["path"])
    _need(expected is not None and oid == expected["git_oid"]
          and len(original) == expected["git_bytes"] and _sha(original) == expected["git_sha256"]
          and len(packaged) == expected["bytes"] and _sha(packaged) == expected["sha256"],
          "transform_identity_mismatch")
    _lf_to_crlf(original, packaged)
    return {"transform": "LF_TO_CRLF", "git_bytes": len(original),
            "git_sha256": _sha(original), "git_blob_oid": oid}


def _packaged_lock(raw, component):
    lock = _toml(raw)
    _object(lock, {"version", "package"}, "packaged_lock_invalid")
    _need(type(lock["version"]) is int and lock["version"] in {3, 4}
          and isinstance(lock["package"], list) and 1 <= len(lock["package"]) <= 4096,
          "packaged_lock_invalid")
    own = 0
    for row in lock["package"]:
        _need(isinstance(row, dict) and set(row) <= {
            "name", "version", "source", "checksum", "dependencies", "replace"},
            "packaged_lock_invalid")
        for key in ("name", "version"):
            _need(isinstance(row.get(key), str) and 0 < len(row[key]) <= 128, "packaged_lock_invalid")
        if "source" in row:
            _need(isinstance(row["source"], str) and 0 < len(row["source"]) <= 8192, "packaged_lock_invalid")
        if "checksum" in row:
            _need(isinstance(row["checksum"], str) and HEX64.fullmatch(row["checksum"]), "packaged_lock_invalid")
        if "replace" in row:
            _need(isinstance(row["replace"], str) and 0 < len(row["replace"]) <= 1024, "packaged_lock_invalid")
        if "dependencies" in row:
            _need(isinstance(row["dependencies"], list) and len(row["dependencies"]) <= 4096
                  and all(isinstance(value, str) and 0 < len(value) <= 1024
                          for value in row["dependencies"]), "packaged_lock_invalid")
        if row["name"] == component["name"] and row["version"] == component["version"] and "source" not in row:
            own += 1
    _need(own == 1, "packaged_lock_component_mismatch")


def _workspace(git, package, package_path, declared_path):
    _need(declared_path is None or isinstance(declared_path, str), "workspace_invalid")
    explicit = package.get("workspace")
    if explicit is not None:
        _need(isinstance(explicit, str) and explicit and "\\" not in explicit
              and not explicit.startswith("/") and ":" not in explicit, "workspace_invalid")
        parts = list(_path(package_path))
        for part in explicit.split("/"):
            if part == "..":
                _need(bool(parts), "workspace_escape")
                parts.pop()
            elif part not in {"", "."}:
                _path(part)
                parts.append(part)
        selected = "/".join((*parts, "Cargo.toml"))
        _need(declared_path == selected, "workspace_mismatch")
    else:
        selected = None
        parts = _path(package_path)
        for depth in range(len(parts) - 1, -1, -1):
            candidate = "/".join((*parts[:depth], "Cargo.toml"))
            if git.entry(candidate) is not None:
                document = _toml(git.blob(candidate))
                if isinstance(document.get("workspace"), dict):
                    selected = candidate
                    break
        _need(selected == declared_path, "workspace_mismatch")
    _need(selected is not None, "workspace_missing")
    document = _toml(git.blob(selected))
    table = document.get("workspace", {}).get("package")
    _need(isinstance(table, dict), "workspace_package_missing")
    return table


def _notice_text(raw):
    _need(0 < len(raw) <= MAX_BLOB and b"\0" not in raw and raw.decode("utf-8-sig").strip(),
          "notice_text_invalid")


def verify_supplement(evidence_root: Path, *, expected_record_sha256: str,
                      component_identity: dict, crate_members: Mapping[str, bytes],
                      expected_crate_sha256: str) -> VerifiedSupplement:
    """Verify one tracked record.json against a caller-authenticated lazy crate mapping."""
    try:
        return _verify(evidence_root, expected_record_sha256, component_identity,
                       crate_members, expected_crate_sha256)
    except SupplementError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as error:
        raise SupplementError("cargo_supplement_input_unverified") from error


def _verify(root, expected_record_sha256, component, crate_members, expected_crate_sha256):
    _need(isinstance(expected_record_sha256, str) and HEX64.fullmatch(expected_record_sha256)
          and isinstance(expected_crate_sha256, str) and HEX64.fullmatch(expected_crate_sha256),
          "expected_digest_invalid")
    _object(component, {"id", "name", "version", "source", "license"}, "component_invalid")
    _need(component["source"] == REGISTRY and all(isinstance(component[key], str)
          and 0 < len(component[key]) <= (8192 if key == "id" else 4096)
          for key in ("id", "name", "version", "license")), "component_invalid")
    reader = _Reader(root)
    encoded = reader.read("record.json", MAX_RECORD)
    _need(_sha(encoded) == expected_record_sha256, "record_digest_mismatch")
    record = _json(encoded)
    _object(record, {"schema", "component", "repository", "commit", "package_path",
                     "workspace_manifest", "objects", "members", "notices"})
    _need(record["schema"] == "motionwright.cargo-notice-supplement/1", "schema_invalid")
    _need(record["component"] == {**component, "crate_sha256": expected_crate_sha256},
          "component_binding_mismatch")
    _need(record["repository"] in REPOSITORIES and isinstance(record["commit"], str)
          and HEX40.fullmatch(record["commit"]), "upstream_invalid")
    package_path = "/".join(_path(record["package_path"]))
    _need(isinstance(crate_members, Mapping), "crate_mapping_invalid")
    actual, aliases = set(), set()
    for name in crate_members:
        _need(len(actual) < MAX_MEMBERS, "member_budget")
        name = "/".join(_path(name))
        _need(name not in actual and name.casefold() not in aliases, "member_alias")
        actual.add(name); aliases.add(name.casefold())
    _need(isinstance(record["members"], list) and 1 <= len(record["members"]) <= MAX_MEMBERS,
          "member_budget")
    roster = {}
    for row in record["members"]:
        _object(row, {"path", "kind", "bytes", "sha256"}, "member_record_invalid")
        name = "/".join(_path(row["path"]))
        _need(name not in roster and _integer(row["bytes"], MAX_MEMBER)
              and isinstance(row["sha256"], str) and HEX64.fullmatch(row["sha256"]),
              "member_record_invalid")
        roster[name] = row
    _need(set(roster) == actual and {"Cargo.toml", "Cargo.toml.orig", ".cargo_vcs_info.json"} <= actual,
          "member_roster_mismatch")
    total, small = 0, {}
    def member(name):
        nonlocal total
        if name in small:
            return small[name]
        raw = crate_members[name]
        _need(isinstance(raw, bytes), "member_bytes_invalid")
        descriptor = roster[name]
        _need(len(raw) == descriptor["bytes"] and _sha(raw) == descriptor["sha256"], "member_bytes_changed")
        total += len(raw)
        _need(len(raw) <= MAX_MEMBER and total <= MAX_SOURCE, "source_budget")
        if name in {"Cargo.toml", "Cargo.toml.orig", ".cargo_vcs_info.json"}:
            small[name] = raw
        return raw
    normalized = _toml(member("Cargo.toml")).get("package")
    original = _toml(member("Cargo.toml.orig")).get("package")
    _need(isinstance(normalized, dict) and isinstance(original, dict), "package_manifest_invalid")
    vcs = _json(member(".cargo_vcs_info.json"))
    _object(vcs, {"git", "path_in_vcs"}, "vcs_invalid")
    _need(isinstance(vcs["git"], dict) and set(vcs["git"]) in ({"sha1"}, {"sha1", "dirty"})
          and vcs["git"].get("dirty", False) is False and vcs["git"]["sha1"] == record["commit"]
          and vcs["path_in_vcs"] == package_path, "vcs_mismatch_or_dirty")
    git = _Git(reader, record["objects"], record["commit"])
    _need(_oid("blob", member("Cargo.toml.orig")) == git.blob_id(package_path + "/Cargo.toml"),
          "original_manifest_git_mismatch")
    inherited = None
    for field, expected in (("name", component["name"]), ("version", component["version"]),
                            ("license", component["license"]), ("repository", record["repository"])):
        value = original.get(field)
        if isinstance(value, dict):
            _need(value == {"workspace": True} and field != "name", "inheritance_invalid")
            if inherited is None:
                inherited = _workspace(git, original, package_path, record["workspace_manifest"])
            value = inherited.get(field)
        _need(value == expected and normalized.get(field) == expected, "manifest_identity_mismatch")
    _need((inherited is not None) == (record["workspace_manifest"] is not None), "workspace_unused_or_missing")
    proof_members = []
    special = {"Cargo.toml": "cargo_normalized_manifest", "Cargo.toml.orig": "git_package_manifest",
               ".cargo_vcs_info.json": "cargo_vcs_metadata"}
    for name, descriptor in roster.items():
        raw = member(name)
        kind = descriptor["kind"]
        item = dict(descriptor)
        if name in special:
            _need(kind == special[name], "member_classification_invalid")
            if name == "Cargo.toml.orig":
                item.update(upstream_path=package_path + "/Cargo.toml", git_blob_oid=_oid("blob", raw))
        elif kind == "cargo_packaged_lock":
            _need(name == "Cargo.lock", "packaged_lock_path_invalid")
            _packaged_lock(raw, component)
            item["git_byte_match"] = False
        else:
            _need(kind in {"git_exact", "git_lf_to_crlf"}, "member_classification_invalid")
            upstream = package_path + "/" + name
            oid = git.blob_id(upstream)
            if kind == "git_exact":
                _need(_oid("blob", raw) == oid, "source_git_mismatch")
            else:
                item.update(_transform(record, descriptor, oid, git.get(oid, "blob"), raw))
            item.update(upstream_path=upstream, git_blob_oid=oid, git_byte_match=kind == "git_exact")
        proof_members.append(item)
    _need(isinstance(record["notices"], list) and 1 <= len(record["notices"]) <= MAX_NOTICES,
          "notice_budget")
    notices, texts, seen = [], {}, set()
    base = {"repository": record["repository"], "commit": record["commit"],
            "record_sha256": expected_record_sha256, "crate_sha256": expected_crate_sha256}
    def emit(raw, source_path, kind, provenance):
        _notice_text(raw)
        digest = _sha(raw)
        _need((kind, source_path) not in seen, "notice_duplicate")
        seen.add((kind, source_path))
        texts[digest] = raw
        _need(sum(len(value) for value in texts.values()) <= MAX_TEXT, "text_budget")
        notices.append({"source_path": source_path, "bytes": len(raw), "sha256": digest,
                        "source_kind": kind, "provenance": {**base, **provenance}})
    ancestors = {"/".join(_path(package_path)[:depth]) for depth in range(len(_path(package_path)) + 1)}
    for notice in record["notices"]:
        _need(isinstance(notice, dict), "notice_invalid")
        if notice.get("kind") == "upstream_git":
            _object(notice, {"kind", "upstream_path", "declared_by"}, "notice_invalid")
            upstream = "/".join(_path(notice["upstream_path"]))
            _need(upstream.rpartition("/")[0] in ancestors
                  and notice["declared_by"] == package_path + "/Cargo.toml", "notice_association_invalid")
            # declared_by identifies the manifest's license expression. Association
            # with this conventional notice path is reviewed in the record; it is
            # not a claim that the manifest explicitly named this notice file.
            _need(NOTICE_BASENAME.fullmatch(upstream.rsplit("/", 1)[-1]) is not None,
                  "notice_name_invalid")
            raw = git.blob(upstream)
            emit(raw, upstream, "supplemental_upstream_git", {
                "upstream_path": upstream, "git_blob_oid": git.blob_id(upstream),
                "declared_by": notice["declared_by"]})
        elif notice.get("kind") == "mozilla_mpl_2_0":
            _object(notice, {"kind", "header_member", "header_offset", "text", "receipt"}, "notice_invalid")
            _need(component["license"] == "MPL-2.0", "official_license_mismatch")
            name = "/".join(_path(notice["header_member"]))
            _need(name in roster and roster[name]["kind"] == "git_exact"
                  and _integer(notice["header_offset"], MAX_MEMBER), "official_header_invalid")
            offset = notice["header_offset"]
            raw_source = member(name)
            _need(raw_source[offset:offset + len(MPL_HEADER)] == MPL_HEADER, "official_header_mismatch")
            text = reader.reference(notice["text"], MAX_BLOB)
            receipt = _json(reader.reference(notice["receipt"], MAX_RECORD))
            _object(receipt, {"schema", "requested_url", "final_url", "http_status",
                              "content_type", "bytes", "sha256"}, "official_receipt_invalid")
            url = urlsplit(receipt["final_url"])
            _need(receipt["schema"] == "motionwright.official-license-acquisition/1"
                  and receipt["requested_url"] == MPL_TEXT_URL
                  and type(receipt["http_status"]) is int and receipt["http_status"] == 200
                  and receipt["final_url"] == MPL_TEXT_URL
                  and url.scheme == "https" and url.netloc in {"mozilla.org", "www.mozilla.org"}
                  and not url.query and not url.fragment
                  and url.path.startswith(("/MPL/2.0/", "/en-US/MPL/2.0/", "/media/MPL/2.0/"))
                  and isinstance(receipt["content_type"], str)
                  and receipt["content_type"].lower().split(";", 1)[0] == "text/plain"
                  and _integer(receipt["bytes"], MAX_BLOB, 1)
                  and receipt["bytes"] == len(text) and receipt["sha256"] == _sha(text),
                  "official_receipt_mismatch")
            upstream = package_path + "/" + name
            witness = {"upstream_path": upstream, "git_blob_oid": git.blob_id(upstream),
                       "header_offset": offset, "header_bytes": len(MPL_HEADER),
                       "header_sha256": _sha(MPL_HEADER)}
            emit(MPL_HEADER, upstream, "upstream_source_license_header", witness)
            emit(text, notice["text"]["path"], "official_license_text_referenced_by_source", {
                **witness, "source_reference_url": MPL_URL, "official_url": receipt["final_url"],
                "acquisition_receipt_sha256": notice["receipt"]["sha256"],
                "official_document_admission": "REVIEWED_RECORD_CONSISTENCY"})
        else:
            raise SupplementError("cargo_supplement_notice_kind_unsupported")
    return VerifiedSupplement({
        "schema": "motionwright.cargo-notice-source-proof/1",
        **base, "git_root_tree": git.root, "package_path": package_path,
        "all_archive_members_classified": True, "members": proof_members,
        "normalized_manifest_derivation": "NOT_PROVED_AS_GIT_BYTES",
        "redistribution_permission": "NOT_INFERRED", "full_package_inventory": "NOT_VERIFIED",
    }, tuple(notices), texts)
