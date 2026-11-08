#!/usr/bin/env python3
"""Require the authenticated old runtime before issuing a resume recipient.

The old capsule retained a whole-package-tree digest, not a distribution lock
file. This guard requires that digest and the observed binary/wheel identities;
it does not infer missing package versions or accept a changed installation.
"""
from __future__ import annotations
import json
import os
from pathlib import Path

import ci_private_evidence as engine
from native_manim_camera_evidence import read, read_object, require, sha
from native_manim_resume_transfer import ORIGIN, hosted_context

ORIGINAL_INVENTORY_SHA = "164a555b48e02f38f13dfe450bb10cbccf8d31b9fd6887877a87a64ac27c9fb9"
ORIGINAL_CAPSULE_SHA = "aceef1c18997de00d9e30c64c74bd149bcec81f5c9da67f9ac9cc0322439618a"
ORIGINAL_PROJECTION_SHA = "5da62ab26082917b408f586be49aaee5dfc0bd136bfd4ec31c5f76b3983cb2d0"
EXPECTED = {
    "binaries": {
        "ffmpeg": {"bytes": 342488, "sha256": "ed16af623947494a72e284b6eb8ff225f2da22b38b5d5069c2fd4b4ba3384e41"},
        "ffprobe": {"bytes": 191888, "sha256": "272f6ebc634a63d9c8b4ca68e964119d980f25154e5aa2c35e5487da48e9a58f"},
        "python": {"bytes": 8020928, "sha256": "e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f"},
    },
    "binary_versions": {
        "ffmpeg": "ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023 the FFmpeg developers",
        "ffprobe": "ffprobe version 6.1.1-3ubuntu5 Copyright (c) 2007-2023 the FFmpeg developers",
        "python": "3.12.3 (main, Aug 31 2026, 10:18:26) [GCC 13.3.0]",
    },
    "files": 4889,
    "inventory_sha256": "63aedb5fc459804cdeeda686b83ed4bd384ba6cfdd54ddcfbb1fcfac12927a77",
    "libav_versions": {"libavcodec": [61, 19, 101], "libavdevice": [61, 3, 100],
                       "libavfilter": [10, 4, 100], "libavformat": [61, 7, 100],
                       "libavutil": [59, 39, 100], "libswresample": [5, 3, 100], "libswscale": [8, 3, 100]},
    "manim_version": "0.21.0", "pyav_version": "15.0.0",
    "platform": {"machine": "x86_64", "system": "Linux"},
    "pyav_native_files_sha256": "83aa70811396a258800c24a5bc972159fea8686778cbe70b96085d1687c1e303",
    "pyav_native_files_count": 88,
    "pyav_wheel": {"bytes": 40010257, "filename": "av-15.0.0-cp312-cp312-manylinux_2_28_x86_64.whl",
                   "sha256": "fc50a7d5f60109221ccf44f8fa4c56ce73f22948b7f19b1717fcc58f7fbc383e"},
    "runtime_receipt_sha256": "a4ec35e38f4ed253082b6dcd7899530b4494815202f1719296512f6c1754051b",
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def project(value):
    fields = ("manim_version", "pyav_version", "libav_versions", "platform",
              "runtime_receipt_sha256", "binary_versions", "pyav_wheel", "files", "inventory_sha256")
    result = {name: value[name] for name in fields}
    require(type(value.get("binaries")) is dict and set(value["binaries"]) == {"ffmpeg", "ffprobe", "python"},
            "resume_runtime_exact_binary_roster")
    result["binaries"] = {name: {key: row[key] for key in ("bytes", "sha256")}
                          for name, row in value["binaries"].items()}
    result["pyav_native_files"] = value["pyav_native_files"]
    require(type(result["pyav_native_files"]) is list and 0 < len(result["pyav_native_files"]) <= 512,
            "resume_runtime_native_roster_bound")
    return result


def compare(value, current):
    require(value.get("schema") == "motionwright.manim.runtime-inventory/2"
            and value.get("context") == current and value.get("native_render_performed") is False,
            "resume_runtime_measured_new_context")
    projection = project(value)
    compact = dict(projection)
    native = compact.pop("pyav_native_files")
    compact.update(pyav_native_files_count=len(native), pyav_native_files_sha256=sha(canonical(native)))
    differences = [name for name in EXPECTED if canonical(compact.get(name)) != canonical(EXPECTED[name])]
    return {"schema": "motionwright.manim.resume.runtime-match/1", "context": current,
            "origin_context": ORIGIN,
            "status": "SAME_ORIGINAL_RUNTIME" if not differences and sha(canonical(projection)) == ORIGINAL_PROJECTION_SHA
                      else "RUNTIME_CHANGED_BEFORE_PRIVATE_INPUTS",
            "original_inventory_sha256": ORIGINAL_INVENTORY_SHA,
            "original_ciphertext_sha256": ORIGINAL_CAPSULE_SHA,
            "expected_projection_sha256": ORIGINAL_PROJECTION_SHA,
            "measured_projection_sha256": sha(canonical(projection)),
            "measured": compact, "differences": differences,
            "original_distribution_versions_available": False,
            "inferred_package_versions": False, "native_effect_invocations": 0,
            "private_source_originals_opened": False, "automatic_retry": False}


def location():
    return Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True) / "verification/native-manim-e2e"


def check(current, root=None):
    root = location() if root is None else Path(root)
    inventory = read(root / "runtime-inventory.json", 2 * 1024 * 1024)
    raw = read(root / "resume-runtime-match.json", 128 * 1024)
    value = read_object(root / "resume-runtime-match.json")
    expected = compare(engine.strict(inventory), current)
    expected["runtime_inventory_sha256"] = sha(inventory)
    require(value == expected and value.get("status") == "SAME_ORIGINAL_RUNTIME"
            and value.get("differences") == [], "resume_same_original_runtime_required_before_key")
    return {"receipt_sha256": sha(raw), "runtime_inventory_sha256": sha(inventory),
            "original_inventory_sha256": ORIGINAL_INVENTORY_SHA,
            "projection_sha256": ORIGINAL_PROJECTION_SHA, "whole_package_tree_equal": True}


def main():
    current = hosted_context()
    root = location()
    raw = read(root / "runtime-inventory.json", 2 * 1024 * 1024)
    value = compare(engine.strict(raw), current)
    value["runtime_inventory_sha256"] = sha(raw)
    engine.new_json(root / "resume-runtime-match.json", value)
    require(value["status"] == "SAME_ORIGINAL_RUNTIME", "resume_runtime_changed_no_key_payload_or_native")
    check(current)
    print(json.dumps({"status": value["status"], "whole_package_tree_equal": True,
                      "native_effect_invocations": 0, "inferred_package_versions": False}))


if __name__ == "__main__":
    main()
