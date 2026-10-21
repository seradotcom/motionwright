"""Custody and readback for the existing Windows candidate lifecycle job.

This module never starts, stops, retries, or approves an installer. The existing
Candidate Packages producer owns each effect. Receipts preserve its original
terminal result separately from filesystem observations and publication errors.
"""
from __future__ import annotations

import argparse
import copy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid

import storage_schema_contract as storage

from nsis_owned_files import (
    _capture, _check_path_syntax, _relative, _require_plain_parents,
    _require_regular, _signature, _source_change_time, _read_flags,
)
from package_receipt import verified_artifact
from release_manifest import ManifestError, _checkout, _strict_object, write_new_manifest
from windows_cold_start import (
    CURRENT_FIRST_USE_CONTRACT, read_project_snapshot, select_first_use_contract,
)

ROOT = Path(__file__).resolve().parents[1]
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
MAX_JSON = 4 * 1024 * 1024
MAX_FILES = 4096
MAX_BYTES = 512 * 1024 * 1024
PHASES = ("install", "upgrade", "uninstall")
STATES = {"ACK", "ERROR", "UNKNOWN"}
SOURCE_PATHS = (
    ".github/workflows/package-candidate.yml",
    "apps/desktop/src-tauri/tauri.conf.json",
    "apps/desktop/src-tauri/build.rs",
    "apps/desktop/src-tauri/Cargo.toml",
    "apps/desktop/src-tauri/windows/installer.nsi",
    "apps/desktop/src-tauri/windows/uninstall-ownership.nsh",
    "tooling/nsis_owned_files.py",
    "tooling/tests/test_nsis_owned_files.py",
    "tooling/windows_installer_lifecycle.py",
    "tooling/tests/test_windows_installer_lifecycle.py",
    "tooling/windows_installer_producer.py",
    "tooling/tests/test_windows_installer_producer.py",
    "tooling/ci_test_runner.py",
    "tooling/package_receipt.py",
    "tooling/release_manifest.py",
    "tooling/v01_release_readiness.py",
    "tooling/candidate_smoke.py",
    "tooling/windows_cold_start.py",
    "tooling/storage_schema_contract.py",
    "tooling/tests/test_storage_schema_contract.py",
    "tooling/windows_nsis_interruption.py",
    "crates/storage/src/lib.rs",
    "tooling/windows_first_project_ui.ps1",
    "apps/studio/package.json",
    "apps/studio/package-lock.json",
    "Cargo.toml", "Cargo.lock", "SOURCE_LOCK.json", "rust-toolchain.toml",
)
REFERENCE_ROLES = {
    "compiled", "inputs", "candidate", "profile", "rendered",
    "previous_setup", "current_setup", "previous_capture",
    "previous_main", "current_main", "version_overlay",
}


