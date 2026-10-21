#!/usr/bin/env python3
"""One producer for source-bound Windows installer observations.

Effects are available only through explicit disposable Windows job commands.
Readonly custody, checkpoint and lifecycle readers remain separate modules.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import windows_installer_lifecycle as life
import windows_nsis_interruption as nsis
import storage_schema_contract as storage
from nsis_owned_files import _capture, emit_predecessor_include, _predecessor_include
from release_manifest import write_new_manifest
from windows_cold_start import native_windows_launch_environment


FIXED_PREDECESSOR = json.loads(r'''{"run":39032220978,"source":"679cc48baf7c6f9bb4a0b803508c9b2a9891d944","tree":"4ce7c4423c73da4f3af638427088ee6e962d12d5","packageArtifact":{"id":12101032310,"bytes":7134483,"sha256":"740ff6b52b1c655755f693ab9e620b36e15d5d5b642199f38e9a122d1b28155e"},"evidenceArtifact":{"id":12101216495,"bytes":125856,"sha256":"cab9c044019be1909162b9541b21f9a34bc46a44b4406a6bbda5b23aaf4f7e55"},"package":{"member":"target/release/bundle/nsis/Motionwright_0.1.0_x64-setup.exe","bytes":7149373,"sha256":"c071870c1c5f12f1728389f526471bee43b23eb7ad0bae3410147349a8da9f46"},"main":{"member":"motionwright-desktop.exe","bytes":32794112,"sha256":"4c1f8f492a505340ec3ca8e521254ff753a01875c92052a48c0dc42ad1314de8"},"self":{"member":"unchanged-control-original-uninstaller.exe","bytes":79338,"sha256":"dd03a5e87b450ae24950d67d2935dd18f78a27b45e1c52558625d47b60852224"},"receipt":{"member":"receipt.json","bytes":17792,"sha256":"ae15f0e7f21678b9fd950ec46a0b9cfe3601290851744a3d9aea158e39c5193e"}}''')

# Physical schema-four selection remains closed until a reviewed product is frozen.
REVIEWED_SCHEMA4_PRODUCT_SHA = None
REVIEWED_SCHEMA4_ORACLE_SHA = "0d17946cb9a233f491bf6f199906f1d20f88f929"
LEGACY_ORACLE_SHA = "016cd88c4ef20ada70fcc446361c40e4a0b81ca1"
FIXED_PREDECESSOR_STORAGE = {
    "git_blob": "9dc9878a0544d1121f7d5281f160004a774b35c4",
    "bytes": 82840,
    "sha256": "860e708abfb102765f595b7f68c0eb275c285c2127c37f86b1d31a9b303e7330",
}


def require_reviewed_product_source(source):
    nsis.require(isinstance(REVIEWED_SCHEMA4_PRODUCT_SHA, str)
                 and nsis.SHA40.fullmatch(REVIEWED_SCHEMA4_PRODUCT_SHA),
                 "schema4_product_source_not_frozen")
    nsis.require(source == REVIEWED_SCHEMA4_PRODUCT_SHA,
                 "schema4_product_source_outside_reviewed_contract")


def retain_storage_originals(root, observation, raw, *, provenance):
    """Retain bounded source representation without widening legacy metadata."""
    cohort = storage.admit_source(observation, raw)
    source = root / "source.rs"
    observed = root / "source-observation.json"
    nsis.write_new(source, raw)
    nsis.write_json(observed, {**observation, "provenance": provenance})
    return {"schema": "motionwright.nsis-storage-originals/1", "cohort": cohort,
            "source": life.retained_ref(root.parent, source),
            "observation": life.retained_ref(root.parent, observed)}


def admit_retained_storage(root, value, *, source_sha, source_tree):
    nsis.require(isinstance(value, dict) and set(value) == {
        "schema", "cohort", "source", "observation"}
        and value["schema"] == "motionwright.nsis-storage-originals/1",
        "storage_originals_roster")
    cohort = storage.validate_cohort(value["cohort"], source_sha=source_sha, source_tree=source_tree)
    for key in ("source", "observation"):
        life.ref_identity(value[key])
    nsis.require(0 < value["source"]["bytes"] <= storage.MAX_SOURCE_BYTES,
                 "storage_original_size_bound")
    path = life.bound_file(root, value["source"])
    raw, identity = nsis.bounded_read(path, storage.MAX_SOURCE_BYTES)
    nsis.require(identity == life.ref_identity(value["source"]), "storage_original_changed")
    observation = life.bound_json(root, value["observation"])
    nsis.require(storage.admit_source(observation, raw) == cohort, "storage_original_cohort_changed")
    return cohort, observation, raw


def admit_compiled_storage(root, preparation, summary, profile, current):
    """Admit transported originals against the actual compilation and profile."""
    current = storage.validate_cohort(current, source_sha=summary["product_sha"],
                                     source_tree=summary["source_before"]["product"]["tree"])
    nsis.require(preparation.get("storage_cohort") == current,
                 "preparation_current_storage_cohort_changed")
    originals = preparation.get("storage_originals")
    nsis.require(isinstance(originals, dict) and set(originals) == {"before", "after"},
                 "preparation_storage_originals_missing")
    retained = root / "inputs/predecessor"
    after, observation, raw = admit_retained_storage(retained, originals["after"],
        source_sha=current["source_sha"], source_tree=current["source_tree"])
    nsis.require(after == current, "compiled_current_storage_cohort_changed")
    declared = summary["source_before"]["product"]["files"][storage.SOURCE_PATH]
    nsis.require(observation["files"] == {storage.SOURCE_PATH: declared},
                 "compiled_storage_observation_changed")
    git_raw, git_identity = nsis.bounded_read(
        root / "original-source/product" / storage.SOURCE_PATH, storage.MAX_SOURCE_BYTES)
    nsis.require(git_identity == current["source_input"]["git"], "compiled_storage_git_original_changed")
    expected = (git_raw if current["source_input"]["checkout_encoding"] == "EXACT_GIT_BYTES"
                else git_raw.replace(b"\n", b"\r\n"))
    nsis.require((current["source_input"]["checkout_encoding"] != "EXACT_LF_TO_CRLF"
                  or b"\r" not in git_raw) and raw == expected,
                 "compiled_storage_representation_changed")
    before, historical, old_raw = admit_retained_storage(retained, originals["before"],
        source_sha=profile["source_sha"], source_tree=profile["source_tree"])
    nsis.require(life.ref_identity(originals["before"]["source"]) == {
        key: FIXED_PREDECESSOR_STORAGE[key] for key in ("bytes", "sha256")},
        "predecessor_storage_literal_changed")
    nsis.require(hashlib.sha1(b"blob " + str(len(old_raw)).encode() + b"\0" + old_raw).hexdigest()
                 == FIXED_PREDECESSOR_STORAGE["git_blob"], "predecessor_storage_git_blob_changed")
    nsis.require(historical.get("provenance") == {
        "kind": "RETAINED_REVIEWED_GIT_BLOB", "source_sha": profile["source_sha"],
        "source_tree": profile["source_tree"], "path": storage.SOURCE_PATH,
        "git_blob": FIXED_PREDECESSOR_STORAGE["git_blob"]},
        "predecessor_storage_provenance_changed")
    return {"before": before, "after": after}, {
        "before_storage_source": life.bound_file(retained, originals["before"]["source"]),
        "before_storage_observation": life.bound_file(retained, originals["before"]["observation"])}


class _StorageConsumerAdmission:
    """An internal admission result; a source pin alone is not this capability."""
    __slots__ = ("cohorts", "refs", "oracle")

    def __init__(self, cohorts, refs, oracle):
        self.cohorts = {key: storage.validate_cohort(value) for key, value in cohorts.items()}
        self.refs = {key: dict(value) for key, value in refs.items()}
        self.oracle = oracle


def admit_storage_consumer(root, compiled, cohort, oracle_root, oracle_sha, oracle_observation):
    require_reviewed_product_source(cohort["source_sha"])
    nsis.require(oracle_sha == REVIEWED_SCHEMA4_ORACLE_SHA
                 and oracle_observation.get("sha") == oracle_sha
                 and set(oracle_observation.get("files", {})) == set(ORACLE_PATHS),
                 "schema4_oracle_source_closure_not_admitted")
    for relative in ORACLE_PATHS:
        nsis.require(life.file_identity(oracle_root / relative)
                     == oracle_observation["files"][relative].get("checkout"),
                     "schema4_oracle_original_changed")
    # Import the reviewed independent implementation; do not duplicate its oracle.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "motionwright_reviewed_updated_application", oracle_root / "tooling/windows_updated_application.py")
    nsis.require(spec is not None and spec.loader is not None, "schema4_oracle_module_missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    nsis.require(tuple(module.SOURCE_PATHS) == ORACLE_PATHS
                 and module.INPUT_SCHEMA_V2 == "motionwright.windows-updated-application-input/2",
                 "schema4_oracle_contract_changed")
    dependencies = ((module.cold, "tooling/windows_cold_start.py"),
                    (module.life, "tooling/windows_installer_lifecycle.py"),
                    (module.storage, "tooling/storage_schema_contract.py"),
                    (sys.modules[module._capture.__module__], "tooling/nsis_owned_files.py"),
                    (sys.modules[module.write_new_manifest.__module__], "tooling/release_manifest.py"))
    for loaded, relative in dependencies:
        nsis.require(life.file_identity(Path(loaded.__file__))
                     == oracle_observation["files"][relative]["checkout"],
                     "schema4_oracle_loaded_dependency_changed")
    refs = {key: life.retained_ref(root, path) for key, path in compiled["storage_original_paths"].items()}
    value = {"schema": module.INPUT_SCHEMA_V2, "product_sha": cohort["source_sha"],
             "storage_cohorts": compiled["storage_cohorts"], "refs": refs}
    records = module.admit_storage_originals(root, value, compiled["summary"],
                                             compiled["profile_document"], cohort)
    return _StorageConsumerAdmission(records, refs, module)


def prepare_predecessor(*, root=None, source=None, work=None, proof_path=None,
                        version_path=None, main_path=None, source_paths=None,
                        fixture_shape="legacy", export_environment=True,
                        child_environment=None):
    """Admit the same pinned predecessor for legacy or canonical compilation.

    The canonical caller supplies its own product roster and retained build;
    no new gate path is required to exist in the older product checkout.
    """
    nsis.require(fixture_shape in ("legacy", "resources"), "fixture_shape_closed")
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

    ROOT = Path(root) if root is not None else Path(os.environ["GITHUB_WORKSPACE"])
    SOURCE = source if source is not None else os.environ["GITHUB_SHA"]
    WORK = Path(work) if work is not None else Path(os.environ["RUNNER_TEMP"]) / "motionwright-nsis-guarded-update"
    INPUT = WORK / "input"
    PROOF = Path(proof_path) if proof_path is not None else ROOT / "ci-evidence/windows-nsis-update-inputs.json"
    CHILD_ENV = (dict(child_environment) if child_environment is not None
                 else native_windows_launch_environment(os.environ))
    CHILD_ENV.pop("GH_TOKEN", None)
    CHILD_ENV.pop("GITHUB_TOKEN", None)
    FIXED = FIXED_PREDECESSOR

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
    if source_paths is not None:
        SOURCE_PATHS = tuple(source_paths)

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
    PROOF.parent.mkdir(parents=True, exist_ok=True)
    source_before = source_observation()
    storage_cohort = storage.admit_checkout(ROOT, SOURCE)
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
    version_overlay_path = Path(version_path) if version_path is not None else ROOT / "ci-evidence/windows-nsis-update-version-overlay.json"
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
    main = Path(main_path) if main_path is not None else ROOT / "target/release/motionwright-desktop.exe"
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
    if fixture_shape == "legacy":
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
        "storage_cohort": storage_cohort,
        "profile_sha256": profile_sha, "profile": profile, "new_main_before_bundle": main_before,
        "source_version": config["version"],
        "version_overlay": version_overlay_identity,
        "version_scope": "ISOLATED_INSTALLER_AND_PE_FIXTURE",
        "previous_pe_version": previous_pe, "current_pe_version": current_pe,
        "render_overlay": overlay_identity, "expected_fixture_payloads": expected_fixture,
        "fixture_scope": ("two_resources_and_two_nonexecuted_pe_sidecars_only"
                          if fixture_shape == "legacy" else "two_synthetic_resources_only"),
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
    if fixture_shape == "resources":
        import base64
        metadata = WORK / "previous-source-metadata"
        metadata.mkdir()
        records = {}
        for relative in ("SOURCE_LOCK.json", "Cargo.toml", "apps/desktop/src-tauri/tauri.conf.json"):
            record = api_json("/contents/" + relative + "?ref=" + FIXED["source"])
            require(record.get("type") == "file" and record.get("path") == relative
                    and record.get("encoding") == "base64", "previous_metadata_api_contract")
            body = base64.b64decode(record["content"], validate=False)
            require(0 < len(body) <= 1024 * 1024 and len(body) == record["size"]
                    and hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest() == record["sha"],
                    "previous_metadata_git_blob_mismatch")
            destination = metadata / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            write_new(destination, body)
            records[relative] = identity(destination)
        proof["previous_source_metadata"] = records
        # The historical file is a reviewed Git object, not a fabricated checkout.
        relative = storage.SOURCE_PATH
        record = api_json("/contents/" + relative + "?ref=" + FIXED["source"])
        require(record.get("type") == "file" and record.get("path") == relative
                and record.get("encoding") == "base64"
                and record.get("sha") == FIXED_PREDECESSOR_STORAGE["git_blob"],
                "previous_storage_api_contract")
        body = base64.b64decode(record["content"], validate=False)
        require(len(body) == record.get("size") == FIXED_PREDECESSOR_STORAGE["bytes"]
                and hashlib.sha256(body).hexdigest() == FIXED_PREDECESSOR_STORAGE["sha256"]
                and hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()
                    == FIXED_PREDECESSOR_STORAGE["git_blob"], "previous_storage_literal_mismatch")
        literal = {key: FIXED_PREDECESSOR_STORAGE[key] for key in ("bytes", "sha256")}
        historical = {"sha": FIXED["source"], "tree": FIXED["tree"], "files": {
            relative: {"git_bytes": len(body), "git_sha256": literal["sha256"],
                       "checkout": literal, "checkout_encoding": "EXACT_GIT_BYTES"}}}
        before_storage = retain_storage_originals(WORK / "before-storage", historical, body,
            provenance={"kind": "RETAINED_REVIEWED_GIT_BLOB", "source_sha": FIXED["source"],
                        "source_tree": FIXED["tree"], "path": relative,
                        "git_blob": FIXED_PREDECESSOR_STORAGE["git_blob"]})
        current_observation = nsis.source_observation(ROOT, SOURCE, (relative,))
        current_raw = nsis.bounded_read(ROOT / relative, storage.MAX_SOURCE_BYTES)[0]
        require(storage.admit_source(current_observation, current_raw) == storage_cohort,
                "current_storage_changed_during_preparation")
        after_storage = retain_storage_originals(WORK / "after-storage", current_observation, current_raw,
            provenance={"kind": "ORIGINAL_PRODUCT_CHECKOUT", "source_sha": SOURCE,
                        "source_tree": current_observation["tree"], "path": relative})
        require(nsis.source_observation(ROOT, SOURCE, (relative,)) == current_observation,
                "current_storage_changed_during_retention")
        proof["storage_originals"] = {"before": before_storage, "after": after_storage}
    write_new(PROOF, (json.dumps(proof, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    require(not any(c in str(profile_path) for c in "\r\n"), "profile_environment_path_invalid")
    if export_environment:
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
    return {"profile": profile_path, "profile_sha256": profile_sha,
            "overlay": overlay, "proof": proof, "proof_path": PROOF,
            "expected_payloads": expected_fixture, "input_root": INPUT}



def process_identity(process):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    creation, exited, kernel_time, user_time = (wintypes.FILETIME() for unused in range(4))
    life.require(kernel.GetProcessTimes(
        wintypes.HANDLE(int(process._handle)), ctypes.byref(creation),
        ctypes.byref(exited), ctypes.byref(kernel_time), ctypes.byref(user_time)),
        "installer_process_creation_unavailable")
    stamp = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
    life.require(process.pid > 0 and stamp > 0, "installer_process_identity_invalid")
    return {"pid": process.pid, "creation_filetime": stamp}

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
    require_installed_storage_consumer(storage.admit_checkout(ROOT, SOURCE))
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
        launched = launch_once_and_observe(executable, command_line, CHILD_ENV)
        identity_value = launched["process"]
        secondary = launched["custody_errors"]
        original = launched["original_terminal"]
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



def launch_once_and_observe(executable, command_line, environment, *,
                            timeout=180, token=None, live_observer=None,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL):
    """One launch, original handle only; uncertainty never authorizes a replay."""
    original = life.observed_terminal(None)
    identity_value, live, child_token = None, None, None
    secondary = []
    try:
        process = subprocess.Popen(
            command_line, executable=str(executable), cwd=str(executable.parent),
            env=environment, stdin=subprocess.DEVNULL,
            stdout=stdout, stderr=stderr)
        try:
            identity_value = process_identity(process)
        except Exception as error:
            secondary.append(life.custody_error("process_identity", error))
        try:
            if token is not None:
                child_token = observe_non_elevated_token(process)
                require_same_token(token, child_token)
                require_original_image(process, executable)
            if live_observer is not None and not secondary:
                live = live_observer(process, identity_value)
        except Exception as error:
            secondary.append(life.custody_error("live_observation", error))
        try:
            original = life.observed_terminal(process.wait(timeout=timeout))
        except subprocess.TimeoutExpired:
            original = life.observed_terminal(None, timed_out=True)
        except Exception as error:
            secondary.append(life.custody_error("installer_wait", error))
    except Exception as error:
        secondary.append(life.custody_error("installer_launch", error))
    return {"original_terminal": original, "process": identity_value,
            "custody_errors": secondary, "live_observation": live, "token": child_token}


def observe_non_elevated_token(process=None, *, before_admission=None):
    """Read the actual current/child token, not a manifest or currentUser label."""
    nsis.require(os.name == "nt", "token_windows_required")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    handle = kernel.GetCurrentProcess() if process is None else wintypes.HANDLE(int(process._handle))
    token = wintypes.HANDLE()
    nsis.require(advapi.OpenProcessToken(handle, 0x0008, ctypes.byref(token)), "token_open_failed")
    try:
        def information(kind):
            needed = wintypes.DWORD()
            advapi.GetTokenInformation(token, kind, None, 0, ctypes.byref(needed))
            nsis.require(0 < needed.value <= 65536, "token_information_bound")
            buffer = ctypes.create_string_buffer(needed.value)
            nsis.require(advapi.GetTokenInformation(token, kind, buffer, len(buffer),
                                                  ctypes.byref(needed)), "token_information_failed")
            return buffer

        elevation = ctypes.cast(information(20), ctypes.POINTER(wintypes.DWORD)).contents.value
        session = ctypes.cast(information(12), ctypes.POINTER(wintypes.DWORD)).contents.value
        class SidAttributes(ctypes.Structure):
            _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]
        user_buffer, integrity_buffer = information(1), information(25)
        user = ctypes.cast(user_buffer, ctypes.POINTER(SidAttributes)).contents.sid
        integrity = ctypes.cast(integrity_buffer, ctypes.POINTER(SidAttributes)).contents.sid
        advapi.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
        advapi.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
        advapi.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        advapi.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
        count = advapi.GetSidSubAuthorityCount(integrity).contents.value
        nsis.require(0 < count <= 15, "token_integrity_sid_invalid")
        level = advapi.GetSidSubAuthority(integrity, count - 1).contents.value
        advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
        text = wintypes.LPWSTR()
        nsis.require(advapi.ConvertSidToStringSidW(user, ctypes.byref(text)), "token_user_sid_failed")
        try:
            sid = text.value
        finally:
            kernel.LocalFree.argtypes = [ctypes.c_void_p]
            kernel.LocalFree.restype = ctypes.c_void_p
            kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
        value = {"elevated": bool(elevation), "integrity_rid": int(level),
                 "user_sid": sid, "session_id": int(session)}
        if before_admission is not None:
            before_admission(dict(value))
        admit_token(value)
        return value
    finally:
        kernel.CloseHandle(token)


def admit_token(value):
    nsis.require(isinstance(value, dict) and set(value) == {
        "elevated", "integrity_rid", "user_sid", "session_id"}, "token_fields")
    nsis.require(value["elevated"] is False and type(value["integrity_rid"]) is int
                 and 0x2000 <= value["integrity_rid"] < 0x3000
                 and type(value["session_id"]) is int and value["session_id"] > 0
                 and isinstance(value["user_sid"], str)
                 and re.fullmatch(r"S-1-[0-9]+(?:-[0-9]+){1,15}", value["user_sid"]),
                 "actual_non_elevated_interactive_token_required")
    return value


def require_same_token(before, after):
    admit_token(before)
    admit_token(after)
    nsis.require(before == after, "installer_token_changed")


def require_original_image(process, executable):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    buffer = ctypes.create_unicode_buffer(32768)
    size = wintypes.DWORD(len(buffer))
    nsis.require(kernel.QueryFullProcessImageNameW(wintypes.HANDLE(int(process._handle)),
        0, buffer, ctypes.byref(size)), "original_process_image_unavailable")
    nsis.require(os.path.normcase(os.path.abspath(buffer.value))
                 == os.path.normcase(os.path.abspath(executable)),
                 "original_process_image_changed")
    return {"path": buffer.value, "identity": life.file_identity(executable)}


def actual_profile(environment):
    """Match native known folders before effects; never redirect an application profile."""
    nsis.require(os.name == "nt", "profile_windows_required")
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                          wintypes.HANDLE, ctypes.POINTER(wintypes.LPWSTR)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    paths = {}
    for key, guid in (("LOCALAPPDATA", "F1B32785-6FBA-4FCF-9D55-7B8E7F157091"),
                      ("APPDATA", "3EB685DB-65F9-4CF6-A03A-E3EF65729F3D")):
        identifier = ctypes.create_string_buffer(uuid.UUID(guid).bytes_le)
        value = wintypes.LPWSTR()
        nsis.require(shell.SHGetKnownFolderPath(identifier, 0, None, ctypes.byref(value)) == 0,
                     "native_profile_path_unavailable")
        try:
            path = Path(value.value).absolute()
        finally:
            ole.CoTaskMemFree(ctypes.cast(value, ctypes.c_void_p))
        nsis.require(key in environment and Path(environment[key]).absolute() == path,
                     "redirected_profile_not_admitted")
        plain_directory(path)
        paths[key] = path
    return paths


def plain_directory(path):
    for parent in (path, *path.parents):
        info = parent.lstat()
        nsis.require(__import__("stat").S_ISDIR(info.st_mode)
                     and not __import__("stat").S_ISLNK(info.st_mode)
                     and not getattr(info, "st_file_attributes", 0) & 0x400,
                     "nonordinary_directory")


def physical_scope(environment, case):
    nsis.require(case in ("default-uninstall", "interruption-recovery"), "physical_case_closed")
    nsis.require(os.name == "nt" and environment.get("GITHUB_ACTIONS") == "true"
                 and environment.get("RUNNER_ENVIRONMENT") == "github-hosted"
                 and environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
                 and environment.get("MW_NSIS_VERIFICATION_SCOPE") == "physical",
                 "explicit_synthetic_disposable_windows_required")
    nsis.require(environment.get("GITHUB_RUN_ATTEMPT") == "1", "physical_rerun_not_admitted")
    return {"repository": environment["GITHUB_REPOSITORY"],
            "run_id": int(environment["GITHUB_RUN_ID"]), "run_attempt": 1}


def installer_arguments(phase, install_root=None):
    if phase == "default":
        nsis.require(install_root is None, "default_install_must_not_override_destination")
        return ["/S"]
    nsis.require(phase in ("previous", "interrupt", "recover", "uninstall")
                 and isinstance(install_root, Path) and install_root.is_absolute()
                 and not any(x in str(install_root) for x in '"\r\n\0'),
                 "installer_arguments_closed")
    if phase == "uninstall":
        return ["/S", "_?=" + str(install_root)]
    return ["/S", *(["/UPDATE"] if phase in ("interrupt", "recover") else []),
            "/D=" + str(install_root)]


def command_line(executable, arguments):
    # Only NSIS's documented last destination argument is left unquoted.
    if arguments and (arguments[-1].startswith("/D=") or arguments[-1].startswith("_?=")):
        return subprocess.list2cmdline([str(executable), *arguments[:-1]]) + " " + arguments[-1]
    return subprocess.list2cmdline([str(executable), *arguments])


def preserve_project(before, after):
    nsis.require(before["install_root"] == after["install_root"]
                 and before["database"] == after["database"]
                 and life.family_map(before) == life.family_map(after)
                 and before["project"] == after["project"], "project_family_changed")


def admit_interrupted_state(before, after, checkpoint, previous, main, keys):
    preserve_project(before, after)
    old, actual = life.file_map(before), life.file_map(after)
    nsis.require(all(old.get(name) == value for name, value in previous.items()),
                 "predecessor_owned_subset_changed")
    stage = checkpoint["stage"].replace("\\", "/")
    nsis.require(stage not in old and "motionwright-desktop.exe" not in actual,
                 "interrupted_main_not_withdrawn")
    expected = {key: value for key, value in old.items() if key != "motionwright-desktop.exe"}
    expected[stage] = main
    nsis.require(actual == expected and after["registry"] == before["registry"],
                 "interrupted_namespace_or_registry_changed")
    nsis.require(before["project"] is not None, "synthetic_project_baseline_missing")
    life.installed_registry(before, keys[0], "0.1.0")
    return {"state": "INTERRUPTED_STATE_ADMITTED", "stage": stage,
            "foreign_preserved": sorted(set(old) - set(previous))}


def admit_recovered_state(before, interrupted, after, current, previous, keys, stage):
    preserve_project(before, after)
    old, actual = life.file_map(before), life.file_map(after)
    foreign = {key: value for key, value in old.items() if key not in previous}
    expected = {**foreign, **current, stage: life.file_map(interrupted)[stage]}
    nsis.require(actual.get("uninstall.exe") is not None
                 and actual["uninstall.exe"] != previous["uninstall.exe"],
                 "new_uninstaller_not_observed")
    expected["uninstall.exe"] = actual["uninstall.exe"]
    nsis.require(actual == expected, "recovery_payload_foreign_or_stage_changed")
    life.installed_registry(after, keys[0], "0.1.1")
    nsis.require(all(before["registry"][view][keys[1]] == after["registry"][view][keys[1]]
                     for view in ("32", "64")), "recovery_product_registration_changed")
    return {"state": "RECOVERED_STATE_ADMITTED", "foreign_preserved": sorted(foreign)}


def admit_default_install(before, after, current, keys, expected_root, arguments):
    nsis.require(arguments == ["/S"] and after["install_root"] == str(expected_root)
                 and before["install_root"] == str(expected_root),
                 "current_default_destination_not_observed")
    nsis.require(not life.file_map(before)
                 and all(row is None for view in before["registry"].values() for row in view.values())
                 and all(value is None for value in life.family_map(before).values()),
                 "default_namespace_not_fresh")
    actual = life.file_map(after)
    nsis.require(set(actual) == set(current) | {"uninstall.exe"}
                 and all(actual.get(key) == value for key, value in current.items()),
                 "default_current_payload_mismatch")
    preserve_project(before, after)
    life.installed_registry(after, keys[0], "0.1.1")
    return {"state": "CURRENT_DEFAULT_INSTALL_OBSERVED", "destination_override": False}


def admit_mixed_uninstall(before, after, current, keys, changed, intact, foreign):
    preserve_project(before, after)
    old, actual = life.file_map(before), life.file_map(after)
    nsis.require(changed in current and intact in current and changed != intact
                 and old.get(changed) is not None and old[changed] != current[changed]
                 and old.get(intact) == current[intact] and foreign not in current
                 and foreign in old, "mixed_uninstall_fixture_invalid")
    # Every intact owned file must disappear; every modified/foreign byte remains.
    expected = {key: value for key, value in old.items()
                if key not in current or value != current[key]}
    nsis.require(actual == expected and intact not in actual
                 and actual.get(changed) == old[changed]
                 and actual.get(foreign) == old[foreign], "mixed_uninstall_preservation_failed")
    nsis.require(all(rows[keys[0]] is None for rows in after["registry"].values()),
                 "uninstall_registration_retained")
    nsis.require(all(before["registry"][view][keys[1]] == after["registry"][view][keys[1]]
                     for view in ("32", "64")), "uninstall_product_metadata_changed")
    return {"state": "MIXED_UNINSTALL_OBSERVED", "modified_owned": changed,
            "removed_intact_owned": intact, "foreign": foreign}


def admit_profile_family(profile_path, pin, target_main, output):
    nsis.require(life.file_identity(profile_path)["sha256"] == pin, "profile_pin_changed")
    output.mkdir()
    admitted = emit_predecessor_include(output / "mw-predecessor.nsh",
        application="Motionwright", bundle_id="com.seradotcom.motionwright", arch="x64",
        target_version="0.1.1", target_main_sha256=target_main["sha256"],
        environment={"MW_NSIS_PREDECESSOR_MANIFEST": str(profile_path),
                     "MW_NSIS_PREDECESSOR_MANIFEST_SHA256": pin})
    nsis.require(admitted["enabled"] is True and admitted["files"] == 2,
                 "complete_reviewed_predecessor_not_admitted")
    return admitted


def admit_compiled(root, gate_sha, product_sha, run, *, storage_cohort=None):
    """Verify transported original bytes without rewriting their old absolute paths."""
    nsis.archive_inventory(root)
    summary = json.loads(nsis.bounded_read(root / "compile-summary.json", nsis.MAX_RENDER_BYTES)[0])
    nsis.require(summary["schema"] == "motionwright.nsis-interruption-compile/1"
                 and summary["compile"] == "TWO_VARIANTS_COMPILED"
                 and summary["gate_sha"] == gate_sha and summary["product_sha"] == product_sha
                 and summary["run_id"] == str(run["run_id"])
                 and summary["run_attempt"] == str(run["run_attempt"])
                 and summary["source_before"] == summary["source_after"]
                 and summary["source_before"]["gate"]["sha"] == gate_sha
                 and summary["source_before"]["product"]["sha"] == product_sha
                 and summary["predecessor_profile"] == "ADMITTED",
                 "physical_compile_source_or_profile_not_admitted")
    main_path = root / "inputs/compiled-application/motionwright-desktop.exe"
    main = nsis.regular_file(main_path, nsis.MAX_PAYLOAD_BYTES)
    nsis.require(main == summary["real_application"], "compiled_main_transport_changed")
    profile = root / "inputs/predecessor/input/predecessor.json"
    preparation = json.loads(nsis.bounded_read(
        root / "inputs/predecessor-admission.json", nsis.MAX_RENDER_BYTES)[0])
    nsis.require(preparation["fixture_scope"] == "two_synthetic_resources_only"
                 and preparation["profile_sha256"] == life.file_identity(profile)["sha256"]
                 and preparation["new_main_before_bundle"] == main
                 and preparation["source_sha"] == product_sha, "physical_fixture_profile_changed")
    variants = summary["variants"]
    nsis.require([item["variant"] for item in variants] == ["normal", "opt-in"],
                 "compile_variant_roster")
    paths = {}
    for variant in variants:
        folder = root / "variants" / variant["variant"]
        custody_path = folder / "retained-files.json"
        nsis.require(life.file_identity(custody_path) == variant["variant_custody"],
                     "variant_transport_custody_changed")
        custody = json.loads(nsis.bounded_read(custody_path, nsis.MAX_RENDER_BYTES)[0])
        nsis.require(custody["schema"] == "motionwright.nsis-variant-custody/1"
                     and set(custody["trees"]) == {"render", "bundle"}, "variant_custody_roster")
        for part in ("render", "bundle"):
            nsis.require(nsis.tree_manifest(folder / part) == custody["trees"][part]["files"],
                         "variant_original_transport_changed")
        setup = folder / "bundle/Motionwright_0.1.1_x64-setup.exe"
        nsis.require(life.file_identity(setup) == variant["setup"]
                     and variant["result"] == "COMPILE_ONLY" and variant["main_capture"] == main,
                     "variant_setup_transport_changed")
        nsis.inspect_predecessor_capture(folder, profile, preparation["profile_sha256"])
        resources = nsis.inspect_synthetic_resources(folder, preparation["expected_fixture_payloads"])
        nsis.require(resources == variant["synthetic_resources"], "resource_transport_changed")
        nsis.require(variant["predecessor_profile"] == "ADMITTED", "variant_profile_not_admitted")
        paths[variant["variant"]] = setup
    nsis.require(life.file_identity(paths["normal"]) != life.file_identity(paths["opt-in"]),
                 "setup_variants_not_distinct")
    current = {key: {"bytes": value["bytes"], "sha256": value["sha256"]}
               for key, value in preparation["expected_fixture_payloads"].items()}
    nsis.require(set(current) == {"mw-ci-render/resource-alpha.dat", "mw-ci-render/resource-beta.dat"},
                 "physical_resource_roster")
    current["motionwright-desktop.exe"] = main
    document = json.loads(profile.read_bytes())
    admit_reviewed_predecessor(document)
    nsis.require(document == preparation["profile"]
                 and preparation["source_before"] == preparation["source_after"]
                 and preparation["source_before"]["checkout"] == product_sha,
                 "preparation_source_or_profile_changed")
    previous = {item["relative"]: {"bytes": item["bytes"], "sha256": item["sha256"]}
                for item in document["files"]}
    selected = {}
    if storage_cohort is not None:
        cohorts, originals = admit_compiled_storage(root, preparation, summary, document, storage_cohort)
        selected = {"storage_cohorts": cohorts, "storage_original_paths": originals}
    return {**selected, "summary": summary, "main": main, "main_path": main_path,
            "profile": profile, "profile_pin": preparation["profile_sha256"],
            "previous": previous, "current": current, "setups": paths,
            "profile_document": document, "preparation": preparation}


def observe_installer(root, label, phase, executable, arguments, environment, token, source, run,
                      observe_after, *, live_observer=None, operation=None):
    """Retain the original result before state inspection; never launch in callbacks."""
    operation = str(uuid.uuid4()) if operation is None else life.operation_id(operation)
    directory = root / "operations" / label
    directory.mkdir(parents=True)
    executable_before = life.file_identity(executable)
    nsis.write_json(directory / "intent.json", {
        "schema": "motionwright.synthetic-windows-installer-intent/1", "phase": phase,
        "operation_id": operation, "source_sha": source, "run": run,
        "executable": executable_before, "arguments": arguments,
        "launch_limit": 1, "replay_authorized": False})
    outcome = launch_once_and_observe(executable, command_line(executable, arguments),
        environment, token=token, live_observer=live_observer)
    context = {"operation_id": operation, "process": outcome["process"],
               "executable": executable_before, "source_sha": source, "run": run}
    def after():
        nsis.require(outcome["original_terminal"]["exit_code"] is not None,
                     "installer_terminal_not_quiescent")
        return observe_after(operation, outcome)
    retained = life.retain_observed_result(directory, phase, outcome["original_terminal"], after,
                                          context=context)
    errors = outcome["custody_errors"] + retained["custody_errors"]
    record = {"schema": "motionwright.synthetic-windows-installer-operation/1",
              "operation_id": operation, "phase": phase, "context": context,
              "original_terminal": outcome["original_terminal"], "custody_errors": errors,
              "token": outcome["token"], "observation": retained["observation"],
              "live_observation": outcome["live_observation"], "launch_count": 1}
    nsis.write_json(directory / "operation.json", record)
    nsis.require(not errors and life.file_identity(executable) == executable_before,
                 "installer_custody_incomplete")
    return record, life.retained_ref(root, directory / (phase + "-terminal.json"))


def require_known_success(record):
    nsis.require(record["original_terminal"] == life.observed_terminal(0)
                 and not record["custody_errors"] and record["observation"] is not None,
                 "known_success_and_state_required")


def terminate_original_once(process):
    """One TerminateProcess on the retained original handle; never locate by PID."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    handle = wintypes.HANDLE(int(process._handle))
    # The caller has published the one-attempt intent and checked liveness.
    succeeded = bool(kernel.TerminateProcess(handle, 99))
    waited = kernel.WaitForSingleObject(handle, 15000)
    code = wintypes.DWORD()
    observed = bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) if waited == 0 else False
    return {"termination_attempts": 1, "same_original_handle": True,
            "terminate_returned_success": succeeded,
            "wait_result": "WAIT_OBJECT_0" if waited == 0 else "UNKNOWN",
            "exit_code": int(code.value) if observed else None,
            "ambiguous": not (succeeded and waited == 0 and observed and code.value == 99),
            "state": "TERMINATED" if succeeded and waited == 0 and observed and code.value == 99 else "UNKNOWN"}


