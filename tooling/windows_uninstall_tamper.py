"""Pure readback of isolated NSIS same-size tamper observations.

This consumer never downloads, installs, launches, mutates, or cleans a fixture.
Transport authenticates producer_binding independently before calling this module.
The original clean three-phase lifecycle contract remains unchanged.
"""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path, PureWindowsPath
import re

import windows_installer_lifecycle as lifecycle

SCHEMA = "motionwright.windows-uninstall-tamper/1"
SCOPE = "DISPOSABLE_WINDOWS_SAME_SIZE_TAMPER"
INTENT_SCHEMA = "motionwright.windows-uninstall-tamper-intent/1"
ROLES = ("main", "resource", "external_binary")
COMMANDS = {
    "install": "nsis_install_current_for_tamper",
    "uninstall": "nsis_uninstall_current_in_place",
}
CLAIMS = {
    "human_acceptance": "NOT_RUN",
    "interruption": "NOT_RUN",
    "published_product_upgrade": "NOT_CLAIMED",
    "release_published": False,
}
ARTIFACT_NAME = "motionwright-windows-nsis-lifecycle-original"
MAX_FOREIGN_BYTES = 64 * 1024
CHUNK_BYTES = 1024 * 1024
RECORD_FIELDS = {
    "schema", "scope", "case_id", "source_sha", "source_tree", "run", "role",
    "producer_binding", "producer_refs", "family_origin", "mutation",
    "foreign_fixture", "operations", "source_before", "source_after", "claims",
}
OPERATION_FIELDS = {
    "phase", "operation_id", "launch_count", "command", "process", "executable",
    "executable_path", "terminal", "terminal_receipt", "custody_errors",
    "before", "after", "intent",
}
INTENT_FIELDS = {
    "schema", "source_sha", "run", "case_id", "role", "phase", "command",
    "operation_id", "executable", "executable_path", "arguments",
    "launch_limit", "replay_authorized",
}
require = lifecycle.require
exact = lifecycle.exact


def _producer_binding(value, expected_source, expected_run):
    exact(value, {
        "artifact_id", "artifact_name", "artifact_bytes", "artifact_sha256",
        "source_sha", "run",
    }, "tamper_producer_binding_fields")
    require(type(value["artifact_id"]) is int and 0 < value["artifact_id"] < 2**63,
            "tamper_artifact_id")
    require(value["artifact_name"] == ARTIFACT_NAME, "tamper_artifact_name")
    require(type(value["artifact_bytes"]) is int
            and 0 < value["artifact_bytes"] <= lifecycle.MAX_BYTES, "tamper_artifact_size")
    lifecycle.identity({"bytes": value["artifact_bytes"], "sha256": value["artifact_sha256"]})
    require(value["source_sha"] == lifecycle.source_id(expected_source),
            "tamper_artifact_source")
    require(lifecycle.run_identity(value["run"]) == lifecycle.run_identity(expected_run)
            and expected_run["run_attempt"] == 1, "tamper_artifact_run")
    return value