class LifecycleError(ValueError):
    """Closed technical failure; never an installation or acceptance verdict."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise LifecycleError(code)


def exact(value, fields, code):
    require(isinstance(value, dict) and set(value) == set(fields), code)
    return value


def identity(value):
    exact(value, {"bytes", "sha256"}, "file_identity_fields")
    require(type(value["bytes"]) is int and 0 <= value["bytes"] <= MAX_BYTES,
            "file_identity_size")
    require(isinstance(value["sha256"], str) and SHA64.fullmatch(value["sha256"]),
            "file_identity_digest")
    return value


def version(value):
    require(isinstance(value, str) and VERSION.fullmatch(value), "version_not_canonical")
    values = tuple(int(part) for part in value.split("."))
    require(all(part <= 65535 for part in values), "version_outside_pe_range")
    return values


def source_id(value):
    require(isinstance(value, str) and SHA40.fullmatch(value), "source_identity_invalid")
    return value


def operation_id(value):
    require(isinstance(value, str), "operation_identity_invalid")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise LifecycleError("operation_identity_invalid") from error
    require(str(parsed) == value and parsed.int != 0, "operation_identity_invalid")
    return value


def run_identity(value):
    exact(value, {"repository", "run_id", "run_attempt"}, "run_identity_fields")
    require(isinstance(value["repository"], str) and re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"]), "repository_invalid")
    for key in ("run_id", "run_attempt"):
        require(type(value[key]) is int and value[key] > 0, "run_identity_invalid")
    return value


def path_under(root: Path, relative: str) -> Path:
    canonical = _relative(relative, "retained evidence")
    require(canonical == relative, "retained_path_not_canonical")
    result = root.joinpath(*canonical.split("/"))
    _require_plain_parents(result)
    return result


def file_identity(path: Path) -> dict:
    """Stable regular bytes, including an existing zero-byte WAL/SHM file."""
    _check_path_syntax(str(path))
    _require_plain_parents(path)
    first = path.lstat()
    _require_regular(first, "evidence")
    require(0 <= first.st_size <= MAX_BYTES, "evidence_size_bound")
    if first.st_size:
        _, size, digest = verified_artifact(path.parent, path)
        return {"bytes": size, "sha256": digest}
    with os.fdopen(os.open(path, _read_flags()), "rb") as stream:
        opened = os.fstat(stream.fileno())
        change = _source_change_time(stream.fileno())
        require(_signature(opened) == _signature(os.fstat(stream.fileno())),
                "empty_evidence_changed")
        require(stream.read(1) == b"" and opened.st_size == 0, "empty_evidence_changed")
        require(_source_change_time(stream.fileno()) == change, "empty_evidence_changed")
    final = path.lstat()
    require(_signature(first) == _signature(final)
            and (first.st_dev, first.st_ino) == (opened.st_dev, opened.st_ino),
            "empty_evidence_changed")
    return {"bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()}


def ref_identity(ref):
    exact(ref, {"path", "bytes", "sha256"}, "reference_fields")
    return identity({key: ref[key] for key in ("bytes", "sha256")})


def bound_file(root: Path, ref: dict) -> Path:
    expected = ref_identity(ref)
    path = path_under(root, ref["path"])
    require(file_identity(path) == expected, "retained_bytes_mismatch")
    return path


def bound_json(root: Path, ref: dict):
    path = bound_file(root, ref)
    require(0 < ref["bytes"] <= MAX_JSON, "json_size_bound")
    raw = path.read_bytes()
    require(len(raw) == ref["bytes"]
            and hashlib.sha256(raw).hexdigest() == ref["sha256"]
            and file_identity(path) == ref_identity(ref), "json_changed")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (ValueError, UnicodeError) as error:
        raise LifecycleError("json_not_strict") from error


def retained_ref(root: Path, path: Path) -> dict:
    return {"path": path.relative_to(root).as_posix(), **file_identity(path)}


def source_observation(root: Path, expected_sha: str) -> dict:
    _checkout(root, source_id(expected_sha))
    run = subprocess.run(["git", "ls-files", "--error-unmatch", "--", *SOURCE_PATHS],
                         cwd=root, capture_output=True, check=True, timeout=15)
    tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=root,
                          capture_output=True, text=True, check=True, timeout=15).stdout.strip()
    result = {"checkout": expected_sha, "tree": source_id(tree),
              "files": {name: file_identity(root / name) for name in SOURCE_PATHS}}
    _checkout(root, expected_sha)
    return result


def fixed_pe_versions(words):
    require(isinstance(words, (list, tuple)) and len(words) == 13
            and all(type(word) is int and 0 <= word <= 0xFFFFFFFF for word in words),
            "pe_fixed_info_invalid")
    require(words[0] == 0xFEEF04BD and words[1] in {0, 0x00010000}, "pe_fixed_info_signature")
    split = lambda high, low: [high >> 16, high & 65535, low >> 16, low & 65535]
    return {"file_version": split(words[2], words[3]),
            "product_version": split(words[4], words[5])}


def read_pe_versions(path: Path) -> dict:
    """Read actual VS_FIXEDFILEINFO and present version strings; no execution."""
    require(os.name == "nt", "pe_observation_requires_windows")
    before = file_identity(path)
    from ctypes import wintypes
    library = ctypes.WinDLL("version", use_last_error=True)
    library.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    library.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    library.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                          wintypes.DWORD, ctypes.c_void_p]
    library.GetFileVersionInfoW.restype = wintypes.BOOL
    library.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                     ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    library.VerQueryValueW.restype = wintypes.BOOL
    unused = wintypes.DWORD()
    size = library.GetFileVersionInfoSizeW(str(path), ctypes.byref(unused))
    require(52 <= size <= 1024 * 1024, "pe_version_resource_missing")
    data = ctypes.create_string_buffer(size)
    require(library.GetFileVersionInfoW(str(path), 0, size, data), "pe_version_resource_read")
    address = ctypes.c_void_p()
    length = wintypes.UINT()
    require(library.VerQueryValueW(data, "\\", ctypes.byref(address), ctypes.byref(length))
            and address.value and length.value >= 52, "pe_fixed_info_missing")
    words = list(ctypes.cast(address, ctypes.POINTER(wintypes.DWORD * 13)).contents)
    result = {**fixed_pe_versions(words), "strings": {}}
    if library.VerQueryValueW(data, r"\VarFileInfo\Translation",
                             ctypes.byref(address), ctypes.byref(length)):
        require(address.value and length.value % 4 == 0 and 0 < length.value <= 64,
                "pe_translation_invalid")
        translations = list(ctypes.cast(
            address, ctypes.POINTER(wintypes.WORD * (length.value // 2))).contents)
        for offset in range(0, len(translations), 2):
            key = f"{translations[offset]:04x}{translations[offset + 1]:04x}"
            require(key not in result["strings"], "pe_translation_duplicate")
            strings = {}
            for name in ("FileVersion", "ProductVersion"):
                query = rf"\StringFileInfo\{key}\{name}"
                if library.VerQueryValueW(data, query, ctypes.byref(address), ctypes.byref(length)):
                    require(address.value and 1 <= length.value <= 128, "pe_version_string_bound")
                    strings[name] = ctypes.wstring_at(address, length.value).rstrip("\0")
            result["strings"][key] = strings
    require(file_identity(path) == before, "pe_changed_during_read")
    return {"binary": before, **result}


def require_pe_version(observation: dict, expected_version: str, expected_file: dict):
    exact(observation, {"binary", "file_version", "product_version", "strings"}, "pe_observation_fields")
    expected = [*version(expected_version), 0]
    require(observation["binary"] == expected_file, "pe_binary_binding")
    require(observation["file_version"] == expected
            and observation["product_version"] == expected, "pe_version_mismatch")
    require(isinstance(observation["strings"], dict), "pe_strings_invalid")
    for translation, values in observation["strings"].items():
        require(re.fullmatch(r"[0-9a-f]{8}", translation)
                and isinstance(values, dict) and set(values) <= {"FileVersion", "ProductVersion"},
                "pe_strings_invalid")
        for value in values.values():
            require(value in {expected_version, expected_version + ".0"}, "pe_string_version_mismatch")


def rendered_define(text: str, name: str) -> str:
    values = re.findall(r'^!define ' + re.escape(name) + r' "([^"\r\n]*)"\r?$',
                        text, re.MULTILINE)
    require(len(values) == 1, "rendered_identity_missing_or_duplicate")
    return values[0]


def payloads(value, *, with_self):
    require(isinstance(value, dict) and 1 <= len(value) <= 64, "payload_inventory_bound")
    result = {}
    names = set()
    kinds = []
    for relative, item in value.items():
        require(_relative(relative, "payload") == relative, "payload_path_invalid")
        require(relative.casefold() not in names, "payload_alias")
        names.add(relative.casefold())
        require(isinstance(item, dict) and item.get("kind") in {
            "main", "resource", "external_binary", "uninstaller"}, "payload_kind_invalid")
        kind = item["kind"]
        require((relative == "uninstall.exe") == (kind == "uninstaller"), "payload_self_role")
        kinds.append(kind)
        result[relative] = identity({key: item[key] for key in ("bytes", "sha256")})
    require(kinds.count("main") == 1
            and kinds.count("uninstaller") == int(with_self), "payload_roles_invalid")
    return result


def validate_rendered_resource_directories(text: str, resource_paths) -> dict:
    """Bind the rendered directory instructions to the captured resource names.

    This checks the existing template's closed instruction shape, not runtime
    directory creation or a successful installation.
    """
    require(isinstance(text, str) and 0 < len(text) <= MAX_JSON,
            "rendered_template_size")
    require("{{" not in text and "}}" not in text, "unexpanded_template_expression")
    require(isinstance(resource_paths, (list, tuple)) and len(resource_paths) <= 64,
            "rendered_resource_inventory")

    def canonical(value, *, empty=False):
        require(isinstance(value, str) and not any(c in value for c in '$"\r\n\0'),
                "rendered_resource_directory_literal")
        if not value:
            require(empty, "rendered_resource_directory_empty")
            return ""
        normalized = value.replace("\\", "/")
        try:
            require(_relative(normalized, "rendered resource") == normalized,
                    "rendered_resource_directory_path")
        except ValueError as error:
            raise LifecycleError("rendered_resource_directory_path") from error
        return normalized

    resources = [canonical(path) for path in resource_paths]
    require(all(path == original for path, original in zip(resources, resource_paths))
            and len({path.casefold() for path in resources}) == len(resources),
            "rendered_resource_inventory")
    expected_dirs = {path.rpartition("/")[0] for path in resources}
    expected_ancestors = set()
    for directory in expected_dirs:
        while directory:
            expected_ancestors.add(directory)
            directory = directory.rpartition("/")[0]

    def section(name):
        matches = re.findall(r"^[ \t]*Section " + name
                             + r"[ \t]*\r?\n(.*?)^[ \t]*SectionEnd[ \t]*\r?$",
                             text, re.MULTILINE | re.DOTALL)
        require(len(matches) == 1, "rendered_resource_section")
        return [line.strip() for line in matches[0].splitlines()
                if line.strip() and not line.lstrip().startswith(";")]

    install, uninstall = section("Install"), section("Uninstall")
    main = '!insertmacro MWINST_INSTALL_PAYLOAD "${MAINBINARYNAME}.exe" MW_MAIN'
    require(install.count(main) == 1, "rendered_resource_main_instruction")
    main_at = install.index(main)

    def resource_calls(lines, deletion):
        pattern = (r'!insertmacro MWINST_DELETE_OWNED "([^"]+)" '
                   r'"\$\{MW_RESOURCE_([0-9]+)_SHA256\}" "\$\{MW_RESOURCE_\2_SIZE\}"'
                   if deletion else
                   r'!insertmacro MWINST_INSTALL_PAYLOAD "([^"]+)" MW_RESOURCE_([0-9]+)')
        found = [(i, canonical(match.group(1)))
                 for i, line in enumerate(lines)
                 if (match := re.fullmatch(pattern, line))]
        require(len(found) == len(resources)
                and {path for _, path in found} == set(resources),
                "rendered_resource_calls")
        return [i for i, _ in found]

    install_calls = resource_calls(install, False)
    delete_calls = resource_calls(uninstall, True)

    def directories(lines, operation):
        result = []
        for i, line in enumerate(lines):
            if not re.match(operation + r"\b", line):
                continue
            require(line == operation + r' "$INSTDIR\$MWINST_Relative"',
                    "rendered_resource_directory_instruction")
            assignment_at = i - (2 if operation == "CreateDirectory" else 1)
            require(assignment_at >= 0, "rendered_resource_directory_assignment")
            assignment = re.fullmatch(r'StrCpy \$MWINST_Relative "([^"]*)"',
                                      lines[assignment_at])
            require(assignment is not None, "rendered_resource_directory_assignment")
            directory = canonical(assignment.group(1), empty=operation == "CreateDirectory")
            if operation == "CreateDirectory":
                require(lines[i - 1] == "ClearErrors" and lines[i + 1:i + 4] == [
                    "${If} ${Errors}",
                    '!insertmacro MWINST_FAIL "A resource directory could not be created."',
                    "${EndIf}"], "rendered_resource_directory_error_guard")
                require(main_at < assignment_at
                        and install_calls and i + 3 < min(install_calls),
                        "rendered_resource_directory_order")
            else:
                require(delete_calls and assignment_at > max(delete_calls),
                        "rendered_resource_directory_order")
            result.append(directory)
        require(len(result) == len(set(result)), "rendered_resource_directory_duplicate")
        return result

    created = directories(install, "CreateDirectory")
    removed = directories(uninstall, "RMDir")
    require(set(created) == expected_dirs, "rendered_resource_directories_mismatch")
    require(set(removed) == expected_ancestors, "rendered_resource_ancestors_mismatch")
    depths = [len(path.split("/")) for path in removed]
    require(depths == sorted(depths, reverse=True), "rendered_resource_ancestor_order")
    return {"install_directories": created, "uninstall_ancestors": removed}


def validate_inputs(root: Path, admission: dict, expected_source: str, expected_run: dict,
                    *, pe_reader=read_pe_versions) -> dict:
    exact(admission, {"schema", "source_sha", "source_tree", "run", "refs"}, "admission_fields")
    require(admission["schema"] == "motionwright.windows-lifecycle-inputs/1", "admission_schema")
    require(admission["source_sha"] == source_id(expected_source)
            and admission["run"] == run_identity(expected_run), "admission_source_or_run")
    source_id(admission["source_tree"])
    refs = exact(admission["refs"], REFERENCE_ROLES, "admission_reference_set")
    files = {name: bound_file(root, ref) for name, ref in refs.items()}
    docs = {name: bound_json(root, refs[name]) for name in (
        "compiled", "inputs", "candidate", "profile", "previous_capture", "version_overlay")}
    compiled, inputs, candidate, profile = (docs[name] for name in (
        "compiled", "inputs", "candidate", "profile"))
    require(compiled.get("schema") == "motionwright-windows-nsis-compiled-fixture/1"
            and compiled.get("status") == "COMPILED_SOURCE_BOUND", "compiled_fixture_not_verified")
    before = compiled.get("source_before")
    require(isinstance(before, dict) and before == compiled.get("source_after")
            and before == inputs.get("source_before") == inputs.get("source_after")
            and before.get("checkout") == expected_source
            and before.get("tree") == admission["source_tree"]
            and set(before.get("files", {})) == set(SOURCE_PATHS), "source_observation_mismatch")
    for item in before["files"].values():
        identity(item)
    cohort = inputs.get("storage_cohort")
    if cohort is not None:
        cohort = storage.validate_cohort(cohort, source_sha=expected_source,
                                         source_tree=admission["source_tree"])
        require(before["files"].get(storage.SOURCE_PATH) == cohort["source_input"]["checkout"],
                "storage_cohort_original_not_bound")
    require(compiled.get("source_sha") == expected_source
            and compiled.get("source_tree") == admission["source_tree"]
            and inputs.get("source_sha") == expected_source, "compiled_source_mismatch")
    for field, role in (("inputs_receipt", "inputs"), ("candidate_receipt", "candidate"),
                        ("setup", "current_setup"), ("rendered_installer", "rendered"),
                        ("predecessor_profile", "profile")):
        require(compiled.get(field) == ref_identity(refs[role]), "compiled_reference_mismatch")
    require(profile == inputs.get("profile")
            and inputs.get("profile_sha256") == refs["profile"]["sha256"]
            and compiled.get("profile_sha256") == refs["profile"]["sha256"],
            "predecessor_profile_mismatch")
    require(profile.get("schema") == "motionwright.nsis-predecessor/1"
            and profile.get("identity") == {"application": "Motionwright",
                "bundle_id": "com.seradotcom.motionwright", "arch": "x64"},
            "predecessor_identity")
    old_version = profile.get("version")
    target_version = profile.get("target", {}).get("version")
    require(version(target_version) > version(old_version), "upgrade_versions_not_distinct_and_increasing")
    require(docs["version_overlay"] == {"version": target_version}
            and inputs.get("version_overlay") == ref_identity(refs["version_overlay"]),
            "fixture_version_overlay_not_bound")
    require(inputs.get("version_scope") == "ISOLATED_INSTALLER_AND_PE_FIXTURE",
            "fixture_version_scope_missing")
    text = files["rendered"].read_text(encoding="utf-8-sig")
    for key, value in {"VERSION": target_version, "PRODUCTNAME": "Motionwright",
                       "BUNDLEID": "com.seradotcom.motionwright", "ARCH": "x64",
                       "INSTALLMODE": "currentUser", "MAINBINARYNAME": "motionwright-desktop"}.items():
        require(rendered_define(text, key) == value, "rendered_identity_mismatch")
    manufacturer = rendered_define(text, "MANUFACTURER")
    require(re.fullmatch(r"[A-Za-z0-9 ._-]{1,64}", manufacturer), "manufacturer_not_bounded")
    require(file_identity(files["rendered"]) == ref_identity(refs["rendered"]), "rendered_changed")
    source_id(profile.get("source_sha"))
    source_id(profile.get("source_tree"))
    require({key: profile["package"][key] for key in ("bytes", "sha256")}
            == ref_identity(refs["previous_setup"]), "previous_setup_binding")
    require({key: profile["capture_receipt"][key] for key in ("bytes", "sha256")}
            == ref_identity(refs["previous_capture"]), "previous_capture_binding")
    previous = docs["previous_capture"]
    require(previous.get("source_sha") == profile["source_sha"]
            and previous.get("source_before") == previous.get("source_after")
            and previous["source_before"]["checkout"] == profile["source_sha"]
            and previous["source_before"]["tree"] == profile["source_tree"]
            and previous.get("candidate") == previous.get("candidate_after"),
            "previous_original_binding")
    old_entries = profile.get("files")
    require(isinstance(old_entries, list) and len({row["relative"] for row in old_entries})
            == len(old_entries), "previous_payload_duplicate")
    old_payloads = payloads({row["relative"]: row for row in old_entries}, with_self=True)
    current_payloads = payloads(compiled.get("current_payloads"), with_self=False)
    validate_rendered_resource_directories(text, [
        relative for relative, item in compiled["current_payloads"].items()
        if item["kind"] == "resource"])
    require(old_payloads.get("motionwright-desktop.exe") == ref_identity(refs["previous_main"])
            and current_payloads.get("motionwright-desktop.exe") == ref_identity(refs["current_main"])
            and profile["target"].get("main_sha256") == refs["current_main"]["sha256"],
            "main_payload_binding")
    require(old_payloads["motionwright-desktop.exe"] != current_payloads["motionwright-desktop.exe"],
            "current_main_not_distinct")
    meta = candidate.get("candidate", {})
    require(candidate.get("schema_version") == 1 and meta.get("source_revision") == expected_source
            and meta.get("application") == "Motionwright" and meta.get("platform") == "windows"
            and meta.get("arch") == "x86_64", "candidate_source_binding")
    version(meta.get("version"))
    require(inputs.get("source_version") == meta["version"], "source_version_relabelled")
    require(candidate.get("artifacts") == [{"bundle_kind": "nsis",
        "filename": files["current_setup"].name, **ref_identity(refs["current_setup"])}],
        "candidate_setup_binding")
    old_pe = pe_reader(files["previous_main"])
    new_pe = pe_reader(files["current_main"])
    require_pe_version(old_pe, old_version, ref_identity(refs["previous_main"]))
    require_pe_version(new_pe, target_version, ref_identity(refs["current_main"]))
    return {"source_sha": expected_source, "source_tree": admission["source_tree"],
        **({"storage_cohort": cohort} if cohort is not None else {}),
        "run": expected_run, "source_before": before,
        "source_version": meta["version"], "previous_version": old_version,
        "fixture_installer_version": target_version,
        "previous_pe": old_pe, "current_pe": new_pe,
        "previous_payloads": old_payloads, "current_payloads": current_payloads,
        "previous_setup": ref_identity(refs["previous_setup"]),
        "current_setup": ref_identity(refs["current_setup"]),
        "previous_source": profile["source_sha"],
        "registry_keys": [
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Motionwright",
            "Software\\" + manufacturer + r"\Motionwright"]}


def namespace_paths(directory: Path) -> dict:
    _check_path_syntax(str(directory))
    _require_plain_parents(directory)
    if not directory.exists():
        return {}
    require(directory.is_dir() and not directory.is_symlink()
            and not getattr(directory.lstat(), "st_file_attributes", 0) & 0x400,
            "namespace_root_not_plain")
    result = {}
    seen = set()
    for parent, dirs, files in os.walk(directory, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(parent) / name
            meta = path.lstat()
            require(not stat.S_ISLNK(meta.st_mode)
                    and not getattr(meta, "st_file_attributes", 0) & 0x400,
                    "namespace_link_or_reparse")
            relative = path.relative_to(directory).as_posix()
            require(_relative(relative, "namespace") == relative, "namespace_path_invalid")
            require(relative.casefold() not in seen, "namespace_case_alias")
            seen.add(relative.casefold())
            require(len(seen) <= MAX_FILES, "namespace_entry_bound")
            if stat.S_ISREG(meta.st_mode):
                result[relative] = _signature(meta)
            else:
                require(stat.S_ISDIR(meta.st_mode), "namespace_special_entry")
    require(sum(value[2] for value in result.values()) <= MAX_BYTES, "namespace_byte_bound")
    return result


def registry_snapshot(keys: list[str]) -> dict:
    require(os.name == "nt", "registry_observation_requires_windows")
    import winreg
    result = {}
    for view, flag in (("32", winreg.KEY_WOW64_32KEY), ("64", winreg.KEY_WOW64_64KEY)):
        rows = {}
        for key in keys:
            try:
                handle = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ | flag)
            except FileNotFoundError:
                rows[key] = None
                continue
            with handle:
                first = winreg.QueryInfoKey(handle)
                require(first[0] == 0 and first[1] <= 64, "registry_fixture_shape")
                values = {}
                for index in range(first[1]):
                    name, value, kind = winreg.EnumValue(handle, index)
                    require(isinstance(name, str) and len(name) <= 128, "registry_name_bound")
                    require(name not in values, "registry_duplicate")
                    require(kind in {winreg.REG_SZ, winreg.REG_EXPAND_SZ, winreg.REG_DWORD},
                            "registry_fixture_value_type")
                    require((isinstance(value, str) and len(value) <= 4096)
                            or (type(value) is int and 0 <= value <= 0xFFFFFFFF),
                            "registry_value_bound")
                    values[name] = {"type": kind, "value": value}
                require(winreg.QueryInfoKey(handle) == first, "registry_changed")
                rows[key] = values
        result[view] = rows
    return result


def optional_regular_stamp(path: Path):
    """Absence may start at a missing tail, never at an unreadable/reparse parent."""
    _check_path_syntax(str(path))
    for parent in reversed(path.parents):
        try:
            item = parent.lstat()
        except FileNotFoundError:
            return None
        require(stat.S_ISDIR(item.st_mode) and not stat.S_ISLNK(item.st_mode)
                and not getattr(item, "st_file_attributes", 0) & 0x400,
                "optional_file_parent_not_plain")
    try:
        item = path.lstat()
    except FileNotFoundError:
        return None
    _require_regular(item, "SQLite family")
    require(item.st_size < 64 * 1024 * 1024, "sqlite_family_size_bound")
    return _signature(item)


def capture_snapshot(root: Path, *, snapshot_name: str, operation: str, position: str,
                     install_root: Path, database: Path, registry_keys: list[str],
                     registry_reader=registry_snapshot,
                     first_use_contract: str = CURRENT_FIRST_USE_CONTRACT, storage_cohort=None) -> dict:
    """Capture a quiescent fixture; never follows a reparse or modifies its store."""
    if storage_cohort is not None:
        storage_cohort = storage.validate_cohort(storage_cohort)
    operation_id(operation)
    require(position in {"before", "after"}, "snapshot_position")
    require(re.fullmatch(r"[a-z][a-z0-9-]{0,63}", snapshot_name), "snapshot_name")
    require(database.name == "motionwright.sqlite3", "database_name")
    require(install_root.is_absolute() and database.is_absolute() and root.is_absolute(),
            "snapshot_paths_absolute")
    require(not root.is_relative_to(install_root)
            and not install_root.is_relative_to(root)
            and not database.is_relative_to(install_root)
            and not root.is_relative_to(database.parent), "snapshot_path_overlap")
    output = root / snapshot_name
    output.mkdir()
    before_registry = registry_reader(registry_keys)
    inventory = namespace_paths(install_root)
    retained = {}
    capture_root = output / "installed"
    capture_root.mkdir()
    for relative in sorted(inventory):
        destination = capture_root.joinpath(*relative.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        _capture(install_root.joinpath(*relative.split("/")), destination)
        retained[relative] = retained_ref(root, destination)
    require(namespace_paths(install_root) == inventory, "namespace_changed_during_capture")
    family = {}
    originals = output / "sqlite-original"
    originals.mkdir()
    database_files = [Path(str(database) + suffix) for suffix in ("", "-wal", "-shm")]
    family_before = {}
    for path in database_files:
        family_before[path.name] = optional_regular_stamp(path)
    for path in database_files:
        if family_before[path.name] is None:
            family[path.name] = None
        else:
            destination = originals / path.name
            _capture(path, destination)
            family[path.name] = retained_ref(root, destination)
    for path in database_files:
        require(optional_regular_stamp(path) == family_before[path.name], "sqlite_family_changed")
    project = None
    if family[database.name] is not None:
        inspection = output / "sqlite-inspection"
        inspection.mkdir()
        for name, ref in family.items():
            if ref is not None:
                _capture(bound_file(root, ref), inspection / name)
        # SQLite may update its reader locks in these disposable inspection
        # copies. The original DB/WAL/SHM captures above remain untouched.
        project = read_project_snapshot(
            inspection / database.name, first_use_contract=first_use_contract,
            **({"storage_cohort": storage_cohort} if storage_cohort is not None else {}),
        )
    else:
        require(all(value is None for value in family.values()), "orphan_sqlite_sidecar")
    require(registry_reader(registry_keys) == before_registry, "registry_changed_during_capture")
    value = {"schema": ("motionwright.windows-lifecycle-snapshot/1" if storage_cohort is None
                        else "motionwright.windows-lifecycle-snapshot/2"),
        **({"storage_cohort": storage_cohort} if storage_cohort is not None else {}),
        "operation_id": operation, "position": position,
        "install_root": str(install_root), "database": str(database),
        "installed": retained, "registry": before_registry,
        "sqlite_family": family, "project": project}
    write_new_manifest(root, output / "snapshot.json", value)
    return retained_ref(root, output / "snapshot.json")


def observed_terminal(exit_code: int | None, *, timed_out: bool = False) -> dict:
    require(type(timed_out) is bool and
            (exit_code is None or type(exit_code) is int), "terminal_input_invalid")
    require(not timed_out or exit_code is None, "timeout_with_known_exit")
    state = "UNKNOWN" if timed_out or exit_code is None or exit_code == 3 else (
        "ACK" if exit_code == 0 else "ERROR")
    return {"state": state, "exit_code": exit_code,
            "reason": "WAIT_TIMEOUT" if timed_out else ("NO_EXIT_OBSERVED" if exit_code is None else "EXIT_OBSERVED")}


def validate_terminal(value):
    exact(value, {"state", "exit_code", "reason"}, "terminal_fields")
    require(value["state"] in STATES, "terminal_state")
    expected = observed_terminal(value["exit_code"], timed_out=value["reason"] == "WAIT_TIMEOUT")
    require(value == expected, "terminal_state_does_not_match_original_exit")
    return value


def custody_error(stage: str, error: Exception) -> dict:
    code = type(error).__name__
    try:
        candidate = str(error) if isinstance(error, (ManifestError, LifecycleError)) else code
        if re.fullmatch(r"[A-Za-z0-9_.]{1,128}", candidate):
            code = candidate
    except Exception:
        pass
    publication = stage in {"terminal_publication", "diagnostic_publication",
                            "record_publication", "result_publication"}
    unknown = (isinstance(error, ManifestError) and code == "manifest_output_commit_uncertain") or (
        publication and not isinstance(error, ManifestError))
    return {"stage": stage, "type": type(error).__name__, "code": code,
            "state": "UNKNOWN" if unknown else "ERROR"}


def retain_observed_result(root: Path, phase: str, original: dict, observation,
                           *, context: dict, publisher=write_new_manifest,
                           diagnostic_stream=None) -> dict:
    """Preserve an already observed result before any derived work.

    The callback only collects observations. It must never invoke the effect.
    A publisher or observation failure cannot turn ACK into ERROR, hide UNKNOWN,
    replace an earlier receipt, or authorize a second installer invocation.
    """
    require(phase in PHASES, "phase_invalid")
    preserved = copy.deepcopy(validate_terminal(original))
    binding = copy.deepcopy(exact(context, {"operation_id", "process", "executable",
                                          "source_sha", "run"}, "terminal_context_fields"))
    operation_id(binding["operation_id"])
    source_id(binding["source_sha"])
    run_identity(binding["run"])
    identity(binding["executable"])
    if binding["process"] is not None:
        exact(binding["process"], {"pid", "creation_filetime"}, "process_fields")
        require(all(type(value) is int and value > 0 for value in binding["process"].values()),
                "process_identity_invalid")
    result = {"original_terminal": preserved, "observation": None, "custody_errors": []}
    if binding["process"] is None:
        result["custody_errors"].append({"stage": "process_identity",
            "type": "LifecycleError", "code": "process_identity_unavailable", "state": "UNKNOWN"})
    stream = sys.stderr if diagnostic_stream is None else diagnostic_stream
    try:
        publisher(root, root / (phase + "-terminal.json"), {
            "schema": "motionwright.windows-lifecycle-terminal/1", "phase": phase,
            "context": binding, "original_terminal": copy.deepcopy(preserved)})
    except Exception as error:
        result["custody_errors"].append(custody_error("terminal_publication", error))
    try:
        result["observation"] = observation()
    except Exception as error:
        result["custody_errors"].append(custody_error("post_effect_observation", error))
    if result["custody_errors"]:
        try:
            publisher(root, root / (phase + "-custody-diagnostic.json"), {
                "schema": "motionwright.windows-lifecycle-custody/1", "phase": phase,
                "context": binding, **copy.deepcopy(result)})
        except Exception as error:
            result["custody_errors"].append(custody_error("diagnostic_publication", error))
        try:
            stream.write("windows-lifecycle-custody=INCOMPLETE original_terminal=" + preserved["state"] + "\n")
            stream.flush()
        except Exception:
            pass
    return result


def snapshot_value(root: Path, ref: dict, operation: str, position: str, keys: list[str],
                   *, storage_cohort=None):
    value = bound_json(root, ref)
    fields = {"schema", "operation_id", "position", "install_root", "database",
              "installed", "registry", "sqlite_family", "project"}
    schema = "motionwright.windows-lifecycle-snapshot/1"
    if storage_cohort is not None:
        storage_cohort = storage.validate_cohort(storage_cohort)
        fields.add("storage_cohort")
        schema = "motionwright.windows-lifecycle-snapshot/2"
    exact(value, fields, "snapshot_fields")
    require(storage_cohort is None or value["storage_cohort"] == storage_cohort,
            "snapshot_storage_cohort_mismatch")
    require(value["schema"] == schema
            and value["operation_id"] == operation and value["position"] == position,
            "snapshot_phase_binding")
    require(isinstance(value["installed"], dict) and len(value["installed"]) <= MAX_FILES,
            "snapshot_inventory_bound")
    names = set()
    for name, reference in value["installed"].items():
        require(_relative(name, "installed") == name and name.casefold() not in names,
                "snapshot_inventory_alias")
        names.add(name.casefold())
        bound_file(root, reference)
    require(set(value["registry"]) == {"32", "64"}, "registry_views_missing")
    for view in value["registry"].values():
        require(isinstance(view, dict) and set(view) == set(keys), "registry_keys_changed")
    exact(value["sqlite_family"],
          {"motionwright.sqlite3", "motionwright.sqlite3-wal", "motionwright.sqlite3-shm"},
          "sqlite_family_incomplete")
    for reference in value["sqlite_family"].values():
        if reference is not None:
            bound_file(root, reference)
    return value


def file_map(snapshot):
    return {path: ref_identity(reference) for path, reference in snapshot["installed"].items()}


def family_map(snapshot):
    return {name: None if reference is None else ref_identity(reference)
            for name, reference in snapshot["sqlite_family"].items()}


def installed_registry(snapshot, key, expected_version):
    found = []
    for view, rows in snapshot["registry"].items():
        values = rows[key]
        if values is not None:
            require(isinstance(values, dict), "registry_record_invalid")
            def string(name):
                item = values.get(name, {})
                require(item.get("type") == 1 and isinstance(item.get("value"), str),
                        "registry_required_string_missing")
                return item["value"]
            require(string("DisplayVersion") == expected_version, "registry_version_mismatch")
            require(string("DisplayName") == "Motionwright"
                    and string("InstallLocation") == '"' + snapshot["install_root"] + '"'
                    and string("UninstallString") == '"' + snapshot["install_root"] + r'\uninstall.exe"',
                    "registry_install_identity")
            found.append(view)
    require(bool(found), "registry_install_missing")
    # HKCU keys may be shared across registry views. Equal readbacks are one
    # logical registration; conflicting readbacks already fail the assertions.


def validate_lifecycle(root: Path, record: dict, inputs: dict, admission_digest: str) -> dict:
    exact(record, {"schema", "source_sha", "source_tree", "run", "admission_sha256",
                   "scope", "operations", "project_origin", "interruption", "claims"},
          "lifecycle_fields")
    require(isinstance(admission_digest, str) and SHA64.fullmatch(admission_digest),
            "admission_digest_invalid")
    require(record["schema"] == "motionwright.windows-installer-lifecycle/1"
            and record["source_sha"] == inputs["source_sha"]
            and record["source_tree"] == inputs["source_tree"]
            and record["run"] == inputs["run"]
            and record["admission_sha256"] == admission_digest, "lifecycle_identity")
    require(record["scope"] == "DISPOSABLE_WINDOWS_FIXTURE_UPGRADE", "lifecycle_scope")
    require(record["claims"] == {"human_acceptance": "NOT_RUN",
        "published_product_upgrade": "NOT_CLAIMED", "release_published": False}, "lifecycle_claims")
    # No current source-pinned checkpoint can prove a controlled interruption.
    require(record["interruption"] == {"status": "NOT_RUN",
        "reason": "OBSERVABLE_PID_START_GUID_CHECKPOINT_REQUIRED"}, "interruption_not_supported")
    operations = record["operations"]
    require(isinstance(operations, list) and 1 <= len(operations) <= 3, "operation_count")
    originals = []
    snapshots = []
    seen_ids, seen_processes = set(), set()
    for index, row in enumerate(operations):
        exact(row, {"phase", "operation_id", "launch_count", "process", "executable",
                    "terminal", "terminal_receipt", "command", "custody_errors", "before", "after"},
              "operation_fields")
        require(row["command"] == ("nsis_install_previous", "nsis_upgrade_current",
                                   "nsis_uninstall_current_in_place")[index], "operation_command")
        require(row["phase"] == PHASES[index] and type(row["launch_count"]) is int
                and row["launch_count"] == 1, "operation_order_or_replay")
        oid = operation_id(row["operation_id"])
        require(oid not in seen_ids, "operation_identity_reused")
        seen_ids.add(oid)
        terminal = copy.deepcopy(validate_terminal(row["terminal"]))
        process = row["process"]
        if process is None:
            require((terminal["state"] == "UNKNOWN" and terminal["exit_code"] is None)
                    or (isinstance(row["custody_errors"], list) and bool(row["custody_errors"])),
                    "missing_process_for_observed_exit")
        else:
            exact(process, {"pid", "creation_filetime"}, "process_fields")
            require(all(type(value) is int and value > 0 for value in process.values()),
                    "process_identity_invalid")
            process_key = (process["pid"], process["creation_filetime"])
            require(process_key not in seen_processes, "process_identity_reused")
            seen_processes.add(process_key)
        originals.append({"phase": row["phase"], "original_terminal": terminal})
        require(isinstance(row["custody_errors"], list), "custody_error_list")
        if terminal["state"] != "ACK" or row["custody_errors"]:
            require(index == len(operations) - 1, "operation_after_error_or_unknown")
            return {"status": "INCOMPLETE", "originals": originals,
                "reason": "ORIGINAL_EFFECT_OR_CUSTODY_NOT_CONFIRMED",
                "human_acceptance": "NOT_RUN", "release_published": False}
        terminal_receipt = bound_json(root, row["terminal_receipt"])
        require(terminal_receipt == {
            "schema": "motionwright.windows-lifecycle-terminal/1", "phase": row["phase"],
            "context": {"operation_id": oid, "process": process,
                        "executable": row["executable"], "source_sha": inputs["source_sha"],
                        "run": inputs["run"]}, "original_terminal": terminal},
                "original_terminal_receipt_binding")
        before = snapshot_value(root, row["before"], oid, "before", inputs["registry_keys"])
        after = snapshot_value(root, row["after"], oid, "after", inputs["registry_keys"])
        require(before["install_root"] == after["install_root"]
                and before["database"] == after["database"], "snapshot_location_changed")
        snapshots.append((before, after))
        if index < 2:
            require(row["executable"] == inputs[("previous_setup", "current_setup")[index]],
                    "operation_setup_binding")
        else:
            require(row["executable"] == ref_identity(before["installed"]["uninstall.exe"]),
                    "uninstaller_not_the_observed_installed_self")
    require(len(operations) == 3, "mandatory_lifecycle_phase_missing")
    install_before, install_after = snapshots[0]
    upgrade_before, upgrade_after = snapshots[1]
    uninstall_before, uninstall_after = snapshots[2]
    locations = {(item["install_root"], item["database"]) for pair in snapshots for item in pair}
    require(len(locations) == 1, "lifecycle_location_switch")
    registry_key = inputs["registry_keys"][0]
    require(not file_map(install_before)
            and all(all(value is None for value in row.values())
                    for row in install_before["registry"].values())
            and all(value is None for value in family_map(install_before).values()),
            "initial_fixture_not_empty")
    require(file_map(install_after) == inputs["previous_payloads"], "previous_install_inventory")
    installed_registry(install_after, registry_key, inputs["previous_version"])
    installed_registry(upgrade_before, registry_key, inputs["previous_version"])
    installed_registry(upgrade_after, registry_key, inputs["fixture_installer_version"])
    require(family_map(upgrade_before).get("motionwright.sqlite3") is not None
            and upgrade_before["project"] is not None, "real_project_baseline_missing")
    project_origin = bound_json(root, record["project_origin"])
    require(project_origin.get("schema") == "motionwright-windows-nsis-cold-relaunch/1"
            and project_origin.get("source_sha") == inputs["previous_source"]
            and project_origin.get("installed_binary_sha256")
                == inputs["previous_payloads"]["motionwright-desktop.exe"]["sha256"]
            and project_origin.get("first_use_contract") == select_first_use_contract(
                inputs["previous_source"],
                inputs["previous_payloads"]["motionwright-desktop.exe"]["sha256"],
            )
            and project_origin.get("first_launch_real_visible_hwnd") is True
            and project_origin.get("second_launch_real_visible_hwnd") is True
            and project_origin.get("separate_process_lifetimes") is True
            and project_origin.get("same_project_generation_revision_document") is True
            and project_origin.get("project") == upgrade_before["project"]
            and project_origin.get("human_install_acceptance") == "NOT_RUN",
            "project_origin_not_bound")
    old = inputs["previous_payloads"]
    current = inputs["current_payloads"]
    before_files, after_files = file_map(upgrade_before), file_map(upgrade_after)
    require(all(before_files.get(path) == value for path, value in old.items()),
            "predecessor_bytes_changed_before_upgrade")
    foreign = {path: value for path, value in before_files.items() if path not in old}
    require(bool(foreign), "foreign_file_preservation_not_exercised")
    require(not (set(foreign) & set(current)), "foreign_file_conflicts_with_new_payload")
    require(all(after_files.get(path) == value for path, value in current.items())
            and all(after_files.get(path) == value for path, value in foreign.items()),
            "upgrade_payload_or_foreign_preservation")
    require(set(after_files) == set(current) | set(foreign) | {"uninstall.exe"},
            "upgrade_namespace_not_complete")
    require(after_files["uninstall.exe"] != old["uninstall.exe"], "new_self_not_distinct")
    require(family_map(upgrade_before) == family_map(upgrade_after)
            and upgrade_before["project"] == upgrade_after["project"], "upgrade_project_changed")
    require(file_map(uninstall_before) == after_files
            and family_map(uninstall_before) == family_map(upgrade_after)
            and uninstall_before["project"] == upgrade_after["project"]
            and uninstall_before["registry"] == upgrade_after["registry"],
            "uninstall_does_not_continue_the_observed_upgrade")
    require(file_map(uninstall_after) == {**foreign, "uninstall.exe": after_files["uninstall.exe"]},
            "uninstall_removed_foreign_or_retained_owned_payload")
    require(family_map(uninstall_before) == family_map(uninstall_after)
            and uninstall_before["project"] == uninstall_after["project"],
            "uninstall_project_changed")
    require(all(row[registry_key] is None for row in uninstall_after["registry"].values()),
            "uninstall_registration_retained")
    product_key = inputs["registry_keys"][1]
    require(all(uninstall_before["registry"][view][product_key]
                == uninstall_after["registry"][view][product_key] for view in ("32", "64")),
            "uninstall_product_metadata_changed")
    return {"status": "SOURCE_BOUND_FIXTURE_OBSERVATIONS", "originals": originals,
        "source_sha": inputs["source_sha"], "source_version": inputs["source_version"],
        "previous_installer_version": inputs["previous_version"],
        "fixture_installer_version": inputs["fixture_installer_version"],
        "pe_versions": {"previous": inputs["previous_pe"], "current": inputs["current_pe"]},
        "project_preserved_during_installer_operations": True,
        "interruption": "NOT_RUN", "human_acceptance": "NOT_RUN",
        "published_product_upgrade": "NOT_CLAIMED", "release_published": False,
        "authenticity": "REQUIRES_EXTERNAL_ORIGINAL_WORKFLOW_ADMISSION"}


def main(argv=None, *, checkout_root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--admission", required=True)
    parser.add_argument("--admission-sha256", required=True)
    parser.add_argument("--record", required=True)
    parser.add_argument("--record-sha256", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    root = args.evidence_root
    try:
        before = source_observation(checkout_root, args.source_sha)
        refs = {}
        for role, relative, digest in (("admission", args.admission, args.admission_sha256),
                                        ("record", args.record, args.record_sha256)):
            path = path_under(root, relative)
            observed = file_identity(path)
            require(observed["sha256"] == digest, role + "_independent_pin_mismatch")
            refs[role] = {"path": relative, **observed}
        admission = bound_json(root, refs["admission"])
        inputs = validate_inputs(root, admission, args.source_sha, {
            "repository": args.repository, "run_id": args.run_id, "run_attempt": args.run_attempt})
        require(before == inputs["source_before"], "consumer_source_differs_from_compiler")
        record = bound_json(root, refs["record"])
        result = validate_lifecycle(root, record, inputs, refs["admission"]["sha256"])
        require(source_observation(checkout_root, args.source_sha) == before, "consumer_source_changed")
        write_new_manifest(root, args.output, result)
    except Exception as error:
        try:
            sys.stderr.write("windows-lifecycle=REFUSED type=" + type(error).__name__ + "\n")
        except Exception:
            pass
        return 2
    return 0 if result["status"] == "SOURCE_BOUND_FIXTURE_OBSERVATIONS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
