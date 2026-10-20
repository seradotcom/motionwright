#!/usr/bin/env python3
"""Private custody around the single existing MLT verification producer.

This tool fetches admitted source artifacts, transfers an installation receipt,
and seals selected original evidence. It never installs a module or starts,
retries, or substitutes a renderer. Only a closed public projection is emitted.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import zipfile

from ci_private_evidence import (
    CustodyRejected, HEX, SOURCE, MAX_FILE, MAX_TOTAL, MAX_FILES, MAX_JSON,
    CONTEXT_KEYS, MAGIC, SEAL_SCHEMA, require, sha, json_bytes, strict,
    regular, new_bytes, new_json, public_key, metadata_identity,
    gather_snapshot, encrypt_snapshot, decrypt_snapshot,
)
import native_mlt_g2_observation as g2
import native_mlt_runtime_admission as admission

DISPATCH_SCHEMA = "motionwright.native.mlt-g2-dispatch/1"
PUBLIC_SCHEMA = "motionwright.native.mlt-g2-public/1"
REPO = "seradotcom/motionwright"
ARTIFACT_KEYS = {"artifact_id", "run_id", "run_attempt", "source_sha", "zip_sha256"}
PUBLIC_KEYS = {
    "schema", "context", "scope", "native_terminal", "producer_exit_code",
    "source_content_and_live_module_verified", "job_outcome_at_seal",
    "sealed_originals_sha256", "sealed_originals_bytes", "recipient_spki_sha256",
    "custody_scope", "source_unchanged", "selected_originals_complete",
    "native_operation_retried", "packet_PTS_and_AAC_priming_verified",
    "physical_AV_master_acceptance", "human_acceptance", "release_authorized",
}


def context(env):
    require(env.get("GITHUB_ACTIONS") == "true" and env.get("CI") == "true"
            and env.get("RUNNER_OS") == "Linux"
            and env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and env.get("GITHUB_RUN_ATTEMPT") == "1"
            and env.get("MW_MLT_SCOPE") == "repaired-g2", "closed_manual_selection_required")
    require(SOURCE.fullmatch(env.get("GITHUB_SHA", "")) is not None
            and re.fullmatch(r"[1-9][0-9]*", env.get("GITHUB_RUN_ID", "")) is not None,
            "execution_identity_invalid")
    return {"source_sha": env["GITHUB_SHA"], "semwright_sha": g2.SEMWRIGHT_PIN,
            "run_id": env["GITHUB_RUN_ID"], "run_attempt": "1"}


def runner_root(env):
    path = Path(env.get("RUNNER_TEMP", ""))
    require(path.is_absolute() and path.is_dir() and path.resolve(strict=True) == path
            and path.as_posix().startswith("/home/runner/work/_temp")
            and not any(p.is_symlink() for p in path.parents), "runner_temp_invalid")
    return path


def names(env):
    current = context(env)
    root = runner_root(env)
    suffix = current["run_id"] + "-1"
    return (root / ("mw-mlt-g2-control-" + suffix),
            root / ("mw-mlt-g2-installation-" + suffix),
            root / ("mw-mlt-g2-original-" + suffix))


def artifact_descriptor(value):
    require(type(value) is dict and set(value) == ARTIFACT_KEYS, "artifact_descriptor_keys")
    require(all(type(value[k]) is int and value[k] > 0 for k in ("artifact_id", "run_id"))
            and value["run_attempt"] == 1 and type(value["run_attempt"]) is int
            and SOURCE.fullmatch(value["source_sha"]) is not None
            and HEX.fullmatch(value["zip_sha256"]) is not None, "artifact_descriptor_identity")
    return value


def safe_member(value):
    require(type(value) is str and 0 < len(value) <= 256 and
            "\\" not in value and not value.startswith("/") and
            all(p not in ("", ".", "..") for p in value.split("/"))
            and all(32 <= ord(c) < 127 for c in value), "artifact_member_path")
    return value


def validate_dispatch(raw, current):
    value = strict(raw)
    require(set(value) == {"schema", "motionwright_sha", "semwright_sha",
                           "official_runtime", "compiler", "recipient"}
            and value["schema"] == DISPATCH_SCHEMA
            and value["motionwright_sha"] == current["source_sha"]
            and value["semwright_sha"] == current["semwright_sha"], "dispatch_source_binding")
    official = value["official_runtime"]
    require(type(official) is dict and set(official) == {
        "artifact", "receipt_sha256", "known_original_module_sha256", "known_melt_sha256"},
        "official_runtime_contract")
    artifact_descriptor(official["artifact"])
    require(all(type(official[k]) is str and HEX.fullmatch(official[k]) is not None
                for k in ("receipt_sha256", "known_original_module_sha256", "known_melt_sha256")),
            "official_runtime_pins_required")
    compiler = value["compiler"]
    require(type(compiler) is dict and set(compiler) == {"artifact", "members"}, "compiler_contract")
    artifact_descriptor(compiler["artifact"])
    require(type(compiler["members"]) is dict and set(compiler["members"]) == {
        "original_c_source", "repaired_c_source", "compiled_module", "compiler_receipt_path"},
        "compiler_member_contract")
    selected = [safe_member(p) for p in compiler["members"].values()]
    require(len(set(selected)) == len(selected), "duplicate_selected_compiler_member")
    public_key(value["recipient"])
    return value


def source_guard(repo, current):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull,
               GIT_CONFIG_GLOBAL=os.devnull, GIT_GRAFT_FILE=os.devnull,
               GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    def git(*argv):
        return subprocess.check_output(["/usr/bin/git", "--no-replace-objects", "-C", str(repo),
                                        "-c", "core.fsmonitor=false", *argv], env=env, timeout=20)
    require(git("rev-parse", "HEAD").decode().strip() == current["source_sha"], "source_HEAD_changed")
    require(not git("diff", "--name-only", "HEAD", "--"), "tracked_source_changed")
    selected = [
        "SOURCE_LOCK.json", ".github/workflows/native-mlt-sequence-e2e.yml",
        "tooling/ci_private_evidence.py", "tooling/requirements/mlt-g2-custody.txt",
        "tooling/native_mlt_g2_custody.py", "tooling/native_mlt_g2_observation.py",
        "tooling/native_mlt_sequence_e2e.py", "tooling/native_mlt_runtime_admission.py",
        "tooling/native_mlt_loaded_module.py", "tooling/mlt_official_runtime_intake.py",
        "tooling/native_mlt_ci_pcm_envelope.py", "tooling/native_pcm_source_fidelity.py",
        "crates/native/examples/native-mlt-sequence-e2e.rs",
    ]
    result = {}
    for path in selected:
        raw = git("show", current["source_sha"] + ":" + path)
        require(regular(repo / path)[0] == raw, "source_blob_changed")
        result[path] = sha(raw)
    lock = strict(regular(repo / "SOURCE_LOCK.json", MAX_JSON)[0])
    require(lock["dependencies"]["semwright"]["revision"] == current["semwright_sha"], "source_lock_changed")
    return {"context": current, "tree": git("rev-parse", "HEAD^{tree}").decode().strip(), "files": result}


def gh_json_or_bytes(route, name, directory):
    command = ["/usr/bin/gh", "api", route]
    new_json(directory / (name + ".intent.json"), {"argv": command, "automatic_retry": False})
    with (directory / (name + ".stdout")).open("xb") as output, \
            (directory / (name + ".stderr")).open("xb") as error:
        result = subprocess.run(command, stdout=output, stderr=error, check=False, timeout=120)
    new_json(directory / (name + ".exit.json"), {"exit_code": result.returncode})
    require(result.returncode == 0, "artifact_API_failed_no_retry")
    return regular(directory / (name + ".stdout"), MAX_FILE)[0]


def download_artifact(descriptor, label, directory):
    value = artifact_descriptor(descriptor)
    prefix = "repos/" + REPO + "/actions/"
    run_raw = gh_json_or_bytes(prefix + "runs/" + str(value["run_id"]), label + "-run", directory)
    run = strict(run_raw)
    require(run["id"] == value["run_id"] and run["run_attempt"] == value["run_attempt"]
            and run["head_sha"] == value["source_sha"] and run["status"] == "completed",
            "artifact_original_run_changed")
    raw = gh_json_or_bytes(prefix + "artifacts/" + str(value["artifact_id"]), label + "-api", directory)
    item = strict(raw)
    require(item["id"] == value["artifact_id"] and item["expired"] is False
            and item["workflow_run"]["id"] == value["run_id"]
            and item["workflow_run"]["head_sha"] == value["source_sha"]
            and item["digest"] == "sha256:" + value["zip_sha256"]
            and 0 < item["size_in_bytes"] <= MAX_FILE, "artifact_original_metadata_changed")
    archive = gh_json_or_bytes(prefix + "artifacts/" + str(value["artifact_id"]) + "/zip",
                               label + "-zip", directory)
    require(len(archive) == item["size_in_bytes"] and sha(archive) == value["zip_sha256"],
            "artifact_original_zip_changed")
    return archive


def zip_member(raw, member, expected_sha, limit):
    safe_member(member)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(len(entries) <= MAX_FILES and len({p.filename for p in entries}) == len(entries)
                and sum(p.file_size for p in entries) <= MAX_TOTAL, "artifact_container_budget")
        for entry in entries:
            safe_member(entry.filename.rstrip("/"))
            require(not stat.S_ISLNK(entry.external_attr >> 16), "artifact_link")
        matches = [p for p in entries if p.filename == member]
        require(len(matches) == 1 and not matches[0].is_dir()
                and stat.S_IFMT(matches[0].external_attr >> 16) in (0, stat.S_IFREG)
                and 0 < matches[0].file_size <= limit, "artifact_selected_regular_member")
        require(archive.testzip() is None, "artifact_CRC_failed")
        data = archive.read(matches[0])
    require(sha(data) == expected_sha, "artifact_selected_digest_changed")
    return data


def prepare(env):
    require(sys.platform == "linux" and os.geteuid() != 0, "prepare_requires_normal_Linux_runner")
    current = context(env)
    dispatch = validate_dispatch(env.get("MW_MLT_G2_ADMISSION", "").encode(), current)
    control, installed, session = names(env)
    require(not installed.exists() and not session.exists(), "original_effect_directory_preexists")
    control.mkdir(mode=0o700, exist_ok=False)
    for name in ("records", "intake", "handoff", "logs", "public"):
        (control / name).mkdir(mode=0o700)
    new_json(control / "records/dispatch.json", dispatch)
    new_json(control / "records/context.json", current)
    new_json(control / "records/source-before.json", source_guard(Path(env["GITHUB_WORKSPACE"]), current))
    original = {"artifact_id": 12109323512, "run_id": 39054798740, "run_attempt": 1,
                "source_sha": g2.REFERENCE_PR_HEAD, "zip_sha256": g2.REFERENCE_ZIP_SHA256}
    archive = download_artifact(original, "reference", control / "intake")
    g2.reference_source(archive)
    new_bytes(control / "intake/original-artifact.zip", archive)
    official = dispatch["official_runtime"]
    raw = download_artifact(official["artifact"], "official", control / "intake")
    receipt = zip_member(raw, "distribution/official-runtime-receipt.json",
                         official["receipt_sha256"], MAX_JSON)
    proof = strict(receipt)
    g2.verify_official_runtime_bindings(
        proof, official["known_original_module_sha256"], official["known_melt_sha256"])
    new_bytes(control / "intake/official-runtime-receipt.json", receipt)
    raw = download_artifact(dispatch["compiler"]["artifact"], "compiler", control / "intake")
    pins = {"original_c_source": admission.ORIGINAL_SHA, "repaired_c_source": admission.REPAIRED_SHA,
            "compiled_module": g2.REPAIRED_MODULE_SHA256, "compiler_receipt_path": g2.COMPILER_RECEIPT_SHA256}
    for name, pin in pins.items():
        data = zip_member(raw, dispatch["compiler"]["members"][name], pin, admission.MAX_ELF_BYTES)
        new_bytes(control / "intake" / name, data)
    admission.source_compiler_closure(
        control / "intake/original_c_source", control / "intake/repaired_c_source",
        control / "intake/compiled_module", g2.REPAIRED_MODULE_SHA256,
        control / "intake/compiler_receipt_path", g2.COMPILER_RECEIPT_SHA256)
    new_json(control / "records/intake-complete.json",
             {"context": current, "source_inputs": "ADMITTED_BYTES_MATCHED",
              "module_installed": False, "native_execution": "NOT_RUN"})
    return control


def handoff(env, uid, gid):
    """Copy existing installation originals without changing their permissions.

    The copy also runs after a failed admission. Partial originals are retained
    for sealing, but only a complete verified handoff can feed the producer.
    """
    require(sys.platform == "linux" and os.geteuid() == 0 and uid > 0 and gid > 0,
            "handoff_requires_privileged_reader_and_normal_recipient")
    control, installed, _ = names(env)
    require(control.stat().st_uid == uid and control.stat().st_gid == gid
            and control.resolve(strict=True) == control, "handoff_control_owner")
    dispatch = validate_dispatch(regular(control / "records/dispatch.json", MAX_JSON)[0], context(env))
    directory = control / "handoff"
    require(directory.stat().st_uid == uid and not any(directory.iterdir()), "handoff_target_not_fresh")
    official = dispatch["official_runtime"]
    copied, errors, admitted = {}, [], False
    directory_status = "ABSENT"
    if installed.exists():
        before = installed.lstat()
        require(stat.S_ISDIR(before.st_mode) and before.st_uid == 0
                and stat.S_IMODE(before.st_mode) == 0o700
                and installed.resolve(strict=True) == installed, "handoff_root_receipt_directory")
        directory_status = "OBSERVED"
        observed_names = sorted(p.name for p in installed.iterdir())
        allowed = {"installation.json": MAX_JSON,
                   "original_system_mlt_avformat.so": admission.MAX_ELF_BYTES}
        if set(observed_names) - set(allowed):
            errors.append({"stage": "directory", "code": "unexpected_installation_member",
                           "names": observed_names})
        for name, limit in allowed.items():
            original = installed / name
            if not original.exists():
                errors.append({"stage": name, "code": "original_absent"})
                continue
            try:
                data, observed = regular(original, limit)
                require(observed["before"]["uid"] == 0, "installation_original_not_root_owned")
                new_bytes(directory / name, data, owner=(uid, gid), mode=0o400)
                copied[name] = {"sha256": sha(data), "bytes": len(data), "observation": observed}
                require(regular(original, limit)[0] == data, "handoff_original_changed")
            except Exception as error:
                errors.append({"stage": name, "error_type": type(error).__name__,
                               "error": str(error), "automatic_retry": False})
        try:
            require(metadata_identity(installed.lstat()) == metadata_identity(before)
                    and sorted(p.name for p in installed.iterdir()) == observed_names,
                    "installation_directory_changed_during_handoff")
            receipt = strict(regular(directory / "installation.json", MAX_JSON)[0])
            backup = regular(directory / "original_system_mlt_avformat.so", admission.MAX_ELF_BYTES)[0]
            require(receipt.get("schema") == "motionwright.native.mlt-module-installed/1"
                    and receipt.get("actual_installed_module_sha256") == g2.REPAIRED_MODULE_SHA256
                    and receipt.get("original_module_sha256") == official["known_original_module_sha256"]
                    and receipt.get("module_replaced") is True
                    and receipt.get("new_native_master_issued") is False,
                    "handoff_installation_contract")
            require(sha(backup) == official["known_original_module_sha256"]
                    and receipt.get("original_backup_sha256") == sha(backup), "handoff_backup_digest")
            admitted = not errors
        except Exception as error:
            errors.append({"stage": "contract", "error_type": type(error).__name__,
                           "error": str(error), "automatic_retry": False})
    proof = {
        "schema": "motionwright.native.mlt-receipt-handoff/1", "context": context(env),
        "installation_directory_status": directory_status,
        "copied_originals": copied, "custody_errors": errors,
        "installation_receipt_admitted": admitted,
        "receipt_sha256": copied.get("installation.json", {}).get("sha256"),
        "backup_sha256": copied.get("original_system_mlt_avformat.so", {}).get("sha256"),
        "recipient_uid": uid, "recipient_gid": gid,
        "copy_mode": "0400", "original_directory_mode": "0700",
        "original_permissions_changed": False, "producer_executed_as_root": False,
    }
    new_json(directory / "handoff.json", proof, owner=(uid, gid), mode=0o400)
    return admitted


def make_inputs(env):
    require(sys.platform == "linux" and os.geteuid() != 0, "inputs_require_normal_runner")
    control, _, session = names(env)
    current = context(env)
    dispatch = validate_dispatch(regular(control / "records/dispatch.json", MAX_JSON)[0], current)
    handoff_proof = strict(regular(control / "handoff/handoff.json", MAX_JSON)[0])
    installed = control / "handoff/installation.json"
    require(handoff_proof["context"] == current and handoff_proof["recipient_uid"] == os.getuid()
            and handoff_proof.get("installation_receipt_admitted") is True
            and handoff_proof.get("custody_errors") == []
            and handoff_proof["receipt_sha256"] == sha(regular(installed, MAX_JSON)[0]),
            "handoff_copy_readback_changed")
    official = dispatch["official_runtime"]
    data = {
        "schema": g2.SCHEMA, "motionwright_sha": current["source_sha"],
        "semwright_sha": current["semwright_sha"], "run_id": current["run_id"], "run_attempt": "1",
        "original_artifact_zip": str(control / "intake/original-artifact.zip"),
        "private_evidence_dir": str(session),
        "known_original_module_sha256": official["known_original_module_sha256"],
        "known_melt_sha256": official["known_melt_sha256"],
        "official_runtime_receipt_path": str(control / "intake/official-runtime-receipt.json"),
        "official_runtime_receipt_sha256": official["receipt_sha256"],
        "compiled_module_sha256": g2.REPAIRED_MODULE_SHA256,
        "compiler_receipt_sha256": g2.COMPILER_RECEIPT_SHA256,
        "installation_receipt_path": str(installed),
        "installation_receipt_sha256": handoff_proof["receipt_sha256"],
    }
    data.update({name: str(control / "intake" / name) for name in (
        "original_c_source", "repaired_c_source", "compiled_module", "compiler_receipt_path")})
    require(set(data) == g2.INPUT_KEYS, "producer_input_contract_changed")
    new_json(control / "records/g2-inputs.json", data)


def guard_before_admission(env):
    require(sys.platform == "linux" and os.geteuid() != 0, "guard_requires_normal_runner")
    control, _, _ = names(env)
    current = context(env)
    observed = source_guard(Path(env["GITHUB_WORKSPACE"]), current)
    require(strict(regular(control / "records/source-before.json", MAX_JSON)[0]) == observed,
            "source_changed_before_admission")
    new_json(control / "records/source-before-admission.json", observed)
    new_json(control / "records/admission-started.json",
             {"context": current, "command": "native_mlt_runtime_admission.py --admit",
              "automatic_retry": False, "native_master_started": False})


def begin(env):
    require(sys.platform == "linux" and os.geteuid() != 0, "producer_must_not_be_root")
    control, _, _ = names(env)
    require((control / "records/g2-inputs.json").is_file(), "producer_inputs_absent")
    observed = source_guard(Path(env["GITHUB_WORKSPACE"]), context(env))
    require(strict(regular(control / "records/source-before.json", MAX_JSON)[0]) == observed,
            "source_changed_before_producer")
    new_json(control / "records/source-before-producer.json", observed)
    new_json(control / "records/producer-started.json",
             {"context": context(env), "command": "tooling/native_mlt_sequence_e2e.py --g2-inputs",
              "automatic_retry": False, "producer_uid": os.getuid()})


def record_exit(env, code):
    require(type(code) is int and 0 <= code <= 255, "producer_exit_invalid")
    control, _, _ = names(env)
    require((control / "records/producer-started.json").is_file(), "producer_start_absent")
    new_json(control / "records/producer-exit.json", {"context": context(env), "exit_code": code,
                                                   "native_operation_retried": False})


def projection(current, final, exit_code, job_status, encrypted, recipient, *,
               producer_started=False, source_unchanged=False, selected_originals_complete=True):
    require(job_status in {"success", "failure", "cancelled"} and
            (exit_code is None or type(exit_code) is int and 0 <= exit_code <= 255),
            "public_terminal_inputs")
    terminal = "NOT_STARTED"
    verified = False
    if final is not None:
        require(final.get("schema") == "motionwright.native.mlt-g2-custody/1"
                and final.get("terminal_scope") == g2.TERMINAL_SCOPE
                and final.get("native_operation_retried") is False, "private_final_contract")
        terminal = final.get("native_terminal")
        require(terminal in {"NOT_STARTED", "ERROR_OR_UNKNOWN", "SUCCESS_REPORTED",
                             "RESULT_REPORTED_UNVERIFIED"}, "private_terminal_unknown")
        verified = (source_unchanged and selected_originals_complete
                    and job_status == "success" and exit_code == 0
                    and terminal == "SUCCESS_REPORTED"
                    and final.get("source_content_and_live_module_verified") is True
                    and final.get("secondary_failures") == []
                    and final.get("packet_PTS_and_AAC_priming_verified") is False
                    and final.get("physical_AV_master_acceptance") is False
                    and final.get("human_acceptance") is False)
    elif producer_started or exit_code is not None:
        terminal = "ERROR_OR_UNKNOWN"
    result = {"schema": PUBLIC_SCHEMA, "context": current,
              "scope": "source-content-and-live-module-only",
              "native_terminal": terminal, "producer_exit_code": exit_code,
              "source_content_and_live_module_verified": verified,
              "job_outcome_at_seal": job_status, "sealed_originals_sha256": sha(encrypted),
              "sealed_originals_bytes": len(encrypted), "recipient_spki_sha256": recipient["sha256"],
              "custody_scope": "selected-original-files-at-read-time",
              "source_unchanged": source_unchanged,
              "selected_originals_complete": selected_originals_complete,
              "native_operation_retried": False, "packet_PTS_and_AAC_priming_verified": False,
              "physical_AV_master_acceptance": False, "human_acceptance": False,
              "release_authorized": False}
    require(set(result) == PUBLIC_KEYS, "public_projection_keys")
    return result


def seal(env, job_status):
    require(sys.platform == "linux" and os.geteuid() != 0, "seal_requires_normal_runner")
    control, _, session = names(env)
    current = context(env)
    dispatch = validate_dispatch(regular(control / "records/dispatch.json", MAX_JSON)[0], current)
    repo = Path(env["GITHUB_WORKSPACE"])
    source_unchanged, custody_valid = False, True
    def retain_error(name, error):
        new_json(control / ("records/" + name + ".json"),
                 {"error_type": type(error).__name__, "error": str(error), "no_retry": True})
    try:
        source_after = source_guard(repo, current)
        new_json(control / "records/source-after.json", source_after)
        source_unchanged = strict(regular(control / "records/source-before.json", MAX_JSON)[0]) == source_after
    except Exception as error:
        retain_error("source-after-error", error)
    private_final = session / "final-custody.json"
    final, exit_code = None, None
    try:
        final = strict(regular(private_final, MAX_JSON)[0]) if private_final.exists() else None
    except Exception as error:
        custody_valid = False
        retain_error("final-read-error", error)
    exit_path = control / "records/producer-exit.json"
    try:
        if exit_path.exists():
            value = strict(regular(exit_path, MAX_JSON)[0])
            require(set(value) == {"context", "exit_code", "native_operation_retried"}
                    and value["context"] == current and value["native_operation_retried"] is False
                    and type(value["exit_code"]) is int and 0 <= value["exit_code"] <= 255,
                    "producer_exit_receipt_contract")
            exit_code = value["exit_code"]
    except Exception as error:
        custody_valid = False
        retain_error("exit-read-error", error)
    started = (control / "records/producer-started.json").exists() or session.exists()
    try:
        # Validate the terminal contract before capturing originals. A corrupt
        # terminal is retained privately and yields ERROR_OR_UNKNOWN publicly.
        projection(current, final, exit_code, job_status, b"validation", dispatch["recipient"])
    except Exception as error:
        custody_valid = False
        final = None
        retain_error("terminal-contract-error", error)
    roots = {name: control / name for name in ("records", "intake", "handoff", "logs")}
    roots.update(observation=session, producer=repo / "verification/native-mlt-sequence-e2e",
                 contracts=runner_root(env) / ("mw-mlt-g2-contracts-" + current["run_id"] + "-1"))
    owner_marker = session / "owner-workspace.json"
    try:
        if owner_marker.exists():
            owner = strict(regular(owner_marker, MAX_JSON)[0])
            path = Path(owner["path"])
            info = path.lstat()
            require(owner["schema"] == "motionwright.native.mlt-g2-owner-workspace/1"
                    and owner["motionwright_sha"] == current["source_sha"]
                    and owner["semwright_sha"] == current["semwright_sha"]
                    and owner["run_id"] == current["run_id"] and owner["run_attempt"] == "1"
                    and path.parent == runner_root(env)
                    and path.name.startswith("motionwright-native-av-g2-")
                    and path.resolve(strict=True) == path
                    and stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
                    and (info.st_dev, info.st_ino, info.st_uid, info.st_gid) ==
                        (owner["device"], owner["inode"], owner["uid"], owner["gid"])
                    and info.st_uid == os.getuid(), "owner_workspace_custody_identity")
            roots["owner"] = path
        elif started:
            custody_valid = False
            retain_error("owner-marker-absent", CustodyRejected("original_owner_tree_not_observed"))
    except Exception as error:
        custody_valid = False
        retain_error("owner-identity-error", error)
    raw, captured = gather_snapshot(roots, preserve_partial=True)
    encrypted = encrypt_snapshot(raw, dispatch["recipient"], current)
    public = projection(current, final, exit_code, job_status, encrypted, dispatch["recipient"],
                        producer_started=started, source_unchanged=source_unchanged,
                        selected_originals_complete=captured and custody_valid)
    output = control / "public"
    require(not any(output.iterdir()), "public_output_preexists")
    new_bytes(output / "sealed-originals.bin", encrypted)
    new_json(output / "public-evidence.json", public)
    require({p.name for p in output.iterdir()} == {"sealed-originals.bin", "public-evidence.json"},
            "public_output_extra_file")
    return public


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "guard", "handoff", "inputs", "begin", "exit", "seal"))
    parser.add_argument("--uid", type=int)
    parser.add_argument("--gid", type=int)
    parser.add_argument("--exit-code", type=int)
    parser.add_argument("--job-status")
    args = parser.parse_args(argv)
    env = os.environ
    if args.action == "prepare":
        prepare(env)
    elif args.action == "guard":
        guard_before_admission(env)
    elif args.action == "handoff":
        return 0 if handoff(env, args.uid, args.gid) else 2
    elif args.action == "inputs":
        make_inputs(env)
    elif args.action == "begin":
        begin(env)
    elif args.action == "exit":
        record_exit(env, args.exit_code)
    else:
        result = seal(env, args.job_status)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["source_content_and_live_module_verified"] else 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        # The original raw evidence remains private. No exception payload,
        # owner path, input envelope or recovery key is printed to CI logs.
        print("MLT G2 custody gate failed; no automatic retry.", file=sys.stderr)
        raise SystemExit(2)