def admit_producer(producer_root: Path, producer_refs: dict, producer_binding: dict,
                   expected_source: str, expected_run: dict, *,
                   pe_reader=lifecycle.read_pe_versions) -> dict:
    """Revalidate the transported originals; do not authenticate transport here."""
    _producer_binding(producer_binding, expected_source, expected_run)
    exact(producer_refs, {"admission", "record", "result"}, "tamper_producer_refs")
    for key, reference in producer_refs.items():
        lifecycle.ref_identity(reference)
        require(reference["path"] == key + ".json", "tamper_producer_reference_path")
    admission = lifecycle.bound_json(producer_root, producer_refs["admission"])
    inputs = lifecycle.validate_inputs(
        producer_root, admission, expected_source, expected_run, pe_reader=pe_reader)
    record = lifecycle.bound_json(producer_root, producer_refs["record"])
    result = lifecycle.validate_lifecycle(
        producer_root, record, inputs, producer_refs["admission"]["sha256"])
    require(result["status"] == "SOURCE_BOUND_FIXTURE_OBSERVATIONS",
            "tamper_producer_lifecycle_incomplete")
    wrapper = lifecycle.bound_json(producer_root, producer_refs["result"])
    exact(wrapper, {"schema", "source_before", "source_after", "admission", "record", "result"},
          "tamper_producer_result_fields")
    require(wrapper["schema"] == "motionwright.windows-lifecycle-verification/1"
            and wrapper["source_before"] == wrapper["source_after"] == inputs["source_before"]
            and wrapper["admission"] == producer_refs["admission"]
            and wrapper["record"] == producer_refs["record"]
            and wrapper["result"] == result, "tamper_producer_result_binding")
    compiled = lifecycle.bound_json(producer_root, admission["refs"]["compiled"])
    return {"inputs": inputs, "record": record, "result": result,
            "compiled": compiled, "source": inputs["source_before"]}


def validate_target_role(compiled: dict, inputs: dict, role: str, relative: str) -> dict:
    """Select an authenticated compiled role without touching the installed file."""
    require(isinstance(role, str) and role in ROLES, "tamper_target_role")
    require(isinstance(relative, str)
            and lifecycle._relative(relative, "tamper target") == relative,
            "tamper_target_path")
    declared = compiled.get("current_payloads")
    require(isinstance(declared, dict) and relative in declared
            and relative in inputs["current_payloads"] and relative != "uninstall.exe",
            "tamper_target_role")
    entry = exact(declared[relative], {"kind", "bytes", "sha256", "symbol"},
                  "tamper_target_entry_fields")
    require(entry["kind"] == role, "tamper_target_role")
    pattern = {"main": r"MW_MAIN", "resource": r"MW_RESOURCE_(?:0|[1-9][0-9]*)",
               "external_binary": r"MW_BINARY_(?:0|[1-9][0-9]*)"}[role]
    require(isinstance(entry["symbol"], str) and len(entry["symbol"]) <= 64
            and re.fullmatch(pattern, entry["symbol"])
            and (role != "main" or relative == "motionwright-desktop.exe"),
            "tamper_target_symbol")
    expected = lifecycle.identity({key: entry[key] for key in ("bytes", "sha256")})
    require(expected["bytes"] > 0 and expected == inputs["current_payloads"][relative],
            "tamper_target_payload_binding")
    return copy.deepcopy(entry)


def _live_path(value, code):
    """Validate a Windows path as data; never open the recorded live path."""
    require(isinstance(value, str) and 1 <= len(value) <= 4096
            and not any(char in value for char in '"\r\n\0'), code)
    path = PureWindowsPath(value)
    require(path.is_absolute() and re.fullmatch(r"[A-Za-z]:", path.drive)
            and str(path) == value and len(path.parts) > 1, code)
    relative = "/".join(path.parts[1:])
    require(lifecycle._relative(relative, "live observation") == relative, code)
    return path


def _locations(snapshots):
    first = snapshots[0]
    install = _live_path(first["install_root"], "tamper_install_path")
    database = _live_path(first["database"], "tamper_database_path")
    require(database.name == "motionwright.sqlite3" and not database.is_relative_to(install),
            "tamper_database_location")
    require(all(item["install_root"] == first["install_root"]
                and item["database"] == first["database"] for item in snapshots),
            "tamper_snapshot_location_changed")
    return str(install), str(install / "uninstall.exe")