def interrupt_observer(root, checkpoint_dir, operation, compiled, gate_sha, product_sha, run):
    checkpoint_path = checkpoint_dir / ("motionwright-nsis-" + operation + ".checkpoint")
    nsis.require(not checkpoint_path.exists(), "checkpoint_original_already_exists")
    setup = compiled["setups"]["opt-in"]
    def observe(process, initial):
        nsis.require(initial is not None, "original_process_identity_missing")
        deadline = time.monotonic() + 20
        raw = None
        # Observe one existing process and one CREATE_NEW receipt; do not replay it.
        while time.monotonic() < deadline:
            nsis.require(process.poll() is None, "original_exited_before_checkpoint")
            if checkpoint_path.exists():
                try:
                    raw, identity = nsis.bounded_read(checkpoint_path, 2048)
                except PermissionError:
                    # The producer has not closed its no-sharing handle yet.
                    time.sleep(0.05)
                    continue
                break
            time.sleep(0.05)
        nsis.require(raw is not None, "checkpoint_not_observed")
        checkpoint = nsis.parse_checkpoint(raw)
        retained = root / "interruption/checkpoint.original"
        nsis.write_new(retained, raw)
        nsis.require(life.file_identity(checkpoint_path) == identity
                     and life.file_identity(retained) == identity, "checkpoint_original_changed")
        nsis.require(process.poll() is None and process_identity(process) == initial,
                     "original_handle_expired_or_changed")
        require_original_image(process, setup)
        nsis.require(checkpoint["id"] == operation
                     and checkpoint["pid"] == str(initial["pid"])
                     and checkpoint["creation_filetime"] == str(initial["creation_filetime"])
                     and checkpoint["candidate_sha256"] == compiled["main"]["sha256"]
                     and checkpoint["candidate_size"] == str(compiled["main"]["bytes"]),
                     "checkpoint_original_identity_mismatch")
        context = {"schema": "motionwright.nsis-live-context/1", "state": "LIVE",
            "original_handle_retained": True, "same_handle_identity_observed": True,
            "private_directory_admitted": True, "predecessor_profile_admitted": True,
            **{key: checkpoint[key] for key in ("id", "pid", "creation_filetime",
                                               "candidate_sha256", "candidate_size")},
            "setup_sha256": life.file_identity(setup)["sha256"],
            "predecessor_profile_sha256": compiled["profile_pin"],
            "gate_sha": gate_sha, "product_sha": product_sha,
            "run_id": str(run["run_id"]), "run_attempt": str(run["run_attempt"]),
            "job": os.environ["GITHUB_JOB"], "launch_count": 1}
        nsis.validate_live_context(checkpoint, context)
        nsis.write_json(root / "interruption/live-context.json", context)
        # Publication failure here authorizes no termination. A possible effect is never repeated.
        nsis.write_json(root / "interruption/termination-intent.json", {
            "operation_id": operation, "original_process": initial,
            "termination_limit": 1, "replay_authorized": False})
        nsis.require(process.poll() is None and process_identity(process) == initial,
                     "original_handle_not_live_at_termination")
        original = terminate_original_once(process)
        terminal = {"schema": "motionwright.nsis-terminal/1",
            **{key: context[key] for key in ("id", "pid", "creation_filetime", "setup_sha256",
                "predecessor_profile_sha256", "gate_sha", "product_sha", "run_id", "run_attempt", "job")},
            **original}
        nsis.write_json(root / "interruption/terminal.json", terminal)
        nsis.validate_termination(checkpoint, context, terminal)
        return {"checkpoint": checkpoint, "context": context, "terminal": terminal,
                "checkpoint_ref": life.retained_ref(root, retained),
                "context_ref": life.retained_ref(root, root / "interruption/live-context.json"),
                "terminal_ref": life.retained_ref(root, root / "interruption/terminal.json")}
    return observe


