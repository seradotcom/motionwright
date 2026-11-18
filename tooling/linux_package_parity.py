"""Verify byte-for-byte common payload parity of one original Linux DEB + AppImage.

No package execution, archive extraction or origin/license promotion. Both
inventories were authenticated earlier by candidate_package_inventory.consume.
This joins already extracted and observed member identities, not raw archives.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import candidate_package_inventory as inventory

SCHEMA = "motionwright.linux-common-package-payload/1"
SHA1 = re.compile(r"[0-9a-f]{40}\Z")
CONTROL_FILES = frozenset({"DEBIAN/control", "DEBIAN/md5sums"})
CONTROL_NAMESPACE = CONTROL_FILES | {"DEBIAN"}


def require(value: bool, code: str):
    if not value:
        raise ValueError(code)


def member_identity(row):
    kind = row["kind"]
    if kind == "file":
        return {"kind": kind, "bytes": row["bytes"], "sha256": row["sha256"]}
    if kind == "symlink":
        return {"kind": kind, "target": row["target"]}
    require(kind == "directory", "linux_parity_unexpected_member_kind")
    return {"kind": "directory"}


def verify_common_payload(appimage, deb):
    """Return only a bounded, derived cross-package equivalence assertion.

    Both inputs must be *complete structurally valid inventory/1* reports.
    A common path with unequal bytes is fatal. No caller-supplied list can
    omit a DEB payload member or silently mark an extra control file safe.
    """
    inventory.validate(appimage)
    inventory.validate(deb)
    a, d = appimage["context"], deb["context"]
    require(a["kind"] == "appimage" and d["kind"] == "deb"
            and a["platform"] == d["platform"] == "linux"
            and a["arch"] == d["arch"], "linux_parity_package_profile")
    for key in ("source", "source_tree", "run_id", "run_attempt",
                "candidate_receipt", "cargo_index",
                "artifact_identities_sha256", "generated_metadata_sha256"):
        require(a[key] == d[key], "linux_parity_cross_run_context:" + key)
    require(a["run_attempt"] == 1, "linux_parity_not_first_attempt")
    for value in (appimage, deb):
        require(value["claims"]["full_package_inventory"] == "NOT_VERIFIED"
                and value["claims"]["native_license_review"] == "NOT_VERIFIED"
                and value["claims"]["redistribution_permission"] == "NOT_INFERRED"
                and value["claims"]["formal_g2"] == "NOT_GRANTED"
                and value["claims"]["formal_g3"] == "NOT_GRANTED",
                "linux_parity_not_release_authority")
        require(value["custody"]["status"] == "DIRECT_CAPTURED_EXTRACTION",
                "linux_parity_extraction_not_captured")
    app_rows = {r["path"]: r for r in appimage["roots"][0]["members"]}
    deb_rows = {r["path"]: r for r in deb["roots"][0]["members"]}
    require(set(deb_rows) & CONTROL_NAMESPACE == CONTROL_NAMESPACE,
            "linux_parity_debian_control_roster_incomplete")
    require({p for p in deb_rows if p == "DEBIAN" or p.startswith("DEBIAN/")}
            == CONTROL_NAMESPACE, "linux_parity_unrecognized_debian_control")
    require(not any(p == "DEBIAN" or p.startswith("DEBIAN/") for p in app_rows),
            "linux_parity_appimage_has_debian_control")
    common = {}
    for path, item in sorted(deb_rows.items()):
        if path in CONTROL_NAMESPACE:
            continue
        require(path.startswith("usr/") or path == "usr",
                "linux_parity_deb_payload_not_under_usr")
        require(path in app_rows, "linux_parity_appimage_missing:" + path)
        before = member_identity(item)
        after = member_identity(app_rows[path])
        require(before == after, "linux_parity_shared_payload_changed:" + path)
        common[path] = before

    require(common and any(v["kind"] == "file" for v in common.values()),
            "linux_parity_empty_common_payload")
    # Pure identity assertion. The consumer has not authenticated AppImage-only
    # dependencies, resolved native linkage, checked licenses or published a build.
    shared_files = {p: row for p, row in common.items() if row["kind"] == "file"}
    app_only = sorted(set(app_rows) - set(common))
    return {
        "schema": SCHEMA, "status": "EXTRACTED_COMMON_APPLICATION_PAYLOAD_BYTES_MATCH",
        "context": {
            "source": a["source"], "source_tree": a["source_tree"],
            "run_id": a["run_id"], "run_attempt": a["run_attempt"],
            "platform": "linux", "arch": a["arch"],
            "candidate_receipt": a["candidate_receipt"],
            "appimage_package": a["package"], "deb_package": d["package"],
        },
        "shared_regular_files": len(shared_files),
        "shared_other_members": len(common) - len(shared_files),
        "shared_regular_total_bytes": sum(r["bytes"] for r in shared_files.values()),
        "shared_member_sha256": inventory.capture.sha(
            inventory.capture.encoded(common)),
        "deb_control_files_excluded": sorted(CONTROL_FILES),
        "appimage_extra_members": len(app_only),
        "appimage_extra_regular_files": sum(
            app_rows[p]["kind"] == "file" for p in app_only),
        "gaps": {
            "appimage_only_origin_and_legal": "NOT_VERIFIED",
            "deb_control_producer": "NOT_VERIFIED",
            "container_archive_headers": "NOT_INVENTORIED",
            "native_linkage_and_license": "NOT_VERIFIED",
            "complete_package_inventory": "NOT_VERIFIED",
        },
        "claims": {
            "formal_g2": "NOT_GRANTED", "formal_g3": "NOT_GRANTED",
            "human_acceptance": "NOT_RUN", "release_authorized": False,
            "historical_results_transfer_to_new_source": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--appimage", type=Path, required=True)
    parser.add_argument("--deb", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--expected-appimage-sha256", required=True)
    parser.add_argument("--expected-deb-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(SHA1.fullmatch(args.expected_source) is not None,
            "linux_parity_expected_source")
    inputs = []
    for path, expected in ((args.appimage, args.expected_appimage_sha256),
                           (args.deb, args.expected_deb_sha256)):
        require(re.fullmatch(r"[0-9a-f]{64}", expected) is not None,
                "linux_parity_expected_report_sha256")
        original_path = path.absolute()
        raw = inventory.capture.stable_read(
            Path(original_path.anchor), original_path, inventory.MAX_REPORT)
        require(len(raw) <= inventory.MAX_REPORT
                and inventory.capture.sha(raw) == expected,
                "linux_parity_original_report_changed")
        inputs.append(inventory.capture.parse_json(raw))
    result = verify_common_payload(*inputs)
    require(result["context"]["source"] == args.expected_source,
            "linux_parity_source_not_selected")
    require(not args.output.exists() and args.output.parent.is_dir(),
            "linux_parity_output_already_exists_or_parent_missing")
    inventory.capture.write_new(args.output, inventory.capture.encoded(result))
    output_path = args.output.absolute()
    readback = inventory.capture.stable_read(
        Path(output_path.anchor), output_path, inventory.MAX_REPORT)
    require(inventory.capture.parse_json(readback) == result,
            "linux_parity_readback_changed")
    print("linux-package-parity=PASS shared_files=" +
          str(result["shared_regular_files"]) + " appimage_extra=" +
          str(result["appimage_extra_regular_files"]) +
          " g3=NOT_GRANTED", flush=True)


if __name__ == "__main__":
    main()