def _operation(evidence_root, row, phase, record, inputs, *, expected_executable,
               expected_path, arguments):
    exact(row, OPERATION_FIELDS, "tamper_operation_fields")
    require(row["phase"] == phase and row["command"] == COMMANDS[phase],
            "tamper_operation_command")
    operation = lifecycle.operation_id(row["operation_id"])
    require(type(row["launch_count"]) is int and row["launch_count"] == 1,
            "tamper_operation_replay")
    exact(row["process"], {"pid", "creation_filetime"}, "tamper_process_fields")
    require(all(type(value) is int and value > 0 for value in row["process"].values()),
            "tamper_process_identity")
    require(lifecycle.identity(row["executable"]) == expected_executable
            and row["executable_path"] == expected_path, "tamper_executable_binding")
    original = lifecycle.validate_terminal(row["terminal"])
    expected_terminal = lifecycle.observed_terminal(0 if phase == "install" else 2)
    require(original == expected_terminal, "tamper_" + phase + "_terminal")
    require(type(row["custody_errors"]) is list and not row["custody_errors"],
            "tamper_custody_incomplete")
    terminal = lifecycle.bound_json(evidence_root, row["terminal_receipt"])
    context = {"operation_id": operation, "process": row["process"],
               "executable": row["executable"], "source_sha": record["source_sha"],
               "run": record["run"]}
    require(terminal == {"schema": "motionwright.windows-lifecycle-terminal/1",
                        "phase": phase, "context": context, "original_terminal": original},
            "tamper_terminal_receipt_binding")
    intent = lifecycle.bound_json(evidence_root, row["intent"])
    exact(intent, INTENT_FIELDS, "tamper_intent_fields")
    require(type(intent["launch_limit"]) is int and intent["launch_limit"] == 1
            and type(intent["replay_authorized"]) is bool
            and intent["replay_authorized"] is False, "tamper_intent_replay")
    require(intent == {
        "schema": INTENT_SCHEMA, "source_sha": record["source_sha"], "run": record["run"],
        "case_id": record["case_id"], "role": record["role"], "phase": phase,
        "command": COMMANDS[phase], "operation_id": operation,
        "executable": row["executable"], "executable_path": expected_path,
        "arguments": arguments, "launch_limit": 1, "replay_authorized": False,
    }, "tamper_intent_binding")
    return [lifecycle.snapshot_value(evidence_root, row[position], operation, position,
                                    inputs["registry_keys"]) for position in ("before", "after")]


def _one_xor_byte(evidence_root, original_ref, changed_ref, offset):
    original_id = lifecycle.ref_identity(original_ref)
    changed_id = lifecycle.ref_identity(changed_ref)
    require(original_id["bytes"] > 0 and original_id["bytes"] == changed_id["bytes"]
            and original_id["sha256"] != changed_id["sha256"], "tamper_same_size_changed_identity")
    require(type(offset) is int and 0 <= offset < original_id["bytes"], "tamper_offset")
    paths = [lifecycle.bound_file(evidence_root, reference)
             for reference in (original_ref, changed_ref)]
    path_stamps = [path.lstat() for path in paths]
    hashes = [hashlib.sha256(), hashlib.sha256()]
    count = 0
    with os.fdopen(os.open(paths[0], lifecycle._read_flags()), "rb") as left, \
            os.fdopen(os.open(paths[1], lifecycle._read_flags()), "rb") as right:
        streams = (left, right)
        opened = [os.fstat(stream.fileno()) for stream in streams]
        changes = [lifecycle._source_change_time(stream.fileno()) for stream in streams]
        for index, meta in enumerate(opened):
            lifecycle._require_regular(meta, "retained tamper bytes")
            require((meta.st_dev, meta.st_ino) ==
                    (path_stamps[index].st_dev, path_stamps[index].st_ino),
                    "tamper_retained_identity_changed")
        while count < original_id["bytes"]:
            wanted = min(CHUNK_BYTES, original_id["bytes"] - count)
            a, b = left.read(wanted), right.read(wanted)
            require(len(a) == wanted and len(b) == wanted, "tamper_retained_short_read")
            hashes[0].update(a)
            hashes[1].update(b)
            expected = a
            if count <= offset < count + wanted:
                local = offset - count
                expected = a[:local] + bytes([a[local] ^ 1]) + a[local + 1:]
            require(b == expected, "tamper_not_one_xor_byte")
            count += wanted
        require(left.read(1) == right.read(1) == b"", "tamper_retained_grew")
        for index, stream in enumerate(streams):
            require(lifecycle._signature(os.fstat(stream.fileno())) ==
                    lifecycle._signature(opened[index])
                    and lifecycle._source_change_time(stream.fileno()) == changes[index],
                    "tamper_retained_identity_changed")
    for index, (path, reference) in enumerate(zip(paths, (original_ref, changed_ref))):
        require(lifecycle._signature(path.lstat()) == lifecycle._signature(path_stamps[index])
                and hashes[index].hexdigest() == reference["sha256"]
                and lifecycle.file_identity(path) == lifecycle.ref_identity(reference),
                "tamper_retained_identity_changed")