def recovery_environment(source, normal, opt_in):
    nsis.require(life.file_identity(normal) != life.file_identity(opt_in),
                 "recovery_requires_distinct_normal_setup")
    environment = dict(source)
    environment.pop("MOTIONWRIGHT_NSIS_INTERRUPT_ID", None)
    nsis.require("MOTIONWRIGHT_NSIS_INTERRUPT_ID" not in environment, "recovery_interrupt_not_cleared")
    return environment


def bootstrap_previous(root, compiled, installed, scratch, *, storage_cohort=None):
    """Use the existing real cold-start bootstrap with only the pinned old metadata."""
    import candidate_smoke
    from windows_cold_start import run_installed_relaunch, select_first_use_contract
    previous = compiled["profile_document"]
    capture = json.loads((compiled["profile"].parent / "previous-capture-receipt.json").read_bytes())
    metadata = compiled["profile"].parent.parent / "previous-source-metadata"
    pins = compiled["preparation"]["previous_source_metadata"]
    nsis.require(set(pins) == {"SOURCE_LOCK.json", "Cargo.toml", "apps/desktop/src-tauri/tauri.conf.json"},
                 "previous_metadata_roster")
    for relative, value in pins.items():
        nsis.require(life.file_identity(metadata / relative) == value, "previous_metadata_changed")
    config = json.loads((metadata / "apps/desktop/src-tauri/tauri.conf.json").read_bytes())
    nsis.require(config["bundle"]["windows"]["nsis"]["installMode"] == "currentUser",
                 "previous_elevation_not_admitted")
    package_path = root / "previous-bootstrap/Motionwright_0.1.0_x64-setup.exe"
    package_path.parent.mkdir()
    _capture(compiled["profile"].parent / "previous-setup.exe", package_path)
    candidate = package_path.parent / "candidate.json"
    nsis.write_json(candidate, capture["candidate"])
    before = candidate_smoke.ROOT
    try:
        candidate_smoke.ROOT = metadata
        smoke = candidate_smoke.build_receipt(candidate_receipt=candidate, artifact=package_path,
            installed_binary=installed, kind="nsis", method="nsis_silent_current_user")
    finally:
        candidate_smoke.ROOT = before
    smoke_path = package_path.parent / "installed-smoke.json"
    nsis.write_json(smoke_path, smoke)
    contract = select_first_use_contract(previous["source_sha"],
                                        compiled["previous"]["motionwright-desktop.exe"]["sha256"])
    output = package_path.parent / "real-project.json"
    run_installed_relaunch(executable=installed, smoke_receipt=smoke_path,
        expected_sha=previous["source_sha"], scratch=scratch, output=output,
        first_use_contract=contract,
        **({"storage_cohort": storage_cohort} if storage_cohort is not None else {}))
    return life.retained_ref(root, output), contract


