"""Pin original Tauri AppImage packaging tools before bundle execution.

The three upstream releases may be replaced (especially continuous).
On the admitted Linux CI runner, install exact original SHA-256 bytes into a
fresh, private XDG cache before invoking Tauri. Tauri legitimately zeroes
linuxdeploy bytes [8:11] after prepare_tools; verify only that exact allowed
post-transform hash. No G2, G3, legal or release authorization is inferred.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import urllib.request

import candidate_package_inventory as inventory

SCHEMA = "motionwright.tauri-appimage-pinned-tool-cache/1"
MAX_ONE = 24 * 1024 * 1024
MAX_TOTAL = 48 * 1024 * 1024
MAX_REPORT = 64 * 1024
SHA = re.compile(r"[0-9a-f]{64}\Z")
ASSETS = (
    {
        "name": "AppRun-x86_64", "cache": "AppRun-x86_64",
        "asset_id": 274691722,
        "url": "https://github.com/tauri-apps/binary-releases/releases/download/apprun-old/AppRun-x86_64",
        "bytes": 31552,
        "sha256": "f30140a43a0a59e46db21bdefdf749b9e9f2c6946e92afabbacf98b8ae73fb4f",
    },
    {
        "name": "linuxdeploy-x86_64.AppImage",
        "cache": "linuxdeploy-07333c6-x86_64.AppImage",
        "asset_id": 576945544,
        "url": "https://github.com/tauri-apps/binary-releases/releases/download/linuxdeploy-07333c6/linuxdeploy-x86_64.AppImage",
        "bytes": 19810808,
        "sha256": "36a2d7e274d12e1050d0e9ecfe11d339ed54720b2bec464c286d53f8b07f5c62",
    },
    {
        "name": "linuxdeploy-plugin-appimage-x86_64.AppImage",
        "cache": "linuxdeploy-plugin-appimage.AppImage",
        "asset_id": 602435573,
        "url": "https://github.com/linuxdeploy/linuxdeploy-plugin-appimage/releases/download/continuous/linuxdeploy-plugin-appimage-x86_64.AppImage",
        "bytes": 16488952,
        "sha256": "49d6a17160675a6bd1781699aae6bdf7692d98552e02a3671d2183d10547842e",
    },
)


def need(ok, code):
    if not ok:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def identity(raw):
    return {"bytes": len(raw), "sha256": sha(raw)}


def raw_tool(cache_dir, name, limit=MAX_ONE):
    need(isinstance(name, str) and name.isascii() and name
         and "/" not in name and "\\" not in name
         and name not in (".", ".."), "tauri_cache_tool_name")
    root = Path(cache_dir).resolve(strict=True)
    path = root / name
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
         and info.st_size <= limit and info.st_size >= 0,
         "tauri_tool_regular_only")
    return inventory.capture.stable_read(root, path, limit)


def pinned_bytes(url, expected_bytes, expected_sha, *, opener=None):
    """Never execute network content; enforce a short, hard response budget."""
    need(isinstance(url, str) and url.startswith("https://github.com/")
         and type(expected_bytes) is int and 0 < expected_bytes < MAX_ONE
         and isinstance(expected_sha, str) and SHA.fullmatch(expected_sha),
         "tauri_tool_vendor_identity")
    if opener is None:
        opener = lambda remote: urllib.request.urlopen(
            urllib.request.Request(remote, headers={
                "User-Agent": "Motionwright-CI-Bounded-Tauri-Tool-Pin",
            }), timeout=60)
    data = bytearray()
    with opener(url) as response:
        while True:
            block = response.read(min(1024 * 1024, expected_bytes + 1 - len(data)))
            if not block:
                break
            data.extend(block)
            need(len(data) <= expected_bytes, "tauri_tool_download_size_mismatch")
    result = bytes(data)
    need(identity(result) == {"bytes": expected_bytes, "sha256": expected_sha},
         "tauri_tool_download_sha256_mismatch")
    return result


def stage(cache_home, *, assets=ASSETS, opener=None):
    """Install new vendor originals into a private empty XDG cache."""
    home = Path(cache_home).absolute()
    need(home.is_dir() and not home.is_symlink() and not list(home.iterdir()),
         "tauri_tool_cache_home_not_fresh")
    need(len(assets) == 3 and
         [row["name"] for row in assets] == [row["name"] for row in ASSETS],
         "tauri_tool_roster_not_closed")
    total = 0
    directory = home / "tauri"
    need(not os.path.lexists(directory), "tauri_tool_cache_replay")
    directory.mkdir(mode=0o700)
    rows = []
    for asset in assets:
        data = pinned_bytes(
            asset["url"], asset["bytes"], asset["sha256"], opener=opener)
        total += len(data)
        need(total <= MAX_TOTAL, "tauri_tool_download_total_budget")
        dst = directory / asset["cache"]
        need(not os.path.lexists(dst), "tauri_tool_preexisting")
        inventory.capture.write_new(dst, data)
        os.chmod(dst, 0o700)
        need(raw_tool(directory, asset["cache"]) == data,
             "tauri_tool_original_readback_changed")
        post = data
        if asset["name"] == "linuxdeploy-x86_64.AppImage":
            post = data[:8] + b"\x00\x00\x00" + data[11:]
        rows.append({
            "asset_id": asset["asset_id"], "name": asset["name"],
            "cache": asset["cache"], "url": asset["url"],
            "original": identity(data), "allowed_post_sha256": sha(post),
            "post_rule": ("EXACT_THREE_BYTE_LINUXDEPLOY_ZERO_PATCH"
                          if post != data else "UNCHANGED"),
        })
    return {
        "schema": SCHEMA,
        "status": "PINNED_VENDOR_TOOLS_STAGED_BEFORE_TAURI_BUNDLE",
        "tools": rows, "total_bytes": total,
        "claims": {
            "tools_exec_in_this_phase": False,
            "pre_exec_cache_files_exact": True,
            "bundler_operations_traced": False,
            "downstream_type2_runtime_download_pinned": False,
            "redistribution_permission": "NOT_INFERRED",
            "G2": "NOT_GRANTED", "G3": "STOP",
        },
    }


def verify_cached(cache_home, staged, *, post=False):
    need(staged["schema"] == SCHEMA and
         staged["status"] == "PINNED_VENDOR_TOOLS_STAGED_BEFORE_TAURI_BUNDLE"
         and len(staged["tools"]) == 3, "tauri_tool_staged_receipt")
    home = Path(cache_home).absolute()
    need(home.is_dir() and not home.is_symlink()
         and (home / "tauri").is_dir() and not (home / "tauri").is_symlink(),
         "tauri_cache_redirected")
    rows = []
    for original_asset, item in zip(ASSETS, staged["tools"]):
        need(item["name"] == original_asset["name"]
             and item["cache"] == original_asset["cache"]
             and item["asset_id"] == original_asset["asset_id"]
             and item["original"] == {
                 "bytes": original_asset["bytes"],
                 "sha256": original_asset["sha256"],
             }, "tauri_tool_source_roster_changed")
        raw = raw_tool(home / "tauri", item["cache"])
        found = identity(raw)
        expected = (item["allowed_post_sha256"] if post
                    else item["original"]["sha256"])
        need(found["bytes"] == original_asset["bytes"]
             and found["sha256"] == expected,
             "tauri_tool_cache_" + ("post" if post else "pre") + "_changed")
        rows.append({"name": item["name"], "cache": item["cache"],
                     "identity": found, "post": post, "rule": item["post_rule"]})
    return {
        "status": ("POST_BUNDLE_TOOLS_MATCH_PINNED_TRANSFORM" if post
                   else "PRE_BUNDLE_TOOLS_MATCH_PINNED_ORIGINALS"),
        "tools": rows,
        "claims": {
            "subprocess_tool_use_proven": False,
            "full_packager_transform_chain_proven": False,
            "rights_review": "NOT_PERFORMED", "G3": "STOP",
        },
    }
