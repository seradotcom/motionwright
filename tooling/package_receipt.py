#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLATFORMS = {"linux", "windows", "macos"}
EXPECTED_SUFFIXES = {
    "linux": {".deb": "deb", ".appimage": "appimage"},
    "windows": {".exe": "nsis"},
    "macos": {".dmg": "dmg"},
}


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify(platform: str, path: pathlib.Path) -> str:
    lowered = path.name.lower()
    for suffix, kind in EXPECTED_SUFFIXES[platform].items():
        if lowered.endswith(suffix):
            if platform == "windows" and not lowered.endswith("-setup.exe"):
                raise ValueError("Windows candidate artifact must be the NSIS -setup.exe bundle")
            return kind
    raise ValueError(f"unsupported {platform} candidate artifact: {path.name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record exact Motionwright candidate bundles without claiming publication or signing."
    )
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
    parser.add_argument("--arch", required=True)
    parser.add_argument("--artifact", action="append", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    git_sha = args.git_sha.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", git_sha):
        raise SystemExit("--git-sha must be an exact 40-character commit SHA")

    arch = args.arch.strip()
    if not arch or len(arch) > 64 or not re.fullmatch(r"[A-Za-z0-9_.-]+", arch):
        raise SystemExit("--arch must be a bounded platform architecture token")

    workspace = tomllib.loads((ROOT / "Cargo.toml").read_text())["workspace"]["package"]
    source_lock = json.loads((ROOT / "SOURCE_LOCK.json").read_text())
    semwright = source_lock["dependencies"]["semwright"]

    records = []
    seen_names: set[str] = set()
    for raw in args.artifact:
        path = pathlib.Path(raw).resolve()
        if not path.exists():
            raise SystemExit(f"candidate artifact does not exist: {raw}")
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f"candidate artifact must be a regular non-symlink file: {raw}")
        if path.name in seen_names:
            raise SystemExit(f"duplicate candidate artifact name: {path.name}")
        seen_names.add(path.name)
        try:
            kind = classify(args.platform, path)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        stat = path.stat()
        if stat.st_size <= 0:
            raise SystemExit(f"candidate artifact is empty: {path.name}")
        records.append(
            {
                "filename": path.name,
                "bundle_kind": kind,
                "bytes": stat.st_size,
                "sha256": sha256(path),
            }
        )

    kinds = {record["bundle_kind"] for record in records}
    required = {"appimage", "deb"} if args.platform == "linux" else ({"nsis"} if args.platform == "windows" else {"dmg"})
    if kinds != required:
        raise SystemExit(
            f"{args.platform} candidate requires bundle kinds {sorted(required)}, observed {sorted(kinds)}"
        )

    receipt = {
        "schema_version": 1,
        "candidate": {
            "application": "Motionwright",
            "version": workspace["version"],
            "source_revision": git_sha,
            "semwright_revision": semwright["revision"],
            "platform": args.platform,
            "arch": arch,
        },
        "artifacts": sorted(records, key=lambda record: record["filename"].lower()),
        "claims": {
            "published": False,
            "distribution_signature": "NOT_CLAIMED",
            "notarization": "NOT_CLAIMED",
            "human_install_acceptance": "NOT_RUN",
            "runtime_renderer_acceptance": "SEPARATE_GATE",
        },
    }

    output = pathlib.Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(
        f"candidate-receipt={output} platform={args.platform} arch={arch} "
        f"artifacts={len(records)} source={git_sha}"
    )


if __name__ == "__main__":
    main()
