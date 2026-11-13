"""Authenticate one isolated ARM64 macOS Cargo build and its copied DMG payload.

This existing macos_bundle development scope grants no signing, notarization,
GUI/Native acceptance, redistribution permission or complete package inventory.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys

import ci_windows_dependency_capture as shared

ROOT = Path(__file__).resolve().parents[1]
capture = shared.capture
require = shared.require
TARGET = "aarch64-apple-darwin"
BASE = Path("ci-evidence/macos-cargo")
BINARY = Path("target/macos-captured") / TARGET / "release/motionwright-desktop"
PROFILE = "motionwright.macos-cargo-generated-metadata/1"
LAYOUT = {"base": BASE, "target": TARGET, "binary": BINARY, "profile_schema": PROFILE}


def macos_source(environment):
    expected = {
        "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "seradotcom/motionwright",
        "GITHUB_REF": "refs/heads/integrate/phase1-final-composition-20261027",
        "MW_MACOS_CANDIDATE_SCOPE": "macos_bundle", "GITHUB_RUN_ATTEMPT": "1",
        "RUNNER_OS": "macOS", "RUNNER_ARCH": "ARM64",
    }
    require(all(environment.get(key) == value for key, value in expected.items()),
            "macos_dependency_scope_refused")
    source = environment.get("GITHUB_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", source) is not None
            and re.fullmatch(r"[1-9][0-9]*", environment.get("GITHUB_RUN_ID", "")) is not None,
            "macos_dependency_identity_invalid")
    return source


def cargo_binding(repo, source):
    return shared.cargo_binding(repo, source, **LAYOUT)


def stage_resources(repo, source):
    return shared.stage_resources(repo, source, **LAYOUT)


def verified_overlay(repo, raw):
    mapped = shared.resource_mapping(repo, raw, base=BASE)
    overlay = capture.stable_read(repo, BASE / "tauri-resources.json", 2 * 1024 * 1024)
    require(capture.parse_json(overlay) == {"bundle": {"resources": mapped}},
            "macos_cargo_overlay_changed")
    return overlay


def bundle_dmg(repo, source, environment):
    before = cargo_binding(repo, source)
    overlay = verified_overlay(repo, before[2])
    command = [
        "npm", "--prefix", "../studio", "exec", "--", "tauri", "bundle",
        "--target", TARGET, "--features", "tauri/custom-protocol", "--bundles", "dmg",
        "--config", "../../" + (BASE / "tauri-resources.json").as_posix(),
        "--no-binary-patching", "--no-sign", "--ci",
    ]
    capture.run_captured(command, repo / "apps/desktop", repo / BASE, "bundle",
                         env=environment, timeout=900)
    require(cargo_binding(repo, source) == before
            and verified_overlay(repo, before[2]) == overlay,
            "macos_captured_bundle_input_changed")


def verify_dmg(repo, source):
    from candidate_smoke import _verify_cargo_notice_subtree
    from npm_archive_authentication import relative_path
    from package_receipt import make_receipt

    before = cargo_binding(repo, source)
    snapshot, complete, raw, observed = before
    overlay = verified_overlay(repo, raw)
    candidate_raw = capture.stable_read(repo, "ci-evidence/macos-candidate.json", 1024 * 1024)
    candidate = capture.parse_json(candidate_raw)
    rows = candidate.get("artifacts")
    require(isinstance(rows, list) and len(rows) == 1 and rows[0].get("bundle_kind") == "dmg",
            "macos_candidate_dmg_required")
    row = rows[0]
    name = relative_path(row.get("filename"))
    require("/" not in name, "macos_candidate_filename_invalid")
    artifact = repo / BINARY.parent / "bundle/dmg" / name
    rebuilt = make_receipt(
        repo, source, "macos", "arm64", [artifact],
        cargo_index=repo / BASE / "resources/cargo/index.json",
        cargo_binary=repo / BINARY, require_cargo_binding=True)
    require(candidate == rebuilt, "macos_candidate_changed")
    smoke_raw = capture.stable_read(repo, "ci-evidence/macos-dmg-smoke.json", 2 * 1024 * 1024)
    smoke = capture.parse_json(smoke_raw)
    inspection = smoke.get("automated_inspection", {})
    require(smoke.get("schema_version") == 1 and smoke.get("bundle_kind") == "dmg"
            and smoke.get("candidate") == candidate["candidate"]
            and smoke.get("candidate_archive") == {
                key: row[key] for key in ("filename", "bytes", "sha256")}
            and inspection.get("status") == "PASS"
            and inspection.get("method") == "dmg_readonly_mount_copy",
            "macos_package_observation_mismatch")
    notices = _verify_cargo_notice_subtree(
        capture=capture, index_raw=raw, anchor=repo / BASE / "resources", relative=Path("cargo"))
    require(inspection.get("cargo_build_binding") == {**observed, "notice_resources": notices},
            "macos_package_cargo_binding_mismatch")
    identity = inspection.get("binary", {})
    require(identity.get("binary_bytes") == observed["bytes"]
            and identity.get("binary_sha256") == observed["sha256"],
            "macos_extracted_binary_identity_mismatch")
    require(smoke.get("claims") == {
                "human_install_acceptance": "NOT_RUN", "runtime_renderer_acceptance": "SEPARATE_GATE",
                "signing": "NOT_CLAIMED", "notarization": "NOT_CLAIMED", "release_published": False}
            and smoke["claims"]["release_published"] is False,
            "macos_package_claims_changed")
    frontend = shared.read_json(repo, BASE / "frontend/after-bundle.json")
    require(frontend == shared.read_json(repo, BASE / "frontend/before-build.json")
            and frontend.get("source_sha") == source and frontend.get("source_tree") == snapshot["tree"]
            and frontend.get("observed_npm_source_and_notice_authentication") == "VERIFIED"
            and frontend.get("source_binding") == "EXACT_HEAD_TREE_INDEX_AND_TRACKED_BYTES_REVALIDATED",
            "macos_frontend_authentication_required")
    require(cargo_binding(repo, source) == before and verified_overlay(repo, raw) == overlay
            and capture.stable_read(repo, "ci-evidence/macos-candidate.json", 1024 * 1024) == candidate_raw,
            "macos_package_inputs_changed_during_readback")
    report = {
        "schema": "motionwright.macos-packaged-cargo-binding/1",
        "source": source, "source_tree": snapshot["tree"], "target": TARGET,
        "cargo_index_sha256": complete["cargo_index_sha256"],
        "artifact_identities_sha256": complete["artifact_identities_sha256"],
        "generated_metadata_sha256": complete["generated_metadata_sha256"],
        "captured_binary": {key: observed[key] for key in ("bytes", "sha256")},
        "archive": smoke["candidate_archive"], "smoke_sha256": capture.sha(smoke_raw),
        "cargo_binding": observed["status"], "cargo_notices": notices,
        "frontend_index_sha256": frontend["frontend_index_sha256"],
        "full_package_inventory": "NOT_VERIFIED", "human_install_acceptance": "NOT_RUN",
        "native_renderer_execution": "NOT_RUN", "distribution_signature": "NOT_CLAIMED",
        "notarization": "NOT_CLAIMED", "redistribution_permission": "NOT_INFERRED", "published": False,
    }
    capture.write_new(repo / "ci-evidence/macos-cargo-binding.json", capture.encoded(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("frontend", "stage", "bundle", "frontend-recheck", "verify"))
    args = parser.parse_args(argv)
    try:
        source = macos_source(os.environ)
        require(Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True) == ROOT,
                "macos_dependency_checkout_mismatch")
        if args.phase.startswith("frontend"):
            shared.authenticate_frontend(ROOT, source, base=BASE,
                                         recheck=args.phase == "frontend-recheck")
        elif args.phase == "stage":
            stage_resources(ROOT, source)
        elif args.phase == "bundle":
            bundle_dmg(ROOT, source, dict(os.environ))
        else:
            verify_dmg(ROOT, source)
        print("macos-dependency-" + args.phase + "=verified formal_acceptance=NOT_GRANTED")
    except (capture.CaptureError, OSError, ValueError, KeyError, TypeError) as error:
        detail = str(error)
        code = detail if re.fullmatch(r"[a-z][a-z0-9_]{0,95}", detail) else type(error).__name__
        print("macos-dependency-" + args.phase + "-failed:" + code, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