def snapshot(root, label, operation, position, install, database, keys, contract,
             *, storage_cohort=None):
    return life.capture_snapshot(root, snapshot_name=label, operation=operation,
        position=position, install_root=install, database=database, registry_keys=keys,
        first_use_contract=contract,
        **({"storage_cohort": storage_cohort} if storage_cohort is not None else {}))


def read_snapshot(root, reference, operation, position, keys, *, storage_cohort=None):
    return life.snapshot_value(root, reference, operation, position, keys,
        **({"storage_cohort": storage_cohort} if storage_cohort is not None else {}))


def run_default_uninstall_case(context, *, storage_consumer=None):
    root, compiled, environment, token, source, run, install, database, keys, contract = context
    selected = ({} if storage_consumer is None else {
        "storage_cohort": require_installed_storage_consumer(
            storage_consumer.cohorts["after"], consumer=storage_consumer)})
    oid = str(uuid.uuid4())
    before_ref = snapshot(root, "default-before", oid, "before", install, database, keys, contract, **selected)
    before = read_snapshot(root, before_ref, oid, "before", keys, **selected)
    args = installer_arguments("default")
    def installed(operation, outcome):
        nsis.require(outcome["original_terminal"] == life.observed_terminal(0), "default_not_known_success")
        after_ref = snapshot(root, "default-after", operation, "after", install, database, keys, contract, **selected)
        after = read_snapshot(root, after_ref, operation, "after", keys, **selected)
        admit_default_install(before, after, compiled["current"], keys, install, args)
        life.require_pe_version(life.read_pe_versions(install / "motionwright-desktop.exe"),
                                "0.1.1", compiled["main"])
        return after_ref
    row, terminal = observe_installer(root, "default-install", "install", compiled["setups"]["normal"],
        args, environment, token, source, run, installed, operation=oid)
    require_known_success(row)
    changed, intact = "mw-ci-render/resource-alpha.dat", "mw-ci-render/resource-beta.dat"
    foreign = "synthetic-user-foreign.txt"
    nsis.require(life.file_identity(install / changed) == compiled["current"][changed],
                 "owned_fixture_changed_before_deliberate_edit")
    # One deliberate synthetic user edit, only after the current installation was admitted.
    with (install / changed).open("ab") as stream:
        body = b"Synthetic user modification: preserve these bytes.\n"
        nsis.require(stream.write(body) == len(body), "synthetic_edit_short_write")
        stream.flush()
        os.fsync(stream.fileno())
    nsis.write_new(install / foreign, b"Synthetic foreign data: never installer ownership.\n")
    uninstall_id = str(uuid.uuid4())
    mixed_ref = snapshot(root, "uninstall-before", uninstall_id, "before",
                         install, database, keys, contract, **selected)
    mixed = read_snapshot(root, mixed_ref, uninstall_id, "before", keys, **selected)
    def uninstalled(operation, outcome):
        nsis.require(outcome["original_terminal"] == life.observed_terminal(0), "uninstall_not_known_success")
        after_ref = snapshot(root, "uninstall-after", operation, "after", install, database, keys, contract, **selected)
        after = read_snapshot(root, after_ref, operation, "after", keys, **selected)
        admit_mixed_uninstall(mixed, after, compiled["current"], keys, changed, intact, foreign)
        return after_ref
    row, uninstall_terminal = observe_installer(root, "mixed-uninstall", "uninstall",
        install / "uninstall.exe", installer_arguments("uninstall", install),
        environment, token, source, run, uninstalled, operation=uninstall_id)
    require_known_success(row)
    return {"state": "CURRENT_DEFAULT_AND_MIXED_UNINSTALL_OBSERVED",
            "default_terminal": terminal, "uninstall_terminal": uninstall_terminal,
            "default_before": before_ref, "mixed_before": mixed_ref, "mixed_after": row["observation"]}


