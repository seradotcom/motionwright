"""Fail-closed seven Linux ELF-system copyright notice resources.

A prior same-run original AppImage inventory (44341371709/1, ac79a72d5)
proved seven manifest-linked copyright files were absent from the bundle.
Retain exact pinned bytes, Debian .list originals and installed package
versions before Tauri bundling, then require the actual extracted AppImage
to contain the staged notice bytes.

This establishes bytes and delivery ONLY: not a license-rights opinion, not
complete linker closure, and never formal G2/G3 acceptance.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat

import candidate_package_inventory as inventory
import linux_appimage_system_origins as origins

SCHEMA = "motionwright.linux-native-system-seven-notices/1"
BASE = Path("ci-evidence/linux-cargo")
ROOT = BASE / "resources/linux-native-notices"
RECEIPT = BASE / "linux-native-notices.original.json"
SHA = re.compile(r"[0-9a-f]{64}\Z")
SOURCE = re.compile(r"[0-9a-f]{40}\Z")
MAX_MANIFEST = 1024 * 1024
MAX_RECEIPT = 64 * 1024
MAX_NOTICE = 128 * 1024
SPEC = (
    ("dconf-gsettings-backend", 1912,
     "a9d427b6a6e5cb665da3b4115039750c9d1092f558d6dee391d20800fb5c850e"),
    ("glib-networking", 2305,
     "fddac026b2ccc0b629de7afd738f5e0d1fb538d4f43dc55edc6e70b8d24fd7d6"),
    ("libgdk-pixbuf-2.0-dev", 5506,
     "a0189e53d508d32aeec6462c4740013de70b40f06e507f5e700d3cceb6e8f178"),
    ("libglib2.0-dev", 54957,
     "b52574b109fb876dfa71dea7a98b8aee1bb583f6aa90c327d1e4dc0a1d59f211"),
    ("libpango1.0-dev", 20042,
     "10e2b3c2cd6c6ecd7c65ebb7319be463f514096a16e832441ee61dfef72a107f"),
    ("librsvg2-common", 20481,
     "2777f2b3753caf1e049a866d79a500467f2d0f066b576c1c754dd55cb38bb73b"),
    ("librsvg2-dev", 20481,
     "2777f2b3753caf1e049a866d79a500467f2d0f066b576c1c754dd55cb38bb73b"),
)


def need(ok, code):
    if not ok:
        raise ValueError(code)


def digest(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def validate_spec(spec):
    need(len(spec) == 7 and len({item[0] for item in spec}) == 7,
         "native_notice_seven_fixed_packages")
    for package, size, sha in spec:
        need(re.fullmatch(r"[a-z0-9][a-z0-9.+-]*", package)
             and type(size) is int and 0 < size < MAX_NOTICE
             and SHA.fullmatch(sha) is not None,
             "native_notice_pin_invalid")


def original_path(package):
    return "/usr/share/doc/" + package + "/copyright"


def system_manifest(info_root, package):
    """Return exact original dpkg .list. No glob, symlink or guessed owners."""
    root = Path(info_root).resolve(strict=True)
    path = root / (package + ":amd64.list")
    entry = path.lstat()
    need(stat.S_ISREG(entry.st_mode) and entry.st_nlink == 1
         and 0 < entry.st_size <= MAX_MANIFEST,
         "native_notice_dpkg_list_nonregular")
    raw = inventory.capture.stable_read(root, path, MAX_MANIFEST)
    need(original_path(package) in origins.list_paths(raw),
         "native_notice_not_in_original_package_manifest")
    return raw


def stage(repo, source, *, system_root=Path("/"), info_root=Path("/var/lib/dpkg/info"),
          installed=None, spec=SPEC):
    """Stage same-host source originals under a never-overwritten CI evidence tree."""
    validate_spec(spec)
    need(SOURCE.fullmatch(source) is not None, "native_notice_source_sha")
    repo = Path(repo).resolve(strict=True)
    source_root = Path(system_root).resolve(strict=True)
    installed = (origins.installed_versions(origins.captured_status()[0])
                 if installed is None else installed)
    need(isinstance(installed, dict), "native_notice_installed_not_observed")
    root = repo / ROOT
    need(root.parent.is_dir() and not os.path.lexists(root)
         and not os.path.lexists(repo / RECEIPT),
         "native_notice_original_staging_replay")
    root.mkdir(mode=0o700)
    rows = []
    total = 0
    for package, size, expected_sha in spec:
        name = package + ":amd64"
        need(name in installed and origins.VERSION.fullmatch(installed[name]),
             "native_notice_installed_package_missing")
        manifest_raw = system_manifest(info_root, package)
        data = origins.regular_host_bytes(
            source_root, original_path(package), MAX_NOTICE)
        need(data is not None and digest(data) ==
             {"bytes": size, "sha256": expected_sha},
             "native_notice_original_bytes_differ_from_reviewed_pin")
        total += len(data)
        need(total <= 256 * 1024, "native_notice_total_budget")
        directory = root / package
        directory.mkdir(mode=0o700)
        target = directory / "copyright"
        manifest_target = directory / "dpkg-list.original"
        inventory.capture.write_new(target, data)
        inventory.capture.write_new(manifest_target, manifest_raw)
        need(inventory.capture.stable_read(repo, target, MAX_NOTICE) == data
             and inventory.capture.stable_read(repo, manifest_target, MAX_MANIFEST)
                 == manifest_raw
             and origins.regular_host_bytes(
                 source_root, original_path(package), MAX_NOTICE) == data,
             "native_notice_staged_or_host_original_changed")
        rows.append({
            "package": name, "version": installed[name],
            "host_path": original_path(package),
            "original": digest(data), "manifest_original": digest(manifest_raw),
            "staged_source": target.as_posix(),
            "staged_manifest": manifest_target.relative_to(repo).as_posix(),
            "destination": "licenses/linux-system-notices/" + package + "/copyright",
            "extracted_appimage_member":
                "usr/lib/Motionwright/licenses/linux-system-notices/" + package +
                "/copyright",
            "redistribution_license_review": "NOT_PERFORMED",
        })
    receipt = {
        "schema": SCHEMA, "source": source, "notices": rows,
        "status": "SEVEN_PINNED_INSTALLED_PACKAGE_NOTICES_STAGED",
        "total_bytes": total,
        "claims": {
            "package_origin_manifest_verified": True,
            "same_run_host_bytes_pinned": True,
            "legal_review": "NOT_PERFORMED",
            "native_linker_closure": "NOT_VERIFIED",
            "G2": "NOT_GRANTED", "G3": "STOP",
            "release_authorized": False,
        },
    }
    raw = inventory.capture.encoded(receipt)
    need(len(raw) <= MAX_RECEIPT, "native_notice_receipt_budget")
    inventory.capture.write_new(repo / RECEIPT, raw)
    need(inventory.capture.stable_read(repo, repo / RECEIPT, MAX_RECEIPT) == raw,
         "native_notice_receipt_original_changed")
    return receipt


def mapping(repo, source, *, spec=SPEC):
    """Rehash all staged source notices before Tauri build or receipt audit."""
    validate_spec(spec)
    repo = Path(repo).resolve(strict=True)
    path = repo / RECEIPT
    raw = inventory.capture.stable_read(repo, path, MAX_RECEIPT)
    record = inventory.capture.parse_json(raw)
    need(record["schema"] == SCHEMA and record["source"] == source
         and record["status"] == "SEVEN_PINNED_INSTALLED_PACKAGE_NOTICES_STAGED"
         and len(record["notices"]) == 7 and
         [r["package"] for r in record["notices"]] ==
         [p + ":amd64" for p, _, _ in spec]
         and record["claims"]["G3"] == "STOP"
         and record["claims"]["legal_review"] == "NOT_PERFORMED"
         and record["claims"]["release_authorized"] is False,
         "native_notice_staging_receipt_changed")
    result = {}
    for (pkg, size, expected_sha), row in zip(spec, record["notices"]):
        target = repo / ROOT / pkg / "copyright"
        manifest_path = repo / ROOT / pkg / "dpkg-list.original"
        need(row["original"] == {"bytes": size, "sha256": expected_sha}
             and row["host_path"] == original_path(pkg)
             and row["staged_source"] == target.as_posix()
             and row["destination"] ==
                "licenses/linux-system-notices/" + pkg + "/copyright"
             and row["extracted_appimage_member"] ==
                "usr/lib/Motionwright/licenses/linux-system-notices/" +
                pkg + "/copyright"
             and row["staged_manifest"] == manifest_path.relative_to(repo).as_posix(),
             "native_notice_staged_mapping_changed")
        data = inventory.capture.stable_read(repo, target, MAX_NOTICE)
        manifest = inventory.capture.stable_read(repo, manifest_path, MAX_MANIFEST)
        need(digest(data) == row["original"]
             and digest(manifest) == row["manifest_original"]
             and original_path(pkg) in origins.list_paths(manifest),
             "native_notice_original_staged_bytes_changed")
        result[target.as_posix()] = row["destination"]
    return result


def verify_extracted(original_inventory, receipt, *, kind="appimage", spec=SPEC):
    """Read an original DEB or AppImage member inventory; never execute code."""
    validate_spec(spec)
    inventory.validate(original_inventory)
    need(kind in ("appimage", "deb")
         and original_inventory["context"]["kind"] == kind
         and original_inventory["context"]["source"] == receipt["source"]
         and original_inventory["custody"]["status"] == "DIRECT_CAPTURED_EXTRACTION"
         and len(original_inventory["roots"]) == 1
         and original_inventory["roots"][0]["role"] == "payload"
         and receipt["claims"]["G3"] == "STOP"
         and receipt["claims"]["legal_review"] == "NOT_PERFORMED",
         "native_notice_extracted_original_context")
    members = {item["path"]: item for item in
               original_inventory["roots"][0]["members"]}
    need(len(members) == len(original_inventory["roots"][0]["members"]),
         "native_notice_extracted_duplicate_member")
    results = []
    for pkg, size, expected_sha in spec:
        row = next((r for r in receipt["notices"]
                    if r["package"] == pkg + ":amd64"), None)
        need(row is not None, "native_notice_receipt_package_missing")
        member = members.get(row["extracted_appimage_member"])
        need(member is not None and member["kind"] == "file"
             and {"bytes": member["bytes"], "sha256": member["sha256"]} ==
             {"bytes": size, "sha256": expected_sha},
             "native_notice_extracted_member_not_original")
        results.append({"package": pkg, "member": row["extracted_appimage_member"],
                        "sha256": member["sha256"], "status": "EXACT_BUNDLED_NOTICE_BYTES"})
    return {
        "schema": "motionwright.linux-seven-bundled-native-notices/1",
        "source": receipt["source"], "kind": kind,
        "count": len(results), "notices": results,
        "status": "SEVEN_ORIGINAL_NOTICES_EXTRACTED_BYTES_VERIFIED",
        "G2": "NOT_GRANTED", "G3": "STOP",
        "legal_review": "NOT_PERFORMED",
        "release_authorized": False,
    }


def main(argv=None):
    """Inspect *original* extracted payload inventory, never execute a binary."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--appimage-inventory", type=Path, required=True)
    parser.add_argument("--deb-inventory", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    need(sys.platform == "linux" and
         os.environ.get("GITHUB_ACTIONS") == "true"
         and os.environ.get("GITHUB_SHA") == args.expected_source
         and os.environ.get("GITHUB_RUN_ATTEMPT") == "1",
         "native_notice_runner_context")
    repo = Path(__file__).resolve().parents[1]
    need(Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True) == repo,
         "native_notice_workspace")
    mapping(repo, args.expected_source)  # Rehash every sealed staged original.
    receipt_raw = inventory.capture.stable_read(repo, repo / RECEIPT, MAX_RECEIPT)
    receipt = inventory.capture.parse_json(receipt_raw)
    original_inventories, originals = {}, {}
    for kind, source_path in (
        ("deb", args.deb_inventory.absolute()),
        ("appimage", args.appimage_inventory.absolute()),
    ):
        need(source_path.is_relative_to(repo),
             "native_notice_image_original_outside_repo")
        raw = inventory.capture.stable_read(repo, source_path, inventory.MAX_REPORT)
        original = inventory.capture.parse_json(raw)
        need(original["context"]["source"] == args.expected_source
             and str(original["context"]["run_id"]) == os.environ["GITHUB_RUN_ID"]
             and original["context"]["run_attempt"] == 1
             and original["context"]["kind"] == kind,
             "native_notice_image_run_not_current")
        original_inventories[kind] = original
        originals[kind] = digest(raw)
    app = original_inventories["appimage"]
    deb = original_inventories["deb"]
    for key in ("source_tree", "candidate_receipt", "cargo_index",
                "artifact_identities_sha256", "generated_metadata_sha256"):
        need(app["context"][key] == deb["context"][key],
             "native_notice_cross_bundle_source:" + key)
    app_result = verify_extracted(app, receipt, kind="appimage")
    deb_result = verify_extracted(deb, receipt, kind="deb")
    need(app_result["notices"] == deb_result["notices"],
         "native_notice_cross_bundle_members_not_identical")
    result = {
        "schema": "motionwright.linux-seven-original-notices-pair/1",
        "source": args.expected_source,
        "run_id": app["context"]["run_id"], "run_attempt": 1,
        "appimage": app_result, "deb": deb_result,
        "original_inventories": originals,
        "seven_original_notice_receipt": digest(receipt_raw),
        "status": "SEVEN_ORIGINAL_NOTICES_IN_BOTH_LINUX_BUNDLES",
        "G2": "NOT_GRANTED", "G3": "STOP",
        "legal_review": "NOT_PERFORMED", "release_authorized": False,
    }
    output = inventory.capture.encoded(result)
    need(len(output) <= MAX_RECEIPT, "native_notice_extracted_report_budget")
    path = args.output.absolute()
    need(path.is_relative_to(repo) and not os.path.lexists(path)
         and path.parent.is_dir(), "native_notice_report_replay_or_redirected")
    inventory.capture.write_new(path, output)
    need(inventory.capture.stable_read(repo, path, MAX_RECEIPT) == output,
         "native_notice_extracted_report_readback")
    print("linux-seven-original-native-notices=PASS count=7 G2=NOT_GRANTED G3=STOP")


if __name__ == "__main__":
    main()
