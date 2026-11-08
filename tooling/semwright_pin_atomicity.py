#!/usr/bin/env python3
"""Offline consistency preflight for a deliberately reviewed Semwright source pin.

This is not a pin updater, Cargo resolver, remote Git trust claim, or G2/G3 gate.
All repository and upstream inputs are read-only. The separately reviewed
SOURCE_LOCK.json evidence is verified by the existing source_lock_admission.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tomllib

from source_lock_admission import (
    MAX_EVIDENCE_BYTES, MAX_SOURCE_BYTES, SourceAdmissionError,
    _read, load_source_lock, verify_source_lock,
)

REPO = "https://github.com/seradotcom/semwright"
LOCK_PREFIX = "git+" + REPO + "?rev="
PIN = re.compile(r"[0-9a-f]{40}\Z")
PATH_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+\Z")
MAX_INPUT = 2 * 1024 * 1024
UPSTREAM_EVIDENCE_FILES = ("Cargo.toml", "LICENSE-MIT", "LICENSE-APACHE")


class PinConsistencyError(ValueError):
    """No unreviewed source pin may silently be substituted."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise PinConsistencyError("semwright_atomic_pin_" + code)


def read_toml(root: Path, relative: str) -> dict:
    # Reuse the bounded source-admission reader: lstat every parent, reject
    # symlink/reparse ancestors and hardlinked leaves, then bind the opened fd
    # to its path identity and byte contents before and after the read. A
    # normal path.is_file() check alone follows redirected parent directories.
    try:
        encoded = _read(Path(root), relative, MAX_INPUT)
    except SourceAdmissionError as error:
        raise PinConsistencyError("semwright_atomic_pin_cargo_file_redirected") from error
    try:
        parsed = tomllib.loads(encoded.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as error:
        raise PinConsistencyError("semwright_atomic_pin_cargo_toml_invalid") from error
    require(type(parsed) is dict, "cargo_toml_shape")
    return parsed


def manifest_paths(workspace: dict) -> list[str]:
    members = workspace.get("workspace", {}).get("members")
    require(type(members) is list and bool(members), "workspace_members")
    paths = ["Cargo.toml"]
    for member in members:
        require(type(member) is str and bool(member) and "\\" not in member
                and "*" not in member and "?" not in member, "workspace_member_path")
        path = PurePosixPath(member)
        require(not path.is_absolute()
                and all(PATH_SEGMENT.fullmatch(part) is not None
                        and part not in (".", "..") for part in path.parts)
                and str(path) == member and len(path.parts) > 0,
                "workspace_member_path")
        paths.append(member + "/Cargo.toml")
    require(len(set(paths)) == len(paths), "workspace_member_duplicate")
    return paths


def manifest_pins(
    document: dict, pin: str, location: str, workspace_deps: dict
) -> list[str]:
    """Walk every relevant Git dependency table, including target-specific ones."""
    found = []

    def visit(node: object, keys: tuple[str, ...] = ()) -> None:
        if type(node) is dict:
            git = node.get("git")
            name = keys[-1] if keys else ""
            package = node.get("package", name)
            semwright_package = (type(package) is str
                                 and package.startswith("semwright-"))
            direct = "git" in node and (
                (type(git) is str and ("github.com/seradotcom/semwright" in git
                                       or "semwright" in git.lower()))
                or name.startswith("semwright-")
                or semwright_package
            )
            if direct:
                require(git == REPO, "cargo_git_origin")
                require(node.get("rev") == pin and PIN.fullmatch(pin) is not None,
                        "cargo_git_revision")
                require("branch" not in node and "tag" not in node, "cargo_git_selector")
                require(semwright_package, "cargo_git_package")
                found.append(location + ":" + package)
            elif (name.startswith("semwright-") or semwright_package) and "git" not in node:
                # The workspace dependency table is the root authority. It
                # must declare a real pinned Git source, never inherit itself.
                if location == "Cargo.toml" and keys[:2] == ("workspace", "dependencies"):
                    require(False, "cargo_workspace_unpinned")
                require(node.get("workspace") is True, "cargo_direct_source")
                require(not any(key in node for key in (
                    "version", "path", "registry", "rev", "branch", "tag"
                )), "cargo_direct_source")
                original = workspace_deps.get(name)
                require(type(original) is dict and original.get("git") == REPO
                        and original.get("rev") == pin
                        and original.get("package", name) == package
                        and "branch" not in original and "tag" not in original,
                        "cargo_workspace_unpinned")
            for key, value in node.items():
                if type(value) in (dict, list):
                    visit(value, (*keys, str(key)))
                elif str(key).startswith("semwright-"):
                    # Cargo accepts string shorthand for registry dependency.
                    # It must not silently replace a reviewed Git source.
                    require(False, "cargo_direct_source")
        elif type(node) is list:
            for index, value in enumerate(node):
                visit(value, (*keys, str(index)))

    visit(document)
    return found


def check_material(
    pinned: str, parsed_manifests: dict[str, dict], cargo_lock: dict
) -> dict:
    require(type(pinned) is str and PIN.fullmatch(pinned) is not None, "source_pin_invalid")
    require(bool(parsed_manifests) and "Cargo.toml" in parsed_manifests,
            "workspace_manifest_missing")
    expected = manifest_paths(parsed_manifests["Cargo.toml"])
    require(set(parsed_manifests) == set(expected), "workspace_roster_mismatch")
    workspace_deps = parsed_manifests["Cargo.toml"].get(
        "workspace", {}
    ).get("dependencies")
    require(type(workspace_deps) is dict, "workspace_dependencies_missing")
    direct = []
    for location in expected:
        direct += manifest_pins(
            parsed_manifests[location], pinned, location, workspace_deps
        )
    require(bool(direct), "direct_dependencies_missing")
    packages = cargo_lock.get("package")
    require(type(packages) is list and bool(packages), "cargo_lock_packages_missing")
    resolved = []
    for package in packages:
        require(type(package) is dict, "cargo_lock_package_shape")
        name = package.get("name")
        source = package.get("source", "")
        if (type(name) is str and name.startswith("semwright-")) or (
            type(source) is str and "github.com/seradotcom/semwright" in source
        ):
            require(type(name) is str and name.startswith("semwright-"),
                    "cargo_lock_foreign_package")
            require(source == LOCK_PREFIX + pinned + "#" + pinned,
                    "cargo_lock_revision")
            resolved.append(name)
    require(bool(resolved), "resolved_dependencies_missing")
    require(len(set(resolved)) == len(resolved), "resolved_package_duplicate")
    for reference in direct:
        name = reference.rpartition(":")[-1]
        require(name in resolved, "direct_dependency_not_locked")
    return {
        "source_pin": pinned,
        "workspace_manifest_count": len(expected),
        "direct_git_dependency_count": len(direct),
        "locked_upstream_package_count": len(resolved),
        "manifest_paths": expected,
        "status": "CONSISTENT_ONLY_NOT_RELEASE_APPROVED",
    }


def audit(root: Path) -> dict:
    root = root.resolve(strict=True)
    source = load_source_lock(root)
    verify_source_lock(root, source)
    pin = source["dependencies"]["semwright"]["revision"]
    workspace = read_toml(root, "Cargo.toml")
    manifests = {
        name: (workspace if name == "Cargo.toml" else read_toml(root, name))
        for name in manifest_paths(workspace)
    }
    lock = read_toml(root, "Cargo.lock")
    result = check_material(pin, manifests, lock)
    result["retained_source_evidence"] = "VERIFIED_OFFLINE"
    result["promotion_authorized"] = False
    return result


def candidate_evidence(
    root: Path, upstream: Path, candidate: str
) -> dict:
    """Compare new commit's three exact Git blobs to already retained bytes.

    If any metadata/license changes, block the cheap reuse: a new reviewed
    retained source set is required. This does not edit either repository.
    """
    require(type(candidate) is str and PIN.fullmatch(candidate) is not None,
            "candidate_commit_invalid")
    root = root.resolve(strict=True)
    upstream = upstream.resolve(strict=True)
    actual = subprocess.check_output(
        ["git", "-C", str(upstream), "cat-file", "-t", candidate],
        stderr=subprocess.DEVNULL, timeout=10,
    ).strip()
    require(actual == b"commit", "candidate_not_commit")
    current = load_source_lock(root)
    verify_source_lock(root, current)
    old_pin = current["dependencies"]["semwright"]["revision"]
    # A candidate that only copies the current Cargo/license bytes but has
    # unrelated commit history is not a forward revision of the reviewed
    # Semwright source. This checks LOCAL Git ancestry, not remote trust.
    try:
        history = subprocess.run(
            ["git", "-C", str(upstream), "merge-base", "--is-ancestor",
             old_pin, candidate],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PinConsistencyError(
            "semwright_atomic_pin_candidate_ancestry_unavailable"
        ) from error
    if history.returncode == 1:
        raise PinConsistencyError("semwright_atomic_pin_candidate_not_descendant")
    require(history.returncode == 0, "candidate_ancestry_unavailable")
    evidence_ref = current["dependencies"]["semwright"]["verification"]["evidence"]
    try:
        verified_bytes = _read(root, evidence_ref["path"], MAX_EVIDENCE_BYTES)
    except SourceAdmissionError as error:
        raise PinConsistencyError("semwright_atomic_pin_candidate_evidence_redirected") from error
    # The source lock's evidence SHA is the immutable reviewed source. The
    # re-read must match it even if the file was replaced between the earlier
    # source-admission check and our own candidate blob comparisons.
    require(
        hashlib.sha256(verified_bytes).hexdigest() == evidence_ref["sha256"],
        "candidate_evidence_changed_after_verification",
    )
    retained = json.loads(verified_bytes)
    files = retained["files"]
    require({entry["upstream_path"] for entry in files}
            == set(UPSTREAM_EVIDENCE_FILES), "candidate_evidence_roster")
    exact = []
    for entry in files:
        name = entry["upstream_path"]
        upstream_bytes = subprocess.check_output(
            ["git", "-C", str(upstream), "show", candidate + ":" + name],
            stderr=subprocess.DEVNULL, timeout=10,
        )
        try:
            original = _read(root, entry["path"], MAX_SOURCE_BYTES)
        except SourceAdmissionError as error:
            raise PinConsistencyError("semwright_atomic_pin_candidate_evidence_redirected") from error
        require(upstream_bytes == original
                and hashlib.sha256(upstream_bytes).hexdigest() == entry["sha256"],
                "candidate_new_metadata_requires_review")
        exact.append({"path": name, "same_upstream_git_blob": True,
                      "sha256": entry["sha256"]})
    candidate_record = copy.deepcopy(retained)
    candidate_record["revision"] = candidate
    encoded = (json.dumps(candidate_record, indent=2, sort_keys=True) + "\n").encode()
    return {
        "candidate_sha": candidate,
        "existing_evidence_source_pin": current["dependencies"]["semwright"]["revision"],
        "verified_shared_metadata": exact,
        "future_evidence_path":
            "third_party/semwright-source-evidence/" + candidate + "/evidence.json",
        "future_evidence_sha256": hashlib.sha256(encoded).hexdigest(),
        "copy_source_metadata_required": False,
        "source_lock_cargo_manifest_cargo_lock_update_required": True,
        "cargo_resolution_regeneration_required": True,
        "candidate_evidence_bytes_not_written": len(encoded),
        "ancestor_of_candidate_confirmed_locally": True,
        "source_pin_promotion_authorized": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--upstream", type=Path)
    parser.add_argument("--candidate")
    args = parser.parse_args()
    result = {"schema": "motionwright.semwright-atomic-pin-audit/1",
              "current": audit(args.root)}
    if args.candidate is not None:
        require(args.upstream is not None, "candidate_upstream_missing")
        result["candidate"] = candidate_evidence(
            args.root, args.upstream, args.candidate)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