def assemble_updated_application_input(root, compiled, refs, *, source, gate_sha, run,
                                       install, database, keys, project, storage_consumer=None):
    required = {"compile_summary", "normal_setup", "opt_in_setup", "current_main",
                "predecessor_profile", "checkpoint", "interruption_context",
                "interruption_terminal", "recovery_terminal", "before_snapshot", "recovered_snapshot"}
    nsis.require(set(refs) == required, "observer_reference_roster")
    if storage_consumer is not None:
        require_installed_storage_consumer(storage_consumer.cohorts["after"], consumer=storage_consumer)
        nsis.require(source == storage_consumer.cohorts["after"]["source_sha"],
                     "observer_product_cohort_mismatch")
        refs = {**refs, **storage_consumer.refs}
    for ref in refs.values():
        life.bound_file(root, ref)
    interruption = life.bound_json(root, refs["interruption_context"])
    recovery = life.bound_json(root, refs["recovery_terminal"])
    life.validate_terminal(recovery["original_terminal"])
    nsis.require(recovery["original_terminal"] == life.observed_terminal(0)
                 and recovery["context"]["executable"] == life.ref_identity(refs["normal_setup"])
                 and refs["normal_setup"]["sha256"] != refs["opt_in_setup"]["sha256"],
                 "observer_requires_known_normal_recovery")
    operation = str(uuid.uuid4())
    nsis.require(len({operation, str(uuid.UUID(interruption["id"].strip("{}"))),
                      recovery["context"]["operation_id"]}) == 3, "observer_operation_reused")
    value = {"schema": "motionwright.windows-updated-application-input/1",
        "operation_id": operation, "run": run, "product_sha": source, "gate_sha": gate_sha,
        "version": "0.1.1", "refs": refs, "registry_keys": keys, "install_root": str(install),
        "database": str(database), "executable": str(install / "motionwright-desktop.exe"),
        "project_id": project["project_id"], "generation": project["generation"],
        "revision": project["revision"], "old_title": project["title"],
        "new_title": "Motionwright synthetic recovered project"}
    if storage_consumer is not None:
        value["schema"] = "motionwright.windows-updated-application-input/2"
        value["storage_cohorts"] = storage_consumer.cohorts
        storage_consumer.oracle.input_shape(value)
        storage_consumer.oracle.admit_originals(root, value, nsis,
            admitted_product_cohort=storage_consumer.cohorts["after"])
    output = root / "updated-application-input.json"
    nsis.write_json(output, value)
    return output, life.file_identity(output)["sha256"]


