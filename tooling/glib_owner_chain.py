#!/usr/bin/env python3
"""Source-only dependency owner-chain guard for the open glib 0.18.5 advisory.

This admits only the exact audited FFI backport, never waives RustSec or
claims that the compiled/installed binary has been independently verified.
Cargo.lock includes dependencies of multiple targets, so its reverse graph
is a conservative *structural* review boundary, not Linux reachability proof.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re
from v01_glib_product_admission import ProductBackportError, admit_patched_glib
from typing import Any

SCHEMA = "motionwright.v01-glib-owner-chain/1"
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.+-]+)?$")


class OwnerChainError(ValueError):
    """Fail-closed source graph review; do not accept an unrecognized owner."""


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise OwnerChainError(reason)


def _key(row: dict) -> tuple[str, str]:
    name = row.get("name")
    version = row.get("version")
    _require(isinstance(name, str) and bool(name)
             and isinstance(version, str) and bool(VERSION.fullmatch(version)),
             "malformed_lock_package_identity")
    return name, version


def lock_reverse_edges(lock: dict) -> dict[tuple[str, str], set[tuple[str, str]]]:
    """Use Cargo.lock's fully-qualified names/versions, never fuzzy prefix matching."""
    packages = lock.get("package")
    _require(isinstance(packages, list) and bool(packages),
             "cargo_lock_has_no_packages")
    by_key: dict[tuple[str, str], dict] = {}
    by_name: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in packages:
        _require(isinstance(row, dict), "malformed_lock_package")
        key = _key(row)
        _require(key not in by_key, "duplicated_lock_package_identity")
        by_key[key] = row
        by_name[key[0]].add(key)

    reverse: dict[tuple[str, str], set[tuple[str, str]]] = {
        name: set() for name in by_key
    }
    for parent, row in by_key.items():
        deps = row.get("dependencies", [])
        _require(isinstance(deps, list), "malformed_lock_dependencies")
        for dep in deps:
            _require(isinstance(dep, str) and bool(dep),
                     "malformed_lock_dependency")
            parts = dep.split()
            _require(1 <= len(parts) <= 3, "malformed_lock_dependency")
            name = parts[0]
            matches = by_name.get(name, set())
            _require(bool(matches), "missing_lock_dependency_package")
            if len(parts) >= 2 and VERSION.fullmatch(parts[1]):
                matches = {key for key in matches if key[1] == parts[1]}
            _require(len(matches) == 1,
                     "ambiguous_or_mismatched_lock_dependency_version")
            child = next(iter(matches))
            reverse[child].add(parent)
    return reverse


