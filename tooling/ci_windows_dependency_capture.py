"""Compose existing dependency verifiers for isolated Windows installation CI.

This helper does not grant redistribution, run Native, or replace package
inspection. Original capture directories are create-only and are never replayed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import candidate_dependency_capture as capture
import candidate_frontend_authentication as frontend
from npm_archive_authentication import relative_path

TARGET = "x86_64-pc-windows-msvc"
BASE = Path("ci-evidence/internal-windows-cargo")
BINARY = Path("target/windows-captured") / TARGET / "release/motionwright-desktop.exe"
FRONTEND = Path("apps/studio/dist")
BOOTSTRAP_SOURCE = "apps/studio/public/desktop-bootstrap.html"
INVENTORY = BASE / "application-inventory.json"
INSTALL_DIRECTORY = "motionwright-nsis-current-user"
MAX_ARCHIVE = 32 * 1024 * 1024
MAX_ARCHIVES = 128 * 1024 * 1024


def require(value, code):
    if not value:
        raise ValueError(code)


def internal_source(environment):
    required = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "seradotcom/motionwright",
        "GITHUB_REF": "refs/heads/integrate/phase1-final-composition-20261027",
        "MW_WINDOWS_CANDIDATE_SCOPE": "windows_installed",
        "GITHUB_RUN_ATTEMPT": "1",
    }
    require(all(environment.get(key) == value for key, value in required.items()),
            "internal_windows_dependency_scope_refused")
    source = environment.get("GITHUB_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", source) is not None
            and re.fullmatch(r"[1-9][0-9]*", environment.get("GITHUB_RUN_ID", "")) is not None,
            "internal_windows_dependency_identity_invalid")
    return source


def read_json(repo, path, limit=16 * 1024 * 1024):
    return capture.parse_json(capture.stable_read(repo, path, limit))


def archive_url(value):
    require(isinstance(value, str) and not re.search(r"[\x00-\x20\x7f\\]", value),
            "npm_archive_url_refused")
    parsed = urllib.parse.urlsplit(value)
    require(parsed.scheme == "https" and parsed.hostname == "registry.npmjs.org"
            and parsed.port in (None, 443) and parsed.username is None
            and parsed.password is None and not parsed.query and not parsed.fragment
            and parsed.path.startswith("/") and parsed.path.endswith(".tgz"),
            "npm_archive_url_refused")
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError("npm_archive_redirect_refused")


def retain_frontend_inputs(repo, source_state, directory, phase):
    """Keep bounded originals before parsing or authenticating their contents."""
    require(phase in ("pre-archive", "pre-recheck"), "frontend_observation_phase_invalid")
    index_name = "assets/licenses/frontend/index.json"
    observation_path = directory / (phase + "-emitted-set.json")
    record = {
        "schema": "motionwright.frontend-input-observation/1",
        "source_sha": source_state["source"], "source_tree": source_state["tree"],
        "phase": phase, "status": "PRE_AUTHENTICATION_OBSERVATION",
        "collection_complete": False, "index": None,
        "npm_authentication": "NOT_VERIFIED",
        "full_package_inventory": "NOT_VERIFIED",
        "redistribution_permission": "NOT_INFERRED",
        "limitations": ["Bounded readback is not an atomic filesystem snapshot."],
    }
    try:
        out = capture.admitted(repo, FRONTEND, directory=True)
        index_raw = capture.stable_read(out, index_name, 16 * 1024 * 1024)
        capture.write_new(directory / (phase + "-index.original.json"), index_raw)
        record["index"] = {"path": index_name, "bytes": len(index_raw),
                           "sha256": capture.sha(index_raw)}
        entries, files = frontend.emitted_snapshot(out, index_name)
        require(files.get(index_name) == index_raw, "frontend_index_changed_during_observation")
        metadata = {
            **record, "collection_complete": True, "entries": dict(sorted(entries.items())),
            "files": {name: {"bytes": len(raw), "sha256": capture.sha(raw)}
                      for name, raw in sorted(files.items())},
        }
        del files
        raw_metadata = capture.encoded(metadata)
        require(len(raw_metadata) <= 16 * 1024 * 1024, "frontend_observation_budget")
        capture.write_new(observation_path, raw_metadata)
        return index_raw
    except (ValueError, capture.CaptureError, OSError, KeyError, TypeError) as error:
        # Retain the original index even when collection or parsing cannot finish.
        # This failure record never presents a partial member set as complete.
        try:
            if not observation_path.exists():
                capture.write_new(observation_path, capture.encoded({
                    **record, "collection_failure": type(error).__name__,
                    "original_exception": "PROPAGATED",
                }))
        except OSError as retention_error:
            error.add_note("Secondary observation recording failed: " + type(retention_error).__name__)
        raise


def authenticate_frontend(repo, source, *, recheck=False, base=None):
    base = BASE if base is None else base
    source_state = capture.source_snapshot(repo, source)
    directory = repo / base / "frontend"
    if not recheck:
        capture.new_directory(repo, directory)
    else:
        capture.admitted(repo, directory, directory=True)
    index_raw = retain_frontend_inputs(
        repo, source_state, directory, "pre-recheck" if recheck else "pre-archive")
    if not recheck:
        archives = capture.new_directory(repo, directory / "archives")
        index = capture.parse_json(index_raw)
        lock = read_json(repo, "apps/studio/package-lock.json")
        require(index.get("source_binding", {}).get("source_sha") == source,
                "frontend_source_sha_mismatch")
        components = index.get("components")
        require(isinstance(components, list) and 0 < len(components) <= 512,
                "frontend_components_invalid")
        mapping, total = {}, 0
        opener = urllib.request.build_opener(NoRedirect())
        for component in components:
            key = relative_path(component.get("lock_key"))
            require(key not in mapping, "frontend_lock_key_repeated")
            locked = lock["packages"][key]
            url = archive_url(locked["resolved"])
            require(component.get("source", {}).get("resolved") == url
                    and component.get("source", {}).get("integrity") == locked["integrity"],
                    "frontend_archive_lock_mismatch")
            name = hashlib.sha256(key.encode("utf-8")).hexdigest() + ".tgz"
            request = urllib.request.Request(url, headers={"User-Agent": "Motionwright-CI"})
            count = 0
            with opener.open(request, timeout=30) as response, (archives / name).open("xb") as output:
                require(response.status == 200 and response.geturl() == url,
                        "npm_archive_response_refused")
                while chunk := response.read(65536):
                    count += len(chunk)
                    total += len(chunk)
                    require(count <= MAX_ARCHIVE and total <= MAX_ARCHIVES,
                            "npm_archive_budget_exceeded")
                    output.write(chunk)
            require(count > 0, "npm_archive_empty")
            mapping[key] = name
        capture.write_new(directory / "archive-map.json", capture.encoded(mapping))
    mapping = read_json(repo, directory / "archive-map.json", 1024 * 1024)
    receipt = directory / ("after-bundle.json" if recheck else "before-build.json")
    report = frontend.authenticate_build(
        repo=repo, expected_source=source, output_directory=FRONTEND,
        archive_root=directory / "archives", archive_map=mapping, receipt_path=receipt,
    )
    require(report.get("frontend_index_sha256") == capture.sha(index_raw),
            "frontend_authenticated_index_differs_from_retained_original")
    bootstrap = [row for row in report.get("public_copies", [])
                 if row.get("path") == "desktop-bootstrap.html"]
    require(report.get("public_copy_source_authentication") ==
            "EXACT_TRACKED_GIT_RAW_SOURCE_BYTES_VERIFIED"
            and len(bootstrap) == 1 and bootstrap[0].get("source_path") == BOOTSTRAP_SOURCE,
            "windows_bootstrap_source_authentication_required")
    if recheck:
        before = read_json(repo, directory / "before-build.json")
        require(report == before, "frontend_changed_between_capture_and_bundle")
    return report


def cargo_binding(repo, source, *, base=None, target=None, binary=None, profile_schema=None):
    base = BASE if base is None else base
    target = TARGET if target is None else target
    binary = BINARY if binary is None else binary
    snapshot = capture.source_snapshot(repo, source)
    complete = read_json(repo, base / "state/capture-complete.json")
    raw = capture.stable_read(repo, base / "resources/cargo/index.json", 16 * 1024 * 1024)
    require(complete.get("source") == source
            and complete.get("cargo_index_sha256") == capture.sha(raw)
            and complete.get("full_package_inventory") == "NOT_VERIFIED",
            "windows_cargo_completion_mismatch")
    result = capture.verify_binary_binding(
        raw, expected_index_sha256=complete["cargo_index_sha256"],
        expected_source=source, expected_source_tree=snapshot["tree"], expected_target=target,
        expected_package="motionwright-desktop", expected_binary="motionwright-desktop",
        binary_root=repo, binary_path=repo / binary,
    )
    require(result["artifact_identities_sha256"] == complete.get("artifact_identities_sha256"),
            "windows_cargo_roster_mismatch")
    custody_raw = capture.stable_read(repo, base / "state/tauri-generated-schemas.json", 1024 * 1024)
    digest = capture.sha(custody_raw)
    require(complete.get("generated_metadata_sha256") == digest
            and capture.parse_json(raw).get("capture", {}).get("generated_metadata_sha256") == digest,
            "windows_generated_custody_mismatch")
    custody = capture.parse_json(custody_raw)
    profile = custody.get("profile", {})
    require(profile.get("source") == source and profile.get("target") == target,
            "windows_generated_custody_source_mismatch")
    if profile_schema is not None:
        require(profile.get("schema") == profile_schema, "cargo_generated_custody_profile_mismatch")
    native_key = "sqlite_native_output_sha256"
    native_capture = capture.parse_json(raw).get("capture", {})
    if custody.get("sqlite_native_output_required") or native_key in complete or native_key in native_capture:
        require(custody.get("sqlite_native_output_required") is True
                and complete.get(native_key) == native_capture.get(native_key),
                "sqlite_native_custody_mismatch")
        native_raw = capture.stable_read(repo, base / "state/cargo-native-outputs.json", 1024 * 1024)
        native_output = capture._load_sibling("cargo_sqlite_native_output").verify(
            capture, native_raw, expected_sha256=complete.get(native_key),
            repo=repo, state=repo / base / "state", expected_source=source,
            expected_tree=snapshot["tree"], expected_target=target)
        original_inputs = native_output["original_inputs"]
        require(original_inputs["metadata.stdout"]["sha256"] == native_capture.get("metadata_sha256")
                and original_inputs["build.stdout"]["sha256"] == native_capture.get("build_stdout_sha256")
                and original_inputs["cargo-artifacts.json"]["sha256"] == complete["artifact_identities_sha256"],
                "sqlite_native_capture_join_mismatch")
    return snapshot, complete, raw, result


def resource_mapping(repo, raw, *, base=None):
    base = BASE if base is None else base
    from candidate_smoke import _verify_cargo_notice_subtree

    config = read_json(repo, "apps/desktop/src-tauri/tauri.conf.json")
    resources = config.get("bundle", {}).get("resources")
    expected = {
        "bundle-licenses/LICENSE": "licenses/motionwright/LICENSE",
        "bundle-licenses/THIRD_PARTY_NOTICES.md": "licenses/THIRD_PARTY_NOTICES.md",
        "bundle-licenses/glib-0.18.5/LICENSE": "licenses/glib-0.18.5/LICENSE",
        "bundle-licenses/glib-0.18.5/COPYRIGHT": "licenses/glib-0.18.5/COPYRIGHT",
    }
    require(resources == expected, "windows_original_notice_mapping_changed")
    require(not config.get("build", {}).get("beforeBundleCommand")
            and not config.get("build", {}).get("features")
            and not config.get("bundle", {}).get("externalBin"),
            "windows_captured_bundle_configuration_changed")
    root = repo / base / "resources"
    _verify_cargo_notice_subtree(capture=capture, index_raw=raw,
                                anchor=root, relative=Path("cargo"))
    index = capture.parse_json(raw)
    names = ["index.json", *("texts/" + digest + ".txt" for digest in sorted(
        {row["sha256"] for component in index["components"] for row in component["notices"]}))]
    mapped = dict(resources)
    for name in names:
        # These are already authenticated Cargo-notice bytes under this
        # checkout. Do not hand Tauri a lexical ../../../ci-evidence source:
        # the NSIS owner deliberately refuses parent components. Tauri's
        # resource map accepts absolute source paths with explicit targets.
        source = root / "cargo" / name
        require(source.is_absolute() and ".." not in source.parts,
                "windows_cargo_resource_source_not_checkout_absolute")
        source_path = source.as_posix()
        mapped[source_path] = "licenses/cargo/" + name
    return mapped


def stage_resources(repo, source, *, base=None, target=None, binary=None, profile_schema=None):
    base = BASE if base is None else base
    snapshot, _, raw, binding = cargo_binding(
        repo, source, base=base, target=target, binary=binary, profile_schema=profile_schema)
    mapped = resource_mapping(repo, raw, base=base)
    require(capture.source_snapshot(repo, source) == snapshot,
            "windows_source_changed_during_resource_staging")
    capture.write_new(repo / base / "tauri-resources.json",
                      capture.encoded({"bundle": {"resources": mapped}}))
    return binding


def application_expectation(repo, source, run_id, install_root):
    """Derive only from source/build originals; never inspect the destination."""
    from candidate_smoke import NOTICE_FILES, MAX_NOTICE_BYTES
    from package_receipt import make_receipt

    require(isinstance(run_id, str) and re.fullmatch(r"[1-9][0-9]*", run_id),
            "windows_inventory_run_invalid")
    require(install_root.is_absolute() and install_root.name == INSTALL_DIRECTORY,
            "windows_inventory_destination_invalid")
    parent = install_root.parent.resolve(strict=True)
    parent_info = parent.stat()
    require(parent.is_dir(), "windows_inventory_destination_parent")
    destination_observation = {
        "locator_sha256": capture.sha(str(parent / INSTALL_DIRECTORY).encode("utf-8")),
        "parent_device": str(parent_info.st_dev), "parent_inode": str(parent_info.st_ino)}
    snapshot, complete, raw, binding = cargo_binding(repo, source)
    mapped = resource_mapping(repo, raw)
    overlay_raw = capture.stable_read(repo, BASE / "tauri-resources.json", 2 * 1024 * 1024)
    require(capture.parse_json(overlay_raw) == {"bundle": {"resources": mapped}},
            "windows_inventory_overlay_changed")
    files = {"motionwright-desktop.exe": {
        key: binding[key] for key in ("bytes", "sha256")}}
    for original, destination in NOTICE_FILES:
        files[destination] = capture.stable_hash(repo, original, limit=MAX_NOTICE_BYTES)
    files["licenses/cargo/index.json"] = {"bytes": len(raw), "sha256": capture.sha(raw)}
    digests = {row["sha256"] for component in capture.parse_json(raw)["components"]
               for row in component["notices"]}
    require(len(files) + len(digests) <= 4000, "windows_inventory_member_bound")
    for digest in sorted(digests):
        name = "cargo/texts/" + digest + ".txt"
        item = capture.stable_hash(repo, BASE / "resources" / name)
        require(item["sha256"] == digest, "windows_inventory_cargo_text_changed")
        files["licenses/" + name] = item
    require(all(item["bytes"] > 0 for item in files.values())
            and sum(item["bytes"] for item in files.values()) < 512 * 1024 * 1024,
            "windows_inventory_byte_bound")
    receipt_path = Path("ci-evidence/windows-candidate.json")
    receipt_raw = capture.stable_read(repo, receipt_path, 1024 * 1024)
    receipt = capture.parse_json(receipt_raw)
    artifacts = receipt.get("artifacts")
    require(isinstance(artifacts, list) and len(artifacts) == 1,
            "windows_inventory_candidate_count")
    filename = relative_path(artifacts[0].get("filename"))
    require("/" not in filename, "windows_inventory_setup_filename")
    artifact = repo / BINARY.parent / "bundle/nsis" / filename
    rebuilt = make_receipt(
        repo, source, "windows", "x86_64", [artifact],
        cargo_index=repo / BASE / "resources/cargo/index.json",
        cargo_binary=repo / BINARY, require_cargo_binding=True)
    require(receipt == rebuilt, "windows_inventory_candidate_changed")
    require(capture.source_snapshot(repo, source) == snapshot
            and capture.stable_read(repo, receipt_path, 1024 * 1024) == receipt_raw,
            "windows_inventory_source_changed")
    return {
        "schema": "motionwright.windows-application-expectation/1",
        "source": source, "source_tree": snapshot["tree"], "target": TARGET,
        "workflow_run_id": run_id, "workflow_run_attempt": 1,
        "install_directory_name": INSTALL_DIRECTORY, "install_destination": destination_observation,
        "candidate_receipt": {"bytes": len(receipt_raw), "sha256": capture.sha(receipt_raw)},
        "setup": {key: artifacts[0][key] for key in ("filename", "bytes", "sha256")},
        "cargo_index_sha256": complete["cargo_index_sha256"],
        "generated_metadata_sha256": complete["generated_metadata_sha256"],
        "resource_overlay_sha256": capture.sha(overlay_raw), "application_files": files,
        "pending_generated_members": {"uninstall.exe": {
            "expected_role": "INSTALLER_GENERATED_SELF",
            "reference_status": "NOT_OBSERVED_BEFORE_PUBLICATION"}},
        "full_package_inventory": "NOT_VERIFIED",
    }


def static_reader_directory(repo, run_id):
    require(re.fullmatch(r"[1-9][0-9]*", run_id) is not None,
            "windows_static_reader_run_invalid")
    runner = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    require(not runner.is_relative_to(repo.resolve(strict=True))
            and not repo.resolve(strict=True).is_relative_to(runner),
            "windows_static_reader_private_root")
    return runner / ("motionwright-nsis-reader-" + run_id + "-1")


def provision_static_reader(repo, run_id):
    import shutil
    import nsis_static_uninstaller as static

    require(os.name == "nt", "windows_static_reader_platform")
    executable = static.resolve_bootstrap(
        capture, program_files=os.environ.get("ProgramFiles"),
        path_executable=shutil.which("7z.exe"))
    return static.provision_reader(
        capture=capture, directory=static_reader_directory(repo, run_id), bootstrap=executable)


def static_uninstaller_reference(repo, expected, *, prepare=False, expected_reference_sha256=None):
    import nsis_static_uninstaller as static

    run_id = expected["workflow_run_id"]
    reader_directory = static_reader_directory(repo, run_id)
    runner = reader_directory.parent
    observations = repo / BASE / "uninstaller"
    private_payload = runner / ("motionwright-uninstaller-" + run_id + "-1")
    context = {
        "source": expected["source"], "source_tree": expected["source_tree"],
        "run_id": run_id, "run_attempt": expected["workflow_run_attempt"],
        "base_inventory_sha256": capture.sha(capture.encoded(expected)),
        "setup": expected["setup"],
    }
    setup = repo / BINARY.parent / "bundle/nsis" / expected["setup"]["filename"]
    args = dict(capture=capture, repo=repo, setup=setup, observations=observations,
                private_root=runner, private_payload=private_payload, context=context)
    if prepare:
        intake = capture.stable_read(reader_directory, "intake.json", 1024 * 1024)
        capture.write_new(repo / BASE / "uninstaller-reader-intake.json", intake)
        return static.observe(reader=reader_directory / "7z.exe", **args)
    return static.verify(expected_reference_sha256=expected_reference_sha256, **args)


def with_static_uninstaller(expected, reference):
    return {
        **expected, "schema": "motionwright.windows-application-expectation/2",
        "pending_generated_members": {}, "generated_members": {"uninstall.exe": reference},
    }


def prepare_application_inventory(repo, source, run_id, install_root, *, static_uninstaller=False):
    require(install_root.is_absolute() and install_root.name == INSTALL_DIRECTORY,
            "windows_inventory_destination_invalid")
    try:
        install_root.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError("windows_inventory_destination_preexisting")
    expected = application_expectation(repo, source, run_id, install_root)
    if static_uninstaller:
        reference = static_uninstaller_reference(repo, expected, prepare=True)
        expected = with_static_uninstaller(expected, reference)
    raw = capture.encoded(expected)
    capture.write_new(repo / INVENTORY, raw)
    return capture.sha(raw)


def verify_application_inventory(repo, source, run_id, install_root, expected_sha256,
                                 *, static_uninstaller=False):
    from candidate_smoke import verify_windows_application_files

    require(isinstance(expected_sha256, str)
            and re.fullmatch(r"[0-9a-f]{64}", expected_sha256),
            "windows_inventory_preinstall_digest_required")
    require(install_root.is_absolute() and install_root.name == INSTALL_DIRECTORY,
            "windows_inventory_destination_invalid")
    raw = capture.stable_read(repo, INVENTORY, 2 * 1024 * 1024)
    require(capture.sha(raw) == expected_sha256, "windows_inventory_original_changed")
    expected = capture.parse_json(raw)
    rebuilt = application_expectation(repo, source, run_id, install_root)
    reference = None
    if static_uninstaller:
        sealed = expected.get("generated_members", {}).get("uninstall.exe", {})
        reference = static_uninstaller_reference(
            repo, rebuilt, expected_reference_sha256=sealed.get("reference_sha256"))
        rebuilt = with_static_uninstaller(rebuilt, reference)
    require(expected == rebuilt, "windows_inventory_expectation_mismatch")
    result = verify_windows_application_files(
        capture=capture, install_root=install_root, expected_files=expected["application_files"],
        expected_uninstaller=reference)
    require(capture.stable_read(repo, INVENTORY, 2 * 1024 * 1024) == raw,
            "windows_inventory_original_changed")
    result["preinstall_expectation_sha256"] = expected_sha256
    return result


def verify_installed(repo, source, *, run_id, install_root, expected_inventory_sha256,
                     static_uninstaller=False):
    _, complete, _, observed = cargo_binding(repo, source)
    smoke = read_json(repo, "ci-evidence/windows-nsis-smoke.json")
    require(smoke.get("candidate", {}).get("source_revision") == source,
            "windows_installed_source_mismatch")
    installed = smoke.get("automated_inspection", {}).get("cargo_build_binding", {})
    require(installed.get("status") == "CAPTURED_ROOT_BINARY_BYTE_EXACT",
            "windows_installed_cargo_binding_required")
    for key, value in observed.items():
        require(installed.get(key) == value,
                "windows_installed_cargo_identity_mismatch")
    notices = installed.get("notice_resources", {})
    require(notices.get("status") == "DECLARED_CARGO_TEXTS_PRESENT_BYTE_EXACT"
            and notices.get("scope") == "CARGO_NOTICE_SUBTREE_ONLY"
            and notices.get("subtree_closed") is True
            and notices.get("full_package_inventory") == "NOT_VERIFIED",
            "windows_installed_cargo_notices_required")
    frontend_report = read_json(repo, BASE / "frontend/after-bundle.json")
    require(frontend_report.get("source_sha") == source
            and frontend_report.get("source_tree") == observed["source_tree"]
            and frontend_report.get("observed_npm_source_and_notice_authentication") == "VERIFIED"
            and frontend_report.get("source_binding") == "EXACT_HEAD_TREE_INDEX_AND_TRACKED_BYTES_REVALIDATED",
            "windows_frontend_authentication_required")
    application = verify_application_inventory(
        repo, source, run_id, install_root, expected_inventory_sha256,
        static_uninstaller=static_uninstaller)
    inventory = read_json(repo, INVENTORY, 2 * 1024 * 1024)
    candidate = read_json(repo, "ci-evidence/windows-candidate.json", 1024 * 1024)
    require(smoke.get("candidate_archive") == inventory["setup"]
            and smoke.get("candidate") == candidate["candidate"],
            "windows_installed_inventory_package_mismatch")
    report = {
        "schema": "motionwright.windows-installed-cargo-binding/1",
        "source": source, "source_tree": observed["source_tree"], "target": TARGET,
        "cargo_index_sha256": complete["cargo_index_sha256"],
        "artifact_identities_sha256": complete["artifact_identities_sha256"],
        "installed_binary": {"bytes": observed["bytes"], "sha256": observed["sha256"]},
        "cargo_binding": installed["status"], "cargo_notices": notices,
        "frontend_index_sha256": frontend_report["frontend_index_sha256"],
        "frontend_observed_npm_authentication": "VERIFIED",
        "application_payload": application,
        "full_package_inventory": "NOT_VERIFIED", "human_install_acceptance": "NOT_RUN",
        "distribution_signature": "NOT_CLAIMED", "published": False,
    }
    capture.write_new(repo / "ci-evidence/windows-cargo-binding.json", capture.encoded(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("reader", "frontend", "frontend-recheck", "stage", "inventory", "verify"))
    parser.add_argument("--installed-root", type=Path)
    parser.add_argument("--expected-inventory-sha256")
    parser.add_argument("--require-static-uninstaller", action="store_true")
    args = parser.parse_args(argv)
    try:
        source = internal_source(os.environ)
        require(Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True) == ROOT,
                "windows_dependency_checkout_mismatch")
        if args.phase in ("reader", "frontend", "frontend-recheck", "stage"):
            require(args.installed_root is None and args.expected_inventory_sha256 is None
                    and not args.require_static_uninstaller, "windows_inventory_unexpected_inputs")
        if args.phase == "reader":
            provision_static_reader(ROOT, os.environ["GITHUB_RUN_ID"])
        elif args.phase.startswith("frontend"):
            authenticate_frontend(ROOT, source, recheck=args.phase == "frontend-recheck")
        elif args.phase == "stage":
            stage_resources(ROOT, source)
        else:
            runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
            destination = runner_temp / INSTALL_DIRECTORY
            if args.phase == "inventory":
                require(args.installed_root is None and args.expected_inventory_sha256 is None,
                        "windows_inventory_unexpected_inputs")
                prepare_application_inventory(
                    ROOT, source, os.environ["GITHUB_RUN_ID"], destination,
                    static_uninstaller=args.require_static_uninstaller)
            else:
                require(args.installed_root is not None and args.installed_root.is_absolute()
                        and args.installed_root.name == INSTALL_DIRECTORY
                        and args.installed_root.parent.resolve(strict=True) == runner_temp,
                        "windows_inventory_original_destination_required")
                verify_installed(
                    ROOT, source, run_id=os.environ["GITHUB_RUN_ID"], install_root=destination,
                    expected_inventory_sha256=args.expected_inventory_sha256,
                    static_uninstaller=args.require_static_uninstaller)
        print("windows-dependency-" + args.phase + "=verified public_distribution=NOT_AUTHORIZED")
    except (capture.CaptureError, OSError, ValueError, KeyError, TypeError) as error:
        detail = str(error)
        code = detail if re.fullmatch(r"[a-z][a-z0-9_]{0,95}", detail) else type(error).__name__
        print("windows-dependency-" + args.phase + "-failed:" + code, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
