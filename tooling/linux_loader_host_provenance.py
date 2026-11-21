#!/usr/bin/env python3
"""Bind original Linux AppImage live host loader mappings to bounded dpkg originals.

This is a same-run host file/manifest observation after the installed window
closed, NOT a complete dlopen trace, mapped-memory byte hash, package
redistribution license review, or a G2/G3 grant.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import sys

import candidate_package_inventory as inventory
import linux_appimage_system_origins as origin

SCHEMA = "motionwright.linux-installed-loader-host-origins/1"
WINDOW_MAX = 128 * 1024
MAX_HOST_FILES = 128
MAX_MAPPED_FILES = 512
MAX_HOST_READ_TOTAL = 512 * 1024 * 1024
# Large host-side LLVM shared objects exceed the generic member cap (64 MiB).
# This bound is scoped only to live, uniquely-owned host loader libraries;
# the independent 512 MiB total and original AppImage member caps remain.
MAX_HOST_FILE_READ = 256 * 1024 * 1024
DEVICE = re.compile(r"[0-9a-fA-F]+:[0-9a-fA-F]+\Z")


def need(condition, code):
    if not condition:
        raise ValueError(code)


def host_path(path):
    return (isinstance(path, str) and len(path) <= 2048
            and path.startswith(("/usr/lib/", "/lib/"))
            and not path.startswith("//") and "\x00" not in path
            and all(p not in ("", ".", "..") for p in path.split("/")[1:]))


def host_maps(window, source):
    """Validate original first and reopened window sources, no synthetic success."""
    need(type(window) is dict and window.get("schema") ==
         "motionwright.ci.installed-cold-relaunch/2", "loader_original_schema")
    need(window.get("motionwright_sha") == source and
         isinstance(source, str) and re.fullmatch("[0-9a-f]{40}", source),
         "loader_original_source")
    need(window.get("status") == "WINDOW_OBSERVED" and
         window.get("launch_count") == 2 and
         window.get("persistence", {}).get("status") == "PASS",
         "loader_original_window_and_store")
    sets = []
    for arm in ("first_launch", "reopened_launch"):
        observed = window.get(arm)
        need(isinstance(observed, dict) and observed.get("status") == "WINDOW_OBSERVED",
             "loader_original_launch_missing")
        loader = observed.get("loader_observation")
        need(isinstance(loader, dict) and loader.get("schema") ==
             "motionwright.linux-installed-loader-path-snapshot/1" and
             loader.get("claims", {}).get("G3") == "STOP",
             "loader_original_scope")
        if loader.get("status") != "LIVE_EXECUTABLE_MAPPINGS_OBSERVED":
            return None
        need(type(loader.get("process_pid")) is int and loader["process_pid"] > 1
             and type(loader.get("process_starttime_ticks")) is int
             and loader["process_starttime_ticks"] > 0, "loader_original_process")
        rows = loader.get("mapped_files")
        need(isinstance(rows, list) and len(rows) <= MAX_MAPPED_FILES,
             "loader_original_roster_budget")
        captured = {}
        for row in rows:
            need(type(row) is dict and
                 row.get("origin_class") in ("BUNDLED", "HOST_SYSTEM"),
                 "loader_original_mapping_type")
            if row["origin_class"] != "HOST_SYSTEM":
                continue
            path = row.get("path")
            device = row.get("mapped_device")
            inode = row.get("mapped_inode")
            need(host_path(path) and isinstance(device, str) and DEVICE.fullmatch(device)
                 and type(inode) is int and inode > 0,
                 "loader_original_host_identity")
            key = (device.lower(), inode)
            need(path not in captured or captured[path] == key,
                 "loader_original_conflicting_host_map")
            captured[path] = key
        need(len(captured) <= MAX_HOST_FILES, "loader_original_host_budget")
        sets.append(captured)
    return sets


def same_host_file(root, path, mapping):
    """Confirm current regular inode/device equals the earlier kernel maps."""
    if not host_path(path):
        return False
    full = Path(root) / path.lstrip("/")
    try:
        meta = full.lstat()
        if not stat.S_ISREG(meta.st_mode) or meta.st_size <= 0:
            return False
        if not hasattr(os, "major") or not hasattr(os, "minor"):
            return False
        major, minor = (int(part, 16) for part in mapping[0].split(":"))
        return (os.major(meta.st_dev) == major and os.minor(meta.st_dev) == minor
                and meta.st_ino == mapping[1])
    except (OSError, ValueError, OverflowError):
        return False


def audit_original(window, source, installed, manifests, root, *,
                   host_budget=MAX_HOST_READ_TOTAL, identity_reader=same_host_file):
    """Observe only uniquely manifest-declared, unchanged host file originals."""
    need(type(host_budget) is int and 0 <= host_budget <= MAX_HOST_READ_TOTAL,
         "loader_host_read_budget")
    scans = host_maps(window, source)
    report = {
        "schema": SCHEMA, "source": source, "G3": "STOP", "G2": "NOT_GRANTED",
        "status": "NOT_OBSERVED", "matches": [], "unresolved": [],
        "host_file_read_limit_bytes": MAX_HOST_FILE_READ,
        "original_scope": {
            "only_live_host_library_paths": True,
            "mapped_bytes_equal_rehashed_disk_bytes": "NOT_ATTESTED",
            "all_dynamic_loader_events": "NOT_OBSERVED",
            "host_package_ownership": "SCOPED_ORIGINAL_MANIFEST_OBSERVATION",
            "native_license_review": "NOT_PERFORMED",
            "redistribution_permission": "NOT_INFERRED",
            "human_acceptance": False, "release_authorized": False,
        },
    }
    if scans is None:
        report["reason"] = "LOADER_SNAPSHOT_NOT_OBSERVED_IN_BOTH_LAUNCHES"
        return report, {}
    first, second = scans
    paths = set(first) | set(second)
    declared = origin.candidate_index(manifests, installed, paths)
    spent = 0
    for path in sorted(paths):
        before, after = first.get(path), second.get(path)
        reason = None
        if before is None or after is None:
            reason = "NOT_IN_BOTH_LAUNCHES"
        elif before != after:
            reason = "MAPPED_INODE_OR_DEVICE_CHANGED_BETWEEN_LAUNCHES"
        elif not identity_reader(root, path, before):
            reason = "MAPPED_DISK_IDENTITY_NOT_STABLE"
        else:
            owners = declared.get(path, [])
            if len(owners) != 1:
                reason = ("MULTIPLE_DECLARED_MANIFESTS" if owners
                          else "NO_EXACT_OBSERVED_MANIFEST")
            elif spent >= host_budget:
                reason = "HOST_FILE_READ_BUDGET"
            else:
                observed = origin.regular_hash(
                    root, path, min(MAX_HOST_FILE_READ, host_budget - spent))
                if not observed or observed.get("bytes", 0) <= 0:
                    reason = "REGULAR_HOST_BYTES_NOT_OBSERVED"
                elif not identity_reader(root, path, before):
                    reason = "MAPPED_DISK_IDENTITY_CHANGED_DURING_READ"
                else:
                    spent += observed["bytes"]
                    report["matches"].append({
                        "path": path, "mapped_device": before[0],
                        "mapped_inode": before[1], "host_file": observed,
                        "origin": owners[0],
                        "status": "SAME_INODE_AND_DECLARED_HOST_FILE_BYTES_OBSERVED",
                    })
        if reason is not None:
            report["unresolved"].append({"path": path, "reason": reason})
    report["status"] = "SCOPED_HOST_ORIGIN_OBSERVATION"
    report["mapped_host_paths"] = len(paths)
    report["exact_host_manifest_matches"] = len(report["matches"])
    report["unresolved_count"] = len(report["unresolved"])
    report["host_bytes_read"] = spent
    packages, originals = origin.original_package_records(
        report["matches"], manifests, Path(root))
    report["matched_package_originals"] = packages
    report["original_package_folder"] = "loader-sources"
    return report, originals


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--window-original", type=Path, required=True)
    p.add_argument("--expected-source", required=True)
    p.add_argument("--output-directory", type=Path, required=True)
    args = p.parse_args(argv)
    need(sys.platform == "linux", "loader_linux_runner_required")
    import ci_linux_dependency_capture as gate
    source = gate.linux_source(os.environ)
    need(source == args.expected_source, "loader_ci_source")
    raw = inventory.capture.stable_read(
        Path(args.window_original.anchor), args.window_original.absolute(), WINDOW_MAX)
    window = inventory.capture.parse_json(raw)
    installed_raw, installed_receipt = origin.captured_status()
    installed = origin.installed_versions(installed_raw)
    manifests, skipped = origin.captured_manifests("/var/lib/dpkg/info", installed)
    report, originals = audit_original(window, source, installed, manifests, Path("/"))
    report.update({
        "original_window": origin.digest(raw),
        "installed_status": installed_receipt,
        "installed_manifest_count": len(manifests),
        "catalog_complete": not skipped,
        "unobserved_manifests": skipped,
    })
    target = args.output_directory.absolute()
    need(target.parent.is_dir() and not os.path.lexists(target),
         "loader_output_must_be_new")
    output = inventory.capture.encoded(report)
    need(len(output) <= origin.MAX_REPORT, "loader_report_budget")
    target.mkdir(mode=0o700)
    (target / "loader-sources/package-lists").mkdir(parents=True, mode=0o700)
    (target / "loader-sources/copyright").mkdir(parents=True, mode=0o700)
    inventory.capture.write_new(target / "status.original", installed_raw)
    for name, content in sorted(originals.items()):
        limit = origin.MAX_MANIFEST if name.startswith("package-lists/") else origin.MAX_COPYRIGHT
        inventory.capture.write_new(target / "loader-sources" / name, content)
        need(inventory.capture.stable_read(
            target, "loader-sources/" + name, limit) == content,
            "loader_manifest_original_readback")
    inventory.capture.write_new(target / "report.json", output)
    need(inventory.capture.stable_read(target, "report.json", origin.MAX_REPORT) == output,
         "loader_report_original_readback")
    print("linux-loader-host-origins=" + report["status"] +
          " matches=" + str(len(report["matches"])) + " G3=STOP")


if __name__ == "__main__":
    main()