def validate_uninstall_tamper(evidence_root: Path, record: dict, *, producer_root: Path,
                             producer_binding: dict, expected_source: str,
                             expected_run: dict, expected_role: str,
                             pe_reader=lifecycle.read_pe_versions) -> dict:
    """Require one fresh install and one original, fully observed refusal."""
    exact(record, RECORD_FIELDS, "tamper_record_fields")
    require(record["schema"] == SCHEMA and record["scope"] == SCOPE, "tamper_record_scope")
    require(isinstance(expected_role, str) and expected_role in ROLES
            and record["role"] == expected_role, "tamper_role")
    case_id = lifecycle.operation_id(record["case_id"])
    require(record["source_sha"] == lifecycle.source_id(expected_source)
            and lifecycle.run_identity(record["run"]) == lifecycle.run_identity(expected_run),
            "tamper_record_source_or_run")
    _producer_binding(record["producer_binding"], expected_source, expected_run)
    require(record["producer_binding"] == producer_binding, "tamper_producer_binding_changed")
    require(record["claims"] == CLAIMS and type(record["claims"]["release_published"]) is bool,
            "tamper_claims")
    admitted = admit_producer(producer_root, record["producer_refs"], producer_binding,
                              expected_source, expected_run, pe_reader=pe_reader)
    inputs, clean = admitted["inputs"], admitted["record"]
    require(record["source_tree"] == inputs["source_tree"]
            and record["source_before"] == record["source_after"] == admitted["source"],
            "tamper_source_observation_changed")
    exact(record["family_origin"], {"source_fixture", "project_origin"}, "tamper_family_origin_fields")
    source_fixture_ref = clean["operations"][1]["after"]
    require(record["family_origin"] == {
        "source_fixture": source_fixture_ref, "project_origin": clean["project_origin"],
    }, "tamper_family_origin_binding")
    source_fixture = lifecycle.snapshot_value(
        producer_root, source_fixture_ref, clean["operations"][1]["operation_id"],
        "after", inputs["registry_keys"])
    source_family = lifecycle.family_map(source_fixture)
    require(source_family["motionwright.sqlite3"] is not None
            and isinstance(source_fixture["project"], dict), "tamper_source_project_missing")

    exact(record["mutation"], {"relative", "offset"}, "tamper_mutation_fields")
    target = record["mutation"]["relative"]
    validate_target_role(admitted["compiled"], inputs, expected_role, target)
    exact(record["foreign_fixture"], {"relative", "reference"}, "tamper_foreign_fields")
    foreign_name = record["foreign_fixture"]["relative"]
    require(isinstance(foreign_name, str)
            and lifecycle._relative(foreign_name, "foreign fixture") == foreign_name
            and foreign_name not in inputs["current_payloads"]
            and foreign_name != "uninstall.exe", "tamper_foreign_path")
    foreign_ref = record["foreign_fixture"]["reference"]
    foreign_id = lifecycle.ref_identity(foreign_ref)
    require(0 < foreign_id["bytes"] <= MAX_FOREIGN_BYTES, "tamper_foreign_size")
    lifecycle.bound_file(evidence_root, foreign_ref)
    require(type(record["operations"]) is list and len(record["operations"]) == 2,
            "tamper_operation_count")
    installation, refusal = record["operations"]
    for row in record["operations"]:
        exact(row, OPERATION_FIELDS, "tamper_operation_fields")
        lifecycle.operation_id(row["operation_id"])
    producer_ids = {row["operation_id"] for row in clean["operations"]}
    ids = {case_id, installation["operation_id"], refusal["operation_id"]}
    require(len(ids) == 3 and not ids & producer_ids, "tamper_operation_identity_reused")

    # Read locations only as receipt data. No recorded live path is opened.
    snapshots = [lifecycle.snapshot_value(
        evidence_root, row[position], row["operation_id"], position, inputs["registry_keys"])
        for row in record["operations"] for position in ("before", "after")]
    install_root, self_path = _locations(snapshots)
    install_before, install_after, before, after = snapshots
    require(not lifecycle.file_map(install_before)
            and all(all(value is None for value in view.values())
                    for view in install_before["registry"].values()),
            "tamper_install_not_fresh")
    expected_files = inputs["current_payloads"]
    installed_files = lifecycle.file_map(install_after)
    require(set(installed_files) == set(expected_files) | {"uninstall.exe"}
            and all(installed_files.get(name) == value for name, value in expected_files.items())
            and installed_files["uninstall.exe"]["bytes"] > 0, "tamper_fresh_install_payloads")
    for snapshot in snapshots:
        require(lifecycle.family_map(snapshot) == source_family
                and snapshot["project"] == source_fixture["project"],
                "tamper_project_or_sqlite_family_changed")
    key = inputs["registry_keys"][0]
    for snapshot in (install_after, before, after):
        lifecycle.installed_registry(snapshot, key, inputs["fixture_installer_version"])
    require(before["registry"] == install_after["registry"]
            and after["registry"] == before["registry"], "tamper_registry_changed")
    admission = lifecycle.bound_json(producer_root, record["producer_refs"]["admission"])
    setup_path = str(lifecycle.bound_file(producer_root, admission["refs"]["current_setup"]))
    _operation(evidence_root, installation, "install", record, inputs,
               expected_executable=inputs["current_setup"], expected_path=setup_path,
               arguments=["/S", "/D=" + install_root])
    _operation(evidence_root, refusal, "uninstall", record, inputs,
               expected_executable=installed_files["uninstall.exe"], expected_path=self_path,
               arguments=["/S", "_?=" + install_root])
    require(installation["process"] != refusal["process"], "tamper_process_identity_reused")

    before_files = lifecycle.file_map(before)
    require(set(before_files) == set(installed_files) | {foreign_name},
            "tamper_pre_uninstall_inventory")
    wanted = {**installed_files, foreign_name: foreign_id}
    wanted[target] = before_files[target]
    require(before_files == wanted, "tamper_unrelated_file_changed")
    _one_xor_byte(evidence_root, install_after["installed"][target],
                  before["installed"][target], record["mutation"]["offset"])
    require(lifecycle.file_map(after) == before_files, "tamper_files_not_preserved")
    return {
        "status": "SOURCE_BOUND_TAMPER_REFUSAL_OBSERVATIONS",
        "source_sha": expected_source, "source_tree": inputs["source_tree"],
        "run": copy.deepcopy(expected_run), "case_id": case_id, "role": expected_role,
        "producer_binding": copy.deepcopy(producer_binding),
        "original_terminal": copy.deepcopy(refusal["terminal"]),
        "observed_files_preserved": True, "registry_preserved": True,
        "sqlite_family_preserved": True,
        "project_observation_scope": "HISTORICAL_PREDECESSOR_PRESERVATION",
        "current_application_storage_open": "NOT_RUN",
        "current_application_storage_migration": "NOT_RUN",
        "directory_preservation": "NOT_OBSERVED",
        **copy.deepcopy(CLAIMS),
        "authenticity": "REQUIRES_EXTERNAL_ORIGINAL_WORKFLOW_ADMISSION",
    }
