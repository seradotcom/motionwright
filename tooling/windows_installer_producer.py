#!/usr/bin/env python3
"""One producer for source-bound Windows installer observations.

Effects are available only through explicit disposable Windows job commands.
Readonly custody, checkpoint and lifecycle readers remain separate modules.
"""
from __future__ import annotations

import argparse


def prepare_predecessor():
    import hashlib
    import json
    import os
    from pathlib import Path
    import re
    import shutil
    import stat
    import subprocess
    import sys
    import urllib.error
    import urllib.parse
    import urllib.request
    import zipfile

    sys.path.insert(0, str(Path.cwd() / "tooling"))
    from nsis_owned_files import _capture, emit_predecessor_include
    from package_receipt import verified_artifact
    from release_manifest import _checkout, _path_has_reparse
    from windows_cold_start import native_windows_launch_environment

    ROOT = Path(os.environ["GITHUB_WORKSPACE"])
    SOURCE = os.environ["GITHUB_SHA"]
    WORK = Path(os.environ["RUNNER_TEMP"]) / "motionwright-nsis-guarded-update"
    INPUT = WORK / "input"
    PROOF = ROOT / "ci-evidence/windows-nsis-update-inputs.json"
    CHILD_ENV = native_windows_launch_environment(os.environ)
    CHILD_ENV.pop("GH_TOKEN", None)
    CHILD_ENV.pop("GITHUB_TOKEN", None)
    FIXED = json.loads(r'''{"run":39032220978,"source":"679cc48baf7c6f9bb4a0b803508c9b2a9891d944","tree":"4ce7c4423c73da4f3af638427088ee6e962d12d5","packageArtifact":{"id":12101032310,"bytes":7134483,"sha256":"740ff6b52b1c655755f693ab9e620b36e15d5d5b642199f38e9a122d1b28155e"},"evidenceArtifact":{"id":12101216495,"bytes":125856,"sha256":"cab9c044019be1909162b9541b21f9a34bc46a44b4406a6bbda5b23aaf4f7e55"},"package":{"member":"target/release/bundle/nsis/Motionwright_0.1.0_x64-setup.exe","bytes":7149373,"sha256":"c071870c1c5f12f1728389f526471bee43b23eb7ad0bae3410147349a8da9f46"},"main":{"member":"motionwright-desktop.exe","bytes":32794112,"sha256":"4c1f8f492a505340ec3ca8e521254ff753a01875c92052a48c0dc42ad1314de8"},"self":{"member":"unchanged-control-original-uninstaller.exe","bytes":79338,"sha256":"dd03a5e87b450ae24950d67d2935dd18f78a27b45e1c52558625d47b60852224"},"receipt":{"member":"receipt.json","bytes":17792,"sha256":"ae15f0e7f21678b9fd950ec46a0b9cfe3601290851744a3d9aea158e39c5193e"}}''')

    def require(ok, code):
        if not ok:
            raise RuntimeError(code)

    def identity(path):
        observed, size, digest = verified_artifact(ROOT, path)
        return {"bytes": size, "sha256": digest}

    def plain_directory(path):
        for item in (path, *path.parents):
            info = item.lstat()
            require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                    and not _path_has_reparse(item, info), "nonordinary_input_ancestor")

    def write_new(path, body):
        with path.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())

    def limited(stream, maximum):
        body = stream.read(maximum + 1)
        require(len(body) <= maximum, "response_exceeds_declared_bound")
        return body

    def api_json(route):
        request = urllib.request.Request(
            API + route,
            headers={"Authorization": "Bearer " + TOKEN, "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"})
        with opener.open(request, timeout=30) as response:
            require(response.status == 200, "actions_api_response_not_ok")
            return json.loads(limited(response, 1024 * 1024))


    from windows_installer_lifecycle import SOURCE_PATHS, read_pe_versions, require_pe_version

    def source_bytes(relative):
        require(relative in SOURCE_PATHS, "source_path_outside_the_explicit_gate_set")
        path = ROOT / relative
        observed, size, digest = verified_artifact(ROOT, path)
        require(size <= 4 * 1024 * 1024, "source_text_exceeds_gate_bound")
        with path.open("rb") as stream:
            body = stream.read(4 * 1024 * 1024 + 1)
        final, final_size, final_digest = verified_artifact(ROOT, path)
        require(len(body) == size and hashlib.sha256(body).hexdigest() == digest
                and (final, final_size, final_digest) == (observed, size, digest),
                "source_text_changed_during_read")
        return body

    def source_observation():
        _checkout(ROOT, SOURCE)
        subprocess.run(["git", "ls-files", "--error-unmatch", "--", *SOURCE_PATHS],
                       cwd=ROOT, check=True, capture_output=True, timeout=10)
        tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT,
                              check=True, capture_output=True, text=True).stdout.strip()
        require(re.fullmatch(r"[0-9a-f]{40}", tree) is not None, "source_tree_invalid")
        files = {}
        for relative in SOURCE_PATHS:
            files[relative] = identity(ROOT / relative)
        _checkout(ROOT, SOURCE)
        return {"checkout": SOURCE, "tree": tree, "files": files}

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, new_url):
            return None

    def fetch_artifact(label, record):
        meta = api_json("/actions/artifacts/" + str(record["id"]))
        run = meta.get("workflow_run", {})
        require(meta.get("id") == record["id"] and meta.get("expired") is False
                and meta.get("size_in_bytes") == record["bytes"]
                and meta.get("digest") == "sha256:" + record["sha256"]
                and run.get("id") == FIXED["run"] and run.get("head_sha") == FIXED["source"]
                and run.get("repository_id") == int(os.environ["GITHUB_REPOSITORY_ID"])
                and run.get("head_repository_id") == run.get("repository_id"),
                "retained_actions_artifact_binding_mismatch")
        request = urllib.request.Request(
            API + "/actions/artifacts/" + str(record["id"]) + "/zip",
            headers={"Authorization": "Bearer " + TOKEN,
                     "X-GitHub-Api-Version": "2022-11-28"})
        try:
            response = opener.open(request, timeout=30)
        except urllib.error.HTTPError as redirect:
            require(redirect.code == 302, "artifact_download_did_not_return_a_signed_location")
            location = redirect.headers.get("Location", "")
            redirect.close()
        else:
            response.close()
            raise RuntimeError("artifact_download_redirect_contract_changed")
        parsed = urllib.parse.urlparse(location)
        require(parsed.scheme == "https" and parsed.hostname and parsed.port in (None, 443)
                and parsed.username is None and parsed.password is None and not parsed.fragment,
                "artifact_download_location_invalid")
        output = WORK / (label + ".zip")
        # A new unauthenticated request follows the signed URL. Never forward the
        # repository token to the artifact storage origin or retain that URL.
        with urllib.request.urlopen(urllib.request.Request(location), timeout=30) as response:
            require(response.status == 200, "artifact_body_response_not_ok")
            digest = hashlib.sha256()
            count = 0
            with output.open("xb") as stream:
                while True:
                    block = response.read(min(1024 * 1024, record["bytes"] - count + 1))
                    if not block:
                        break
                    count += len(block)
                    require(count <= record["bytes"], "artifact_exceeds_pinned_size")
                    digest.update(block)
                    stream.write(block)
                stream.flush()
                os.fsync(stream.fileno())
        require(count == record["bytes"] and digest.hexdigest() == record["sha256"],
                "artifact_archive_differs_from_literal_pin")
        with zipfile.ZipFile(output) as archive:
            entries = archive.infolist()
            require(0 < len(entries) <= 64
                    and sum(entry.file_size for entry in entries) <= 128 * 1024 * 1024
                    and len({entry.filename.casefold() for entry in entries}) == len(entries)
                    and archive.testzip() is None, "retained_archive_crc_or_inventory_invalid")
        return output, {"artifact_id": record["id"], **identity(output),
                        "workflow_run": FIXED["run"], "source_sha": FIXED["source"]}

    def member(archive_path, record, output):
        with zipfile.ZipFile(archive_path) as archive:
            entry = archive.getinfo(record["member"])
            require(not entry.is_dir() and entry.file_size == record["bytes"],
                    "retained_member_size_mismatch")
            with archive.open(entry) as stream:
                body = limited(stream, record["bytes"])
            require(len(body) == record["bytes"]
                    and hashlib.sha256(body).hexdigest() == record["sha256"],
                    "retained_member_digest_mismatch")
            write_new(output, body)

    require(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true",
            "guarded_update_requires_the_disposable_windows_job")
    plain_directory(WORK.parent)
    WORK.mkdir()
    INPUT.mkdir()
    (ROOT / "ci-evidence").mkdir(exist_ok=True)
    source_before = source_observation()
    repo = os.environ["GITHUB_REPOSITORY"]
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) is not None,
            "repository_identity_invalid")
    API = "https://api.github.com/repos/" + repo
    TOKEN = os.environ["GH_TOKEN"]
    opener = urllib.request.build_opener(NoRedirect())
    prior_run = api_json("/actions/runs/" + str(FIXED["run"]))
    prior_commit = api_json("/git/commits/" + FIXED["source"])
    require(prior_run.get("id") == FIXED["run"]
            and prior_run.get("head_sha") == FIXED["source"]
            and prior_run.get("repository", {}).get("full_name") == repo
            and prior_commit.get("sha") == FIXED["source"]
            and prior_commit.get("tree", {}).get("sha") == FIXED["tree"],
            "predecessor_run_or_git_tree_mismatch")
    package_zip, package_admission = fetch_artifact("predecessor-package", FIXED["packageArtifact"])
    evidence_zip, evidence_admission = fetch_artifact("predecessor-evidence", FIXED["evidenceArtifact"])
    member(package_zip, FIXED["package"], INPUT / "previous-setup.exe")
    member(evidence_zip, FIXED["self"], INPUT / "previous-uninstall.exe")
    member(evidence_zip, FIXED["receipt"], INPUT / "previous-capture-receipt.json")

    old = json.loads((INPUT / "previous-capture-receipt.json").read_bytes())
    expected_files = {
        "motionwright-desktop.exe": {"bytes": FIXED["main"]["bytes"], "sha256": FIXED["main"]["sha256"]},
        "uninstall.exe": {"bytes": FIXED["self"]["bytes"], "sha256": FIXED["self"]["sha256"]},
    }
    require(old.get("source_sha") == FIXED["source"] and old.get("run_id") == str(FIXED["run"])
            and old.get("source_before") == old.get("source_after")
            and old["source_before"]["checkout"] == FIXED["source"]
            and old["source_before"]["tree"] == FIXED["tree"]
            and old.get("candidate") == old.get("candidate_after"),
            "original_capture_source_binding_mismatch")
    require(len(old["cases"]) == 2
            and {case["id"] for case in old["cases"]} == {"unchanged-control", "modified-owned"},
            "original_capture_cases_mismatch")
    for case in old["cases"]:
        require(case["initial_inventory"] == {
            "directories": [], "directory_present": True, "files": expected_files},
            "predecessor_inventory_is_not_the_reviewed_two_file_case")
    require(old["candidate"]["artifacts"] == [{
        "bundle_kind": "nsis", "bytes": FIXED["package"]["bytes"],
        "filename": "Motionwright_0.1.0_x64-setup.exe", "sha256": FIXED["package"]["sha256"],
    }], "original_package_receipt_does_not_bind_the_setup")
    # Extract one named member from the pinned setup; never run the setup during
    # profile generation. Its full literal digest is independently checked below.
    seven = Path(shutil.which("7z.exe") or r"C:\Program Files\7-Zip\7z.exe")
    seven_before = identity(seven)
    with (WORK / "predecessor-main-extraction.stderr").open("xb") as error_log:
        extraction = subprocess.run(
            [str(seven), "x", "-so", str(INPUT / "previous-setup.exe"), FIXED["main"]["member"]],
            check=False, stdout=subprocess.PIPE, stderr=error_log, timeout=45, env=CHILD_ENV)
    require(extraction.returncode == 0, "predecessor_static_extraction_failed")
    require(len(extraction.stdout) == FIXED["main"]["bytes"]
            and hashlib.sha256(extraction.stdout).hexdigest() == FIXED["main"]["sha256"],
            "predecessor_main_differs_from_literal_pin")
    write_new(INPUT / "previous-main.exe", extraction.stdout)
    require(identity(seven) == seven_before, "static_extraction_tool_changed")

    config = json.loads(source_bytes("apps/desktop/src-tauri/tauri.conf.json"))
    version_overlay_path = ROOT / "ci-evidence/windows-nsis-update-version-overlay.json"
    version_overlay_identity = identity(version_overlay_path)
    version_overlay = json.loads(version_overlay_path.read_bytes())
    require(version_overlay == {"version": "0.1.1"}
            and config["version"] == "0.1.0", "fixture_version_overlay_invalid")
    fixture_version = version_overlay["version"]

    windows = config["bundle"]["windows"]
    require(config["productName"] == "Motionwright" and config["identifier"] == "com.seradotcom.motionwright"
            and config.get("mainBinaryName", "motionwright-desktop") == "motionwright-desktop"
            and not windows.get("certificateThumbprint") and not windows.get("signCommand")
            and not config["bundle"].get("createUpdaterArtifacts"),
            "guarded_update_fixture_requires_the_unsigned_single_nsis_profile")
    require(windows["nsis"]["installMode"] == "currentUser"
            and windows["nsis"]["template"] == "windows/installer.nsi"
            and windows["nsis"]["installerHooks"] == "windows/uninstall-ownership.nsh",
            "guarded_update_template_not_configured")
    main = ROOT / "target/release/motionwright-desktop.exe"
    main_before = identity(main)
    require(main_before["sha256"] != FIXED["main"]["sha256"], "new_main_is_not_a_distinct_build")
    previous_pe = read_pe_versions(INPUT / "previous-main.exe")
    current_pe = read_pe_versions(main)
    require_pe_version(previous_pe, "0.1.0", identity(INPUT / "previous-main.exe"))
    require_pe_version(current_pe, fixture_version, main_before)

    require(not config["bundle"].get("externalBin") and not config["bundle"].get("resources"),
            "the_render_fixture_must_not_replace_configured_application_payloads")
    fixture = WORK / "render-inputs"
    fixture.mkdir()
    resource_inputs = {}
    expected_fixture = {}
    for name, body in (("resource-alpha.dat", b"Motionwright NSIS render input alpha\n"),
                       ("resource-beta.dat", b"Motionwright NSIS render input beta: distinct bytes\n")):
        resource = fixture / name
        write_new(resource, body)
        target = "mw-ci-render/" + name
        resource_inputs[str(resource)] = target
        expected_fixture[target] = {"kind": "resource", **identity(resource)}
    external_inputs = []
    for stem, binary_source in (("mw-ci-current-sidecar", main),
                                ("mw-ci-previous-sidecar", INPUT / "previous-main.exe")):
        sidecar = fixture / (stem + "-x86_64-pc-windows-msvc.exe")
        digest, size, captured = _capture(binary_source, sidecar)
        expected_fixture[stem + ".exe"] = {
            "kind": "external_binary", "bytes": size, "sha256": digest}
        require(identity(sidecar) == {"bytes": size, "sha256": digest},
                "sidecar_capture_changed")
        external_inputs.append(str(fixture / stem))
    require(expected_fixture["mw-ci-current-sidecar.exe"]["sha256"]
            != expected_fixture["mw-ci-previous-sidecar.exe"]["sha256"],
            "the_two_render_sidecars_need_distinct_literal_bytes")
    overlay = {"version": fixture_version, "bundle": {"resources": resource_inputs, "externalBin": external_inputs}}
    overlay_path = INPUT / "render-overlay.json"
    overlay_raw = (json.dumps(overlay, indent=2, sort_keys=True) + "\n").encode("utf-8")
    write_new(overlay_path, overlay_raw)
    overlay_identity = identity(overlay_path)
    require(identity(main) == main_before, "new_main_changed_during_fixture_preparation")

    cli_root = ROOT / "apps/studio/node_modules/@tauri-apps"
    cli_package = json.loads((cli_root / "cli/package.json").read_text(encoding="utf-8"))
    require(cli_package["version"] == "2.12.1", "installed_cli_is_not_the_reviewed_version")
    cli_inputs = {}
    for name in ("cli/package.json", "cli/tauri.js", "cli-win32-x64-msvc/package.json",
                 "cli-win32-x64-msvc/cli.win32-x64-msvc.node"):
        cli_inputs[name] = identity(cli_root / name)
    node = Path(shutil.which("node.exe") or "")
    node_identity = identity(node)
    tool_runs = []
    for label, arguments in (("version", ["--version"]), ("bundle-help", ["bundle", "--help"])):
        run = subprocess.run([str(node), str(cli_root / "cli/tauri.js"), *arguments],
                             cwd=ROOT / "apps/desktop", env=CHILD_ENV,
                             capture_output=True, timeout=20, check=False)
        require(run.returncode == 0 and len(run.stdout) <= 128 * 1024
                and len(run.stderr) <= 128 * 1024, "installed_cli_probe_failed")
        write_new(WORK / ("cli-" + label + ".stdout"), run.stdout)
        write_new(WORK / ("cli-" + label + ".stderr"), run.stderr)
        text = run.stdout.decode("utf-8")
        if label == "version":
            require(text.strip() == "tauri-cli 2.12.1", "installed_cli_version_output_mismatch")
        else:
            require("--no-binary-patching" in text and "--bundles" in text,
                    "installed_cli_does_not_offer_the_reviewed_bundle_option")
        tool_runs.append({"label": label, "exit_code": run.returncode,
                          "stdout_sha256": hashlib.sha256(run.stdout).hexdigest(),
                          "stderr_sha256": hashlib.sha256(run.stderr).hexdigest()})
    require(identity(node) == node_identity
            and all(identity(cli_root / name) == value for name, value in cli_inputs.items()),
            "installed_cli_bytes_changed_during_probe")
    profile = {
        "schema": "motionwright.nsis-predecessor/1",
        "identity": {"application": "Motionwright", "bundle_id": config["identifier"], "arch": "x64"},
        "version": "0.1.0", "source_sha": FIXED["source"], "source_tree": FIXED["tree"],
        "target": {"version": fixture_version, "main_sha256": main_before["sha256"]},
        "package": {"path": "previous-setup.exe", "bytes": FIXED["package"]["bytes"],
                    "sha256": FIXED["package"]["sha256"]},
        "capture_receipt": {"path": "previous-capture-receipt.json", "bytes": FIXED["receipt"]["bytes"],
                            "sha256": FIXED["receipt"]["sha256"]},
        "files": [
            {"relative": "motionwright-desktop.exe", "source": "previous-main.exe", "kind": "main",
             "bytes": FIXED["main"]["bytes"], "sha256": FIXED["main"]["sha256"]},
            {"relative": "uninstall.exe", "source": "previous-uninstall.exe", "kind": "uninstaller",
             "bytes": FIXED["self"]["bytes"], "sha256": FIXED["self"]["sha256"]},
        ],
    }
    raw = (json.dumps(profile, indent=2, sort_keys=True) + "\n").encode("utf-8")
    profile_path = INPUT / "predecessor.json"
    write_new(profile_path, raw)
    profile_sha = hashlib.sha256(raw).hexdigest()
    probe = WORK / "profile-probe"
    probe.mkdir()
    admitted = emit_predecessor_include(
        probe / "mw-predecessor.nsh", application="Motionwright",
        bundle_id=config["identifier"], arch="x64", target_version=fixture_version,
        target_main_sha256=main_before["sha256"],
        environment={"MW_NSIS_PREDECESSOR_MANIFEST": str(profile_path),
                     "MW_NSIS_PREDECESSOR_MANIFEST_SHA256": profile_sha})
    require(admitted == {"enabled": True, "files": 2, "manifest_sha256": profile_sha,
                         "package_sha256": FIXED["package"]["sha256"]},
            "existing_guard_did_not_admit_the_exact_profile")
    source_after = source_observation()
    require(source_after == source_before, "source_changed_during_input_admission")
    proof = {
        "schema": "motionwright-windows-nsis-update-inputs/1", "source_sha": SOURCE,
        "source_before": source_before, "source_after": source_after,
        "profile_sha256": profile_sha, "profile": profile, "new_main_before_bundle": main_before,
        "source_version": config["version"],
        "version_overlay": version_overlay_identity,
        "version_scope": "ISOLATED_INSTALLER_AND_PE_FIXTURE",
        "previous_pe_version": previous_pe, "current_pe_version": current_pe,
        "render_overlay": overlay_identity, "expected_fixture_payloads": expected_fixture,
        "fixture_scope": "two_resources_and_two_nonexecuted_pe_sidecars_only",
        "previous_package_archive": package_admission, "previous_evidence_archive": evidence_admission,
        "previous_run_conclusion": prior_run.get("conclusion"),
        "original_stock_modified_owned_case": "FAIL_PRESERVED_AS_HISTORICAL_RESULT",
        "static_extraction_tool": seven_before, "static_extraction_exit_code": extraction.returncode,
        "installed_cli_version": cli_package["version"], "installed_cli_inputs": cli_inputs,
        "installed_node": node_identity, "cli_probe_commands": tool_runs,
        "upstream_review_sha": "30da1fd6e17de6107ecc850c95dfb16b5729f2dd",
        "upstream_cli_bundle_sha256": "fa883f16f84ad4a0d7d091daa2a57e177b804b481a9ec8f8227c5a398dcafdf6",
        "upstream_bundler_bundle_sha256": "91c8387ccb2e52388cdcfeccfd23f69dac5439aba397d464b1d5446153fdc82e",
        "guard_profile_preflight": admitted,
        "native_installer_execution": "NOT_RUN", "human_install_acceptance": "NOT_RUN",
        "release_published": False,
    }
    write_new(PROOF, (json.dumps(proof, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    require(not any(c in str(profile_path) for c in "\r\n"), "profile_environment_path_invalid")
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as environment:
        environment.write("MW_NSIS_PREDECESSOR_MANIFEST=" + str(profile_path) + "\n")
        environment.write("MW_NSIS_PREDECESSOR_MANIFEST_SHA256=" + profile_sha + "\n")
        require(not any(c in str(overlay_path) for c in "\r\n"), "overlay_environment_path_invalid")
        environment.write("MW_NSIS_RENDER_OVERLAY=" + str(overlay_path) + "\n")
        for variable, value in (("MW_NSIS_BUNDLE_NODE", str(node)),
                                ("MW_NSIS_BUNDLE_CLI", str(cli_root / "cli/tauri.js"))):
            require(not any(c in value for c in "\r\n"), "bundle_tool_environment_path_invalid")
            environment.write(variable + "=" + value + "\n")
    print("nsis-predecessor-profile=ADMITTED source=" + SOURCE
          + " owned_files=2 installer_execution=NOT_RUN human=NOT_RUN")


def run_legacy_lifecycle():
    import ctypes
    from ctypes import wintypes
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    import uuid

    ROOT = Path(os.environ["GITHUB_WORKSPACE"])
    sys.path.insert(0, str(ROOT / "tooling"))
    import candidate_smoke
    from nsis_owned_files import _capture
    from release_manifest import write_new_manifest
    from windows_cold_start import (
        known_databases, native_windows_launch_environment,
        run_installed_relaunch, select_first_use_contract, write_sanitized_failure_record,
    )
    from windows_installer_lifecycle import (
        bound_file, bound_json, capture_snapshot, custody_error, file_identity,
        file_map, family_map, installed_registry,
        observed_terminal, require, retained_ref, retain_observed_result,
        source_observation, validate_inputs, validate_lifecycle,
    )

    SOURCE = os.environ["GITHUB_SHA"]
    RUN = {"repository": os.environ["GITHUB_REPOSITORY"],
           "run_id": int(os.environ["GITHUB_RUN_ID"]),
           "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"])}
    require(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and os.environ.get("MW_NSIS_LIFECYCLE_SCOPE") == "nsis_lifecycle",
            "lifecycle_requires_explicit_disposable_windows_workflow")
    require(RUN["run_attempt"] == 1, "lifecycle_does_not_replay_a_rerun")
    before_source = source_observation(ROOT, SOURCE)
    EVIDENCE = ROOT / "ci-evidence/windows-nsis-lifecycle"
    EVIDENCE.mkdir()
    INPUT = EVIDENCE / "inputs"
    INPUT.mkdir()
    WORK = Path(os.environ["RUNNER_TEMP"]) / "motionwright-nsis-guarded-update"
    CHILD_ENV = native_windows_launch_environment(os.environ)
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        CHILD_ENV.pop(name, None)

    def publish(name, value):
        write_new_manifest(EVIDENCE, EVIDENCE / name, value)
        return retained_ref(EVIDENCE, EVIDENCE / name)

    def capture_input(role, original):
        destination = INPUT / role / original.name
        destination.parent.mkdir()
        expected = file_identity(original)
        digest, size, unused = _capture(original, destination)
        require({"bytes": size, "sha256": digest} == expected
                and file_identity(original) == expected, "lifecycle_input_changed")
        return retained_ref(EVIDENCE, destination)

    # Capture the same originals used by the compiler gate. This does not replace
    # their NOT_RUN fields or turn a compiler receipt into installed evidence.
    setups = list((ROOT / "target/release/bundle/nsis").glob("*-setup.exe"))
    require(len(setups) == 1, "lifecycle_setup_count")
    paths = {
        "compiled": ROOT / "ci-evidence/windows-nsis-update-compiled.json",
        "inputs": ROOT / "ci-evidence/windows-nsis-update-inputs.json",
        "candidate": ROOT / "ci-evidence/windows-nsis-update-candidate.json",
        "profile": WORK / "input/predecessor.json",
        "rendered": ROOT / "target/release/nsis/x64/installer.nsi",
        "previous_setup": WORK / "input/previous-setup.exe",
        "current_setup": setups[0],
        "previous_capture": WORK / "input/previous-capture-receipt.json",
        "previous_main": WORK / "input/previous-main.exe",
        "current_main": ROOT / "target/release/motionwright-desktop.exe",
        "version_overlay": ROOT / "ci-evidence/windows-nsis-update-version-overlay.json",
    }
    refs = {role: capture_input(role, original) for role, original in paths.items()}
    admission = {"schema": "motionwright.windows-lifecycle-inputs/1",
        "source_sha": SOURCE, "source_tree": before_source["tree"],
        "run": RUN, "refs": refs}
    admission_ref = publish("admission.json", admission)
    verified = validate_inputs(EVIDENCE, admission, SOURCE, RUN)
    project_contract = select_first_use_contract(
        verified["previous_source"],
        verified["previous_payloads"]["motionwright-desktop.exe"]["sha256"],
    )
    require(verified["source_before"] == before_source, "lifecycle_compiler_source_changed")
    publish("admitted-inputs-and-pe.json", verified)

    # The retained predecessor receipt is validated against its own source metadata,
    # not the current dependency lock. No old code is loaded or executed here.
    profile = bound_json(EVIDENCE, refs["profile"])
    previous_capture = bound_json(EVIDENCE, refs["previous_capture"])
    old_source = profile["source_sha"]
    fetch = subprocess.run(
        ["git", "--no-replace-objects", "fetch", "--no-tags", "--depth=1", "origin", old_source],
        cwd=ROOT, env=CHILD_ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=90, check=False)
    require(fetch.returncode == 0, "predecessor_source_fetch_failed")
    old_tree = subprocess.run(
        ["git", "--no-replace-objects", "rev-parse", old_source + "^{tree}"],
        cwd=ROOT, env=CHILD_ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=15, check=True).stdout.decode("ascii").strip()
    require(old_tree == profile["source_tree"], "predecessor_source_tree_mismatch")
    metadata = EVIDENCE / "previous-source-metadata"
    metadata.mkdir()
    metadata_files = {}
    for relative in ("SOURCE_LOCK.json", "Cargo.toml", "apps/desktop/src-tauri/tauri.conf.json"):
        data = subprocess.run(
            ["git", "--no-replace-objects", "show", old_source + ":" + relative],
            cwd=ROOT, env=CHILD_ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=15, check=True).stdout
        require(0 < len(data) <= 1024 * 1024, "predecessor_metadata_size")
        output = metadata.joinpath(*relative.split("/"))
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as stream:
            require(stream.write(data) == len(data), "predecessor_metadata_short_write")
            stream.flush()
            os.fsync(stream.fileno())
        metadata_files[relative] = retained_ref(EVIDENCE, output)
    publish("previous-source-metadata.json", {
        "source_sha": old_source, "source_tree": old_tree, "files": metadata_files,
        "old_source_code_execution": "NOT_RUN"})
    old_config = json.loads((metadata / "apps/desktop/src-tauri/tauri.conf.json").read_bytes())
    require(old_config["bundle"]["windows"]["nsis"]["installMode"] == "currentUser",
            "predecessor_must_not_delegate_to_elevation")
    previous_candidate_ref = publish("previous-candidate.json", previous_capture["candidate"])
    # Keep the literal filename required by the unchanged candidate smoke reader.
    old_filename = previous_capture["candidate"]["artifacts"][0]["filename"]
    require(old_filename == "Motionwright_0.1.0_x64-setup.exe", "predecessor_filename")
    old_setup = EVIDENCE / old_filename
    _capture(bound_file(EVIDENCE, refs["previous_setup"]), old_setup)
    require(file_identity(old_setup) == verified["previous_setup"], "predecessor_smoke_setup_binding")

    require("APPDATA" in CHILD_ENV and "LOCALAPPDATA" in CHILD_ENV,
            "native_appdata_missing")
    roots = [Path(CHILD_ENV["APPDATA"]), Path(CHILD_ENV["LOCALAPPDATA"])]
    require(not known_databases(roots), "fixture_project_store_already_present")
    database = roots[0] / "com.seradotcom.motionwright/motionwright.sqlite3"
    install_root = Path(os.environ["RUNNER_TEMP"]) / "mw-nsis-lifecycle-install"
    require(not install_root.exists() and install_root.is_absolute()
            and not any(char in str(install_root) for char in '"\r\n\0'),
            "fixture_install_root_not_fresh")
    project_origin = None
    operations = []
    failure = None
    record_ref = None
    result = None

    # The identity is read from the handle returned by this one Popen invocation,
    # not from a PID search that could attach to another installer.
    def process_identity(process):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetProcessTimes.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME)]
        kernel.GetProcessTimes.restype = wintypes.BOOL
        creation, exited, kernel_time, user_time = (wintypes.FILETIME() for unused in range(4))
        require(kernel.GetProcessTimes(
            wintypes.HANDLE(int(process._handle)), ctypes.byref(creation),
            ctypes.byref(exited), ctypes.byref(kernel_time), ctypes.byref(user_time)),
            "installer_process_creation_unavailable")
        stamp = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        require(process.pid > 0 and stamp > 0, "installer_process_identity_invalid")
        return {"pid": process.pid, "creation_filetime": stamp}

    def observe_phase(phase, executable, command, arguments):
        oid = str(uuid.uuid4())
        executable_before = file_identity(executable)
        baseline = capture_snapshot(EVIDENCE, snapshot_name=phase + "-before",
            operation=oid, position="before", install_root=install_root,
            database=database, registry_keys=verified["registry_keys"],
            first_use_contract=project_contract)
        if phase == "install":
            initial = bound_json(EVIDENCE, baseline)
            require(not initial["installed"]
                    and all(all(value is None for value in row.values())
                            for row in initial["registry"].values())
                    and all(value is None for value in initial["sqlite_family"].values()),
                    "initial_fixture_namespace_not_empty")
        elif phase == "upgrade":
            initial = bound_json(EVIDENCE, baseline)
            require(all(file_map(initial).get(path) == value
                        for path, value in verified["previous_payloads"].items()),
                    "predecessor_bytes_changed_before_upgrade")
            installed_registry(initial, verified["registry_keys"][0], verified["previous_version"])
            require(project_origin is not None and initial["project"] is not None
                    and initial["project"] == bound_json(EVIDENCE, project_origin)["project"],
                    "real_project_baseline_missing")
        else:
            initial = bound_json(EVIDENCE, baseline)
            previous = bound_json(EVIDENCE, operations[-1]["after"])
            require(file_map(initial) == file_map(previous)
                    and family_map(initial) == family_map(previous)
                    and initial["project"] == previous["project"]
                    and initial["registry"] == previous["registry"],
                    "uninstall_does_not_continue_the_observed_upgrade")
        require(source_observation(ROOT, SOURCE) == before_source,
                "source_changed_before_installer_effect")
        require(file_identity(executable) == executable_before, "effect_executable_changed")
        # NSIS /D= and _?= must be last and unquoted, including paths with spaces.
        # Only the fixed executable and flags are quoted by list2cmdline.
        command_line = subprocess.list2cmdline([str(executable), *arguments[:-1]]) + " " + arguments[-1]
        publish(phase + "-intent.json", {
            "schema": "motionwright.windows-lifecycle-intent/1",
            "source_sha": SOURCE, "run": RUN, "operation_id": oid,
            "phase": phase, "command": command, "executable": executable_before,
            "arguments": arguments, "launch_limit": 1, "replay_authorized": False})
        process = None
        identity_value = None
        secondary = []
        # Even launch/wait uncertainty is a terminal UNKNOWN; it cannot authorize
        # another launch. No kill, recovery launch, polling launch, or retry exists.
        original = observed_terminal(None)
        try:
            process = subprocess.Popen(
                command_line, executable=str(executable), cwd=str(executable.parent),
                env=CHILD_ENV, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                identity_value = process_identity(process)
            except Exception as error:
                secondary.append(custody_error("process_identity", error))
            try:
                code = process.wait(timeout=180)
                original = observed_terminal(code)
            except subprocess.TimeoutExpired:
                original = observed_terminal(None, timed_out=True)
            except Exception as error:
                secondary.append(custody_error("installer_wait", error))
        except Exception as error:
            secondary.append(custody_error("installer_launch", error))
        context = {"operation_id": oid, "process": identity_value,
                   "executable": executable_before, "source_sha": SOURCE, "run": RUN}
        def after_observation():
            # A live or unobserved terminal cannot provide a quiescent snapshot.
            require(original["exit_code"] is not None, "installer_terminal_not_quiescent")
            reference = capture_snapshot(EVIDENCE, snapshot_name=phase + "-after",
                operation=oid, position="after", install_root=install_root,
                database=database, registry_keys=verified["registry_keys"],
                first_use_contract=project_contract)
            # Exit zero is only the original process result. Before another effect,
            # require the same observable invariants the final consumer rechecks.
            prior = bound_json(EVIDENCE, baseline)
            actual = bound_json(EVIDENCE, reference)
            old, current = verified["previous_payloads"], verified["current_payloads"]
            key = verified["registry_keys"][0]
            if phase == "install":
                require(file_map(actual) == old, "previous_install_inventory")
                installed_registry(actual, key, verified["previous_version"])
                require(all(value is None for value in family_map(actual).values()),
                        "installer_created_unexpected_project_store")
            elif phase == "upgrade":
                before_files, after_files = file_map(prior), file_map(actual)
                require(all(before_files.get(path) == value for path, value in old.items()),
                        "predecessor_bytes_changed_before_upgrade")
                foreign = {path: value for path, value in before_files.items() if path not in old}
                require(bool(foreign) and not (set(foreign) & set(current)),
                        "foreign_fixture_missing_or_conflicting")
                require(all(after_files.get(path) == value for path, value in current.items())
                        and all(after_files.get(path) == value for path, value in foreign.items())
                        and set(after_files) == set(current) | set(foreign) | {"uninstall.exe"}
                        and after_files["uninstall.exe"] != old["uninstall.exe"],
                        "upgrade_payload_or_foreign_preservation")
                installed_registry(actual, key, verified["fixture_installer_version"])
                require(family_map(prior) == family_map(actual)
                        and prior["project"] == actual["project"] and actual["project"] is not None,
                        "upgrade_project_changed")
            else:
                previous = bound_json(EVIDENCE, operations[-1]["after"])
                require(file_map(prior) == file_map(previous)
                        and family_map(prior) == family_map(previous)
                        and prior["project"] == previous["project"]
                        and prior["registry"] == previous["registry"],
                        "uninstall_does_not_continue_the_observed_upgrade")
                foreign = {path: value for path, value in file_map(prior).items()
                           if path not in current and path != "uninstall.exe"}
                require(file_map(actual) == {**foreign, "uninstall.exe": file_map(prior)["uninstall.exe"]}
                        and family_map(prior) == family_map(actual)
                        and prior["project"] == actual["project"],
                        "uninstall_payload_or_project_preservation")
                require(all(row[key] is None for row in actual["registry"].values()),
                        "uninstall_registration_retained")
                product = verified["registry_keys"][1]
                require(all(prior["registry"][view][product] == actual["registry"][view][product]
                            for view in ("32", "64")), "uninstall_product_metadata_changed")
            return reference
        retained = retain_observed_result(
            EVIDENCE, phase, original, after_observation, context=context)
        row = {"phase": phase, "operation_id": oid, "launch_count": 1,
               "command": command, "process": identity_value,
               "executable": executable_before, "terminal": retained["original_terminal"],
               "terminal_receipt": None,
               "custody_errors": retained["custody_errors"] + secondary,
               "before": baseline, "after": retained["observation"]}
        # Append the original in memory before any further readback can fail.
        operations.append(row)
        try:
            row["terminal_receipt"] = retained_ref(EVIDENCE, EVIDENCE / (phase + "-terminal.json"))
        except Exception as error:
            row["custody_errors"].append(custody_error("terminal_readback", error))
        publish(phase + "-operation.json", row)
        require(row["terminal"]["state"] == "ACK" and not row["custody_errors"],
                "installer_effect_or_custody_incomplete")
        require(file_identity(executable) == executable_before,
                "effect_executable_changed_after_terminal")
        return row

    try:
        observe_phase("install", bound_file(EVIDENCE, refs["previous_setup"]),
                      "nsis_install_previous", ["/S", "/D=" + str(install_root)])
        installed = install_root / "motionwright-desktop.exe"
        old_root = candidate_smoke.ROOT
        try:
            candidate_smoke.ROOT = metadata
            smoke = candidate_smoke.build_receipt(
                candidate_receipt=bound_file(EVIDENCE, previous_candidate_ref),
                artifact=old_setup, installed_binary=installed,
                kind="nsis", method="nsis_silent_current_user")
        finally:
            candidate_smoke.ROOT = old_root
        smoke_ref = publish("previous-installed-smoke.json", smoke)
        project_output = EVIDENCE / "previous-real-project.json"
        app_scratch = Path(os.environ["RUNNER_TEMP"]) / "mw-nsis-lifecycle-real-project"
        try:
            run_installed_relaunch(
                executable=installed, smoke_receipt=bound_file(EVIDENCE, smoke_ref),
                expected_sha=old_source, scratch=app_scratch, output=project_output,
                first_use_contract=project_contract)
        except Exception as error:
            try:
                write_sanitized_failure_record(
                    output=project_output, scratch=app_scratch, expected_sha=old_source, error=error)
            except Exception:
                pass
            raise
        project_origin = retained_ref(EVIDENCE, project_output)
        require(known_databases(roots) == [database], "actual_project_store_path_not_the_fixed_fixture")
        foreign = install_root / "user-created-foreign.txt"
        with foreign.open("xb") as stream:
            body = b"Fixture-owned user data: preserve during update and uninstall.\n"
            require(stream.write(body) == len(body), "foreign_fixture_short_write")
            stream.flush()
            os.fsync(stream.fileno())
        observe_phase("upgrade", bound_file(EVIDENCE, refs["current_setup"]),
                      "nsis_upgrade_current", ["/S", "/UPDATE", "/D=" + str(install_root)])
        observe_phase("uninstall", install_root / "uninstall.exe",
                      "nsis_uninstall_current_in_place", ["/S", "_?=" + str(install_root)])
    except Exception as error:
        failure = custody_error("lifecycle_sequence", error)
    finally:
        record = {"schema": "motionwright.windows-installer-lifecycle/1",
            "source_sha": SOURCE, "source_tree": before_source["tree"], "run": RUN,
            "admission_sha256": admission_ref["sha256"],
            "scope": "DISPOSABLE_WINDOWS_FIXTURE_UPGRADE",
            "operations": operations, "project_origin": project_origin,
            "interruption": {"status": "NOT_RUN",
                            "reason": "OBSERVABLE_PID_START_GUID_CHECKPOINT_REQUIRED"},
            "claims": {"human_acceptance": "NOT_RUN", "published_product_upgrade": "NOT_CLAIMED",
                       "release_published": False}}
        try:
            record_ref = publish("record.json", record)
        except Exception as error:
            # Never overwrite a possibly committed record or repeat an installer.
            failure = failure or custody_error("record_publication", error)
        if record_ref is not None:
            try:
                result = validate_lifecycle(EVIDENCE, record, verified, admission_ref["sha256"])
                require(source_observation(ROOT, SOURCE) == before_source,
                        "source_changed_during_lifecycle")
                publish("result.json", {
                    "schema": "motionwright.windows-lifecycle-verification/1",
                    "source_before": before_source,
                    "source_after": source_observation(ROOT, SOURCE),
                    "admission": admission_ref, "record": record_ref, "result": result})
            except Exception as error:
                failure = failure or custody_error("lifecycle_verification", error)
        if failure is not None:
            try:
                publish("diagnostic.json", {
                    "schema": "motionwright.windows-lifecycle-workflow-diagnostic/1",
                    "failure": failure,
                    "originals": [{"phase": row["phase"], "terminal": row["terminal"],
                                   "custody_errors": row["custody_errors"]} for row in operations],
                    "source_sha": SOURCE, "run": RUN, "installer_replays": 0,
                    "human_acceptance": "NOT_RUN", "release_published": False})
            except Exception:
                try:
                    sys.stderr.write("windows-lifecycle=INCOMPLETE diagnostic_publication=UNKNOWN\n")
                    sys.stderr.flush()
                except Exception:
                    pass
    ok = (failure is None and result is not None
          and result["status"] == "SOURCE_BOUND_FIXTURE_OBSERVATIONS")
    try:
        sys.stdout.write("windows-lifecycle=" + ("SOURCE_BOUND_FIXTURE_OBSERVATIONS" if ok else "INCOMPLETE")
                         + " human=NOT_RUN interruption=NOT_RUN\n")
        sys.stdout.flush()
    except Exception:
        pass
    raise SystemExit(0 if ok else 1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scope", choices=("prepare-legacy", "legacy-lifecycle"))
    args = parser.parse_args(argv)
    if args.scope == "prepare-legacy":
        prepare_predecessor()
    elif args.scope == "legacy-lifecycle":
        run_legacy_lifecycle()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
