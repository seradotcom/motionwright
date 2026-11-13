#!/usr/bin/env python3
"""Bind the existing GLib FFI probe to the current product source and lock.

This is component conformance, not installed-product remediation or a RustSec
waiver. Cargo resolves the isolated fixture from a copy of the product lock;
every selected package and dependency must stay within that original graph.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import tomllib

SCHEMA = "motionwright.v01-glib-product-ffi/1"
PROBE = "tooling/v01-glib-backport-probe"
PROBE_NAME = "motionwright-v01-glib-backport-probe"
REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"
TESTS = (
    "empty_and_single_item_string_variants",
    "forward_and_reverse_utf8_iteration",
    "alternating_front_back_and_exact_size",
    "all_lengths_0_through_64_are_lossless_in_both_directions",
    "last_handles_partial_consumption_and_an_empty_string",
    "nth_back_skips_items_after_partial_forward_consumption",
)
LIMIT = 16 * 1024 * 1024


class FfiEvidenceError(ValueError):
    pass


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise FfiEvidenceError(reason)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read(path: Path, limit: int = LIMIT) -> bytes:
    require(path.is_file() and not path.is_symlink(), "input_not_regular")
    require(path.stat().st_size <= limit, "input_budget_exceeded")
    raw = path.read_bytes()
    require(len(raw) <= limit, "input_budget_exceeded")
    return raw


def pairs(items):
    out = {}
    for key, value in items:
        require(key not in out, "duplicate_json_field")
        out[key] = value
    return out


def json_read(path: Path):
    return json.loads(read(path), object_pairs_hook=pairs)


def write_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def package_map(lock: dict) -> dict:
    rows = lock.get("package")
    require(isinstance(rows, list) and bool(rows), "lock_packages_missing")
    result = {}
    for row in rows:
        require(isinstance(row, dict), "lock_package_malformed")
        key = (row.get("name"), row.get("version"), row.get("source"))
        require(all(isinstance(v, str) and v for v in key[:2]),
                "lock_identity_malformed")
        require(key not in result, "lock_identity_duplicated")
        result[key] = row
    return result


def dependency_key(text: str, packages: dict) -> tuple:
    require(isinstance(text, str), "lock_dependency_malformed")
    match = re.fullmatch(r"([A-Za-z0-9_-]+)(?: ([^ ()]+))?(?: \(([^()]+)\))?", text)
    require(match is not None, "lock_dependency_malformed")
    name, version, source = match.groups()
    hits = [key for key in packages
            if key[0] == name and (version is None or key[1] == version)
            and (source is None or key[2] == source)]
    require(len(hits) == 1, "lock_dependency_not_exact")
    return hits[0]


def verify_resolution(root: Path, product: dict, fixture: dict, metadata: dict) -> dict:
    product_rows, fixture_rows = package_map(product), package_map(fixture)
    fixture_key = (PROBE_NAME, "0.0.0", None)
    glib_key = ("glib", "0.18.5", None)
    require(fixture_key in fixture_rows and glib_key in fixture_rows,
            "fixture_or_local_glib_missing")
    for key, row in fixture_rows.items():
        if key == fixture_key:
            require("checksum" not in row, "fixture_checksum_unexpected")
            continue
        require(key in product_rows, "fixture_package_not_in_product_lock")
        expected = product_rows[key]
        require(row.get("checksum") == expected.get("checksum"),
                "fixture_checksum_drift")
        if key != glib_key:
            require(key[2] == REGISTRY and bool(re.fullmatch(
                r"[0-9a-f]{64}", row.get("checksum", ""))),
                "fixture_non_registry_dependency")
        original_edges = {dependency_key(dep, product_rows)
                          for dep in expected.get("dependencies", [])}
        fixture_edges = {dependency_key(dep, fixture_rows)
                         for dep in row.get("dependencies", [])}
        require(fixture_edges <= original_edges, "fixture_dependency_edge_drift")
    require({dependency_key(dep, fixture_rows)
             for dep in fixture_rows[fixture_key].get("dependencies", [])}
            == {glib_key}, "fixture_root_dependency_changed")

    packages = metadata.get("packages")
    require(isinstance(packages, list) and packages, "metadata_packages_missing")
    by_id = {}
    for package in packages:
        package_id = package.get("id")
        key = (package.get("name"), package.get("version"), package.get("source"))
        require(isinstance(package_id, str) and package_id not in by_id,
                "metadata_id_duplicated_or_missing")
        require(key in fixture_rows, "metadata_package_not_locked")
        manifest = Path(package.get("manifest_path", ""))
        if key in (fixture_key, glib_key):
            expected = root / (PROBE if key == fixture_key else "vendor/glib-0.18.5") / "Cargo.toml"
            require(manifest.is_absolute() and manifest.resolve() == expected.resolve()
                    and not manifest.is_symlink(), "local_manifest_path_changed")
        else:
            require(key[2] == REGISTRY, "metadata_source_changed")
        by_id[package_id] = key
    # Cargo may omit platform-specific lock entries from filtered metadata.
    # Every observed metadata package is still checked against the complete
    # exact-product-derived fixture lock above.
    require(len(by_id) <= len(fixture_rows)
            and set(by_id.values()) <= set(fixture_rows), "metadata_lock_roster_mismatch")
    resolve = metadata.get("resolve", {})
    root_id = resolve.get("root")
    require(root_id in by_id and by_id[root_id] == fixture_key
            and metadata.get("workspace_members") == [root_id],
            "metadata_fixture_root_changed")
    nodes = resolve.get("nodes")
    require(isinstance(nodes, list), "metadata_nodes_missing")
    graph = {}
    for node in nodes:
        node_id = node.get("id")
        dependencies = node.get("dependencies")
        require(node_id in by_id and node_id not in graph
                and isinstance(dependencies, list)
                and all(dep in by_id for dep in dependencies)
                and len(dependencies) == len(set(dependencies)),
                "metadata_dependency_unclosed")
        locked_edges = {dependency_key(dep, fixture_rows)
                        for dep in fixture_rows[by_id[node_id]].get("dependencies", [])}
        require({by_id[dep] for dep in dependencies} <= locked_edges,
                "metadata_dependency_not_locked")
        graph[node_id] = dependencies
    require(set(graph) == set(by_id), "metadata_node_roster_mismatch")
    require({by_id[dep] for dep in graph[root_id]} == {glib_key},
            "metadata_glib_not_selected")
    seen, pending = set(), [root_id]
    while pending:
        current = pending.pop()
        if current not in seen:
            seen.add(current)
            pending.extend(graph[current])
    require(seen == set(by_id), "metadata_unreachable_package")
    return {
        "selected_packages": len(by_id),
        "product_lock_package_identity_and_checksums_exact": True,
        "selected_dependency_edges_within_product_lock": True,
        "glib_package_id": next(key for key, value in by_id.items() if value == glib_key),
        "fixture_package_id": root_id,
        "fixture_glib_manifest": "vendor/glib-0.18.5/Cargo.toml",
    }


def verify_test_output(raw: bytes) -> dict:
    text = raw.decode("utf-8")
    observed = re.findall(r"^test tests::([a-z0-9_]+) \.\.\. ok$", text, re.M)
    require(len(observed) == len(TESTS) and set(observed) == set(TESTS),
            "original_ffi_test_missing_or_repeated")
    summary = (r"^test result: ok\. 6 passed; 0 failed; 0 ignored; "
               r"0 measured; 0 filtered out;")
    require(len(re.findall(summary, text, re.M)) == 1,
            "original_ffi_test_summary_not_complete")
    return {"tests_passed": 6, "tests_failed": 0, "tests_ignored": 0,
            "tests_filtered_out": 0, "stdout_sha256": digest(raw)}


def resolution_record(root: Path, metadata_path: Path) -> dict:
    product_raw = read(root / "Cargo.lock")
    fixture_raw = read(root / PROBE / "Cargo.lock")
    metadata_raw = read(metadata_path)
    return {
        "schema": SCHEMA,
        "product_lock_sha256": digest(product_raw),
        "fixture_lock_sha256": digest(fixture_raw),
        "metadata_sha256": digest(metadata_raw),
        "resolution": verify_resolution(
            root, tomllib.loads(product_raw.decode()),
            tomllib.loads(fixture_raw.decode()),
            json.loads(metadata_raw, object_pairs_hook=pairs)),
        "scope": "CURRENT_PRODUCT_SOURCE_ISOLATED_FFI_COMPONENT",
        "advisory_status": "OPEN_UPSTREAM_BLOCKED",
        "installed_binary_remediation": "NOT_VERIFIED",
        "human_acceptance": "NOT_RUN",
        "release_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = resolution_record(args.root.resolve(strict=True), args.metadata)
    write_new(args.output, report)
    print("glib-ffi-resolution=PRODUCT_LOCK_BOUND advisory=OPEN release=BLOCKED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