def run_interruption_recovery_case(context, *, gate, gate_sha, oracle, oracle_sha, workspace, storage_consumer=None):
    root, compiled, environment, token, source, run, install, database, keys, contract = context
    selected = {}
    if storage_consumer is not None:
        require_installed_storage_consumer(storage_consumer.cohorts["after"], consumer=storage_consumer)
        selected = {"storage_cohort": storage_consumer.cohorts["before"]}
    previous_setup = compiled["profile"].parent / "previous-setup.exe"
    previous_id = str(uuid.uuid4())
    before_ref = snapshot(root, "previous-before", previous_id, "before",
                          install, database, keys, contract, **selected)
    before = read_snapshot(root, before_ref, previous_id, "before", keys, **selected)
    nsis.require(not life.file_map(before)
                 and all(v is None for rows in before["registry"].values() for v in rows.values())
                 and all(v is None for v in life.family_map(before).values()), "previous_namespace_not_fresh")
    def previous_installed(operation, outcome):
        nsis.require(outcome["original_terminal"] == life.observed_terminal(0), "previous_not_known_success")
        ref = snapshot(root, "previous-after", operation, "after", install, database, keys, contract, **selected)
        value = read_snapshot(root, ref, operation, "after", keys, **selected)
        nsis.require(life.file_map(value) == compiled["previous"]
                     and all(x is None for x in life.family_map(value).values()), "previous_inventory_changed")
        life.installed_registry(value, keys[0], "0.1.0")
        return ref
    row, previous_terminal = observe_installer(root, "previous-install", "install", previous_setup,
        installer_arguments("previous", install), environment, token, source, run,
        previous_installed, operation=previous_id)
    require_known_success(row)
    origin, contract = bootstrap_previous(root, compiled, install / "motionwright-desktop.exe",
                                         workspace / "synthetic-bootstrap", **selected)
    nsis.write_new(install / "synthetic-user-foreign.txt", b"Synthetic foreign preservation control.\n")
    old_stage = install / ("mw-stage-{" + str(uuid.uuid4()).upper() + "}")
    nsis.write_new(old_stage / "unrelated-stage.bin", b"Earlier unrelated synthetic stage.\n")
    interruption_guid = "{" + str(uuid.uuid4()).upper() + "}"
    interruption_id = str(uuid.UUID(interruption_guid.strip("{}")))
    original_ref = snapshot(root, "interruption-before", interruption_id, "before",
                            install, database, keys, contract, **selected)
    original = read_snapshot(root, original_ref, interruption_id, "before", keys, **selected)
    checkpoint_dir = workspace / "synthetic-checkpoint"
    private_directory = create_private_checkpoint_directory(checkpoint_dir, token)
    nsis.write_json(root / "interruption/private-directory.json", private_directory)
    interrupt_env = dict(environment)
    interrupt_env.update({"TEMP": str(checkpoint_dir), "TMP": str(checkpoint_dir),
                          "MOTIONWRIGHT_NSIS_INTERRUPT_ID": interruption_guid})
    live = interrupt_observer(root, checkpoint_dir, interruption_guid, compiled,
                              gate_sha, source, run)
    def interrupted(operation, outcome):
        observed = outcome["live_observation"]
        nsis.require(observed is not None and not outcome["custody_errors"]
                     and outcome["original_terminal"]["exit_code"] == observed["terminal"]["exit_code"],
                     "interruption_not_known_terminal")
        ref = snapshot(root, "interruption-after", operation, "after", install, database, keys, contract, **selected)
        value = read_snapshot(root, ref, operation, "after", keys, **selected)
        admission = admit_interrupted_state(original, value, observed["checkpoint"],
                                            compiled["previous"], compiled["main"], keys)
        nsis.write_json(root / "interruption/state-admission.json", admission)
        return ref
    row, unused = observe_installer(root, "interrupt-once", "upgrade", compiled["setups"]["opt-in"],
        installer_arguments("interrupt", install), interrupt_env, token, source, run,
        interrupted, live_observer=live, operation=interruption_id)
    live_result = row["live_observation"]
    nsis.require(live_result is not None and row["observation"] is not None
                 and row["original_terminal"]["exit_code"] == 99, "recovery_not_authorized")
    interrupted_state = read_snapshot(root, row["observation"], interruption_id, "after", keys, **selected)
    recovery_id = str(uuid.uuid4())
    nsis.require(recovery_id != interruption_id, "recovery_operation_reused")
    recovery_env = recovery_environment(environment, compiled["setups"]["normal"], compiled["setups"]["opt-in"])
    stage = live_result["checkpoint"]["stage"].replace("\\", "/")
    def recovered(operation, outcome):
        nsis.require(outcome["original_terminal"] == life.observed_terminal(0), "recovery_not_known_success")
        ref = snapshot(root, "recovery-after", operation, "after", install, database, keys, contract, **selected)
        after = read_snapshot(root, ref, operation, "after", keys, **selected)
        admission = admit_recovered_state(original, interrupted_state, after, compiled["current"],
                                          compiled["previous"], keys, stage)
        nsis.write_json(root / "recovery-state-admission.json", admission)
        return ref
    recovery, terminal = observe_installer(root, "normal-recovery", "upgrade", compiled["setups"]["normal"],
        installer_arguments("recover", install), recovery_env, token, source, run,
        recovered, operation=recovery_id)
    require_known_success(recovery)
    refs = {"compile_summary": life.retained_ref(root, root / "compile/compile-summary.json"),
            "normal_setup": life.retained_ref(root, compiled["setups"]["normal"]),
            "opt_in_setup": life.retained_ref(root, compiled["setups"]["opt-in"]),
            "current_main": life.retained_ref(root, compiled["main_path"]),
            "predecessor_profile": life.retained_ref(root, compiled["profile"]),
            "checkpoint": live_result["checkpoint_ref"],
            "interruption_context": live_result["context_ref"],
            "interruption_terminal": live_result["terminal_ref"], "recovery_terminal": terminal,
            "before_snapshot": original_ref, "recovered_snapshot": recovery["observation"]}
    input_path, pin = assemble_updated_application_input(root, compiled, refs, source=source,
        gate_sha=gate_sha, run=run, install=install, database=database, keys=keys, project=original["project"],
        **({"storage_consumer": storage_consumer} if storage_consumer is not None else {}))
    output = workspace / "observer-output"
    nsis.write_json(root / "observer-intent.json", {"oracle_source": oracle_sha,
        "input_sha256": pin, "launch_limit": 1, "replay_authorized": False})
    arguments = [sys.executable, "-B", "-E", "-s",
        str(oracle / "tooling/windows_updated_application.py"),
        "--product-repo", str(workspace.parents[1] / "product"),
        "--gate-repo", str(gate), "--product-source", source, "--gate-source", gate_sha,
        "--oracle-source", oracle_sha, "--input", str(input_path), "--input-sha256", pin,
        "--evidence-root", str(root), "--output", str(output)]
    invoke_updated_observer(root, arguments, recovery_env)
    return {"state": "INTERRUPTION_RECOVERY_AND_UPDATED_APPLICATION_OBSERVED",
            "previous_terminal": previous_terminal, "project_origin": origin,
            "interruption_terminal": refs["interruption_terminal"], "normal_recovery_terminal": terminal,
            "observer_input_sha256": pin, "oracle_source": oracle_sha,
            "human_acceptance": "NOT_RUN"}


