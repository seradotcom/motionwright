#!/usr/bin/env python3
"""Bounded v0.1 unsigned candidate package receipt for the exact source HEAD.

Pure source/package inspection; never claims an installed owner session,
distribution signature, package attestation, human acceptance or publication.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tomllib

from release_manifest import (
    ManifestError, _checkout, _discard_new_output_on_source_change,
    _path_has_reparse, _stable_read, _strict_object, write_new_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = {"linux", "windows", "macos"}
EXPECTED_SUFFIXES = {
    "linux": {".deb": "deb", ".appimage": "appimage"},
    "windows": {".exe": "nsis"},
    "macos": {".dmg": "dmg"},
}
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024 * 1024


class CandidateError(ValueError):
    """Stable error codes, never original paths or untrusted bundle data."""


def need(ok: bool, reason: str) -> None:
    if not ok:
        raise CandidateError(reason)


def classify(platform: str, path: Path) -> str:
    lowered = path.name.lower()
    for suffix, kind in EXPECTED_SUFFIXES[platform].items():
        if lowered.endswith(suffix):
            if platform == "windows" and not lowered.endswith("-setup.exe"):
                raise ValueError("Windows candidate artifact must be the NSIS -setup.exe bundle")
            return kind
    raise ValueError(f"unsupported {platform} candidate artifact: {path.name}")


def verified_artifact(root: Path, supplied: Path) -> tuple[Path, int, str]:
    """Hash stable regular-file descriptor; refuse junction/symlink components."""
    need(".." not in supplied.parts, "candidate_artifact_relative_escape")
    path = supplied if supplied.is_absolute() else root / supplied
    leaf_info = None
    for component in (path, *path.parents):
        try:
            meta = component.lstat()
            if component == path:
                leaf_info = meta
            need(
                not stat.S_ISLNK(meta.st_mode)
                and not component.is_symlink()
                and not _path_has_reparse(component, meta),
                "candidate artifact must be a regular non-symlink file",
            )
        except OSError as exc:
            raise CandidateError("candidate_artifact_unavailable") from exc
    need(leaf_info is not None and stat.S_ISREG(leaf_info.st_mode),
         "candidate_artifact_not_regular")
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0) |
             getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        with os.fdopen(os.open(path, flags), "rb") as stream:
            first = os.fstat(stream.fileno())
            need(stat.S_ISREG(first.st_mode)
                 and 0 < first.st_size <= MAX_ARTIFACT_BYTES,
                 "candidate_artifact_size_invalid")
            digest = hashlib.sha256()
            seen = 0
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                seen += len(block)
                need(seen <= MAX_ARTIFACT_BYTES, "candidate_artifact_size_invalid")
                digest.update(block)
            after = os.fstat(stream.fileno())
        final = path.stat()
    except OSError as exc:
        raise CandidateError("candidate_artifact_unavailable") from exc
    stamp = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_nlink)
    need(stamp(leaf_info) == stamp(first) == stamp(after) == stamp(final)
         and seen == first.st_size, "candidate_artifact_changed")
    return path, seen, digest.hexdigest()


def sha256(path: Path) -> str:
    """Compatibility with the existing standalone package-hash test."""
    return verified_artifact(Path.cwd(), path)[2]


def read_locked_candidate_source(root: Path) -> tuple[dict, dict]:
    """Read only tracked, bounded, symlink-free Cargo/Native SDK authority."""
    try:
        cargo = tomllib.loads(_stable_read(root, "Cargo.toml").decode("utf-8"))
        source = json.loads(_stable_read(root, "SOURCE_LOCK.json").decode("utf-8"),
                            object_pairs_hook=_strict_object)
        workspace = cargo["workspace"]["package"]
        semwright = source["dependencies"]["semwright"]
        need(isinstance(workspace.get("version"), str) and workspace["version"],
             "candidate_workspace_invalid")
        need(isinstance(semwright.get("revision"), str) and
             re.fullmatch(r"[0-9a-f]{40}", semwright["revision"]) is not None,
             "candidate_semwright_lock_invalid")
        return workspace, semwright
    except (KeyError, TypeError, UnicodeError, ValueError, tomllib.TOMLDecodeError) as exc:
        if isinstance(exc, CandidateError):
            raise
        raise CandidateError("candidate_source_invalid") from exc


CARGO_TARGETS = {
    ("linux", "x86_64"): "x86_64-unknown-linux-gnu",
    ("linux", "amd64"): "x86_64-unknown-linux-gnu",
    ("windows", "x86_64"): "x86_64-pc-windows-msvc",
    ("windows", "amd64"): "x86_64-pc-windows-msvc",
    ("macos", "x86_64"): "x86_64-apple-darwin",
    ("macos", "arm64"): "aarch64-apple-darwin",
    ("macos", "aarch64"): "aarch64-apple-darwin",
}


def _load_cargo_capture():
    """Load the canonical verifier and its read-only Git dependency."""
    import importlib.util

    scripts = ROOT / "scripts"

    def load(name: str, filename: str):
        path = (scripts / filename).resolve(strict=True)
        existing = sys.modules.get(name)
        if existing is not None:
            need(Path(getattr(existing, "__file__", "")).resolve(strict=True) == path,
                 "candidate_cargo_verifier_unavailable")
            return existing
        specification = importlib.util.spec_from_file_location(name, path)
        need(specification is not None and specification.loader is not None,
             "candidate_cargo_verifier_unavailable")
        module = importlib.util.module_from_spec(specification)
        sys.modules[name] = module
        try:
            specification.loader.exec_module(module)
        except BaseException:
            if sys.modules.get(name) is module:
                del sys.modules[name]
            raise
        return module

    try:
        load("cargo_license_sources", "cargo_license_sources.py")
        return load("_motionwright_candidate_receipt_capture",
                    "candidate_dependency_capture.py")
    except CandidateError:
        raise
    except (ImportError, OSError, ValueError, TypeError, AttributeError) as exc:
        raise CandidateError("candidate_cargo_verifier_unavailable") from exc


def _candidate_cargo_binding(root: Path, git_sha: str, platform: str, arch: str,
                            workspace_version: str, cargo_index: Path,
                            cargo_binary: Path) -> dict:
    """Bind build inputs; package extraction remains a separate gate."""
    target = CARGO_TARGETS.get((platform, arch))
    need(target is not None, "candidate_cargo_target_unsupported")
    capture = _load_cargo_capture()
    try:
        anchor = root.resolve(strict=True)
        snapshot = capture.source_snapshot(anchor, git_sha)
        raw = capture.stable_read(anchor, cargo_index, 16 * 1024 * 1024)
        digest = hashlib.sha256(raw).hexdigest()
        observed = capture.verify_binary_binding(
            raw, expected_index_sha256=digest, expected_source=git_sha,
            expected_source_tree=snapshot["tree"], expected_target=target,
            expected_package="motionwright-desktop",
            expected_binary="motionwright-desktop",
            binary_root=anchor, binary_path=cargo_binary,
        )
        expected_id = ("cargo:path:apps/desktop/src-tauri/Cargo.toml"
                       "#motionwright-desktop@" + workspace_version)
        need(observed["package_id"] == expected_id,
             "candidate_cargo_component_mismatch")
        return {"index_sha256": digest, "source_tree": snapshot["tree"],
                "target": target, "root_package": "motionwright-desktop",
                "binary_target": "motionwright-desktop"}
    except CandidateError:
        raise
    except capture.CaptureError as exc:
        # Capture errors may contain file names; keep public diagnostics bounded.
        raise CandidateError("candidate_cargo_capture_invalid") from exc
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        raise CandidateError("candidate_cargo_capture_invalid") from exc


def make_receipt(root: Path, git_sha: str, platform: str,
                 arch: str, artifacts: list[Path], *,
                 cargo_index: Path | None = None,
                 cargo_binary: Path | None = None,
                 require_cargo_binding: bool = False) -> dict:
    need(isinstance(git_sha, str) and re.fullmatch(r"[0-9a-f]{40}", git_sha) is not None,
         "candidate_sha_invalid")
    need(platform in PLATFORMS, "candidate_platform_invalid")
    need(isinstance(arch, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", arch) is not None,
         "candidate_arch_invalid")
    need(1 <= len(artifacts) <= 2, "candidate_artifact_count_invalid")
    need((cargo_index is None) == (cargo_binary is None),
         "candidate_cargo_inputs_incomplete")
    need(not require_cargo_binding or cargo_index is not None,
         "candidate_cargo_binding_required")
    _checkout(root, git_sha)
    workspace, semwright = read_locked_candidate_source(root)
    cargo_binding = None
    if cargo_index is not None:
        cargo_binding = _candidate_cargo_binding(
            root, git_sha, platform, arch, workspace["version"],
            cargo_index, cargo_binary,
        )
    records = []
    names: set[str] = set()
    for supplied in artifacts:
        name = supplied.name
        key = name.casefold()
        need(key not in names, "candidate_artifact_name_reused")
        names.add(key)
        try:
            kind = classify(platform, supplied)
        except ValueError as exc:
            raise CandidateError("candidate_artifact_type_invalid") from exc
        _, size, content_hash = verified_artifact(root, supplied)
        records.append({
            "filename": name, "bundle_kind": kind,
            "bytes": size, "sha256": content_hash,
        })
    found = {item["bundle_kind"] for item in records}
    expected = {"appimage", "deb"} if platform == "linux" else (
        {"nsis"} if platform == "windows" else {"dmg"}
    )
    need(found == expected and len(records) == len(expected),
         "candidate_bundle_set_invalid")
    if cargo_binding is not None:
        current = _candidate_cargo_binding(
            root, git_sha, platform, arch, workspace["version"],
            cargo_index, cargo_binary,
        )
        need(current == cargo_binding, "candidate_cargo_binding_changed")
        for record in records:
            record["cargo_build"] = dict(cargo_binding)
    _checkout(root, git_sha)
    receipt = {
        "schema_version": 1,
        "candidate": {
            "application": "Motionwright",
            "version": workspace["version"],
            "source_revision": git_sha,
            "semwright_revision": semwright["revision"],
            "platform": platform,
            "arch": arch,
        },
        "artifacts": sorted(records, key=lambda row: row["filename"].casefold()),
        "claims": {
            "published": False,
            "distribution_signature": "NOT_CLAIMED",
            "notarization": "NOT_CLAIMED",
            "human_install_acceptance": "NOT_RUN",
            "runtime_renderer_acceptance": "SEPARATE_GATE",
        },
    }
    if cargo_binding is not None:
        receipt["claims"]["package_contents_verification"] = "SEPARATE_EXTRACTION_GATE"
    return receipt


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
    parser.add_argument("--arch", required=True)
    parser.add_argument("--artifact", action="append", required=True)
    parser.add_argument("--cargo-index", type=Path)
    parser.add_argument("--cargo-binary", type=Path)
    parser.add_argument("--require-cargo-binding", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    args = parse_args(argv)
    try:
        # Preserve the legacy early symlink refusal before following paths
        # or inspecting bytes. No source or owner file is opened here.
        for raw in args.artifact:
            if Path(raw).is_symlink():
                raise CandidateError("candidate artifact must be a regular non-symlink file")
        receipt = make_receipt(
            root, args.git_sha.strip().lower(), args.platform,
            args.arch.strip(), [Path(path) for path in args.artifact],
            cargo_index=args.cargo_index, cargo_binary=args.cargo_binary,
            require_cargo_binding=args.require_cargo_binding,
        )
        output = args.output if args.output.is_absolute() else root / args.output
        need(".." not in output.parts, "candidate_output_invalid")
        # CI formerly relied on this directory being created automatically.
        # Only provision one new evidence directory below a known parent.
        if not output.parent.exists():
            ancestor = output.parent.parent
            info = ancestor.lstat()
            need(stat.S_ISDIR(info.st_mode) and
                 not _path_has_reparse(ancestor, info) and
                 not ancestor.is_symlink(), "candidate_output_parent_invalid")
            output.parent.mkdir(mode=0o700)
        write_new_manifest(root, args.output, receipt)
        try:
            _checkout(root, args.git_sha.strip().lower())
            if args.cargo_index is not None:
                current = _candidate_cargo_binding(
                    root, args.git_sha.strip().lower(), args.platform,
                    args.arch.strip(), receipt["candidate"]["version"],
                    args.cargo_index, args.cargo_binary,
                )
                need(all(record.get("cargo_build") == current
                         for record in receipt["artifacts"]),
                     "candidate_cargo_binding_changed")
        except (ManifestError, CandidateError):
            _discard_new_output_on_source_change(output, receipt)
            raise
    except (ManifestError, CandidateError) as exc:
        print("candidate-receipt=REFUSED code=" + str(exc), file=sys.stderr)
        return 2
    except OSError:
        print("candidate-receipt=REFUSED code=candidate_filesystem_unavailable",
              file=sys.stderr)
        return 2
    print("candidate-receipt=CREATED platform=" + args.platform
          + " arch=" + args.arch.strip() + " artifacts=" + str(len(receipt["artifacts"]))
          + " source=" + args.git_sha.strip().lower())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
