"""One explicit disposable NSIS install and one same-size-tamper uninstall.

Only the existing nsis_lifecycle workflow may call this producer. The separate
consumer validates retained observations and never invokes an installer. An
unexpected terminal or incomplete custody stops this invocation without replay.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import uuid

from nsis_owned_files import _capture
from release_manifest import write_new_manifest
from windows_cold_start import (
    known_databases, native_windows_launch_environment, select_first_use_contract,
)
from windows_installer_lifecycle import (
    bound_file, bound_json, capture_snapshot, custody_error, family_map,
    file_identity, file_map, installed_registry, observed_terminal, require,
    retained_ref, retain_observed_result, source_observation,
)
from windows_tamper_artifact import receive_producer
from windows_uninstall_tamper import (
    CLAIMS, COMMANDS, INTENT_SCHEMA, SCHEMA, SCOPE,
    admit_producer, validate_target_role, validate_uninstall_tamper,
)

TARGETS = {
    "main": "motionwright-desktop.exe",
    "resource": "mw-ci-render/resource-alpha.dat",
    "external_binary": "mw-ci-current-sidecar.exe",
}
FOREIGN = "user-created-foreign.txt"


def process_identity(process):
    """Read only the handle returned by this invocation's Popen."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    creation, exited, kernel_time, user_time = (
        wintypes.FILETIME() for unused in range(4)
    )
    require(kernel.GetProcessTimes(
        wintypes.HANDLE(int(process._handle)), ctypes.byref(creation),
        ctypes.byref(exited), ctypes.byref(kernel_time), ctypes.byref(user_time)),
        "tamper_process_creation_unavailable")
    stamp = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
    require(process.pid > 0 and stamp > 0, "tamper_process_identity_invalid")
    return {"pid": process.pid, "creation_filetime": stamp}