def require_installed_storage_consumer(cohort, *, consumer=None):
    """Legacy invocation remains closed; schema four needs original-source admission."""
    value = storage.validate_cohort(cohort)
    if value["storage_schema"] == 4:
        nsis.require(isinstance(consumer, _StorageConsumerAdmission)
                     and consumer.cohorts["after"] == value,
                     "schema4_installed_oracle_requires_reviewed_successor")
        require_reviewed_product_source(value["source_sha"])
    else:
        nsis.require(consumer is None, "historical_consumer_cannot_select_schema4_contract")
    return value


def retain_original_token(root, value):
    """Retain bounded OS observations before the unchanged privilege admission."""
    nsis.require(isinstance(value, dict) and set(value) == {
        "elevated", "integrity_rid", "user_sid", "session_id"},
        "token_original_fields")
    nsis.require(type(value["elevated"]) is bool
                 and all(type(value[key]) is int and 0 <= value[key] <= 0xffffffff
                         for key in ("integrity_rid", "session_id"))
                 and isinstance(value["user_sid"], str)
                 and len(value["user_sid"].encode("utf-8")) <= 256,
                 "token_original_bound")
    nsis.write_json(root / "actual-token-original.json", {
        "schema": "motionwright.original-windows-token/1",
        "observation": value, "admission": "NOT_YET_ATTEMPTED"})


def physical_case_error(phase, error):
    """Keep closed error codes and fixed phases without arbitrary error text."""
    phases = {"storage_cohort_admission", "token_observation", "profile_observation", "compile_admission",
              "profile_admission", "installed_version", "registry_preflight",
              "namespace_preflight", "installer_case", "source_postcheck"}
    stage = phase if phase in phases else "physical_case"
    result = life.custody_error(stage, error)
    if isinstance(error, nsis.ContractError):
        try:
            code = str(error)
            if re.fullmatch(r"[a-z][a-z0-9_]{0,95}", code):
                result["code"] = code
        except Exception:
            pass
    return result


def run_physical_case(args):
    environment = native_windows_launch_environment(dict(os.environ))
    run = physical_scope(environment, args.case)
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "MOTIONWRIGHT_NSIS_INTERRUPT_ID",
                 "MW_NSIS_PREDECESSOR_MANIFEST", "MW_NSIS_PREDECESSOR_MANIFEST_SHA256"):
        environment.pop(name, None)
    nsis.require(args.oracle_sha in (LEGACY_ORACLE_SHA, REVIEWED_SCHEMA4_ORACLE_SHA),
                 "oracle_source_outside_reviewed_contract")
    for value in (args.gate_sha, args.product_sha, args.oracle_sha):
        nsis.require(nsis.SHA40.fullmatch(value), "physical_source_not_exact")
    workspace = Path(os.environ["GITHUB_WORKSPACE"]).absolute()
    gate, product, oracle = (workspace / name for name in ("gate", "product", "oracle"))
    case_root = workspace / "nsis-physical" / args.case
    root = case_root / "originals"
    nsis.require(root.is_dir() and {path.name for path in root.iterdir()} == {"compile"},
                 "physical_output_not_fresh")
    oracle_paths = ORACLE_PATHS if args.oracle_sha == REVIEWED_SCHEMA4_ORACLE_SHA else LEGACY_ORACLE_PATHS
    sources = {"gate": nsis.source_observation(gate, args.gate_sha, nsis.GATE_PATHS),
               "product": nsis.source_observation(product, args.product_sha, nsis.PRODUCT_PATHS),
               "oracle": nsis.source_observation(oracle, args.oracle_sha, oracle_paths)}
    nsis.write_json(root / "source-before.json", sources)
    result = {"schema": "motionwright.synthetic-windows-nsis-case/1", "case": args.case,
              "status": "INCOMPLETE", "source_before": sources, "run": run,
              "human_acceptance": "NOT_RUN", "release_published": False}
    phase = "storage_cohort_admission"
    try:
        cohort = storage.admit_source(sources["product"],
                                      (product / storage.SOURCE_PATH).read_bytes())
        result["storage_cohort"] = cohort
        compiled, storage_consumer = None, None
        if cohort["storage_schema"] == 4 and args.oracle_sha == REVIEWED_SCHEMA4_ORACLE_SHA:
            require_reviewed_product_source(args.product_sha)
            phase = "compile_admission"
            compiled = admit_compiled(root / "compile", args.gate_sha, args.product_sha, run,
                                      storage_cohort=cohort)
            storage_consumer = admit_storage_consumer(root, compiled, cohort, oracle,
                                                      args.oracle_sha, sources["oracle"])
        require_installed_storage_consumer(cohort, consumer=storage_consumer)
        phase = "token_observation"
        token = observe_non_elevated_token(
            before_admission=lambda value: retain_original_token(root, value))
        phase = "profile_observation"
        profile = actual_profile(environment)
        nsis.write_json(root / "actual-token-and-profile.json",
                        {"token": token, "profile": {k: str(v) for k, v in profile.items()}})
        from windows_cold_start import known_databases, CURRENT_FIRST_USE_CONTRACT
        nsis.require(not known_databases(list(profile.values())), "physical_project_store_not_fresh")
        phase = "compile_admission"
        if compiled is None:
            compiled = admit_compiled(root / "compile", args.gate_sha, args.product_sha, run)
        phase = "profile_admission"
        admit_profile_family(compiled["profile"], compiled["profile_pin"], compiled["main"],
                             root / "profile-admission")
        phase = "installed_version"
        life.require_pe_version(life.read_pe_versions(compiled["main_path"]), "0.1.1", compiled["main"])
        phase = "registry_preflight"
        renders = list((root / "compile/variants/normal/render").rglob("installer.nsi"))
        nsis.require(len(renders) == 1, "registry_render_count")
        manufacturer = life.rendered_define(renders[0].read_text(encoding="utf-8-sig"), "MANUFACTURER")
        nsis.require(re.fullmatch(r"[A-Za-z0-9_. -]{1,128}", manufacturer), "registry_manufacturer_invalid")
        keys = [r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Motionwright",
                "Software\\" + manufacturer + r"\Motionwright"]
        install = (profile["LOCALAPPDATA"] / "Motionwright" if args.case == "default-uninstall"
                   else Path(os.environ["RUNNER_TEMP"]) / "motionwright-synthetic-recovery-install")
        phase = "namespace_preflight"
        nsis.require(not install.exists(), "physical_install_root_not_fresh")
        database = profile["APPDATA"] / "com.seradotcom.motionwright/motionwright.sqlite3"
        context = (root, compiled, environment, token, args.product_sha, run,
                   install, database, keys, CURRENT_FIRST_USE_CONTRACT)
        phase = "installer_case"
        if args.case == "default-uninstall":
            result["observation"] = run_default_uninstall_case(context,
                **({"storage_consumer": storage_consumer} if storage_consumer is not None else {}))
        else:
            result["observation"] = run_interruption_recovery_case(context, gate=gate,
                gate_sha=args.gate_sha, oracle=oracle, oracle_sha=args.oracle_sha, workspace=case_root,
                **({"storage_consumer": storage_consumer} if storage_consumer is not None else {}))
        phase = "source_postcheck"
        after = {"gate": nsis.source_observation(gate, args.gate_sha, nsis.GATE_PATHS),
                 "product": nsis.source_observation(product, args.product_sha, nsis.PRODUCT_PATHS),
                 "oracle": nsis.source_observation(oracle, args.oracle_sha, oracle_paths)}
        nsis.require(after == sources, "physical_source_changed")
        result.update(status="OBSERVED", source_after=after)
    except Exception as error:
        result["error"] = physical_case_error(phase, error)
    finally:
        finalize_case_result(root, case_root, result)
    print(json.dumps({"case": args.case, "status": result["status"], "human_acceptance": "NOT_RUN"}))
    return 0 if result["status"] == "OBSERVED" else 1


