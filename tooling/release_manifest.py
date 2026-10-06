#!/usr/bin/env python3
import argparse
import hashlib
import json
import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


parser = argparse.ArgumentParser(description="Build an exact-source Motionwright delivery manifest.")
parser.add_argument("--git-sha", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()

git_sha = args.git_sha.strip().lower()
if not re.fullmatch(r"[0-9a-f]{40}", git_sha):
    raise SystemExit("--git-sha must be an exact 40-character commit SHA")

workspace = tomllib.loads((ROOT / "Cargo.toml").read_text())["workspace"]["package"]
source_lock = json.loads((ROOT / "SOURCE_LOCK.json").read_text())
semwright = source_lock["dependencies"]["semwright"]
if not re.fullmatch(r"[0-9a-f]{40}", semwright["revision"]):
    raise SystemExit("SOURCE_LOCK Semwright revision is not an exact commit SHA")

inputs = [
    "Cargo.lock",
    "SOURCE_LOCK.json",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "apps/studio/package-lock.json",
]
missing = [path for path in inputs if not (ROOT / path).is_file()]
if missing:
    raise SystemExit("delivery manifest inputs missing: " + ", ".join(missing))

manifest = {
    "schema_version": 1,
    "motionwright": {
        "name": "Motionwright",
        "repository": workspace["repository"],
        "version": workspace["version"],
        "license": workspace["license"],
        "source_revision": git_sha,
    },
    "semwright": {
        "repository": semwright["repository"],
        "revision": semwright["revision"],
        "native_sdk_package": semwright["native_sdk_package"],
        "license_observed": semwright["license_observed"],
        "features_planned": semwright["features_planned"],
    },
    "locked_inputs": {
        path: {"sha256": sha256(ROOT / path)}
        for path in inputs
    },
    "delivery_boundary": {
        "automatic_publish": False,
        "hidden_runtime_downloads": False,
        "project_receipts_exported": False,
        "secrets_expected_in_project_bundle": False,
        "portable_project_format": "motionwright-directory-bundle",
    },
}

output = pathlib.Path(args.output)
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
print(f"delivery-manifest={output} source={git_sha} semwright={semwright['revision']}")
