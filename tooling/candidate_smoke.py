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
from pathlib import Path
import plistlib
import re
import struct
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


def verify_linux_desktop_entry(path: Path, executable: Path) -> dict:
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
        extra = verify_linux_desktop_entry(linux_desktop_entry, installed_binary)
    elif platform == "macos":
        require(mac_info_plist is not None, "macOS package requires Info.plist inspection")
        extra = verify_macos_bundle(mac_info_plist, installed_binary)
    if linux_gui_probe is not None:
        require(kind == "appimage" and linux_gui_probe.get("status") == "WINDOW_OBSERVED"
                and linux_gui_probe.get("window_title") == "Motionwright",
                "Candidate GUI probe does not prove a visible Motionwright window")
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
