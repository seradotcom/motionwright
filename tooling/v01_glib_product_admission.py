#!/usr/bin/env python3
"""Fail-closed, offline v0.1 admission of the exact patched glib desktop source.

This proves only source custody/lock selection. It does NOT prove installed
Linux binary linkage, exploitability absence, or release authorization.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tomllib

from v01_glib_backport_integrity import (
    UPSTREAM_SHA256, BackportError, read_upstream, verify_vendor,
)

SCHEMA = "motionwright.v01-glib-desktop-source-admission/1"
VENDOR = "vendor/glib-0.18.5"
UPSTREAM = "tooling/upstream-crates/glib-0.18.5.crate"


class ProductBackportError(ValueError):
    pass


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise ProductBackportError(reason)


def admit_patched_glib(root: Path) -> dict:
    root = root.resolve(strict=True)
    manifest = tomllib.loads((root / "Cargo.toml").read_text(encoding="utf-8"))
    patch = manifest.get("patch", {})
    require(isinstance(patch, dict) and
            isinstance(patch.get("crates-io"), dict) and
            patch["crates-io"] in (
                {"glib": {"path": VENDOR}},
                {"glib": {"path": VENDOR},
                 "tauri": {"path": "vendor/tauri-2.12.1"}},
            ),
            "unexpected_cargo_source_override")
    lock = tomllib.loads((root / "Cargo.lock").read_text(encoding="utf-8"))
    packages = lock.get("package")
    require(isinstance(packages, list), "missing_cargo_lock_packages")
    rows = [pkg for pkg in packages
            if pkg.get("name") == "glib"]
    require(len(rows) == 1 and rows[0].get("version") == "0.18.5",
            "glib_locked_version_or_identity_changed")
    require("source" not in rows[0] and "checksum" not in rows[0],
            "glib_lock_did_not_select_local_patch")
    vendor_root = root / VENDOR
    require(vendor_root.is_dir() and not vendor_root.is_symlink(),
            "glib_vendor_root_not_regular")
    vendor_manifest = tomllib.loads(
        (vendor_root / "Cargo.toml").read_text(encoding="utf-8")
    )
    require(vendor_manifest.get("package", {}).get("name") == "glib"
            and vendor_manifest["package"].get("version") == "0.18.5",
            "glib_vendor_manifest_identity_changed")
    try:
        original = read_upstream(root / UPSTREAM)
        custody = verify_vendor(original, vendor_root)
    except (BackportError, OSError, UnicodeError, ValueError) as exc:
        raise ProductBackportError("glib_upstream_or_patch_source_not_verified") from exc
    require(custody["source_crate_sha256"] == UPSTREAM_SHA256
            and custody["vendor_file_count"] == 121
            and custody["only_two_mutability_edits"] is True,
            "glib_patch_custody_is_incomplete")
    return {
        "schema": SCHEMA,
        "crate": "glib",
        "selected_version": "0.18.5",
        "source_mode": "local-path-exact-two-line-upstream-backport",
        "upstream_sha256": UPSTREAM_SHA256,
        "upstream_file_count": custody["vendor_file_count"],
        "patched_file": custody["patched_file"],
        "original_file_sha256": custody["upstream_patched_file_sha256"],
        "patched_file_sha256": custody["candidate_patched_file_sha256"],
        "local_cargo_lock_selected": True,
        "all_other_files_identical_to_upstream": True,
        "native_linux_binary_linkage_verified": False,
        "real_linux_installed_gui_verified": False,
        "upstream_advisory_open": True,
        "release_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = admit_patched_glib(args.root)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, sort_keys=True, indent=2)
            stream.write("\n")
    print(
        "v01-glib-patched-source=VERIFIED lock=LOCAL_PATCH other_files=EXACT "
        "linux_binary=NOT_VERIFIED rustsec=OPEN release=BLOCKED"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