def produce(*, root, role, artifact_id, artifact_sha256, output):
    source = os.environ["GITHUB_SHA"]
    run = {
        "repository": os.environ["GITHUB_REPOSITORY"],
        "run_id": int(os.environ["GITHUB_RUN_ID"]),
        "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
    }
    require(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and os.environ.get("MW_NSIS_LIFECYCLE_SCOPE") == "nsis_lifecycle"
            and run["run_attempt"] == 1 and role in TARGETS,
            "tamper_requires_explicit_first_disposable_windows_workflow")
    require(root.is_absolute() and output.is_absolute()
            and output.is_relative_to(root / "ci-evidence"),
            "tamper_output_location_invalid")
    before_source = source_observation(root, source)
    output.mkdir(parents=True)
    evidence = output / "case"
    evidence.mkdir()
    case_id = str(uuid.uuid4())
    operations = []
    mutation = {"relative": TARGETS[role], "offset": 0}
    binding = producer_refs = family_origin = foreign_fixture = None
    failure = record_ref = result = None
    install_root = Path(os.environ["RUNNER_TEMP"]) / ("mw-nsis-tamper-" + case_id)
    database = None

    def publish(name, value):
        path = evidence / name
        write_new_manifest(evidence, path, value)
        return retained_ref(evidence, path)

    try:
        binding = receive_producer(
            output / "received", artifact_id=artifact_id,
            artifact_sha256=artifact_sha256,
            expected_source=source, expected_run=run)
        producer_root = output / "received" / "producer"
        producer_refs = {
            name: retained_ref(producer_root, producer_root / (name + ".json"))
            for name in ("admission", "record", "result")
        }
        admitted = admit_producer(
            producer_root, producer_refs, binding, source, run)
        verified = admitted["inputs"]
        validate_target_role(admitted["compiled"], verified, role, TARGETS[role])
        original_record = admitted["record"]
        require(admitted["source"] == before_source,
                "tamper_producer_source_differs_from_checkout")
        family_origin = {
            "source_fixture": original_record["operations"][1]["after"],
            "project_origin": original_record["project_origin"],
        }
        family_fixture = bound_json(
            producer_root, family_origin["source_fixture"])
        original_project = bound_json(
            producer_root, family_origin["project_origin"])
        require(family_fixture["project"] is not None
                and family_fixture["project"] == original_project["project"],
                "tamper_historical_project_missing")
        project_contract = select_first_use_contract(
            verified["previous_source"],
            verified["previous_payloads"]["motionwright-desktop.exe"]["sha256"])
        child_env = native_windows_launch_environment(os.environ)
        for name in ("GH_TOKEN", "GITHUB_TOKEN"):
            child_env.pop(name, None)
        require("APPDATA" in child_env and "LOCALAPPDATA" in child_env,
                "tamper_native_appdata_missing")
        appdata_roots = [Path(child_env["APPDATA"]), Path(child_env["LOCALAPPDATA"])]
        require(not known_databases(appdata_roots),
                "tamper_fixture_project_store_already_present")
        database = appdata_roots[0] / "com.seradotcom.motionwright/motionwright.sqlite3"
        require(not database.parent.exists()
                and not install_root.exists() and install_root.is_absolute()
                and not any(char in str(install_root) for char in '"\r\n\0'),
                "tamper_fixture_roots_not_fresh")
        database.parent.mkdir()
        for name, ref in family_fixture["sqlite_family"].items():
            require(name in {"motionwright.sqlite3", "motionwright.sqlite3-wal",
                             "motionwright.sqlite3-shm"}, "tamper_family_filename")
            if ref is not None:
                original = bound_file(producer_root, ref)
                destination = database.parent / name
                digest, size, unused = _capture(original, destination)
                require({"bytes": size, "sha256": digest} == file_identity(original)
                        == file_identity(destination), "tamper_family_copy_changed")

        def snapshot(phase, position, operation):
            return capture_snapshot(
                evidence, snapshot_name=phase + "-" + position,
                operation=operation, position=position, install_root=install_root,
                database=database, registry_keys=verified["registry_keys"],
                first_use_contract=project_contract)

        def observe(phase, executable, arguments, *, expected_executable):
            operation = str(uuid.uuid4())
            executable_before = file_identity(executable)
            require(executable_before == expected_executable,
                    "tamper_effect_does_not_match_admitted_executable")
            baseline = snapshot(phase, "before", operation)
            initial = bound_json(evidence, baseline)
            if phase == "install":
                require(not initial["installed"]
                        and all(all(value is None for value in row.values())
                                for row in initial["registry"].values()),
                        "tamper_initial_install_or_registry_not_empty")
                require(family_map(initial) == family_map(family_fixture)
                        and initial["project"] == family_fixture["project"],
                        "tamper_initial_historical_family_changed")
            else:
                installed = bound_json(evidence, operations[0]["after"])
                expected_files = file_map(installed)
                expected_files[mutation["relative"]] = file_identity(
                    install_root / mutation["relative"])
                expected_files[FOREIGN] = file_identity(install_root / FOREIGN)
                require(file_map(initial) == expected_files
                        and family_map(initial) == family_map(installed)
                        and initial["project"] == installed["project"]
                        and initial["registry"] == installed["registry"],
                        "tamper_uninstall_preimage_changed")
            require(source_observation(root, source) == before_source,
                    "tamper_source_changed_before_effect")
            require(file_identity(executable) == executable_before,
                    "tamper_effect_executable_changed")
            intent = publish(phase + "-intent.json", {
                "schema": INTENT_SCHEMA, "source_sha": source, "run": run,
                "case_id": case_id, "role": role, "phase": phase,
                "command": COMMANDS[phase], "operation_id": operation,
                "executable": executable_before, "executable_path": str(executable),
                "arguments": arguments, "launch_limit": 1,
                "replay_authorized": False,
            })
            # NSIS requires the final /D= or _?= argument to remain unquoted.
            command_line = subprocess.list2cmdline(
                [str(executable), *arguments[:-1]]) + " " + arguments[-1]
            identity_value = None
            secondary = []
            original = observed_terminal(None)
            try:
                process = subprocess.Popen(
                    command_line, executable=str(executable),
                    cwd=str(executable.parent), env=child_env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
                try:
                    identity_value = process_identity(process)
                except Exception as error:
                    secondary.append(custody_error("process_identity", error))
                try:
                    original = observed_terminal(process.wait(timeout=180))
                except subprocess.TimeoutExpired:
                    original = observed_terminal(None, timed_out=True)
                except Exception as error:
                    secondary.append(custody_error("installer_wait", error))
            except Exception as error:
                secondary.append(custody_error("installer_launch", error))

            def after_observation():
                require(original["exit_code"] is not None,
                        "tamper_installer_terminal_not_quiescent")
                return snapshot(phase, "after", operation)

            retained = retain_observed_result(
                evidence, phase, original, after_observation, context={
                    "operation_id": operation, "process": identity_value,
                    "executable": executable_before, "source_sha": source, "run": run,
                })
            row = {
                "phase": phase, "operation_id": operation, "launch_count": 1,
                "command": COMMANDS[phase], "process": identity_value,
                "executable": executable_before, "executable_path": str(executable),
                "terminal": retained["original_terminal"], "terminal_receipt": None,
                "custody_errors": retained["custody_errors"] + secondary,
                "before": baseline, "after": retained["observation"], "intent": intent,
            }
            operations.append(row)
            try:
                row["terminal_receipt"] = retained_ref(
                    evidence, evidence / (phase + "-terminal.json"))
            except Exception as error:
                row["custody_errors"].append(custody_error("terminal_readback", error))
            publish(phase + "-operation.json", row)
            expected_terminal = observed_terminal(0 if phase == "install" else 2)
            require(row["terminal"] == expected_terminal and not row["custody_errors"],
                    "tamper_effect_or_custody_incomplete")
            require(file_identity(executable) == executable_before,
                    "tamper_effect_executable_changed_after_terminal")
            return bound_json(evidence, row["after"])

        admission = bound_json(producer_root, producer_refs["admission"])
        setup = bound_file(producer_root, admission["refs"]["current_setup"])
        installed = observe("install", setup, ["/S", "/D=" + str(install_root)],
                            expected_executable=verified["current_setup"])
        actual_files = file_map(installed)
        require(set(actual_files) == set(verified["current_payloads"]) | {"uninstall.exe"}
                and all(actual_files.get(name) == value
                        for name, value in verified["current_payloads"].items())
                and actual_files["uninstall.exe"]["bytes"] > 0,
                "tamper_current_install_inventory")
        installed_registry(
            installed, verified["registry_keys"][0], verified["fixture_installer_version"])
        require(family_map(installed) == family_map(family_fixture)
                and installed["project"] == family_fixture["project"],
                "tamper_current_install_changed_historical_project")

        relative = mutation["relative"]
        target = install_root.joinpath(*relative.split("/"))
        require(relative in verified["current_payloads"]
                and file_identity(target) == verified["current_payloads"][relative]
                and file_identity(target)["bytes"] > 0,
                "tamper_target_not_compiled_payload")
        original_identity = file_identity(target)
        # This deliberate edit is confined to the selected owned fixture. Neither
        # a changed main nor a changed external binary is ever executed.
        with target.open("r+b") as stream:
            before_byte = stream.read(1)
            require(len(before_byte) == 1, "tamper_target_empty")
            stream.seek(0)
            require(stream.write(bytes([before_byte[0] ^ 1])) == 1,
                    "tamper_mutation_short_write")
            stream.flush()
            os.fsync(stream.fileno())
        changed_identity = file_identity(target)
        require(changed_identity["bytes"] == original_identity["bytes"]
                and changed_identity["sha256"] != original_identity["sha256"],
                "tamper_mutation_identity_invalid")
        foreign = install_root / FOREIGN
        body = b"Fixture-owned foreign file: preserve during refused uninstall.\n"
        with foreign.open("xb") as stream:
            require(stream.write(body) == len(body), "tamper_foreign_short_write")
            stream.flush()
            os.fsync(stream.fileno())
        foreign_copy = evidence / "foreign-fixture.original"
        _capture(foreign, foreign_copy)
        foreign_fixture = {"relative": FOREIGN, "reference": retained_ref(evidence, foreign_copy)}
        observe("uninstall", install_root / "uninstall.exe",
                ["/S", "_?=" + str(install_root)],
                expected_executable=actual_files["uninstall.exe"])
    except Exception as error:
        failure = custody_error("tamper_sequence", error)
    finally:
        try:
            after_source = source_observation(root, source)
            record = {
                "schema": SCHEMA, "scope": SCOPE, "case_id": case_id,
                "source_sha": source, "source_tree": before_source["tree"],
                "run": run, "role": role, "producer_binding": binding,
                "producer_refs": producer_refs, "family_origin": family_origin,
                "mutation": mutation, "foreign_fixture": foreign_fixture,
                "operations": operations, "source_before": before_source,
                "source_after": after_source, "claims": dict(CLAIMS),
            }
            record_ref = publish("record.json", record)
            if failure is None:
                result = validate_uninstall_tamper(
                    evidence, record, producer_root=output / "received" / "producer",
                    producer_binding=binding, expected_source=source,
                    expected_run=run, expected_role=role)
                publish("result.json", {
                    "schema": "motionwright.windows-uninstall-tamper-verification/1",
                    "record": record_ref, "result": result,
                })
        except Exception as error:
            failure = failure or custody_error("tamper_verification", error)
        if failure is not None:
            try:
                publish("diagnostic.json", {
                    "schema": "motionwright.windows-uninstall-tamper-diagnostic/1",
                    "failure": failure, "case_id": case_id, "role": role,
                    "source_sha": source, "run": run, "installer_replays": 0,
                    "originals": [
                        {"phase": row["phase"], "terminal": row["terminal"],
                         "custody_errors": row["custody_errors"]} for row in operations
                    ],
                    "human_acceptance": "NOT_RUN", "release_published": False,
                })
            except Exception:
                sys.stderr.write("windows-uninstall-tamper=INCOMPLETE diagnostic=UNKNOWN\n")
    ok = failure is None and result is not None
    print("windows-uninstall-tamper=" + (
        "SOURCE_BOUND_TAMPER_REFUSAL_OBSERVATIONS" if ok else "INCOMPLETE")
        + " role=" + role + " human=NOT_RUN interruption=NOT_RUN")
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=tuple(TARGETS))
    parser.add_argument("--artifact-id", required=True, type=int)
    parser.add_argument("--artifact-sha256", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    try:
        return produce(
            root=Path(os.environ["GITHUB_WORKSPACE"]), role=args.role,
            artifact_id=args.artifact_id, artifact_sha256=args.artifact_sha256,
            output=args.output_root)
    except Exception as error:
        sys.stderr.write("windows-uninstall-tamper=INCOMPLETE " + type(error).__name__ + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
