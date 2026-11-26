#!/usr/bin/env python3
"""Describe an owner-prepared offline runtime folder without executing its files.

The folder must already contain tools/node and the complete runtime/ closure.
Creates runtime-profile.json exclusively; never downloads, changes a lock,
installs dependencies, starts a provider or edits source/runtime files.
"""
from __future__ import annotations
import argparse
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

PIN = "c2c40c74e5714a3e680415d0dddcf27743f0f0fa"
SCHEMA = "motionwright.runtime-profile-bundle/1"
MANIFEST = "runtime-profile.json"
MAX_FILE = 512 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_JSON = 8 * 1024 * 1024
MAX_FILES = 30_000
MAX_DIRS = 8_192
MC_PINS = {
    "runtime/render.mjs": "2352639a228212ac19459761cbc4914c02946199dc3e875fc329b68ccfb30b2e",
    "runtime/package-lock.json": "47a141493beca9d80bbf50bb8729e46233048fad490bd5f8f92e965ec228d3a2",
    "runtime/package.json": "d9e8d22fa1b45a3c4caee639d6f1d2f5dca08eafa2c26cbe7fee493a643a4394",
}
HF_RUNNER = "e2352fe31555f06f8a423e1cad4d84e8ff196f9707d04961ab476855d080b7b0"
HF_METADATA = "d3f1dd614e50191bd08391108fb17e521aded5c9a9aa0f30947672f8667ea673"
HF_INTEGRITY = "sha512-rBShZnCVW5xLe+6VViIv+SEAjYQ1Z9z8mt5M+XTCHoRj/eGRg5Z/yyvVNsWZZeuipoba5yhNfodOnwn94n4PPw=="
HF_URL = "https://registry.npmjs.org/hyperframes/-/hyperframes-0.8.181.tgz"


def require(value, message):
    if not value:
        raise ValueError(message)


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON field")
        result[key] = value
    return result


def parse_json(raw):
    require(0 < len(raw) <= MAX_JSON, "JSON exceeds its bounded size")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates,
                      parse_constant=lambda _: require(False, "nonfinite JSON value"))


def relative(path):
    require(isinstance(path, str) and 0 < len(path) <= 1024 and path != MANIFEST, "invalid package path")
    parts = path.split("/")
    require(len(parts) <= 16, "package nesting exceeds its limit")
    for part in parts:
        lower = part.lower()
        require(re.fullmatch(r"[A-Za-z0-9._@+-]{1,255}", part) and not part.endswith("."), "unsafe package component")
        require(lower not in {".", "..", ".git", ".env", ".npmrc", ".netrc", "credentials", "secrets"}
                and not lower.endswith((".session", ".key")), "private material cannot enter a runtime package")
        require(not re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])", lower.split(".")[0]), "reserved device path")


def identity(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_mode)


def safe_file(value):
    require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1 and value.st_uid == os.geteuid()
            and not value.st_mode & 0o022 and value.st_size <= MAX_FILE, "unsafe file identity or permissions")



_DARWIN = None


def host_platform():
    if sys.platform == "linux" and os.uname().machine == "x86_64":
        return "linux-x86_64"
    if sys.platform == "darwin" and os.uname().machine == "arm64":
        return "macos-aarch64"
    raise ValueError("this host has no admitted offline runtime profile")


def firefox_suffix(platform):
    return "/firefox/Nightly.app/Contents/MacOS/firefox" if platform == "macos-aarch64" else "/firefox/firefox"


def native_executable(header, platform):
    if platform == "linux-x86_64":
        return len(header) >= 20 and header[:6] == b"\x7fELF\x02\x01" and header[18:20] == b"\x3e\0"
    if platform == "macos-aarch64":
        return (len(header) >= 32 and header[:4] == b"\xcf\xfa\xed\xfe"
                and header[4:8] == b"\x0c\0\0\x01" and header[8:12] == b"\0" * 4
                and header[12:16] == b"\x02\0\0\0")
    return False


def security_metadata(fd):
    """Read retained Darwin security metadata; never remove an ACL or xattr."""
    if sys.platform != "darwin":
        return
    global _DARWIN
    if _DARWIN is None:
        library = ctypes.CDLL(None, use_errno=True)
        library.flistxattr.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int)
        library.flistxattr.restype = ctypes.c_ssize_t
        library.acl_get_fd_np.argtypes = (ctypes.c_int, ctypes.c_int)
        library.acl_get_fd_np.restype = ctypes.c_void_p
        library.acl_valid.argtypes = (ctypes.c_void_p,)
        library.acl_valid.restype = ctypes.c_int
        library.acl_get_entry.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
        library.acl_get_entry.restype = ctypes.c_int
        library.acl_free.argtypes = (ctypes.c_void_p,)
        library.acl_free.restype = ctypes.c_int
        _DARWIN = library
    refused = "macOS extended permissions or attributes cannot be preserved by this copier; original security metadata was not removed"
    require(_DARWIN.flistxattr(fd, None, 0, 0) == 0, refused)
    ctypes.set_errno(0)
    acl = _DARWIN.acl_get_fd_np(fd, 0x100)  # Apple ACL_TYPE_EXTENDED, sys/acl.h.
    if not acl:
        # filesec_get_property reports ENOENT when the retained file has no ACL.
        require(ctypes.get_errno() == errno.ENOENT, refused)
        return
    try:
        require(_DARWIN.acl_valid(acl) == 0, refused)
        entry = ctypes.c_void_p()
        found = _DARWIN.acl_get_entry(acl, 0, ctypes.byref(entry))
        require(found == -1 and ctypes.get_errno() == errno.EINVAL, refused)
    finally:
        require(_DARWIN.acl_free(acl) == 0, refused)


