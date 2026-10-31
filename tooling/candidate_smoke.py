#!/usr/bin/env python3
"""Source-bound automated candidate *package inspection*, not human acceptance.

Consumes the exact GitHub candidate receipt and re-verifies installer SHA-256
before inspecting the actual binary extracted or installed in a disposable OS
runner. Never claims platform signing, notarization or a human install PASS.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import struct
import stat
import tomllib

ROOT = Path(__file__).resolve().parents[1]
METHODS = {
    "appimage": ("linux", "appimage_extract"),
    "deb": ("linux", "deb_extract"),
    "nsis": ("windows", "nsis_silent_current_user"),
    "dmg": ("macos", "dmg_readonly_mount_copy"),
}
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")


class CandidateError(ValueError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise CandidateError(reason)


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as data:
        for chunk in iter(lambda: data.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def regular(path: Path) -> Path:
    require(path.is_file() and not path.is_symlink(),
            "Candidate inspection requires an existing regular non-symlink file")
    return path



# Only the already declared product notices and the retained GLib backport.
# A complete inventory of the remaining transitive dependencies is separate.
NOTICE_FILES = (
    ("LICENSE", "licenses/motionwright/LICENSE"),
    ("THIRD_PARTY_NOTICES.md", "licenses/THIRD_PARTY_NOTICES.md"),
    ("vendor/glib-0.18.5/LICENSE", "licenses/glib-0.18.5/LICENSE"),
    ("vendor/glib-0.18.5/COPYRIGHT", "licenses/glib-0.18.5/COPYRIGHT"),
)
MAX_NOTICE_BYTES = 128 * 1024


def notice_resource_directory(binary: Path, kind: str) -> Path:
    """Use the installed resource layout of the pinned Tauri bundler."""
    if kind in ("deb", "appimage"):
        require(binary.parent.name == "bin" and binary.parent.parent.name == "usr",
                "candidate_notice_layout_invalid")
        return binary.parent.parent / "lib" / "Motionwright"
    if kind == "nsis":
        return binary.parent
    if kind == "dmg":
        require(binary.parent.name == "MacOS"
                and binary.parent.parent.name == "Contents",
                "candidate_notice_layout_invalid")
        return binary.parent.parent / "Resources"
    raise CandidateError("candidate_notice_kind_invalid")


def _notice_bytes(anchor: Path, relative: Path) -> bytes:
    """Inspect bounded resources below one physical, explicitly supplied anchor.

    The caller resolves the package/source anchor once, allowing locator aliases
    such as macOS /var. Relative package components must remain regular and
    unlinked. This is controlled package inspection, not an atomic filesystem
    snapshot against a concurrent writer.
    """
    require(anchor.is_absolute() and not relative.is_absolute()
            and not relative.drive and bool(relative.parts)
            and all(part not in ("", ".", "..") for part in relative.parts),
            "candidate_notice_relative_path_invalid")
    try:
        path = anchor
        for index, part in enumerate(relative.parts):
            path = path / part
            info = path.lstat()
            require(not stat.S_ISLNK(info.st_mode)
                    and not (getattr(info, "st_file_attributes", 0)
                             & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)),
                    "candidate_notice_link_refused")
            if index < len(relative.parts) - 1:
                require(stat.S_ISDIR(info.st_mode), "candidate_notice_not_directory")
        require(stat.S_ISREG(info.st_mode), "candidate_notice_not_regular")
        require(0 < info.st_size <= MAX_NOTICE_BYTES, "candidate_notice_size_invalid")
        with path.open("rb") as stream:
            data = stream.read(MAX_NOTICE_BYTES + 1)
        require(len(data) == info.st_size and len(data) <= MAX_NOTICE_BYTES,
                "candidate_notice_size_changed")
        return data
    except OSError as error:
        raise CandidateError("candidate_notice_missing_or_unreadable") from error


def compare_notice_bytes(expected: bytes, packaged: bytes) -> dict:
    """Compare content; a license name or source digest alone is insufficient."""
    require(0 < len(expected) <= MAX_NOTICE_BYTES
            and 0 < len(packaged) <= MAX_NOTICE_BYTES,
            "candidate_notice_size_invalid")
    expected_hash = hashlib.sha256(expected).hexdigest()
    packaged_hash = hashlib.sha256(packaged).hexdigest()
    require(len(expected) == len(packaged) and expected_hash == packaged_hash,
            "candidate_notice_bytes_changed")
    return {
        "source_bytes": len(expected), "packaged_bytes": len(packaged),
        "source_sha256": expected_hash, "packaged_sha256": packaged_hash,
    }


def verify_license_notices(*, installed_binary: Path, kind: str,
                           desktop: dict, root: Path = ROOT) -> dict:
    resources = desktop.get("bundle", {}).get("resources")
    require(isinstance(resources, dict)
            and all(resources.get("bundle-licenses/" + source.removeprefix("vendor/")) == target
                    for source, target in NOTICE_FILES),
            "candidate_notice_resource_mapping_invalid")
    resource_root = notice_resource_directory(installed_binary, kind)
    package_root = (installed_binary.parent if kind == "nsis"
                    else installed_binary.parents[2])
    resource_relative = resource_root.relative_to(package_root)
    try:
        source_anchor = root.resolve(strict=True)
        package_anchor = package_root.resolve(strict=True)
    except OSError as error:
        raise CandidateError("candidate_notice_missing_or_unreadable") from error
    records = []
    for source, target in NOTICE_FILES:
        expected = _notice_bytes(source_anchor, Path(source))
        packaged = _notice_bytes(package_anchor, resource_relative / target)
        records.append({
            "source_relative_path": source,
            "resource_relative_path": target,
            **compare_notice_bytes(expected, packaged),
        })
    return {
        "status": "DECLARED_NOTICES_PRESENT_BYTE_EXACT",
        "scope": "PRODUCT_LICENSE_AND_DECLARED_NOTICES_AND_VENDORED_GLIB",
        "digest_kind": "raw-bytes",
        "complete_dependency_inventory": "NOT_VERIFIED",
        "files": records,
    }


def verify_installer(receipt: dict, kind: str, artifact: Path) -> dict:
    require(isinstance(receipt, dict) and receipt.get("schema_version") == 1,
            "Candidate receipt schema is invalid")
    candidate = receipt.get("candidate")
    require(isinstance(candidate, dict), "Candidate source metadata is missing")
    platform, _ = METHODS[kind]
    require(candidate.get("application") == "Motionwright"
            and candidate.get("platform") == platform,
            "Candidate receipt platform or application does not match the installed probe")
    require(isinstance(candidate.get("source_revision"), str)
            and SHA40.fullmatch(candidate["source_revision"]) is not None,
            "Candidate receipt must include a concrete source SHA")
    lock = json.loads((ROOT / "SOURCE_LOCK.json").read_text(encoding="utf-8"))
    pinned = lock["dependencies"]["semwright"]["revision"]
    require(candidate.get("semwright_revision") == pinned,
            "Candidate receipt does not match the pinned Semwright revision")
    desktop = json.loads((ROOT / "apps/desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    workspace = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))
    require(candidate.get("version") == desktop["version"]
            and candidate.get("version") == workspace["workspace"]["package"]["version"],
            "Candidate receipt version differs from desktop/workspace source")
    require(desktop["identifier"] == "com.seradotcom.motionwright",
            "Desktop package identifier unexpectedly changed")
    claims = receipt.get("claims")
    require(isinstance(claims, dict)
            and claims.get("published") is False
            and claims.get("human_install_acceptance") == "NOT_RUN"
            and claims.get("distribution_signature") == "NOT_CLAIMED"
            and claims.get("notarization") == "NOT_CLAIMED",
            "Candidate receipt improperly claims publication or independent acceptance")
    arch = candidate.get("arch")
    require(arch in ("x86_64", "amd64", "aarch64", "arm64"),
            "Unexpected candidate architecture")
    records = receipt.get("artifacts")
    require(isinstance(records, list), "Candidate receipt artifacts are missing")
    matched = [record for record in records
               if isinstance(record, dict) and record.get("bundle_kind") == kind]
    require(len(matched) == 1, "Candidate receipt does not uniquely identify the requested bundle")
    record = matched[0]
    regular(artifact)
    require(artifact.name == record.get("filename") and artifact.stat().st_size == record.get("bytes"),
            "Candidate installer name or byte count differs from build receipt")
    digest = hash_file(artifact)
    require(isinstance(record.get("sha256"), str)
            and SHA64.fullmatch(record["sha256"]) is not None
            and digest == record["sha256"],
            "Candidate installer bytes changed since build receipt")
    return {"candidate": candidate, "record": record, "digest": digest, "desktop": desktop}


def binary_identity(binary: Path, platform: str, arch: str) -> dict:
    regular(binary)
    size = binary.stat().st_size
    require(size >= 128, "Candidate executable is empty or smaller than a valid header")
    with binary.open("rb") as stream:
        header = stream.read(128)
        if platform == "linux":
            require(header[:4] == b"\x7fELF" and header[4] == 2 and header[5] == 1,
                    "Linux installed binary is not 64-bit little-endian ELF")
            machine = struct.unpack_from("<H", header, 18)[0]
            require(machine == 62 and arch in ("x86_64", "amd64"),
                    "Linux installed executable machine differs from receipt")
            format_name = "ELF64_x86_64"
        elif platform == "windows":
            require(header[:2] == b"MZ", "Windows installed executable lacks MZ header")
            pe_offset = struct.unpack_from("<I", header, 0x3c)[0]
            require(64 <= pe_offset < min(size - 6, 8 * 1024 * 1024),
                    "Windows PE header offset is invalid")
            stream.seek(pe_offset)
            pe = stream.read(6)
            require(pe[:4] == b"PE\0\0" and struct.unpack_from("<H", pe, 4)[0] == 0x8664
                    and arch in ("x86_64", "amd64"),
                    "Windows installed executable is not x86_64 PE")
            format_name = "PE64_x86_64"
        elif platform == "macos":
            magic = header[:4]
            require(magic in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"),
                    "macOS installed executable is not a 64-bit Mach-O binary")
            endian = "<" if magic == b"\xcf\xfa\xed\xfe" else ">"
            cpu = struct.unpack_from(endian + "I", header, 4)[0]
            allowed = {"x86_64": 0x01000007, "arm64": 0x0100000c, "aarch64": 0x0100000c}
            require(cpu == allowed.get(arch), "macOS Mach-O CPU differs from candidate receipt")
            format_name = "MACHO64_" + arch
        else:
            raise CandidateError("Unsupported installed executable platform")
    return {
        "binary_filename": binary.name,
        "binary_bytes": size,
        "binary_sha256": hash_file(binary),
        "binary_format": format_name,
    }


def verify_linux_desktop_entry(
    path: Path, executable: Path, *, allow_internal_link: bool = False,
) -> dict:
    if path.is_symlink():
        require(allow_internal_link,
                "Debian desktop entry may not be a symlink")
        # Linuxdeploy/AppImage normally exports a top-level .desktop symlink
        # into its own usr/share/applications. Accept ONLY an internal
        # regular-file target, never an escape into the host filesystem.
        extracted_root = path.parent.resolve(strict=True)
        resolved = path.resolve(strict=True)
        require(resolved.is_relative_to(extracted_root) and resolved.is_file(),
                "AppImage desktop entry symlink escapes the extracted bundle")
    else:
        regular(path)
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    require(parser.has_section("Desktop Entry"), "Linux extracted candidate has no Desktop Entry")
    entry = parser["Desktop Entry"]
    require(entry.get("Name") == "Motionwright", "Linux candidate desktop name changed")
    launch = entry.get("Exec", "").split()[0].strip('"') if entry.get("Exec") else ""
    require(Path(launch).name == executable.name,
            "Desktop entry launches a different executable than the extracted candidate")
    require(entry.get("Type") == "Application", "Candidate desktop entry is not an application")
    return {"desktop_entry_filename": path.name, "desktop_exec": executable.name}


def verify_macos_bundle(path: Path, executable: Path) -> dict:
    regular(path)
    with path.open("rb") as stream:
        info = plistlib.load(stream)
    require(info.get("CFBundleIdentifier") == "com.seradotcom.motionwright",
            "macOS candidate bundle identifier mismatch")
    require(info.get("CFBundleExecutable") == executable.name,
            "macOS bundle executable disagrees with inspected binary")
    return {
        "bundle_info_filename": "Info.plist",
        "bundle_identifier": info["CFBundleIdentifier"],
        "bundle_executable": executable.name,
    }



# Budget for this installed-resource inspection, not a collector provenance claim.
MAX_CARGO_NOTICE_TOTAL_BYTES = 512 * 1024 * 1024


def _closed_cargo_directory(*, capture, anchor: Path,
                            relative: Path, expected: dict[str, bool]) -> None:
    directory = capture.admitted(anchor, relative, directory=True)
    seen = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            require(entry.name in expected and entry.name not in seen,
                    "candidate_cargo_notice_tree_extra")
            # Reject links/reparse points even if the target has the right bytes.
            capture.admitted(anchor, relative / entry.name,
                             directory=expected[entry.name])
            seen.add(entry.name)
    require(seen == set(expected), "candidate_cargo_notice_resource_missing")


def _verify_cargo_notice_subtree(*, capture, index_raw: bytes,
                                 anchor: Path, relative: Path) -> dict:
    # The external index digest and binary/source identity were verified first.
    index = capture.parse_json(index_raw)
    digests = set()
    for component in index["components"]:
        notices = component.get("notices")
        require(isinstance(notices, list), "candidate_cargo_notice_roster_invalid")
        for notice in notices:
            require(isinstance(notice, dict)
                    and isinstance(notice.get("sha256"), str)
                    and SHA64.fullmatch(notice["sha256"]) is not None,
                    "candidate_cargo_notice_roster_invalid")
            digests.add(notice["sha256"])
    expected_root = {"index.json": False}
    if digests:
        expected_root["texts"] = True
    expected_texts = {digest + ".txt": False for digest in digests}

    def inspect_names():
        _closed_cargo_directory(capture=capture, anchor=anchor,
                                relative=relative, expected=expected_root)
        if digests:
            _closed_cargo_directory(capture=capture, anchor=anchor,
                                    relative=relative / "texts", expected=expected_texts)

    inspect_names()
    total = 0
    for digest in sorted(digests):
        observed = capture.stable_hash(
            anchor, relative / "texts" / (digest + ".txt"),
            limit=MAX_CARGO_NOTICE_TOTAL_BYTES - total,
        )
        require(observed["sha256"] == digest, "candidate_cargo_notice_digest_mismatch")
        total += observed["bytes"]
    inspect_names()
    require(capture.stable_read(anchor, relative / "index.json", 16 * 1024 * 1024)
            == index_raw, "candidate_cargo_index_changed_during_inspection")
    return {"status": "DECLARED_CARGO_TEXTS_PRESENT_BYTE_EXACT",
            "scope": "CARGO_NOTICE_SUBTREE_ONLY", "subtree_closed": True,
            "unique_texts": len(digests), "total_bytes": total,
            "full_package_inventory": "NOT_VERIFIED"}


def verify_cargo_build_binding(*, candidate: dict, record: dict,
                               installed_binary: Path, kind: str) -> dict:
    """Join a declared candidate Cargo capture to the inspected executable."""
    if "cargo_build" not in record:
        return {"status": "NOT_VERIFIED", "reason": "candidate_receipt_has_no_cargo_capture"}
    binding = record["cargo_build"]
    require(isinstance(binding, dict)
            and set(binding) == {"index_sha256", "source_tree", "target",
                                 "root_package", "binary_target"},
            "candidate_cargo_binding_invalid")
    targets = {
        ("linux", "x86_64"): "x86_64-unknown-linux-gnu",
        ("linux", "amd64"): "x86_64-unknown-linux-gnu",
        ("windows", "x86_64"): "x86_64-pc-windows-msvc",
        ("windows", "amd64"): "x86_64-pc-windows-msvc",
        ("macos", "x86_64"): "x86_64-apple-darwin",
        ("macos", "arm64"): "aarch64-apple-darwin",
        ("macos", "aarch64"): "aarch64-apple-darwin",
    }
    target = targets.get((candidate["platform"], candidate["arch"]))
    require(target is not None and binding.get("target") == target
            and binding.get("root_package") == "motionwright-desktop"
            and binding.get("binary_target") == "motionwright-desktop",
            "candidate_cargo_target_mismatch")
    import importlib.util
    specification = importlib.util.spec_from_file_location(
        "motionwright_candidate_dependency_capture",
        ROOT / "scripts" / "candidate_dependency_capture.py",
    )
    require(specification is not None and specification.loader is not None,
            "candidate_cargo_verifier_unavailable")
    capture = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(capture)
    package_root = (installed_binary.parent if kind == "nsis"
                    else installed_binary.parents[2])
    resource_root = notice_resource_directory(installed_binary, kind)
    resource_relative = resource_root.relative_to(package_root)
    binary_relative = installed_binary.relative_to(package_root)
    try:
        anchor = package_root.resolve(strict=True)
        raw = capture.stable_read(
            anchor, resource_relative / "licenses/cargo/index.json", 16 * 1024 * 1024,
        )
        result = capture.verify_binary_binding(
            raw, expected_index_sha256=binding["index_sha256"],
            expected_source=candidate["source_revision"],
            expected_source_tree=binding["source_tree"], expected_target=target,
            expected_package="motionwright-desktop", expected_binary="motionwright-desktop",
            binary_root=anchor, binary_path=anchor / binary_relative,
        )
        result["notice_resources"] = _verify_cargo_notice_subtree(
            capture=capture, index_raw=raw, anchor=anchor,
            relative=resource_relative / "licenses/cargo",
        )
        return result
    except capture.CaptureError as error:
        raise CandidateError(str(error)) from error
    except CandidateError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise CandidateError("candidate_cargo_capture_missing_or_invalid") from error

def build_receipt(*, candidate_receipt: Path, artifact: Path,
                  installed_binary: Path, kind: str, method: str,
                  linux_desktop_entry: Path | None = None,
                  mac_info_plist: Path | None = None,
                  linux_gui_probe: dict | None = None) -> dict:
    require(kind in METHODS, "Unknown candidate bundle kind")
    platform, expected_method = METHODS[kind]
    require(method == expected_method, "Candidate extraction/install method does not match bundle")
    raw = json.loads(regular(candidate_receipt).read_text(encoding="utf-8"))
    verified = verify_installer(raw, kind, artifact)
    arch = verified["candidate"]["arch"]
    binary = binary_identity(installed_binary, platform, arch)
    extra = {}
    if platform == "linux":
        require(linux_desktop_entry is not None, "Linux package must expose a Desktop Entry")
        extra = verify_linux_desktop_entry(
            linux_desktop_entry, installed_binary,
            allow_internal_link=(kind == "appimage"),
        )
    elif platform == "macos":
        require(mac_info_plist is not None, "macOS package requires Info.plist inspection")
        extra = verify_macos_bundle(mac_info_plist, installed_binary)
    if linux_gui_probe is not None:
        require(kind == "appimage" and linux_gui_probe.get("status") == "WINDOW_OBSERVED"
                and linux_gui_probe.get("window_title") == "Motionwright",
                "Candidate GUI probe does not prove a visible Motionwright window")
    notices = verify_license_notices(
        installed_binary=installed_binary, kind=kind, desktop=verified["desktop"],
    )
    cargo_binding = verify_cargo_build_binding(
        candidate=verified["candidate"], record=verified["record"],
        installed_binary=installed_binary, kind=kind,
    )
    if cargo_binding.get("status") == "CAPTURED_ROOT_BINARY_BYTE_EXACT":
        require(
            binary["binary_bytes"] == cargo_binding["bytes"]
            and binary["binary_sha256"] == cargo_binding["sha256"],
            "candidate_binary_changed_between_inspections",
        )
    return {
        "schema_version": 1,
        "candidate": verified["candidate"],
        "bundle_kind": kind,
        "candidate_archive": {
            "filename": artifact.name,
            "bytes": verified["record"]["bytes"],
            "sha256": verified["digest"],
        },
        "automated_inspection": {
            "method": method,
            "status": "PASS",
            "binary": binary,
            "license_notices": notices,
            "cargo_build_binding": cargo_binding,
            **extra,
            "gui_window_probe": "WINDOW_OBSERVED" if linux_gui_probe else "NOT_RUN",
        },
        "claims": {
            "human_install_acceptance": "NOT_RUN",
            "runtime_renderer_acceptance": "SEPARATE_GATE",
            "signing": "NOT_CLAIMED",
            "notarization": "NOT_CLAIMED",
            "release_published": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-receipt", required=True, type=Path)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--installed-binary", required=True, type=Path)
    parser.add_argument("--kind", required=True, choices=sorted(METHODS))
    parser.add_argument("--method", required=True)
    parser.add_argument("--linux-desktop-entry", type=Path)
    parser.add_argument("--mac-info-plist", type=Path)
    parser.add_argument("--linux-gui-probe", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        probe = json.loads(regular(args.linux_gui_probe).read_text(encoding="utf-8")) if args.linux_gui_probe else None
        receipt = build_receipt(
            candidate_receipt=args.candidate_receipt,
            artifact=args.artifact,
            installed_binary=args.installed_binary,
            kind=args.kind,
            method=args.method,
            linux_desktop_entry=args.linux_desktop_entry,
            mac_info_plist=args.mac_info_plist,
            linux_gui_probe=probe,
        )
        require(SHA40.fullmatch(args.expected_source_sha) is not None
                and receipt["candidate"]["source_revision"] == args.expected_source_sha,
                "Installed package receipt does not belong to the exact GitHub tested source SHA")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as out:
            json.dump(receipt, out, sort_keys=True, indent=2)
            out.write("\n")
        print("candidate-smoke=PASS bundle=" + args.kind +
              " source=" + receipt["candidate"]["source_revision"] +
              " human_acceptance=NOT_RUN")
    except (CandidateError, OSError, ValueError, KeyError, struct.error) as error:
        parser.exit(1, "candidate-smoke: " + str(error) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