LEGACY_ORACLE_PATHS = (
    "tooling/windows_updated_application.py", "tooling/windows_updated_application_ui.ps1",
    "tooling/tests/test_windows_updated_application.py",
    "tooling/tests/test_windows_updated_application_ui_contract.py",
    "docs/reliability/windows-updated-application.md",
    "tooling/windows_cold_start.py", "tooling/windows_installer_lifecycle.py",
    "tooling/nsis_owned_files.py", "tooling/release_manifest.py",
)

ORACLE_PATHS = (*LEGACY_ORACLE_PATHS,
    "tooling/storage_schema_contract.py", "tooling/tests/test_storage_schema_contract.py",
)


def create_private_checkpoint_directory(path, token):
    """Create the receipt directory with a protected DACL, then inspect it."""
    admit_token(token)
    plain_directory(path.parent)
    nsis.require(not path.exists(), "checkpoint_directory_not_fresh")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    descriptor = ctypes.c_void_p()
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    sddl = "D:P(A;;FA;;;SY)(A;;FA;;;" + token["user_sid"] + ")"
    nsis.require(advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None), "checkpoint_security_descriptor_failed")
    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("descriptor", ctypes.c_void_p),
                    ("inherit", wintypes.BOOL)]
    attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
    kernel.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(SecurityAttributes)]
    kernel.CreateDirectoryW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        nsis.require(kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)),
                     "private_checkpoint_directory_create_failed")
    finally:
        kernel.LocalFree(descriptor)
    observed = ctypes.c_void_p()
    advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    nsis.require(advapi.GetNamedSecurityInfoW(str(path), 1, 4, None, None, None, None,
        ctypes.byref(observed)) == 0, "checkpoint_dacl_readback_failed")
    text = wintypes.LPWSTR()
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p]
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    try:
        nsis.require(advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            observed, 1, 4, ctypes.byref(text), None), "checkpoint_dacl_encoding_failed")
        actual = text.value
        nsis.require(actual in (sddl, "D:P(A;;FA;;;" + token["user_sid"] + ")(A;;FA;;;SY)"),
                     "checkpoint_dacl_not_private")
    finally:
        if text:
            kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
        kernel.LocalFree(observed)
    plain_directory(path)
    return {"path": str(path), "dacl": actual, "private_directory_admitted": True}


def synthetic_upload_budget(case_root):
    """Bound only newly produced originals, excluding the separately retained compile artifact."""
    total = 0
    files = 0
    originals = case_root / "originals"
    for path in sorted(originals.iterdir()):
        if path.name == "compile":
            continue
        if path.is_dir():
            inventory = nsis.archive_inventory(path)
            total += sum(inventory.values())
            files += len(inventory)
        else:
            observed = nsis.regular_file(path, nsis.file_bound(path))
            total += observed["bytes"]
            files += 1
    for relative in ("observer-output", "synthetic-bootstrap", "synthetic-checkpoint"):
        path = case_root / relative
        if path.exists():
            inventory = nsis.archive_inventory(path)
            total += sum(inventory.values())
            files += len(inventory)
    nsis.require(total <= nsis.MAX_DIAGNOSTIC_BYTES and files <= nsis.MAX_DIAGNOSTIC_FILES,
                 "synthetic_upload_budget_exceeded")
    return {"bytes": total, "files": files, "maximum_bytes": nsis.MAX_DIAGNOSTIC_BYTES,
            "maximum_files": nsis.MAX_DIAGNOSTIC_FILES, "compile_artifact": "RETAINED_SEPARATELY"}



def admit_reviewed_predecessor(document):
    fixed = FIXED_PREDECESSOR
    nsis.require(document["source_sha"] == fixed["source"]
                 and document["source_tree"] == fixed["tree"]
                 and document["identity"] == {"application": "Motionwright",
                     "bundle_id": "com.seradotcom.motionwright", "arch": "x64"}
                 and document["version"] == "0.1.0", "predecessor_literal_authority_changed")
    for key, label, path in (("package", "package", "previous-setup.exe"),
                             ("capture_receipt", "receipt", "previous-capture-receipt.json")):
        nsis.require(document[key] == {"path": path,
            "bytes": fixed[label]["bytes"], "sha256": fixed[label]["sha256"]},
            "predecessor_literal_family_changed")
    expected = [
        {"relative": "motionwright-desktop.exe", "source": "previous-main.exe", "kind": "main",
         "bytes": fixed["main"]["bytes"], "sha256": fixed["main"]["sha256"]},
        {"relative": "uninstall.exe", "source": "previous-uninstall.exe", "kind": "uninstaller",
         "bytes": fixed["self"]["bytes"], "sha256": fixed["self"]["sha256"]}]
    nsis.require(document["files"] == expected, "predecessor_literal_owned_inventory_changed")


def invoke_updated_observer(root, arguments, environment):
    """One controller process; a timeout does not kill or replay its application."""
    original = life.observed_terminal(None)
    errors = []
    try:
        with (root / "observer.stdout").open("xb") as out, (root / "observer.stderr").open("xb") as err:
            launched = launch_once_and_observe(Path(arguments[0]),
                subprocess.list2cmdline(arguments), environment, timeout=360,
                stdout=out, stderr=err)
            original = launched["original_terminal"]
            errors.extend(launched["custody_errors"])
    except Exception as error:
        errors.append(life.custody_error("observer_stream_custody", error))
    finally:
        nsis.write_json(root / "observer-terminal.json", {
            "original_terminal": original, "custody_errors": errors,
            "launch_limit": 1, "replay_authorized": False})
    nsis.require(original == life.observed_terminal(0) and not errors, "updated_application_incomplete")
    return original



def finalize_case_result(root, case_root, result):
    """Retain an already known observation even when aggregate custody fails."""
    try:
        budget = synthetic_upload_budget(case_root)
        result["upload_budget"] = budget
        projected = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8")
        nsis.require(len(projected) <= nsis.MAX_RENDER_BYTES
                     and budget["bytes"] + len(projected) <= nsis.MAX_DIAGNOSTIC_BYTES
                     and budget["files"] + 1 <= nsis.MAX_DIAGNOSTIC_FILES,
                     "synthetic_final_projection_budget_exceeded")
    except Exception as error:
        result["status"] = "INCOMPLETE"
        result["upload_admission"] = {"state": "NOT_ADMITTED", "error": type(error).__name__,
                                      "originals_removed": False}
    nsis.write_json(root / "case-result.json", result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scope", choices=("prepare-legacy", "legacy-lifecycle", "physical"))
    parser.add_argument("--case", choices=("default-uninstall", "interruption-recovery"))
    for name in ("gate-sha", "product-sha", "oracle-sha"):
        parser.add_argument("--" + name)
    args = parser.parse_args(argv)
    if args.scope == "physical":
        if args.case is None or any(getattr(args, key) is None for key in ("gate_sha", "product_sha", "oracle_sha")):
            parser.error("physical requires one closed case and three exact source identities")
        return run_physical_case(args)
    if any(getattr(args, key) is not None for key in ("case", "gate_sha", "product_sha", "oracle_sha")):
        parser.error("legacy scopes do not accept physical inputs")
    if args.scope == "prepare-legacy":
        prepare_predecessor()
    elif args.scope == "legacy-lifecycle":
        run_legacy_lifecycle()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
