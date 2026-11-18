"""Refuse unpinned AppRun.wrapped before exercising a Linux AppImage.

The fixed SHA-256 is from tauri-apps/binary-releases release apprun-old,
asset 274691722, AppRun-x86_64 (GitHub asset metadata digest as observed
2026-11-18). An exact byte match is not a license review, signing assertion,
or a trace proving which source file the bundler opened. G3 stays STOP.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re

import candidate_package_inventory as inventory

SCHEMA = "motionwright.linux-appimage-apprun-pinned-provider/1"
PROVIDER_RELEASE = 233774323
PROVIDER_ASSET = 274691722
PROVIDER_BYTES = 31552
PROVIDER_SHA256 = "f30140a43a0a59e46db21bdefdf749b9e9f2c6946e92afabbacf98b8ae73fb4f"
PROVIDER_URL = (
    "https://github.com/tauri-apps/binary-releases/"
    "releases/download/apprun-old/AppRun-x86_64"
)
MAX_APPRUN_READ = 64 * 1024
SOURCE_SHA = re.compile(r"[0-9a-f]{40}\Z")


def need(ok, code):
    if not ok:
        raise ValueError(code)


def identity(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def observe_original(app, payload_root, *, trusted_bytes=PROVIDER_BYTES,
                     trusted_sha256=PROVIDER_SHA256):
    """Observe an original source-bound AppImage member, no process execution."""
    need(isinstance(app, dict) and app["context"]["kind"] == "appimage"
         and app["context"]["platform"] == "linux"
         and app["context"]["arch"] == "x86_64"
         and app["custody"]["status"] == "DIRECT_CAPTURED_EXTRACTION",
         "apprun_original_context")
    need(type(trusted_bytes) is int and 0 < trusted_bytes <= MAX_APPRUN_READ
         and re.fullmatch(r"[0-9a-f]{64}", trusted_sha256) is not None,
         "apprun_trusted_pin_invalid")
    members = app["roots"][0]["members"]
    matches = [r for r in members if r["path"] == "AppRun.wrapped"]
    need(len(matches) == 1, "apprun_original_member_roster")
    member = matches[0]
    need(member["kind"] == "file" and member["native_format"] == "ELF"
         and {key: member[key] for key in ("bytes", "sha256")} ==
         {"bytes": trusted_bytes, "sha256": trusted_sha256},
         "apprun_original_differs_from_provider_asset")
    gaps = [g["members"] for g in app["gaps"]
            if g["code"] == "MEMBER_ORIGIN_UNRESOLVED"]
    need(len(gaps) == 1 and "payload:AppRun.wrapped" in gaps[0],
         "apprun_original_gap_changed")
    root = Path(payload_root).resolve(strict=True)
    raw = inventory.capture.stable_read(root, root / "AppRun.wrapped", MAX_APPRUN_READ)
    need(raw.startswith(b"\x7fELF")
         and identity(raw) == {"bytes": trusted_bytes, "sha256": trusted_sha256},
         "apprun_live_original_differs_from_provider_asset")
    return {"member": "AppRun.wrapped",
            "member_bytes": member["bytes"], "member_sha256": member["sha256"],
            "stable_live_read": "SAME_AS_ORIGINAL_CAPTURE",
            "status": "IDENTICAL_TO_PINNED_RELEASE_ASSET_BYTES"}


def report(app, payload_root, expected_source):
    need(SOURCE_SHA.fullmatch(expected_source) is not None
         and app["context"]["source"] == expected_source
         and app["context"]["run_attempt"] == 1,
         "apprun_original_source_or_attempt")
    need(app["claims"]["formal_g2"] == "NOT_GRANTED"
         and app["claims"]["formal_g3"] == "NOT_GRANTED"
         and app["claims"]["redistribution_permission"] == "NOT_INFERRED",
         "apprun_unearned_claim")
    observed = observe_original(app, payload_root)
    return {
        "schema": SCHEMA,
        "source": expected_source,
        "source_tree": app["context"]["source_tree"],
        "run_id": app["context"]["run_id"],
        "run_attempt": app["context"]["run_attempt"],
        "provider": {
            "repository": "tauri-apps/binary-releases",
            "tag": "apprun-old", "release_id": PROVIDER_RELEASE,
            "asset_id": PROVIDER_ASSET, "asset_name": "AppRun-x86_64",
            "url": PROVIDER_URL,
            "bytes": PROVIDER_BYTES,
            "sha256": PROVIDER_SHA256,
            "provider_asset_digest_pin_source": "original_github_asset_metadata_2026-11-18",
            "provider_metadata_requeried_during_CI": False,
        },
        "original": observed,
        "claims": {
            "release_asset_byte_identity": "EXACT",
            "provider_source_operations_traced": False,
            "bundler_download_verified_before_execution": False,
            "full_linker_dependency_graph": "NOT_VERIFIED",
            "native_license_review": "NOT_PERFORMED",
            "redistribution_permission": "NOT_INFERRED",
            "human_acceptance": False,
            "G2": "NOT_GRANTED", "G3": "STOP",
            "release_authorized": False,
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--appimage-inventory", type=Path, required=True)
    parser.add_argument("--producer-seal", type=Path, required=True)
    parser.add_argument("--live-payload", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    need(os.name == "posix" and os.environ.get("GITHUB_SHA") == args.expected_source,
         "apprun_linux_runner_source")
    workspace = Path(__file__).resolve().parents[1]
    need(Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True) == workspace
         and os.environ.get("GITHUB_RUN_ATTEMPT") == "1",
         "apprun_original_workspace_or_attempt")
    def source_raw(path, limit):
        path = path.absolute()
        need(path.is_relative_to(workspace), "apprun_source_not_in_checkout")
        return inventory.capture.stable_read(workspace, path, limit)
    seal = inventory.capture.parse_json(source_raw(args.producer_seal, 64 * 1024))
    inventory.file_identity(seal)
    raw = source_raw(args.appimage_inventory, inventory.MAX_REPORT)
    need(identity(raw) == {key: seal[key] for key in ("bytes", "sha256")},
         "apprun_original_inventory_seal_changed")
    app = inventory.capture.parse_json(raw)
    inventory.validate(app)
    need(str(app["context"]["run_id"]) == os.environ["GITHUB_RUN_ID"],
         "apprun_original_run_id")
    original = report(app, args.live_payload, args.expected_source)
    original["inventory_original"] = identity(raw)
    output = inventory.capture.encoded(original)
    need(len(output) < 64 * 1024, "apprun_original_report_budget")
    path = args.output.absolute()
    need(path.is_relative_to(workspace) and path.parent.is_dir()
         and not os.path.lexists(path), "apprun_result_path_preexisting")
    inventory.capture.write_new(path, output)
    need(source_raw(path, 64 * 1024) == output,
         "apprun_pin_report_readback_changed")
    print("apprun-original-provider-byte-pin=PASS G2=NOT_GRANTED G3=STOP")


if __name__ == "__main__":
    main()