def create_description(folder: Path, profile: str):
    platform = host_platform()
    require(profile in ("motion_canvas", "hyperframes"), "unsupported runtime profile")
    require(platform != "macos-aarch64" or profile == "motion_canvas", "only Motion Canvas is admitted on macOS Apple Silicon")
    require(folder.is_absolute() and str(folder) == str(folder.resolve(strict=True)), "select an absolute canonical package folder")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    root = os.open("/", flags | os.O_DIRECTORY)
    try:
        for part in folder.parts[1:]:
            require(part not in (".", ".."), "unnormalized package folder")
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=root)
            os.close(root)
            root = child
        security_metadata(root)
        initial = os.fstat(root)
        require(initial.st_uid == os.geteuid() and not initial.st_mode & 0o022, "package folder is not owner controlled")
        rows, small, headers, directories = [], {}, {}, set()
        total = 0

        def walk(directory, prefix):
            nonlocal total
            security_metadata(directory)
            names = sorted(os.listdir(directory))
            require(0 < len(names) <= MAX_FILES + MAX_DIRS, "empty or oversized runtime directory")
            for name in names:
                path = prefix + name
                relative(path)
                require(path == "tools" or path == "runtime" or path == "tools/node" or path.startswith("runtime/"), "only tools/node and runtime/ belong in this package")
                observed = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISDIR(observed.st_mode):
                    require(observed.st_uid == os.geteuid() and not observed.st_mode & 0o022, "unsafe runtime directory")
                    directories.add(path)
                    require(len(directories) <= MAX_DIRS, "too many runtime directories")
                    child = os.open(name, flags | os.O_DIRECTORY, dir_fd=directory)
                    try:
                        require((observed.st_dev, observed.st_ino) == (os.fstat(child).st_dev, os.fstat(child).st_ino), "directory changed")
                        walk(child, path + "/")
                        security_metadata(child)
                    finally:
                        os.close(child)
                    continue
                safe_file(observed)
                require(len(rows) < MAX_FILES and total + observed.st_size <= MAX_TOTAL, "package byte/file budget exceeded")
                fd = os.open(name, flags, dir_fd=directory)
                try:
                    security_metadata(fd)
                    before = os.fstat(fd)
                    safe_file(before)
                    require(identity(observed) == identity(before), "file changed before reading")
                    digest, count, head, captured = hashlib.sha256(), 0, bytearray(), bytearray()
                    capture = path in {"runtime/runtime-lock.json", "runtime/package-lock.json", "runtime/node_modules/hyperframes/package.json"}
                    if capture:
                        require(before.st_size <= MAX_JSON, "runtime metadata exceeds its limit")
                    while True:
                        chunk = os.read(fd, 64 * 1024)
                        if not chunk:
                            break
                        count += len(chunk)
                        require(count <= before.st_size, "file grew while reading")
                        digest.update(chunk)
                        if len(head) < 32:
                            head.extend(chunk[:32-len(head)])
                        if capture:
                            captured.extend(chunk)
                    security_metadata(fd)
                    require(count == before.st_size and identity(os.fstat(fd)) == identity(before)
                            and identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) == identity(before), "file changed while reading")
                    rows.append({"path": path, "sha256": digest.hexdigest(), "bytes": count,
                                 "executable": bool(before.st_mode & 0o111), "role": "dependency"})
                    headers[path] = bytes(head)
                    if capture:
                        small[path] = bytes(captured)
                    total += count
                finally:
                    os.close(fd)

        walk(root, "")
        by_path = {row["path"]: row for row in rows}
        require(len({name.lower() for name in by_path}) == len(by_path), "case-colliding file names")
        require(len({name.lower() for name in directories}) == len(directories), "case-colliding directory names")
        require(not {name.lower() for name in directories} & {name.lower() for name in by_path}, "file/directory collision")
        if profile == "motion_canvas":
            for name, digest in MC_PINS.items():
                require(by_path.get(name, {}).get("sha256") == digest, "Motion Canvas source or lock differs from the supported pin")
            entry, lock_path, version = "runtime/render.mjs", "runtime/package-lock.json", "3.17.2"
            browser_pattern = r"runtime/node_modules/playwright-core/\.local-browsers/firefox-[0-9]+" + re.escape(firefox_suffix(platform))
            browsers = [name for name in by_path if re.fullmatch(browser_pattern, name)]
            require(len(browsers) == 1, "one contained Playwright Firefox executable is required")
            browser = browsers[0]
        else:
            entry, lock_path, version = "runtime/semwright-hyperframes-runner.mjs", "runtime/runtime-lock.json", "0.8.181"
            lock = parse_json(small[lock_path])
            keys = {"schema", "hyperframes_version", "hyperframes_commit", "node_version", "node_sha256", "browser_version", "ffmpeg_version", "ffprobe_version", "files"}
            require(set(lock) == keys and lock["schema"] == "semwright.hyperframes.runtime-lock/1"
                    and lock["hyperframes_version"] == version and lock["hyperframes_commit"] == "ae02e5aceff4742e5d72c20f0859eb3e6b449035"
                    and lock["node_version"] == "v22.22.0" and lock["node_sha256"] == by_path["tools/node"]["sha256"], "HyperFrames lock identity mismatch")
            expected = {name.removeprefix("runtime/"): row for name, row in by_path.items() if name.startswith("runtime/") and name != lock_path}
            require(isinstance(lock["files"], list) and len(lock["files"]) == len(expected), "HyperFrames lock roster mismatch")
            roles, seen = {}, set()
            for row in lock["files"]:
                require(set(row) == {"path", "sha256", "bytes", "role"} and row["path"] not in seen, "HyperFrames lock file shape")
                seen.add(row["path"])
                actual = expected[row["path"]]
                require(row["bytes"] == actual["bytes"] > 0 and row["sha256"] == actual["sha256"], "HyperFrames lock file changed")
                if row["role"] != "dependency":
                    require(row["role"] not in roles, "duplicate HyperFrames role")
                    roles[row["role"]] = row["path"]
            require(set(roles) == {"runner", "hyperframes-cli", "package-lock", "browser", "ffmpeg", "ffprobe"}, "HyperFrames runtime roles mismatch")
            require(roles["runner"] == entry.removeprefix("runtime/") and roles["hyperframes-cli"] == "node_modules/hyperframes/bin/hyperframes.mjs"
                    and roles["package-lock"] == "package-lock.json" and by_path[entry]["sha256"] == HF_RUNNER, "HyperFrames entry mismatch")
            metadata = by_path["runtime/origin/hyperframes-0.8.181.json"]
            require(metadata["sha256"] == HF_METADATA and metadata["bytes"] == 3553, "original registry metadata mismatch")
            package = parse_json(small["runtime/node_modules/hyperframes/package.json"])
            npm = parse_json(small["runtime/package-lock.json"])["packages"]["node_modules/hyperframes"]
            require(package["name"] == "hyperframes" and package["version"] == version and npm["version"] == version
                    and npm["resolved"] == HF_URL and npm["integrity"] == HF_INTEGRITY, "HyperFrames package distribution mismatch")
            for role in ("browser", "ffmpeg", "ffprobe"):
                require(by_path["runtime/" + roles[role]]["executable"], "HyperFrames tool is not executable")
                text = lock[role + "_version"]
                require(isinstance(text, str) and 0 < len(text) <= 512 and not any(ord(c) < 32 or ord(c) == 127 for c in text), "invalid declared tool version")
            browser = "runtime/" + roles["browser"]
        role_paths = {"tools/node": "node", entry: "runtime_entry", lock_path: "runtime_lock", browser: "browser"}
        for path, role in role_paths.items():
            row = by_path[path]
            require(row["bytes"] > 0, "required file is empty")
            row["role"] = role
            row["executable"] = role in {"node", "browser"}
            if row["executable"]:
                header = headers[path]
                require(native_executable(header, platform), "required executable does not match the admitted host architecture")
        result = {"schema": SCHEMA, "profile": profile, "version": version, "platform": platform, "semwright_source_pin": PIN,
                  "node_version": "22.22.0", "files": sorted(rows, key=lambda row: row["path"])}
        raw = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
        require(len(raw) <= MAX_JSON, "runtime manifest exceeds its limit")
        security_metadata(root)
        observed = os.stat(folder, follow_symlinks=False)
        require((observed.st_dev, observed.st_ino) == (initial.st_dev, initial.st_ino), "package root changed")
        output = os.open(MANIFEST, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=root)
        try:
            security_metadata(output)
            with os.fdopen(output, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(output)
            security_metadata(output)
        finally:
            os.close(output)
        os.fsync(root)
        return {"profile": profile, "manifest_sha256": hashlib.sha256(raw).hexdigest(), "files": len(rows) + 1,
                "total_bytes": total + len(raw), "executed": False, "downloaded": False, "locks_modified": False}
    finally:
        os.close(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True, help="existing owner-prepared package folder containing tools/node and runtime/")
    parser.add_argument("--profile", choices=("motion_canvas", "hyperframes"), required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(create_description(args.folder, args.profile), sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Runtime package description refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