def verify_glib_owner_chain(lock: dict[str, Any], glib_policy: dict) -> dict:
    """Check exact reviewed glib owners without claiming zero exploitability."""
    _require(glib_policy.get("id") == "RUSTSEC-2024-0429"
             and glib_policy.get("package") == "glib"
             and glib_policy.get("status") == "OPEN_UPSTREAM_BLOCKED",
             "glib_advisory_policy_not_open")
    version = glib_policy.get("locked_version")
    _require(isinstance(version, str) and bool(VERSION.fullmatch(version)),
             "glib_locked_version_invalid")
    reviewed = glib_policy.get("reviewed_direct_owners")
    _require(isinstance(reviewed, list) and reviewed
             and all(isinstance(item, str) and re.fullmatch(
                 r"[a-z0-9][a-z0-9_-]*@[0-9]+\.[0-9]+\.[0-9]+", item
             ) for item in reviewed)
             and len(set(reviewed)) == len(reviewed),
             "glib_reviewed_owners_invalid")

    reverse = lock_reverse_edges(lock)
    source = ("glib", version)
    _require(source in reverse, "reviewed_glib_absent_or_changed")
    actual_owners = sorted(f"{name}@{ver}" for name, ver in reverse[source])
    _require(actual_owners == sorted(reviewed),
             "glib_direct_owner_chain_drifted")

    # Reverse reachability detects a new *indirect* owner that reuses an
    # already-known GTK crate, not only a new direct glib-dependent package.
    stack = [source]
    ancestors: set[tuple[str, str]] = {source}
    while stack:
        parent = stack.pop()
        for next_owner in reverse[parent]:
            if next_owner not in ancestors:
                ancestors.add(next_owner)
                stack.append(next_owner)
    observed_tree = sorted(
        f"{name}@{version}" for name, version in ancestors if name != "glib"
    )
    expected_tree = glib_policy.get("reviewed_transitive_owners")
    _require(
        isinstance(expected_tree, list) and bool(expected_tree)
        and all(isinstance(item, str) and item for item in expected_tree)
        and len(set(expected_tree)) == len(expected_tree),
        "glib_reviewed_transitive_owner_policy_invalid",
    )
    _require(observed_tree == sorted(expected_tree),
             "glib_indirect_owner_chain_drifted")

    # This exact, source-verified glib 0.18.5 backport is the only approved
    # exception to a crates.io source within the reviewed GTK3 owner graph.
    # This is NOT an advisory waiver: the installed product is still unproven.
    backport = glib_policy.get("reviewed_local_backport")
    _require(backport == {
        "mode": "local-path-exact-two-line-upstream-backport",
        "vendor_path": "vendor/glib-0.18.5",
        "upstream_archive_sha256":
            "233daaf6e83ae6a12a52055f568f9d7cf4671dabb78ff9560ab6da230ce00ee5",
    }, "glib_reviewed_backport_policy_invalid")
    try:
        backport_receipt = admit_patched_glib(Path(__file__).resolve().parents[1])
    except (ProductBackportError, OSError, ValueError) as exc:
        raise OwnerChainError("glib_backport_custody_unverified") from exc
    _require(backport_receipt["local_cargo_lock_selected"] is True
             and backport_receipt["all_other_files_identical_to_upstream"] is True,
             "glib_backport_source_not_verified")

    # Every other reverse ancestor must retain exactly its original locked
    # crates.io source and checksum. A hostile git/path substitution fails.
    for row in lock["package"]:
        key = _key(row)
        if key not in ancestors or key == ("motionwright-desktop", "0.1.0"):
            continue
        if key == source:
            _require(row.get("source") is None and "checksum" not in row,
                     "glib_ancestor_source_changed")
            continue
        _require(row.get("source") ==
                 "registry+https://github.com/rust-lang/crates.io-index",
                 "glib_ancestor_source_changed")
        checksum = row.get("checksum")
        _require(isinstance(checksum, str)
                 and bool(re.fullmatch(r"[0-9a-f]{64}", checksum)),
                 "glib_ancestor_checksum_missing")

    roots = sorted(row for row in ancestors if not reverse[row])
    _require(roots == [("motionwright-desktop", "0.1.0")],
             "glib_dependency_escapes_reviewed_desktop_root")

    observed_first_party = sorted(name for name, _ in ancestors
                                  if name.startswith("motionwright-"))
    _require(observed_first_party == ["motionwright-desktop"],
             "glib_entered_unreviewed_first_party_crate")
    tauri_version = glib_policy.get("required_lock_context", {}).get("tauri")
    _require(("tauri", tauri_version) in ancestors,
             "glib_not_owned_by_reviewed_tauri_stack")
    _require(any(name == "gtk" for name, _ in ancestors)
             and any(name == "webkit2gtk" for name, _ in ancestors),
             "glib_not_linked_through_reviewed_gtk_webkit_stack")

    return {
        "schema": SCHEMA,
        "advisory_id": glib_policy["id"],
        "package": source[0],
        "locked_version": source[1],
        "advisory_status": "OPEN_UPSTREAM_BLOCKED",
        "reviewed_direct_owners": actual_owners,
        "reviewed_transitive_owners": sorted(
            f"{name}@{version}" for name, version in ancestors
            if name != "glib"
        ),
        "terminal_source_owner": "motionwright-desktop@0.1.0",
        "reviewed_tauri_owner": "tauri@" + str(tauri_version),
        "observed_first_party_owners": observed_first_party,
        "source_graph_scope": "Cargo.lock all-target structural dependencies",
        "linux_target_reachability_proven": False,
        "affected_iterator_api_unreachable_proven": False,
        "glib_vulnerability_fixed": False,
        "reviewed_backport_source_verified": True,
        "actual_compiled_linux_glib_verified": False,
        "release_authorized": False,
    }
