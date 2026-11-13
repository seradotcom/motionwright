"""Compose real Linux Cargo capture with exact DEB/AppImage inspection.

Only the existing isolated linux_bundle CI scope is admitted. This does not
grant redistribution, full dependency closure, Native execution or acceptance.
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
TARGET = "x86_64-unknown-linux-gnu"
BASE = Path("ci-evidence/linux-cargo")
BINARY = Path("target/linux-captured") / TARGET / "release/motionwright-desktop"
PROFILE = "motionwright.linux-cargo-generated-metadata/1"
RUNPATH = "$ORIGIN/../lib"
RUSTFLAGS = "-C link-arg=-Wl,--enable-new-dtags,-rpath," + RUNPATH
PATCHELF = "/usr/bin/patchelf"
LAYOUT = {"base": BASE, "target": TARGET, "binary": BINARY, "profile_schema": PROFILE}


def linux_source(environment):
    required = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "seradotcom/motionwright",
        "GITHUB_REF": "refs/heads/integrate/phase1-final-composition-20261027",
        "MW_LINUX_CANDIDATE_SCOPE": "linux_bundle",
        "GITHUB_RUN_ATTEMPT": "1",
    }
    require(all(environment.get(key) == value for key, value in required.items()),
            "linux_dependency_scope_refused")
    source = environment.get("GITHUB_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", source) is not None
            and re.fullmatch(r"[1-9][0-9]*", environment.get("GITHUB_RUN_ID", "")) is not None,
            "linux_dependency_identity_invalid")
    return source


def link_environment(environment):
    expected = {"RUSTFLAGS": RUSTFLAGS, "PATCHELF": PATCHELF, "NO_STRIP": "1"}
    require(all(environment.get(key) == value for key, value in expected.items())
            and not environment.get("CARGO_ENCODED_RUSTFLAGS")
            and not environment.get("CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS"),
            "linux_captured_link_environment_mismatch")
    return expected


def cargo_binding(repo, source):
    return shared.cargo_binding(repo, source, **LAYOUT)


def stage_resources(repo, source):
    return shared.stage_resources(repo, source, **LAYOUT)


def observe_link_layout(repo, source, runner_temp, environment):
    """Observe real patchelf on a disposable copy; never modify Cargo output."""
    selected_environment = link_environment(environment)
    snapshot, _, _, binary = cargo_binding(repo, source)
    tool = Path(PATCHELF)
    require(tool.resolve(strict=True) == tool, "linux_patchelf_redirected")
    tool_identity = capture.stable_hash(tool.parent, tool)
    directory = capture.new_directory(repo, repo / BASE / "link-layout")
    scratch = capture.new_directory(runner_temp, runner_temp / "motionwright-linux-link-layout")
    copied = scratch / "motionwright-desktop"
    original = capture.stable_read(repo, BINARY)
    require({"bytes": len(original), "sha256": capture.sha(original)}
            == {key: binary[key] for key in ("bytes", "sha256")},
            "linux_link_input_changed")
    capture.write_new(copied, original)
    del original
    commands = {
        "version": [PATCHELF, "--version"],
        "runpath": [PATCHELF, "--print-rpath", str(copied)],
        "same-runpath": [PATCHELF, "--set-rpath", RUNPATH, str(copied)],
    }
    observations = {}
    for label, command in commands.items():
        observations[label] = capture.run_captured(
            command, repo, directory, label, env=environment,
            stream_limit=64 * 1024, timeout=20)
        if label == "runpath":
            require(capture.stable_read(directory, "runpath.stdout", 4096)
                    == (RUNPATH + "\n").encode(), "linux_compiled_runpath_mismatch")
    observed = capture.stable_hash(scratch, copied)
    expected = {key: binary[key] for key in ("bytes", "sha256")}
    require(observed == expected, "linux_patchelf_changed_captured_copy")
    require(capture.stable_hash(repo, BINARY) == expected
            and capture.stable_hash(tool.parent, tool) == tool_identity
            and capture.source_snapshot(repo, source) == snapshot,
            "linux_link_probe_input_changed")
    record = {
        "schema": "motionwright.linux-captured-link-layout/1",
        "source": source, "source_tree": snapshot["tree"], "target": TARGET,
        "status": "SAME_RUNPATH_PRESERVES_CAPTURED_COPY",
        "binary": expected, "runpath": RUNPATH,
        "environment": selected_environment, "patchelf": tool_identity,
        "observations": observations,
        "package_byte_identity": "SEPARATE_EXTRACTION_GATE",
    }
    capture.write_new(directory / "result.json", capture.encoded(record))
    return record


def bundle_packages(repo, source, environment):
    """Package the verified captured executable without another Cargo build."""
    link_environment(environment)
    before = cargo_binding(repo, source)
    mapped = shared.resource_mapping(repo, before[2], base=BASE)
    overlay = capture.stable_read(repo, BASE / "tauri-resources.json", 2 * 1024 * 1024)
    require(capture.parse_json(overlay) == {"bundle": {"resources": mapped}},
            "linux_cargo_overlay_changed")
    command = [
        "npm", "--prefix", "../studio", "exec", "--", "tauri", "bundle",
        "--target", TARGET, "--features", "tauri/custom-protocol",
        "--bundles", "deb,appimage",
        "--config", "../../" + (BASE / "tauri-resources.json").as_posix(),
        "--no-binary-patching", "--no-sign", "--ci",
    ]
    capture.run_captured(command, repo / "apps/desktop", repo / BASE, "bundle",
                         env=environment, timeout=900)
    require(cargo_binding(repo, source) == before
            and capture.stable_read(repo, BASE / "tauri-resources.json", 2 * 1024 * 1024)
                == overlay, "linux_captured_bundle_input_changed")


def verify_packages(repo, source):
    """Join each real extraction report to the same original Cargo capture."""
    from candidate_smoke import _verify_cargo_notice_subtree
    from package_receipt import make_receipt
    from npm_archive_authentication import relative_path

    original_binding = cargo_binding(repo, source)
    snapshot, complete, raw, observed = original_binding
    mapped = shared.resource_mapping(repo, raw, base=BASE)
    overlay = capture.stable_read(repo, BASE / "tauri-resources.json", 2 * 1024 * 1024)
    require(capture.parse_json(overlay) == {"bundle": {"resources": mapped}},
            "linux_cargo_overlay_changed")
    original_receipt = capture.stable_read(repo, "ci-evidence/linux-candidate.json", 1024 * 1024)
    candidate = capture.parse_json(original_receipt)
    rows = candidate.get("artifacts")
    require(isinstance(rows, list) and len(rows) == 2
            and {row.get("bundle_kind") for row in rows} == {"deb", "appimage"},
            "linux_candidate_pair_required")
    artifacts = []
    for row in rows:
        name = relative_path(row.get("filename"))
        require("/" not in name, "linux_candidate_filename_invalid")
        artifacts.append(repo / BINARY.parent / "bundle" / row["bundle_kind"] / name)
    rebuilt = make_receipt(
        repo, source, "linux", "x86_64", artifacts,
        cargo_index=repo / BASE / "resources/cargo/index.json",
        cargo_binary=repo / BINARY, require_cargo_binding=True)
    require(candidate == rebuilt, "linux_candidate_changed")
    expected_notices = _verify_cargo_notice_subtree(
        capture=capture, index_raw=raw, anchor=repo / BASE / "resources",
        relative=Path("cargo"))
    packages = {}
    for row in rows:
        kind = row["bundle_kind"]
        smoke_raw = capture.stable_read(repo, "ci-evidence/linux-" + kind + "-smoke.json",
                                        2 * 1024 * 1024)
        smoke = capture.parse_json(smoke_raw)
        inspection = smoke.get("automated_inspection", {})
        require(smoke.get("schema_version") == 1 and smoke.get("bundle_kind") == kind
                and smoke.get("candidate") == candidate["candidate"]
                and smoke.get("candidate_archive") == {
                    key: row[key] for key in ("filename", "bytes", "sha256")}
                and inspection.get("status") == "PASS"
                and inspection.get("method") == kind + "_extract",
                "linux_package_observation_mismatch")
        require(inspection.get("cargo_build_binding") == {
                    **observed, "notice_resources": expected_notices},
                "linux_package_cargo_binding_mismatch")
        identity = inspection.get("binary", {})
        require(identity.get("binary_bytes") == observed["bytes"]
                and identity.get("binary_sha256") == observed["sha256"],
                "linux_extracted_binary_identity_mismatch")
        require(smoke.get("claims") == {
                    "human_install_acceptance": "NOT_RUN",
                    "runtime_renderer_acceptance": "SEPARATE_GATE",
                    "signing": "NOT_CLAIMED", "notarization": "NOT_CLAIMED",
                    "release_published": False},
                "linux_package_claims_changed")
        packages[kind] = {
            "archive": smoke["candidate_archive"],
            "smoke_sha256": capture.sha(smoke_raw),
            "binary_binding": observed["status"], "cargo_notices": expected_notices,
        }
    frontend = shared.read_json(repo, BASE / "frontend/after-bundle.json")
    require(frontend == shared.read_json(repo, BASE / "frontend/before-build.json")
            and frontend.get("source_sha") == source
            and frontend.get("source_tree") == snapshot["tree"]
            and frontend.get("observed_npm_source_and_notice_authentication") == "VERIFIED"
            and frontend.get("source_binding") == "EXACT_HEAD_TREE_INDEX_AND_TRACKED_BYTES_REVALIDATED",
            "linux_frontend_authentication_required")
    require(cargo_binding(repo, source) == original_binding
            and capture.stable_read(repo, "ci-evidence/linux-candidate.json", 1024 * 1024)
                == original_receipt
            and capture.stable_read(repo, BASE / "tauri-resources.json", 2 * 1024 * 1024) == overlay,
            "linux_package_inputs_changed_during_readback")
    report = {
        "schema": "motionwright.linux-packaged-cargo-binding/1",
        "source": source, "source_tree": snapshot["tree"], "target": TARGET,
        "cargo_index_sha256": complete["cargo_index_sha256"],
        "artifact_identities_sha256": complete["artifact_identities_sha256"],
        "generated_metadata_sha256": complete["generated_metadata_sha256"],
        "captured_binary": {key: observed[key] for key in ("bytes", "sha256")},
        "packages": packages,
        "frontend_index_sha256": frontend["frontend_index_sha256"],
        "frontend_observed_npm_authentication": "VERIFIED",
        "full_package_inventory": "NOT_VERIFIED", "human_install_acceptance": "NOT_RUN",
        "native_renderer_execution": "NOT_RUN", "distribution_signature": "NOT_CLAIMED",
        "redistribution_permission": "NOT_INFERRED", "published": False,
    }
    capture.write_new(repo / "ci-evidence/linux-cargo-binding.json", capture.encoded(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("frontend", "stage", "bundle", "frontend-recheck", "verify"))
    args = parser.parse_args(argv)
    try:
        source = linux_source(os.environ)
        require(Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True) == ROOT,
                "linux_dependency_checkout_mismatch")
        link_environment(os.environ)
        if args.phase.startswith("frontend"):
            shared.authenticate_frontend(ROOT, source, base=BASE,
                                         recheck=args.phase == "frontend-recheck")
        elif args.phase == "stage":
            observe_link_layout(ROOT, source, Path(os.environ["RUNNER_TEMP"]).resolve(strict=True),
                                dict(os.environ))
            stage_resources(ROOT, source)
        elif args.phase == "bundle":
            bundle_packages(ROOT, source, dict(os.environ))
        else:
            verify_packages(ROOT, source)
        print("linux-dependency-" + args.phase + "=verified formal_acceptance=NOT_GRANTED")
    except (capture.CaptureError, OSError, ValueError, KeyError, TypeError) as error:
        detail = str(error)
        code = detail if re.fullmatch(r"[a-z][a-z0-9_]{0,95}", detail) else type(error).__name__
        print("linux-dependency-" + args.phase + "-failed:" + code, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
