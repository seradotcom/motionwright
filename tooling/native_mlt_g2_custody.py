#!/usr/bin/env python3
"""Private custody around the single existing MLT verification producer.

The established scopes fetch admitted artifacts and seal selected originals.
A separate, closed Audio scope observes one explicitly admitted live method,
using the existing runtime installer, process primitives and private cipher.
No scope retries an uncertain operation; public output remains closed.
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
import tarfile
import urllib.request
import zlib

from ci_private_evidence import (
    CustodyRejected, HEX, SOURCE, MAX_FILE, MAX_TOTAL, MAX_FILES, MAX_JSON,
    CONTEXT_KEYS, MAGIC, SEAL_SCHEMA, require, sha, json_bytes, strict,
    regular, new_bytes, new_json, public_key, metadata_identity,
    gather_snapshot, encrypt_snapshot, decrypt_snapshot,
)
import native_mlt_g2_observation as g2
import native_mlt_runtime_admission as admission
import native_mlt_evidence_selection as selected
import native_evidence_projection as selected_core

DISPATCH_SCHEMA = "motionwright.native.mlt-g2-dispatch/1"
SOURCE_DISPATCH_SCHEMA = "motionwright.native.mlt-g2-dispatch/2"
SOURCE_MODE = "pinned-upstream-reconstruction"
FILM_SCOPE = "film180-g2"
FILM_DISPATCH_SCHEMA = "motionwright.native.film180-g2-dispatch/1"
FILM_INPUT_SCHEMA = "motionwright.native.film180-g2-inputs/1"
FILM_FINAL_SCHEMA = "motionwright.native.film180-g2-custody/1"
FILM_TERMINAL_SCOPE = "original_and_edited_film180_native_commands"
RUNTIME_INPUT_KEYS = g2.RUNTIME_INPUT_KEYS
UPSTREAM_URL = "https://archive.ubuntu.com/ubuntu/pool/universe/m/mlt/mlt_7.22.0.orig.tar.gz"
UPSTREAM_ARCHIVE_SHA256 = "7cf4d9573a061d76902c6e12a2f09f5f461e7c697635233df82a63a3fe4d6da6"
UPSTREAM_MEMBER = "mlt-7.22.0/src/modules/avformat/consumer_avformat.c"
REPAIR_GENERATOR = "tooling/mlt-audio-diagnostic/repair_planar_source.py"
REPAIR_GENERATOR_SHA256 = "76c4c00139483c44f47c56c297392f20764568fbbed9f15be7e34235c4858084"
MAX_UPSTREAM_ARCHIVE = 16 * 1024 * 1024
MAX_UPSTREAM_TAR = 64 * 1024 * 1024
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
            and env.get("MW_MLT_SCOPE") in {"repaired-g2", FILM_SCOPE}, "closed_manual_selection_required")
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


def validate_dispatch(raw, current, *, scenario="repaired-g2"):
    require(scenario in {"repaired-g2", FILM_SCOPE}, "dispatch_scenario")
    schemas = (FILM_DISPATCH_SCHEMA,) if scenario == FILM_SCOPE else (DISPATCH_SCHEMA, SOURCE_DISPATCH_SCHEMA)
    value = strict(raw)
    require(set(value) == {"schema", "motionwright_sha", "semwright_sha",
                           "official_runtime", "compiler", "recipient"}
            and value["schema"] in schemas
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
    source_reconstruction = value["schema"] in (SOURCE_DISPATCH_SCHEMA, FILM_DISPATCH_SCHEMA)
    keys = {"artifact", "members", "source_mode"} if source_reconstruction else {"artifact", "members"}
    require(type(compiler) is dict and set(compiler) == keys, "compiler_contract")
    if source_reconstruction:
        require(compiler["source_mode"] == SOURCE_MODE, "compiler_source_mode")
    artifact_descriptor(compiler["artifact"])
    members = {"compiled_module", "compiler_receipt_path"}
    if not source_reconstruction:
        members |= {"original_c_source", "repaired_c_source"}
    require(type(compiler["members"]) is dict and set(compiler["members"]) == members,
            "compiler_member_contract")
    selected = [safe_member(p) for p in compiler["members"].values()]
    require(len(set(selected)) == len(selected), "duplicate_selected_compiler_member")
    public_key(value["recipient"])
    return value


def source_guard(repo, current, *, scenario="repaired-g2"):
    require(scenario in {"repaired-g2", FILM_SCOPE}, "source_scenario")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull,
               GIT_CONFIG_GLOBAL=os.devnull, GIT_GRAFT_FILE=os.devnull,
               GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    def git(*argv):
        return subprocess.check_output(["/usr/bin/git", "--no-replace-objects", "-C", str(repo),
                                        "-c", "core.fsmonitor=false", *argv], env=env, timeout=20)
    require(git("rev-parse", "HEAD").decode().strip() == current["source_sha"], "source_HEAD_changed")
    require(not git("diff", "--name-only", "HEAD", "--"), "tracked_source_changed")
    selected_paths = (selected.FILM_SOURCE_PATHS if scenario == FILM_SCOPE
                      else selected.SOURCE_PATHS)
    result = {}
    for path in selected_paths:
        raw = git("show", current["source_sha"] + ":" + path)
        require(regular(repo / path)[0] == raw, "source_blob_changed")
        result[path] = sha(raw)
    lock = strict(regular(repo / "SOURCE_LOCK.json", MAX_JSON)[0])
    require(lock["dependencies"]["semwright"]["revision"] == current["semwright_sha"], "source_lock_changed")
    # The same anchored physical-file versus committed-blob check protects the
    # collector before admission/native effects and again during /2 sealing.
    selected_core.verify_source_bindings(repo, current["source_sha"],
        [{"path": path, "sha256": digest} for path, digest in sorted(result.items())])
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



class NoUpstreamRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CustodyRejected("upstream_redirect_refused")


def retrieve_upstream(directory):
    """Retain one bounded response; no retry or fallback after a partial transfer."""
    new_json(directory / "upstream-download.intent.json", {
        "url": UPSTREAM_URL, "sha256": UPSTREAM_ARCHIVE_SHA256,
        "automatic_retry": False, "redirects_allowed": False,
        "origin": "RETRIEVED_PINNED_UPSTREAM", "compiler_artifact_member": False,
    })
    request = urllib.request.Request(UPSTREAM_URL, headers={"Accept-Encoding": "identity"})
    opener = urllib.request.build_opener(NoUpstreamRedirect())
    output_path = directory / "upstream-source.tar.gz"
    try:
        with output_path.open("xb") as output:
            with opener.open(request, timeout=30) as response:
                headers = dict(response.headers.items())
                require(len(json_bytes(headers)) <= MAX_JSON, "upstream_headers_budget")
                new_json(directory / "upstream-download.response.json", {
                    "url": response.geturl(), "status": response.status, "headers": headers,
                })
                require(response.status == 200 and response.geturl() == UPSTREAM_URL,
                        "upstream_response_identity")
                require(response.headers.get("Content-Encoding", "identity") == "identity",
                        "upstream_content_encoding")
                total = 0
                while True:
                    chunk = response.read(min(64 * 1024, MAX_UPSTREAM_ARCHIVE - total + 1))
                    if not chunk:
                        break
                    output.write(chunk)
                    total += len(chunk)
                    require(total <= MAX_UPSTREAM_ARCHIVE, "upstream_download_budget")
                output.flush()
                os.fsync(output.fileno())
        raw = regular(output_path, MAX_UPSTREAM_ARCHIVE)[0]
        require(raw and sha(raw) == UPSTREAM_ARCHIVE_SHA256, "upstream_archive_digest")
    except BaseException as error:
        new_json(directory / "upstream-download.terminal.json", {
            "status": "ERROR_OR_UNKNOWN", "failure_class": type(error).__name__,
            "automatic_retry": False, "retained_partial_response": output_path.exists(),
        })
        raise
    new_json(directory / "upstream-download.terminal.json", {
        "status": "RECEIVED_PINNED_BYTES", "sha256": sha(raw), "bytes": len(raw),
        "automatic_retry": False, "compiler_artifact_member": False,
    })
    return raw


def original_from_upstream(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_UPSTREAM_ARCHIVE
            and sha(raw) == UPSTREAM_ARCHIVE_SHA256, "upstream_archive_digest")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    expanded = decoder.decompress(raw, MAX_UPSTREAM_TAR + 1)
    require(len(expanded) <= MAX_UPSTREAM_TAR and decoder.eof
            and not decoder.unconsumed_tail and not decoder.unused_data,
            "upstream_single_gzip_stream_budget")
    with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
        entries = []
        seen = set()
        total = 0
        selected = None
        for member in archive:
            name = member.name.rstrip("/") if member.isdir() else member.name
            safe_member(name)
            require(name not in seen, "upstream_duplicate_member")
            seen.add(name)
            entries.append(member)
            total += member.size
            require(len(entries) <= MAX_FILES and total <= MAX_UPSTREAM_TAR,
                    "upstream_tar_budget")
            if name == UPSTREAM_MEMBER:
                require(member.isfile() and member.type in (tarfile.REGTYPE, tarfile.AREGTYPE)
                        and member.size == 96690, "upstream_selected_regular_member")
                selected = member
        require(selected is not None, "upstream_selected_member_missing")
        stream = archive.extractfile(selected)
        require(stream is not None, "upstream_member_unreadable")
        with stream:
            original = stream.read(96691)
    require(len(original) == 96690 and sha(original) == admission.ORIGINAL_SHA,
            "upstream_consumer_digest")
    return original


def reconstruct_sources(raw, repo, directory):
    original = original_from_upstream(raw)
    generator_path = repo / REPAIR_GENERATOR
    generator = regular(generator_path, 256 * 1024)[0]
    require(sha(generator) == REPAIR_GENERATOR_SHA256, "source_generator_digest")
    # Execute only the verified byte snapshot of the existing source generator.
    namespace = {"__name__": "motionwright_pinned_planar_source",
                 "__file__": str(generator_path)}
    exec(compile(generator, str(generator_path), "exec"), namespace)
    repaired, _ = namespace["repair_exact_original"](original)
    require(type(repaired) is bytes and len(repaired) == 99266
            and sha(repaired) == admission.REPAIRED_SHA, "reconstructed_source_digest")
    require(regular(generator_path, 256 * 1024)[0] == generator,
            "source_generator_changed")
    new_bytes(directory / "original_c_source", original)
    new_bytes(directory / "repaired_c_source", repaired)
    new_json(directory / "source-reconstruction.json", {
        "schema": "motionwright.native.mlt-source-reconstruction/1",
        "upstream_url": UPSTREAM_URL, "upstream_archive_sha256": sha(raw),
        "upstream_member": UPSTREAM_MEMBER,
        "original_c_source": {"sha256": sha(original), "bytes": len(original),
                              "origin": "RETRIEVED_PINNED_UPSTREAM"},
        "repaired_c_source": {"sha256": sha(repaired), "bytes": len(repaired),
                              "origin": "DETERMINISTIC_SOURCE_RECONSTRUCTION"},
        "generator_path": REPAIR_GENERATOR, "generator_sha256": sha(generator),
        "source_files_selected_from_compiler_artifact": False,
        "compiler_artifact_source_inventory": "NOT_INSPECTED_BY_RECONSTRUCTION",
        "compiled_binary_rebuilt": False,
        "module_installed": False, "native_execution": "NOT_RUN",
    })


def intake_compiler(dispatch, repo, directory):
    raw = download_artifact(dispatch["compiler"]["artifact"], "compiler", directory)
    pins = {"original_c_source": admission.ORIGINAL_SHA, "repaired_c_source": admission.REPAIRED_SHA,
            "compiled_module": g2.REPAIRED_MODULE_SHA256, "compiler_receipt_path": g2.COMPILER_RECEIPT_SHA256}
    for name, member in dispatch["compiler"]["members"].items():
        data = zip_member(raw, member, pins[name], admission.MAX_ELF_BYTES)
        new_bytes(directory / name, data)
    if dispatch["compiler"].get("source_mode") == SOURCE_MODE:
        upstream = retrieve_upstream(directory)
        reconstruct_sources(upstream, repo, directory)
    admission.source_compiler_closure(
        directory / "original_c_source", directory / "repaired_c_source",
        directory / "compiled_module", g2.REPAIRED_MODULE_SHA256,
        directory / "compiler_receipt_path", g2.COMPILER_RECEIPT_SHA256)


def prepare(env):
    require(sys.platform == "linux" and os.geteuid() != 0, "prepare_requires_normal_Linux_runner")
    current = context(env)
    dispatch = validate_dispatch(env.get("MW_MLT_G2_ADMISSION", "").encode(), current,
                                 scenario=env.get("MW_MLT_SCOPE", "repaired-g2"))
    control, installed, session = names(env)
    require(not installed.exists() and not session.exists(), "original_effect_directory_preexists")
    control.mkdir(mode=0o700, exist_ok=False)
    for name in ("records", "intake", "handoff", "logs", "public"):
        (control / name).mkdir(mode=0o700)
    new_json(control / "records/dispatch.json", dispatch)
    new_json(control / "records/context.json", current)
    new_json(control / "records/source-before.json", source_guard(Path(env["GITHUB_WORKSPACE"]), current,
                 scenario=env.get("MW_MLT_SCOPE", "repaired-g2")))
    if env.get("MW_MLT_SCOPE", "repaired-g2") != FILM_SCOPE:
        original = {"artifact_id": 12109323512, "run_id": 39054798740, "run_attempt": 1,
                    "source_sha": g2.REFERENCE_PR_HEAD, "zip_sha256": g2.REFERENCE_ZIP_SHA256}
        archive = download_artifact(original, "reference", control / "intake")
        g2.reference_source(archive)
        new_bytes(control / "intake/original-artifact.zip", archive)
    else:
        new_json(control / "records/film-source-intent.json", {
            "schema": "motionwright.native.film180-source-intent/1",
            "origin": "FIRST_PARTY_CANONICAL_SIX_SECOND_SOURCE_PENDING",
            "frame_count": 180, "frame_rate": 30, "pcm_frames": 288000,
            "historical_reference_WAV_reused": False, "native_effect_started": False,
        })
    official = dispatch["official_runtime"]
    raw = download_artifact(official["artifact"], "official", control / "intake")
    receipt = zip_member(raw, "distribution/official-runtime-receipt.json",
                         official["receipt_sha256"], MAX_JSON)
    proof = strict(receipt)
    g2.verify_official_runtime_bindings(
        proof, official["known_original_module_sha256"], official["known_melt_sha256"])
    new_bytes(control / "intake/official-runtime-receipt.json", receipt)
    intake_compiler(dispatch, Path(env["GITHUB_WORKSPACE"]), control / "intake")
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
    dispatch = validate_dispatch(regular(control / "records/dispatch.json", MAX_JSON)[0], context(env),
                                 scenario=env.get("MW_MLT_SCOPE", "repaired-g2"))
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
    dispatch = validate_dispatch(regular(control / "records/dispatch.json", MAX_JSON)[0], current,
                                 scenario=env.get("MW_MLT_SCOPE", "repaired-g2"))
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
    expected_keys = g2.INPUT_KEYS
    if env.get("MW_MLT_SCOPE", "repaired-g2") == FILM_SCOPE:
        data["schema"] = FILM_INPUT_SCHEMA
        del data["original_artifact_zip"]
        expected_keys = RUNTIME_INPUT_KEYS | {"schema", "private_evidence_dir"}
    require(set(data) == expected_keys, "producer_input_contract_changed")
    new_json(control / "records/g2-inputs.json", data)


def guard_before_admission(env):
    require(sys.platform == "linux" and os.geteuid() != 0, "guard_requires_normal_runner")
    control, _, _ = names(env)
    current = context(env)
    observed = source_guard(Path(env["GITHUB_WORKSPACE"]), current,
                 scenario=env.get("MW_MLT_SCOPE", "repaired-g2"))
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
    observed = source_guard(Path(env["GITHUB_WORKSPACE"]), context(env),
                            scenario=env.get("MW_MLT_SCOPE", "repaired-g2"))
    require(strict(regular(control / "records/source-before.json", MAX_JSON)[0]) == observed,
            "source_changed_before_producer")
    new_json(control / "records/source-before-producer.json", observed)
    command = ("tooling/native_film180_original_g2.py --g2-inputs"
               if env.get("MW_MLT_SCOPE", "repaired-g2") == FILM_SCOPE else
               "tooling/native_mlt_sequence_e2e.py --g2-inputs")
    new_json(control / "records/producer-started.json",
             {"context": context(env), "command": command,
              "automatic_retry": False, "producer_uid": os.getuid()})


def record_exit(env, code):
    require(type(code) is int and 0 <= code <= 255, "producer_exit_invalid")
    control, _, _ = names(env)
    require((control / "records/producer-started.json").is_file(), "producer_start_absent")
    new_json(control / "records/producer-exit.json", {"context": context(env), "exit_code": code,
                                                   "native_operation_retried": False})


def projection(current, final, exit_code, job_status, encrypted, recipient, *,
               producer_started=False, source_unchanged=False, selected_originals_complete=True,
               scenario="repaired-g2"):
    require(scenario in {"repaired-g2", FILM_SCOPE}, "projection_scenario")
    film = scenario == FILM_SCOPE
    require(job_status in {"success", "failure", "cancelled"} and
            (exit_code is None or type(exit_code) is int and 0 <= exit_code <= 255),
            "public_terminal_inputs")
    terminal = "NOT_STARTED"
    verified = False
    if final is not None:
        require(final.get("schema") == (FILM_FINAL_SCHEMA if film else "motionwright.native.mlt-g2-custody/1")
                and final.get("terminal_scope") == (FILM_TERMINAL_SCOPE if film else g2.TERMINAL_SCOPE)
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
    result = {"schema": "motionwright.native.film180-g2-public/1" if film else PUBLIC_SCHEMA,
              "context": current,
              "scope": ("film180-original-and-edited-content-and-live-module-only" if film else
                        "source-content-and-live-module-only"),
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



FFV1_PREFLIGHT_ADMISSION = "records/ffv1-diagnostic-admission.json"
FFV1_PREFLIGHT_FREEZE = "records/ffv1-diagnostic-freeze.json"
FFV1_PREFLIGHT_SCHEMA = "motionwright.native.ffv1-preflight-freeze/1"
FFV1_PREFLIGHT_METADATA_MAX = 65536


def checked_owner_workspace(env, current, session):
    """The same declared owner identity used by the existing native seal."""
    owner=strict(regular(session/"owner-workspace.json",MAX_JSON)[0])
    path=Path(owner["path"])
    info=path.lstat()
    require(owner["schema"]=="motionwright.native.mlt-g2-owner-workspace/1"
            and owner["motionwright_sha"]==current["source_sha"]
            and owner["semwright_sha"]==current["semwright_sha"]
            and owner["run_id"]==current["run_id"] and owner["run_attempt"]=="1"
            and path.parent==runner_root(env)
            and path.name.startswith("motionwright-native-av-g2-")
            and path.resolve(strict=True)==path
            and stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode)==0o700
            and (info.st_dev,info.st_ino,info.st_uid,info.st_gid)==
                (owner["device"],owner["inode"],owner["uid"],owner["gid"])
            and info.st_uid==os.getuid(),"owner_workspace_custody_identity")
    return path


def _ffv1_preflight_root(env, current):
    root=runner_root(env)/("mw-mlt-preflight-custody-"+current["run_id"]+"-"+current["run_attempt"])
    for path in (root,root/"records"):
        info=path.lstat()
        require(path.resolve(strict=True)==path
                and not path.is_symlink() and not any(item.is_symlink() for item in path.parents)
                and stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode)==0o700
                and info.st_uid==os.geteuid(),"ffv1-preflight-private-root")
    return root


def _ffv1_exact_absence(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    return False


def _ffv1_preflight_inputs(env, current, root):
    raw_initial=regular(root/"records/initial.json",MAX_JSON)[0]
    raw_before=regular(root/"records/source-before.json",MAX_JSON)[0]
    raw_dispatch=regular(root/"records/dispatch.original.json",MAX_JSON)[0]
    initial=strict(raw_initial);before=strict(raw_before)
    require(initial.get("schema")=="motionwright.native.mlt-preflight-initial/1"
            and initial.get("context")==current and initial.get("github_job")==env.get("GITHUB_JOB")
            and initial.get("scope")=="repaired-g2"
            and initial.get("effect_namespaces_initially_absent") is True
            and initial.get("dispatch_input_sha256")==sha(raw_dispatch)
            and initial.get("admission_granted") is False
            and initial.get("native_effect_started") is False
            and initial.get("automatic_retry") is False,"ffv1-preflight-original-input")
    dispatch=validate_dispatch(raw_dispatch,current,scenario="repaired-g2")
    public_key(dispatch["recipient"])
    physical=selected_core.project(before.get("physical"),selected.policies("repaired-g2")["source_binding"],
                                   exact=True)
    require(physical["context"]==current,"ffv1-preflight-source-context")
    bindings=[dict(path=path,sha256=digest) for path,digest in sorted(physical["files"].items())]
    return raw_before,physical,bindings


def checked_ffv1_freeze(value, *, current, expected_bindings=None):
    fields={"schema","context","scenario","state","admission_sha256","admission_bytes",
        "source_before_sha256","source_bindings_sha256","input_sha256",
        "diagnostic_contract_admitted","before_producer_return","before_preflight_seal",
        "native_originals_claimed","native_effects_absent_claimed","automatic_retry"}
    require(type(value) is dict and set(value)==fields
            and value["schema"]==FFV1_PREFLIGHT_SCHEMA and value["context"]==current
            and value["scenario"]=="repaired-g2","ffv1-preflight-freeze-fields")
    selected_core.project(current,selected.FOUR_CONTEXT,exact=True)
    require(type(value["state"]) is str and value["state"] in ("FROZEN","PLAN_ABSENT"),
            "ffv1-preflight-freeze-state")
    require(value["before_producer_return"] is True and value["before_preflight_seal"] is True
            and value["native_originals_claimed"] is False
            and value["native_effects_absent_claimed"] is False
            and value["automatic_retry"] is False,"ffv1-preflight-freeze-scope")
    for name in ("source_before_sha256","source_bindings_sha256"):
        selected_core.project(value[name],selected.SHA,exact=True)
    if expected_bindings is not None:
        require(value["source_bindings_sha256"]==sha(selected_core.canonical(
            sorted(expected_bindings,key=lambda row:row["path"]))),"ffv1-preflight-freeze-source")
    if value["state"]=="FROZEN":
        require(value["diagnostic_contract_admitted"] is True
                and type(value["admission_bytes"]) is int
                and 0<value["admission_bytes"]<=FFV1_PREFLIGHT_METADATA_MAX,
                "ffv1-preflight-freeze-admission")
        for name in ("admission_sha256","input_sha256"):
            selected_core.project(value[name],selected.SHA,exact=True)
    else:
        require(value["diagnostic_contract_admitted"] is False
                and value["admission_sha256"] is None and value["admission_bytes"] is None
                and value["input_sha256"] is None,"ffv1-preflight-absent-without-invented-contract")
    return value


def freeze_ffv1_preflight_expectation(env, evidence_dir):
    """Freeze metadata once before returning the existing G2 producer."""
    current=context(env)
    require(env.get("MW_MLT_SCOPE")=="repaired-g2","ffv1-freeze-scenario")
    control,_,session=names(env)
    require(Path(evidence_dir)==session,"ffv1-freeze-same-native-session")
    root=_ffv1_preflight_root(env,current)
    require(all(_ffv1_exact_absence(path) for path in (
        root/"records/seal-intent.json",root/"public/sealed-source-originals.bin",
        root/"public/source-public-evidence.json",root/FFV1_PREFLIGHT_ADMISSION,
        root/FFV1_PREFLIGHT_FREEZE)),"ffv1-freeze-must-precede-existing-preflight-seal")
    raw_before,physical,bindings=_ffv1_preflight_inputs(env,current,root)
    native_before=strict(regular(control/"records/source-before.json",MAX_JSON)[0])
    require(native_before==physical,"ffv1-freeze-preflight-native-source-binding")
    owner=checked_owner_workspace(env,current,session)
    roots=dict(source=Path(env["GITHUB_WORKSPACE"]),control=control,observation=session,owner=owner)
    admitted=None
    try:
        admitted=selected.read_ffv1_admission(roots=roots,current=current,source_bindings=bindings)
    except selected.Ffv1StagePlanAbsent:
        pass
    packed=None if admitted is None else selected_core.canonical(admitted)
    if packed is not None:
        require(0<len(packed)<=FFV1_PREFLIGHT_METADATA_MAX,"ffv1-preflight-admission-size")
        # Existing writer: exclusive, fsynced and read back. Only derived metadata
        # is placed in source custody; raw seed/argv/plan remain native.
        new_bytes(root/FFV1_PREFLIGHT_ADMISSION,packed)
    status=dict(schema=FFV1_PREFLIGHT_SCHEMA,context=current,scenario="repaired-g2",
        state="PLAN_ABSENT" if admitted is None else "FROZEN",
        admission_sha256=None if packed is None else sha(packed),
        admission_bytes=None if packed is None else len(packed),
        source_before_sha256=sha(raw_before),
        source_bindings_sha256=sha(selected_core.canonical(bindings)),
        input_sha256=None if admitted is None else admitted["stage_plan"]["identity"]["input_sha256"],
        diagnostic_contract_admitted=admitted is not None,before_producer_return=True,
        before_preflight_seal=True,native_originals_claimed=False,
        native_effects_absent_claimed=False,automatic_retry=False)
    checked_ffv1_freeze(status,current=current,expected_bindings=bindings)
    require(_ffv1_exact_absence(root/"records/seal-intent.json"),"ffv1-preflight-seal-started-during-freeze")
    new_bytes(root/FFV1_PREFLIGHT_FREEZE,selected_core.canonical(status))
    return status


def consume_frozen_ffv1_expectation(env, *, roots, current, source_bindings):
    """Consume the earlier freeze; never create authority while selecting a TAR."""
    root=_ffv1_preflight_root(env,current)
    raw_before,physical,bindings=_ffv1_preflight_inputs(env,current,root)
    require(bindings==sorted(source_bindings,key=lambda row:row["path"]),
            "ffv1-frozen-source-binding")
    raw_status,status_observation=regular(root/FFV1_PREFLIGHT_FREEZE,FFV1_PREFLIGHT_METADATA_MAX)
    status=checked_ffv1_freeze(strict(raw_status),current=current,expected_bindings=bindings)
    require(status["source_before_sha256"]==sha(raw_before),"ffv1-frozen-source-original")
    admitted=None
    if status["state"]=="FROZEN":
        raw,observation=regular(root/FFV1_PREFLIGHT_ADMISSION,FFV1_PREFLIGHT_METADATA_MAX)
        require((sha(raw),len(raw))==(status["admission_sha256"],status["admission_bytes"]),
                "ffv1-frozen-admission-bytes")
        admitted=selected.checked_ffv1_admission(selected_core.strict(raw),current=current,
                                                 source_bindings=source_bindings)
        require(admitted["stage_plan"]["identity"]["input_sha256"]==status["input_sha256"],
                "ffv1-frozen-input-identity")
        # Re-read only the fixed originals to compare against already frozen C.
        # The observed object cannot replace or extend the expected contract.
        observed=selected.read_ffv1_admission(roots=roots,current=current,source_bindings=bindings)
        require(observed==admitted,"ffv1-originals-differ-from-frozen-admission")
        require(metadata_identity((root/FFV1_PREFLIGHT_ADMISSION).lstat())==
                tuple(observation["namespace_after"][key] for key in (
                    "device","inode","mode","uid","gid","size","mtime_ns","ctime_ns")),
                "ffv1-frozen-admission-changed")
    else:
        require(_ffv1_exact_absence(root/FFV1_PREFLIGHT_ADMISSION),"ffv1-absent-freeze-has-contract")
        try:
            selected.read_ffv1_admission(roots=roots,current=current,source_bindings=bindings)
        except selected.Ffv1StagePlanAbsent:
            pass
        else:
            require(False,"ffv1-plan-present-after-absent-freeze")
    require(metadata_identity((root/FFV1_PREFLIGHT_FREEZE).lstat())==
            tuple(status_observation["namespace_after"][key] for key in (
                "device","inode","mode","uid","gid","size","mtime_ns","ctime_ns")),
            "ffv1-frozen-status-changed")
    return admitted,status["state"]=="PLAN_ABSENT"


FFV1_PREFLIGHT_PUBLIC_KEYS = {
    "schema","context","github_job","scope","status","selected_source_bindings_unchanged",
    "full_checkout_input_postcheck_at_final_seal","captured_source_originals_complete",
    "native_terminal","native_dispatch_valid","sealed_originals_sha256","sealed_originals_bytes",
    "recipient_spki_sha256","native_originals_claimed","source_test_pass_claimed",
    "admission_granted","native_operation_retried","physical_AV_master_acceptance",
    "human_acceptance","release_authorized",
}


def _ffv1_source_tar_framing(raw):
    """The existing flat preflight writer emits only complete regular headers."""
    require(type(raw) is bytes and len(raw)%512==0,"ffv1-source-tar-framing")
    offset,count=0,0
    while offset+512<=len(raw):
        header=raw[offset:offset+512]
        if header==b"\0"*512:
            require(len(raw)-offset>=1024 and not any(raw[offset:]),"ffv1-source-tar-footer")
            return count
        try:
            info=tarfile.TarInfo.frombuf(header,"utf-8","strict")
        except (tarfile.HeaderError,UnicodeError,ValueError) as error:
            raise CustodyRejected("ffv1-source-tar-header") from error
        require(info.type==tarfile.REGTYPE and not info.linkname
                and 0<=info.size<=MAX_FILE,"ffv1-source-tar-physical-kind")
        count+=1
        require(count<=MAX_FILES+1,"ffv1-source-tar-count")
        end=offset+512+info.size
        following=offset+512+((info.size+511)//512)*512
        require(following<=len(raw) and not any(raw[end:following]),"ffv1-source-tar-padding")
        offset=following
    require(False,"ffv1-source-tar-footer")


def recover_ffv1_preflight_admission(envelope, private_key, public, *, current):
    """Recover C from the independent existing source capsule, entirely in memory."""
    require(type(public) is dict and set(public)==FFV1_PREFLIGHT_PUBLIC_KEYS
            and public["schema"]=="motionwright.native.mlt-preflight-public/1"
            and public["context"]==current and public["scope"]=="source-preflight-originals-only",
            "ffv1-source-public-contract")
    selected_core.project(current,selected.FOUR_CONTEXT,exact=True)
    require(type(public["github_job"]) is str and 0<len(public["github_job"])<=128,
            "ffv1-source-public-job")
    require(type(public["status"]) is str and public["status"] in (
            "SOURCE_ORIGINALS_SEALED","PARTIAL_SOURCE_ORIGINALS_SEALED"),
            "ffv1-source-public-status")
    require(type(public["captured_source_originals_complete"]) is bool
            and public["captured_source_originals_complete"] is
                (public["status"]=="SOURCE_ORIGINALS_SEALED"),
            "ffv1-source-public-completeness")
    require(public["selected_source_bindings_unchanged"] is True
            and public["native_dispatch_valid"] is True
            and type(public["native_terminal"]) is str
            and public["native_terminal"] in ("NOT_STARTED","ERROR_OR_UNKNOWN"),
            "ffv1-source-public-binding")
    require(all(public[name] is False for name in (
        "full_checkout_input_postcheck_at_final_seal","native_originals_claimed",
        "source_test_pass_claimed","admission_granted","native_operation_retried",
        "physical_AV_master_acceptance","human_acceptance","release_authorized")),
        "ffv1-source-public-scope")
    require(type(envelope) is bytes and type(public["sealed_originals_bytes"]) is int
            and (sha(envelope),len(envelope))==(public["sealed_originals_sha256"],
                                              public["sealed_originals_bytes"]),
            "ffv1-source-ciphertext-identity")
    # Existing authenticated encryption checks key, complete ciphertext, canonical
    # header, context, plaintext size and hash. No replacement crypto or key lookup.
    plaintext,authenticated=decrypt_snapshot(envelope,private_key)
    require(authenticated==current,"ffv1-source-authenticated-context")
    length=int.from_bytes(envelope[len(MAGIC):len(MAGIC)+4],"big")
    header=strict(envelope[len(MAGIC)+4:len(MAGIC)+4+length])
    require(header["recipient_spki_sha256"]==public["recipient_spki_sha256"],
            "ffv1-source-recipient-identity")
    wanted={FFV1_PREFLIGHT_ADMISSION:FFV1_PREFLIGHT_METADATA_MAX,
            FFV1_PREFLIGHT_FREEZE:FFV1_PREFLIGHT_METADATA_MAX,
            "records/source-before.json":MAX_JSON,"snapshot-manifest.json":MAX_JSON}
    originals={}
    physical_members=_ffv1_source_tar_framing(plaintext)
    with tarfile.open(fileobj=io.BytesIO(plaintext),mode="r:") as archive:
        infos=archive.getmembers()
        require(not archive.pax_headers and len(infos)==physical_members
                and 1<=len(infos)<=MAX_FILES+1,
                "ffv1-source-snapshot-budget")
        names=[info.name for info in infos]
        require(len(names)==len(set(names)) and set(wanted)<=set(names),
                "ffv1-source-originals-missing-or-duplicate")
        for info in infos:
            # These are the existing flat preflight roots, never native owner
            # roots. No member is extracted or treated as a filesystem path.
            parts=info.name.split("/")
            require(type(info.name) is str and 0<len(info.name)<=4096
                    and "\\" not in info.name and all(part not in ("",".","..") for part in parts)
                    and all(32<=ord(char)<127 for char in info.name)
                    and (info.name=="snapshot-manifest.json"
                         or len(parts)==2 and parts[0] in ("records","common","film","workflow"))
                    and info.type==tarfile.REGTYPE and not info.linkname and not info.sparse
                    and not info.pax_headers and 0<=info.size<=MAX_FILE,
                    "ffv1-source-snapshot-member")
            if info.name in wanted:
                maximum=wanted[info.name]
                require(0<info.size<=maximum,"ffv1-source-original-size")
                stream=archive.extractfile(info)
                require(stream is not None,"ffv1-source-original-stream")
                raw=stream.read(maximum+1)
                require(len(raw)==info.size,"ffv1-source-original-truncated")
                originals[info.name]=raw
    snapshot=strict(originals["snapshot-manifest.json"])
    require(set(snapshot)=={"schema","scope","roots","selected_originals_complete",
                             "complete_owner_state_claimed"}
            and snapshot["schema"]=="motionwright.ci.private-snapshot/1"
            and snapshot["scope"]=="selected-original-files-at-read-time"
            and snapshot["complete_owner_state_claimed"] is False
            and type(snapshot["selected_originals_complete"]) is bool
            and type(snapshot["roots"]) is dict
            and set(snapshot["roots"])=={"records","common","film","workflow"},
            "ffv1-source-snapshot-contract")
    require(not public["captured_source_originals_complete"]
            or snapshot["selected_originals_complete"] is True,
            "ffv1-source-snapshot-public-completeness")
    records=snapshot["roots"]["records"]
    require(type(records) is dict and type(records.get("status")) is str
            and records["status"] in ("CAPTURED","PARTIAL")
            and type(records.get("files")) is list and len(records["files"])<=MAX_FILES,
            "ffv1-source-records-originals")
    observation_keys={"path","sha256","size","before","after","namespace_after"}
    mark_keys={"device","inode","size","mode","uid","gid","mtime_ns","ctime_ns"}
    for path,raw in originals.items():
        if path=="snapshot-manifest.json":
            continue
        rows=[row for row in records["files"]
              if type(row) is dict and row.get("archive_path")==path]
        require(len(rows)==1 and set(rows[0])=={"archive_path","observation"},
                "ffv1-source-original-observation")
        observation=rows[0]["observation"]
        require(type(observation) is dict and set(observation)==observation_keys
                and type(observation["path"]) is str and 0<len(observation["path"])<=4096
                and type(observation["size"]) is int
                and (observation["sha256"],observation["size"])==(sha(raw),len(raw)),
                "ffv1-source-original-byte-binding")
        before=observation["before"]
        require(type(before) is dict and set(before)==mark_keys
                and all(type(value) is int for value in before.values())
                and before["size"]==len(raw) and stat.S_ISREG(before["mode"])
                and before==observation["after"]==observation["namespace_after"],
                "ffv1-source-original-stability")
    status=checked_ffv1_freeze(strict(originals[FFV1_PREFLIGHT_FREEZE]),current=current)
    require(status["state"]=="FROZEN","ffv1-source-has-no-frozen-diagnostic-admission")
    raw=originals[FFV1_PREFLIGHT_ADMISSION]
    require((sha(raw),len(raw))==(status["admission_sha256"],status["admission_bytes"]),
            "ffv1-source-frozen-admission-bytes")
    before_raw=originals["records/source-before.json"]
    require(sha(before_raw)==status["source_before_sha256"],"ffv1-source-before-original")
    before=strict(before_raw)
    physical=selected_core.project(before.get("physical"),
        selected.policies("repaired-g2")["source_binding"],exact=True)
    require(physical["context"]==current,"ffv1-source-before-context")
    bindings=[dict(path=path,sha256=digest) for path,digest in sorted(physical["files"].items())]
    checked_ffv1_freeze(status,current=current,expected_bindings=bindings)
    admitted=selected.checked_ffv1_admission(selected_core.strict(raw),current=current,
                                             source_bindings=bindings)
    require(admitted["stage_plan"]["identity"]["input_sha256"]==status["input_sha256"],
            "ffv1-source-input-identity")
    return admitted


def recover_ffv1_preflight_artifact(source_artifact_zip, private_key, *, current,
                                      expected_source_artifact):
    """Bind the existing source artifact to an independently admitted descriptor.

    The descriptor is the existing download_artifact/ARTIFACT_KEYS contract.
    This offline reader checks its exact ZIP hash and current run/source; it
    never obtains an expected descriptor from an archive or public receipt.
    """
    selected_core.project(current,selected.FOUR_CONTEXT,exact=True)
    expected=artifact_descriptor(strict(json_bytes(expected_source_artifact)))
    require(expected["source_sha"]==current["source_sha"]
            and str(expected["run_id"])==current["run_id"]
            and str(expected["run_attempt"])==current["run_attempt"],
            "ffv1-source-artifact-current-run")
    require(type(source_artifact_zip) is bytes and 0<len(source_artifact_zip)<=MAX_FILE
            and sha(source_artifact_zip)==expected["zip_sha256"],
            "ffv1-source-artifact-original-zip")
    # The existing workflow uploads exactly these two flat originals. No
    # replacement ZIP/container is written by this reader.
    with zipfile.ZipFile(io.BytesIO(source_artifact_zip)) as archive:
        entries=archive.infolist()
        require(len(entries)==2 and {entry.filename for entry in entries}==
                {"sealed-source-originals.bin","source-public-evidence.json"},
                "ffv1-source-artifact-member-roster")
        for entry in entries:
            require(not entry.is_dir() and not entry.flag_bits&1
                    and stat.S_IFMT(entry.external_attr>>16) in (0,stat.S_IFREG)
                    and 0<entry.file_size<=(MAX_JSON if entry.filename.endswith(".json") else MAX_FILE),
                    "ffv1-source-artifact-member")
        require(archive.testzip() is None,"ffv1-source-artifact-crc")
        public_raw=archive.read("source-public-evidence.json")
    public=strict(public_raw)
    # Reuse the existing bounded original-member reader for the exact cipher.
    envelope=zip_member(source_artifact_zip,"sealed-source-originals.bin",
                        public.get("sealed_originals_sha256"),MAX_FILE)
    admitted=recover_ffv1_preflight_admission(envelope,private_key,public,current=current)
    return admitted,public,expected


def verify_ffv1_sealed_custody(source_artifact_zip, native_envelope, private_key,
                               native_public, *, current, expected_source_artifact,
                               require_complete=True):
    """Use the independently admitted source artifact and existing native capsule."""
    require(type(require_complete) is bool,"ffv1-offline-completeness-mode")
    require(type(native_public) is dict and native_public.get("context")==current,
            "ffv1-offline-native-context")
    admitted,source_public,source_artifact=recover_ffv1_preflight_artifact(
        source_artifact_zip,private_key,current=current,
        expected_source_artifact=expected_source_artifact)
    require(source_public["recipient_spki_sha256"]==native_public.get("recipient_spki_sha256"),
            "ffv1-offline-same-recipient")
    result=selected.verify_sealed_selection(native_envelope,private_key,native_public,
        expected_diagnostic_admission=admitted,require_complete=require_complete)
    result.update(expected_contract_recovered_from_independent_source_custody=True,
                  source_artifact_zip_matches_independently_admitted_descriptor=True,
                  source_artifact=source_artifact)
    return result


def selected_projection(current, final, exit_code, job_status, encrypted, recipient, manifest, *,
                        producer_started=False, source_unchanged=False, intake_verified=False,
                        scenario="repaired-g2", diagnostic_admission=None,
                        ffv1_stage_plan_absent=False, diagnostic_intake_verified=None):
    require(type(ffv1_stage_plan_absent) is bool,"ffv1-public-absence-flag")
    require(diagnostic_intake_verified is None or type(diagnostic_intake_verified) is bool,
            "ffv1-public-diagnostic-intake-flag")
    diagnostic_verified=(intake_verified if diagnostic_intake_verified is None
                         else diagnostic_intake_verified)
    if ffv1_stage_plan_absent:
        require(scenario=="repaired-g2" and diagnostic_admission is None
                and not intake_verified and not diagnostic_verified,"ffv1-public-absence-mode")
        selected.checked_ffv1_plan_absence(manifest,current=current)
    complete = manifest.get("required_layers_complete") is True
    value = projection(current, final, exit_code, job_status, encrypted, recipient,
        producer_started=producer_started, source_unchanged=source_unchanged,
        selected_originals_complete=complete and intake_verified, scenario=scenario)
    value.pop("selected_originals_complete")
    value.update(schema=("motionwright.native.film180-g2-public/2" if scenario == FILM_SCOPE
                         else "motionwright.native.mlt-g2-public/2"),
        custody_scope="explicit-source-bound-selected-layers/2",
        selected_layers_complete=complete, selected_intake_verified=intake_verified,
        selected_manifest_sha256=sha(selected_core.canonical(manifest)),
        full_original_json_claimed=False, atomic_sqlite_snapshot_claimed=False,
        upload_authorization="NOT_GRANTED_BY_COLLECTOR")
    if manifest["schema"]==selected_core.MANIFEST_SCHEMA_V3:
        require(scenario=="repaired-g2" and diagnostic_admission is not None,
                "ffv1-public-admission-required")
        admitted=selected.checked_ffv1_admission(diagnostic_admission,current=current,
                                                 source_bindings=manifest["source_bindings"])
        require(manifest["diagnostic_contract"]==admitted["capture_diagnostic_contract"],
                "ffv1-public-manifest-contract")
        value.update(schema="motionwright.native.mlt-g2-public/3",
            custody_scope="explicit-source-bound-selected-layers/3",
            selected_evidence_schema=selected_core.MANIFEST_SCHEMA_V3,
            selected_lost_scope=list(selected_core.LOST_SCOPE_V3),
            diagnostic_status="FFV1_DIAGNOSTICS_ADMITTED",
            diagnostic_admitted=True,diagnostic_plan_absence_verified=False,
            diagnostic_contract_sha256=sha(selected_core.canonical(admitted["capture_diagnostic_contract"])),
            diagnostic_admission_sha256=sha(selected_core.canonical(admitted)),
            diagnostic_intake_verified=diagnostic_verified,
            diagnostic_roster_complete=manifest["diagnostic_roster_complete"],
            raw_diagnostics_included=manifest["raw_diagnostics_included"],
            diagnostic_eof_claimed=False,diagnostic_history_complete_claimed=False,
            diagnostic_payloads_redacted=False)
    elif ffv1_stage_plan_absent:
        value.update(schema="motionwright.native.mlt-g2-public/3",
            selected_evidence_schema=selected_core.MANIFEST_SCHEMA,
            selected_lost_scope=list(selected_core.LOST_SCOPE),
            diagnostic_status="FFV1_DIAGNOSTIC_PLAN_ABSENT",
            diagnostic_admitted=False,diagnostic_plan_absence_verified=True,
            diagnostic_contract_sha256=None,diagnostic_admission_sha256=None,
            diagnostic_intake_verified=False,diagnostic_roster_complete=False,
            raw_diagnostics_included=False,diagnostic_eof_claimed=False,
            diagnostic_history_complete_claimed=False,diagnostic_payloads_redacted=False)
    else:
        require(diagnostic_admission is None,"ffv1-public-downgrade")
    selected.validate_public_evidence(value)
    return value

def seal(env, job_status):
    require(sys.platform == "linux" and os.geteuid() != 0, "seal_requires_normal_runner")
    control, _, session = names(env)
    current = context(env)
    dispatch = validate_dispatch(regular(control / "records/dispatch.json", MAX_JSON)[0], current,
                                 scenario=env.get("MW_MLT_SCOPE", "repaired-g2"))
    repo = Path(env["GITHUB_WORKSPACE"])
    source_unchanged, custody_valid = False, True
    def retain_error(name, error):
        new_json(control / ("records/" + name + ".json"),
                 {"error_type": type(error).__name__, "error": str(error), "no_retry": True})
    try:
        source_after = source_guard(repo, current, scenario=env.get("MW_MLT_SCOPE", "repaired-g2"))
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
        projection(current, final, exit_code, job_status, b"validation", dispatch["recipient"],
                   scenario=env.get("MW_MLT_SCOPE", "repaired-g2"))
    except Exception as error:
        custody_valid = False
        final = None
        retain_error("terminal-contract-error", error)
    roots = {"source": repo, "control": control}
    # These two exact source-gate receipts predate any native session. Preserve
    # missing required roles without probing or enumerating neighboring files.
    suffix = current["run_id"] + "-1"
    contract_roots = [("contracts", "mw-mlt-g2-contracts-" + suffix)]
    if env.get("MW_MLT_SCOPE", "repaired-g2") == FILM_SCOPE:
        contract_roots.append(("film-contracts", "mw-film180-contracts-" + suffix))
    for label, basename in contract_roots:
        path = runner_root(env) / basename
        if path.is_dir():
            roots[label] = path
    if session.is_dir():
        roots["observation"] = session
    owner_marker = session / "owner-workspace.json"
    try:
        if owner_marker.exists():
            roots["owner"]=checked_owner_workspace(env,current,session)
        elif started:
            custody_valid = False
            retain_error("owner-marker-absent", CustodyRejected("original_owner_tree_not_observed"))
    except Exception as error:
        custody_valid = False
        retain_error("owner-identity-error", error)
    scenario = env.get("MW_MLT_SCOPE", "repaired-g2")
    source_before = strict(regular(control / "records/source-before.json", MAX_JSON)[0])
    bindings = [{"path": path, "sha256": digest}
                for path, digest in sorted(source_before["files"].items())]
    diagnostic_admission=None
    ffv1_stage_plan_absent=False
    if scenario=="repaired-g2":
        diagnostic_admission,ffv1_stage_plan_absent=consume_frozen_ffv1_expectation(
            env,roots=roots,current=current,source_bindings=bindings)
    if scenario == FILM_SCOPE:
        from native_film180_evidence_selection import FilmSelection
        collector = FilmSelection(roots=roots, current=current, source_bindings=bindings)
    else:
        collector = selected.Selection(roots=roots, current=current, scenario=scenario,
            source_bindings=bindings,
            role_contract=selected.mlt_contract(seed_diagnostics=diagnostic_admission is not None),
            json_policies=selected.policies(scenario),diagnostic_admission=diagnostic_admission,
            ffv1_stage_plan_absent=ffv1_stage_plan_absent)
    raw, manifest = collector.capture()
    if ffv1_stage_plan_absent:
        selected.checked_ffv1_plan_absence(manifest,current=current,source_bindings=bindings)
    diagnostic_intake_verified=False
    if diagnostic_admission is not None and custody_valid and source_unchanged:
        try:
            selected.verify_decrypted_selection(raw,current=current,scenario=scenario,
                expected_diagnostic_admission=diagnostic_admission,require_complete=False)
            diagnostic_intake_verified=True
        except Exception:
            # The same captured bytes remain sealed; no second capture or retry.
            diagnostic_intake_verified=False
    intake_verified = False
    if (manifest["required_layers_complete"] and custody_valid and not ffv1_stage_plan_absent
            and (diagnostic_admission is None or diagnostic_intake_verified)):
        try:
            selected.verify_decrypted_selection(raw, current=current, scenario=scenario,
                expected_diagnostic_admission=diagnostic_admission)
            intake_verified = True
        except Exception:
            # Already captured originals remain sealed. An independent closure
            # failure cannot be repaired by a second capture or native replay.
            intake_verified = False
    encrypted = encrypt_snapshot(raw, dispatch["recipient"], current)
    public = selected_projection(current, final, exit_code, job_status, encrypted, dispatch["recipient"],
                        manifest, producer_started=started, source_unchanged=source_unchanged,
                        intake_verified=intake_verified, scenario=scenario,
                        diagnostic_admission=diagnostic_admission,
                        ffv1_stage_plan_absent=ffv1_stage_plan_absent,
                        diagnostic_intake_verified=diagnostic_intake_verified)
    output = control / "public"
    require(not any(output.iterdir()), "public_output_preexists")
    new_bytes(output / "sealed-originals.bin", encrypted)
    new_json(output / "public-evidence.json", public)
    require({p.name for p in output.iterdir()} == {"sealed-originals.bin", "public-evidence.json"},
            "public_output_extra_file")
    return public



# The additive source profile uses the existing process observer and Cargo
# producer barrier. It does not admit or invoke either physical MLT profile.
AUDIO_PHASE_SECONDS = {"source-g0": 900, "strict-clippy": 1800,
                       "normal-tools-build": 1800, "affected-test-build": 1800,
                       "enumeration": 300, "cases": 3000, "retention": 600}
AUDIO_TOTAL_SECONDS = sum(AUDIO_PHASE_SECONDS.values())


def audio_clock(started_ns, now_ns):
    require(type(started_ns) is int and type(now_ns) is int and 0 < started_ns <= now_ns,
            "audio-source-original-monotonic-clock")
    deadline = started_ns + AUDIO_TOTAL_SECONDS * 1_000_000_000
    require(now_ns < deadline, "audio-source-clock-already-expired")
    return {"started_ns": started_ns, "deadline_ns": deadline,
            "work_deadline_ns": deadline - AUDIO_PHASE_SECONDS["retention"] * 1_000_000_000}


def audio_environment(original, root):
    forbidden = [key for key, value in original.items() if value and (
        key in {"LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "PYTHONPATH", "RUSTFLAGS",
                "CARGO_ENCODED_RUSTFLAGS", "RUSTC", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER",
                "RUSTDOC", "RUSTFMT", "CARGO_BUILD_TARGET", "CARGO_BUILD_RUSTC"}
        or key.startswith(("CARGO_ALIAS_", "RUST_TEST_", "SEMWRIGHT_")))]
    require(not forbidden, "audio-source-inherited-execution-override:" + ",".join(sorted(forbidden)))
    result = {key: original[key] for key in ("PATH", "LANG", "LC_ALL", "SSL_CERT_FILE",
              "SSL_CERT_DIR", "SYSTEMROOT") if key in original}
    result.update(HOME=str(root / "home"), CARGO_HOME=str(root / "cargo-home"),
        RUSTUP_HOME=str(root / "rustup-home"), RUSTUP_TOOLCHAIN="1.98.1",
        CARGO_TARGET_DIR=str(root / "target"), TMP=str(root / "tmp"), TEMP=str(root / "tmp"),
        TMPDIR=str(root / "tmp"), CARGO_BUILD_JOBS="2", CARGO_INCREMENTAL="0",
        CARGO_PROFILE_DEV_DEBUG="0", CARGO_PROFILE_TEST_DEBUG="0", CARGO_TERM_COLOR="never",
        RUST_BACKTRACE="0", PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
        GIT_GRAFT_FILE=os.devnull, GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1",
        GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1")
    return result


def audio_resource_preflight(root):
    import shutil
    available = shutil.disk_usage(root).free
    memory = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    require(available >= 18 * 1024**3 and memory >= 4 * 1024**3,
            "audio-source-runner-resource-floor")
    return {"disk_free_bytes": available, "physical_memory_available_bytes": memory,
            "disk_floor_bytes": 18 * 1024**3, "memory_floor_bytes": 4 * 1024**3,
            "physical_quota_enforced": False}


def audio_input_paths(store, repo, source):
    names = tuple(x.decode("utf-8") for x in store.git(
        repo, "ls-tree", "-r", "--name-only", "-z", source).split(b"\0") if x)
    require(names and len(names) == len(set(names)) and len(names) <= 4096
            and all(not p.startswith("/") and ".." not in p.split("/") and "\\" not in p for p in names),
            "audio-source-closed-tracked-inventory")
    return names


def audio_prepare_producers(sdk_gate, call, cargo, work, env, logs, ctx, target, phase):
    prepared = {"collections": {}, "steps": [], "clippy": "NOT_RUN"}
    for label, argv in selected.audio_source_producers(cargo):
        phase(label)
        row = call(argv, work, env, logs, label, 1800, ctx)
        require(type(row["exit_code"]) is int and row["exit_code"] == 0,
                "audio-source-producer-failed:" + label)
        prepared["steps"].append(label)
        if label == "strict-clippy":
            prepared["clippy"] = "PASS"
            continue
        raw = sdk_gate.observer.original_stream(logs, row, "stdout")
        normal = selected.audio_source_normal_specs()
        if label == "normal-tools-build":
            paths = sdk_gate.select_binary_paths(raw, row["exit_code"], work, target, normal, testing=False)
            # The two source consumers derive the sibling debug/ paths from
            # their actual test binary. Prebuild in that same target, not a
            # separately cached producer or late Cargo command.
            require(all(Path(paths[name]) == target / "debug" / name for name in paths),
                    "audio-source-normal-tools-not-in-same-target")
            prepared["normal_prebuild"] = paths
        else:
            tests = selected.audio_source_specs()
            paths = sdk_gate.select_binary_paths(raw, row["exit_code"], work, target, tests, testing=True)
            require(all(Path(path).parent == target / "debug/deps" for path in paths.values()),
                    "audio-source-test-target-not-sibling-of-fake-tools")
            final_normal = sdk_gate.select_binary_paths(raw, row["exit_code"], work, target, normal, testing=False)
            require(final_normal == prepared["normal_prebuild"], "audio-source-final-normal-target-changed")
            prepared["collections"]["tests"] = {"label": label, "command": row,
                "specs": tests, "testing": True}
            prepared["collections"]["normal"] = {"label": label, "command": row,
                "specs": normal, "testing": False}
    require(prepared["clippy"] == "PASS" and set(prepared["collections"]) == {"tests", "normal"},
            "audio-source-producer-set-incomplete")
    return prepared


def audio_source_run(gate, sdk, output, *, env=None):
    import shutil
    import time
    import ci_sdk_native_source as sdk_gate
    observer, store = sdk_gate.observer, sdk_gate.custody
    started = time.monotonic_ns()
    original_env = dict(os.environ if env is None else env)
    current = selected.audio_source_context(original_env, sys.platform)
    clock = audio_clock(started, time.monotonic_ns())
    gate, sdk, output = (Path(p).absolute() for p in (gate, sdk, output))
    temp_root = Path(original_env.get("RUNNER_TEMP", ""))
    require(temp_root.is_absolute() and temp_root.resolve(strict=True) == temp_root
            and output == temp_root / ("mw-audio-source-" + current["GITHUB_RUN_ID"] + "-1")
            and not output.exists() and gate != sdk
            and gate.resolve(strict=True) == gate and sdk.resolve(strict=True) == sdk
            and not output.is_relative_to(gate) and not output.is_relative_to(sdk),
            "audio-source-owned-path-or-existing-attempt")
    audio_resource_preflight(temp_root)
    output.mkdir(mode=0o700)
    evidence = output / "evidence"; evidence.mkdir(mode=0o700)
    logs = evidence / "commands"; logs.mkdir(mode=0o700)
    for name in ("target", "home", "cargo-home", "rustup-home", "tmp"):
        (output / name).mkdir(mode=0o700)
    observations, results, rosters = [], [], {}
    report = {"schema": "motionwright.native.audio-source-g1/1", "status": "INCOMPLETE",
        "context": current, "clock": clock, "phase_caps_seconds": dict(AUDIO_PHASE_SECONDS),
        "automatic_retry": False, "results": results, "rosters": rosters,
        "physical_audio": "NOT_RUN", "full_sdk_g1": "NOT_RUN", "release_authorized": False,
        "escaped_descendants": "NOT_OBSERVED", "filesystem_calls": "COOPERATIVE_BOUNDS",
        "retained_sdk_internal_tempdirs": False, "commands": observations}
    phase_state = {}
    phases = []
    def phase(name):
        require(name in AUDIO_PHASE_SECONDS and name not in phases, "audio-source-phase-repeat")
        now = time.monotonic_ns()
        upper = clock["deadline_ns"] if name == "retention" else clock["work_deadline_ns"]
        require(now < upper, "audio-source-phase-not-started-budget-exhausted")
        phase_state.update(name=name, deadline_ns=min(upper, now + AUDIO_PHASE_SECONDS[name] * 1_000_000_000))
        phases.append(name)
    def observed(argv, cwd, environment, folder, label, timeout, ctx):
        now = time.monotonic_ns()
        remaining = (phase_state["deadline_ns"] - now) // 1_000_000_000
        require(remaining > 0, "audio-source-command-not-started-budget-exhausted:" + label)
        row = observer.command(argv, cwd, environment, folder, label, min(timeout, remaining), ctx,
                               (evidence, observations))
        if time.monotonic_ns() >= phase_state["deadline_ns"]:
            raise observer.UnknownExecution("audio-source-returned-after-original-phase-deadline", row)
        return row
    def checked(argv, label, timeout=180):
        row = observed(argv, sdk, environment, logs, label, timeout, current)
        require(type(row["exit_code"]) is int and row["exit_code"] == 0,
                "audio-source-prerequisite-failed:" + label)
        return row
    try:
        phase("source-g0")
        environment = audio_environment(original_env, output)
        require(Path(__file__).resolve() == gate / "tooling/native_mlt_g2_custody.py"
                and Path(selected.__file__).resolve() == gate / "tooling/native_mlt_evidence_selection.py"
                and Path(sdk_gate.__file__).resolve() == gate / "tooling/ci_sdk_native_source.py"
                and Path(observer.__file__).resolve() == gate / "tooling/ci_foundation_causal.py"
                and Path(store.__file__).resolve() == gate / "tooling/ci_storage_apply_recovery.py",
                "audio-source-observer-module-origin")
        gate_paths = tuple(p for p in audio_input_paths(store, gate, current["GITHUB_SHA"])
                           if p.startswith("tooling/") or p in ("SOURCE_LOCK.json",
                           ".github/workflows/native-mlt-sequence-e2e.yml"))
        report["gate_before"] = store.source_snapshot(gate, current["GITHUB_SHA"], gate_paths)
        require(all(report["gate_before"]["files"][p]["sha256"] == h for p,h in sdk_gate.GATE_PINS.items()),
                "audio-source-shared-observer-pin-changed")
        paths = audio_input_paths(store, sdk, selected.AUDIO_SOURCE_SHA)
        before = store.source_snapshot(sdk, selected.AUDIO_SOURCE_SHA, paths, selected.AUDIO_SOURCE_TREE)
        require(before["parents"] == [selected.AUDIO_SOURCE_PARENT]
                and all(before["files"][p]["sha256"] == h for p,h in selected.AUDIO_SOURCE_PINS.items()),
                "audio-source-product-pin-changed")
        report["source_before"] = before
        report["retained_sources"] = observer.retain_source(sdk, before, evidence)
        report["retained_gate_sources"] = observer.retain_source(gate, report["gate_before"], evidence)
        python = str(Path(sys.executable).resolve(strict=True))
        checked([python, "-I", "-B", "-X", "utf8", "-c",
                 "import sys,tomllib,jsonschema,yaml; assert sys.version_info >= (3,12); print('SDK_SOURCE_PREREQUISITES')"],
                "sdk-prerequisites", 45)
        rustup = Path(shutil.which("rustup", path=environment["PATH"]) or "").absolute()
        require(rustup.is_file(), "audio-source-rustup-not-found")
        report["rustup"] = {"absolute_path": str(rustup), **store.fingerprint(rustup, rustup.parent)}
        checked([str(rustup), "toolchain", "install", "1.98.1", "--profile", "minimal",
                 "--component", "rustfmt", "--component", "clippy", "--no-self-update"], "owned-toolchain", 600)
        tools = {}
        for name in ("cargo", "rustc", "rustdoc", "cargo-fmt", "rustfmt", "cargo-clippy", "clippy-driver"):
            found = checked([str(rustup), "which", "--toolchain", "1.98.1", name], name + "-path", 45)
            path = Path(observer.original_stream(logs, found, "stdout").decode().strip())
            require(path.is_absolute() and path.resolve(strict=True) == path
                    and path.is_relative_to(output / "rustup-home"), "audio-source-tool-outside-owned-toolchain")
            stamp = store.fingerprint(path, output)
            version = checked([str(path), "-Vv" if name == "rustc" else "--version"], name + "-version", 45)
            text = observer.original_stream(logs, version, "stdout").decode()
            require(text.strip() and (name != "rustc" or
                    "release: 1.98.1\n" in text and "host: x86_64-unknown-linux-gnu\n" in text),
                    "audio-source-tool-version")
            tools[name] = {"absolute_path": str(path), **stamp, "version": text}
        for name in ("git", "node", "bash"):
            path = Path(shutil.which(name, path=environment["PATH"]) or "").resolve(strict=True)
            stamp = store.fingerprint(path, path.parent)
            version = checked([str(path), "--version"], name + "-version", 45)
            text = observer.original_stream(logs, version, "stdout").decode()
            require(text.strip() and (name != "node" or text.startswith("v22.")), "audio-source-tool-version")
            tools[name] = {"absolute_path": str(path), **stamp, "version": text}
        report["tools"] = tools
        environment.update(RUSTC=tools["rustc"]["absolute_path"], RUSTDOC=tools["rustdoc"]["absolute_path"],
                           RUSTFMT=tools["rustfmt"]["absolute_path"])
        environment["PATH"] = str(Path(tools["cargo"]["absolute_path"]).parent) + os.pathsep + environment["PATH"]
        checked([python, "-I", "-B", "-X", "utf8", str(sdk / "scripts/verify-oss-hygiene.py")], "sdk-hygiene", 180)
        checked([python, "-I", "-B", "-X", "utf8", str(sdk / "scripts/verify-source.py")], "sdk-source-policy", 300)
        checked([tools["cargo-fmt"]["absolute_path"], "fmt", "--all", "--", "--check"], "sdk-format", 180)
        report["sdk_g0"] = "PASS_HYGIENE_SOURCE_FORMAT"
        report["resource_preproducer"] = audio_resource_preflight(output)
        barrier = sdk_gate.ProducerBarrier(observed, tools["cargo"]["absolute_path"])
        prepared = audio_prepare_producers(sdk_gate, barrier, tools["cargo"]["absolute_path"], sdk,
                                          environment, logs, current, output / "target", phase)
        barrier.begin_sealing()
        sealed = sdk_gate.seal_compiled_role(prepared, sdk, output / "target", logs, evidence, barrier)
        report["binaries"] = sealed
        report["producers"] = prepared
        barrier.begin_observation()
        phase("enumeration")
        for spec in selected.audio_source_specs():
            name = spec["name"]; binary = sealed["tests"][name]
            row = barrier([binary["absolute_path"], "--list", "--format", "pretty"], sdk,
                          environment, logs, "list-" + name, 30, current)
            rosters[name] = selected.audio_source_list(observer.original_stream(logs, row, "stdout"), row["exit_code"], name)
            require(not observer.original_stream(logs, row, "stderr"), "audio-source-list-stderr")
        live = sealed["tests"]["live"]
        row = barrier([live["absolute_path"], "--list", "--ignored", "--format", "pretty"], sdk,
                      environment, logs, "list-ignored-live", 30, current)
        report["ignored_physical_roster"] = selected.audio_source_list(
            observer.original_stream(logs, row, "stdout"), row["exit_code"], "live", ignored_only=True)
        require(not observer.original_stream(logs, row, "stderr"), "audio-source-ignored-list-stderr")
        # No execution until all eight complete rosters and the ignored boundary
        # have been admitted. No Cargo command is permitted after sealing.
        phase("cases")
        pair = {"id": "sdk-audio-source", "failures": {}, "repair": {
            "sha": selected.AUDIO_SOURCE_SHA, "tree": selected.AUDIO_SOURCE_TREE,
            "targets": dict(selected.AUDIO_SOURCE_ROSTERS)}}
        for target, cases in selected.AUDIO_SOURCE_ROSTERS.items():
            binary = sealed["tests"][target]
            for name in cases:
                sdk_gate.verify_binaries({target: binary}, output / "target")
                sdk_gate.verify_binaries(sealed["normal"], output / "target")
                row = barrier(observer.case_argv(binary, pair, "repair", name), sdk, environment, logs,
                              "case-" + str(len(results)).zfill(3), 180, current)
                after = {"absolute_path": binary["absolute_path"], **store.fingerprint(Path(binary["absolute_path"]), output / "target")}
                raw = observer.original_stream(logs, row, "stdout") + b"\n" + observer.original_stream(logs, row, "stderr")
                admitted = observer.admit_case(pair, "repair", name, raw, row, binary, after, rosters[target], current)
                results.append({"target": target, **admitted})
        report["selection"] = selected.audio_source_complete(results, rosters)
        phase("retention")
        report["source_after"] = store.source_snapshot(sdk, selected.AUDIO_SOURCE_SHA, paths, selected.AUDIO_SOURCE_TREE)
        report["gate_after"] = store.source_snapshot(gate, current["GITHUB_SHA"], gate_paths)
        require(report["source_after"] == before and report["gate_after"] == report["gate_before"],
                "audio-source-source-changed")
        for collection in sealed.values(): sdk_gate.verify_binaries(collection, output / "target")
        for tool in tools.values():
            path = Path(tool["absolute_path"])
            root = output if path.is_relative_to(output) else path.parent
            require(store.fingerprint(path, root) == {k:tool[k] for k in ("path", "bytes", "sha256", "identity")},
                    "audio-source-tool-changed")
        sdk_gate.verify_commands(evidence, observations)
        require(time.monotonic_ns() < phase_state["deadline_ns"], "audio-source-retention-deadline")
        report["status"] = "PASS_EXACT_AUDIO_SOURCE_G1"
    except (observer.UnknownExecution, store.UnknownObservation) as error:
        report.update(status="UNKNOWN_NO_RETRY", error=str(error), uncertain_command=error.command)
    except Exception as error:
        report.update(status="FAIL_OR_PREPARATION_REJECTED", error=type(error).__name__ + ": " + str(error))
    report.update(ended_monotonic_ns=time.monotonic_ns(), phases_started=phases)
    store.write_new(evidence / "receipt.json", report)
    # This status has meaning only with the original receipt, streams and binary
    # custody. Job SUCCESS is never a substitute for their independent readback.
    return report


# Generic process primitives only; none of the startup admission protocol is
# an Audio authority. All blocking filesystem work belongs in a bounded child.
AUDIO_LIVE_LAUNCHER_SHA256 = "57e0ffbd7ec02bd2dc0fcb590e7ea286074e661f3c09f37f871cdcb536b9906b"
AUDIO_LIVE_PROVIDER_PINS = {
    "tooling/startup_capture_launcher.py": AUDIO_LIVE_LAUNCHER_SHA256,
    "tooling/ci_foundation_causal.py": "1d31c58aa7d63c927ceb2518282a433e0f728fc125da8978fb635b18cff354c2",
    "tooling/ci_storage_apply_recovery.py": "9aea9a41d365a36fd1abae86ec5a0974236d62d9781a372eace0578ef2da1520",
    "tooling/ci_sdk_native_source.py": "65bcda73f16423ebb644a0979bca3a7927f8e7337c124702874bce51adcf8127",
    "tooling/ci_private_evidence.py": "2671eea354f672353a6b4ea1ac9c43de4b682e96f4dcc9146104a5f8d20a8eee",
}
AUDIO_LIVE_PIPE_CAPS = {"stdout": 2 * 1024**2, "stderr": 2 * 1024**2,
                        "status": 65536, "lifetime": 1, "ready": 16}
AUDIO_LIVE_BOOTSTRAP = """import os,resource,select,sys,time
deadline,gate,ready,file_ceiling=map(int,sys.argv[1:5])
try:
    if time.monotonic_ns()>=deadline: os._exit(125)
    if os.write(ready,b'READY\\n')!=6: os._exit(125)
    retired=ready; ready=-1; os.close(retired)
    while time.monotonic_ns()<deadline:
        if select.select([gate],[],[],.01)[0]:
            token=os.read(gate,2)
            if token!=b'G': os._exit(125)
            retired=gate; gate=-1; os.close(retired)
            if time.monotonic_ns()>=deadline: os._exit(125)
            if file_ceiling: resource.setrlimit(resource.RLIMIT_FSIZE,(file_ceiling,file_ceiling))
            os.execve(sys.argv[5],sys.argv[5:],dict(os.environ))
    os._exit(125)
finally:
    for fd in (gate,ready):
        if fd>=0:
            try: os.close(fd)
            except OSError: pass
    os._exit(125)
"""


def audio_live_read(path, limit):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path
            and not any(parent.is_symlink() for parent in path.parents), "audio-live-canonical-file")
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit,
            "audio-live-bounded-single-link-file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        require(metadata_identity(before) == metadata_identity(opened), "audio-live-file-changed-before-open")
        data = bytearray()
        while len(data) <= limit:
            block = os.read(fd, min(65536, limit + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        require(len(data) == before.st_size <= limit
                and metadata_identity(opened) == metadata_identity(os.fstat(fd))
                == metadata_identity(path.lstat()), "audio-live-file-changed-during-read")
        return bytes(data)
    finally:
        os.close(fd)


def audio_live_environment(original, directory):
    require(type(original) is dict and all(type(k) is str and type(v) is str for k,v in original.items()),
            "audio-live-environment-types")
    forbidden = {"BASH_ENV", "ENV", "GCONV_PATH", "LOCPATH", "GLIBC_TUNABLES", "NODE_OPTIONS",
                 "RUSTC", "RUSTDOC", "RUSTFMT", "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS",
                 "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "MLT_REPOSITORY", "MLT_DATA", "MLT_PROFILES_PATH"}
    safe_python = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1"}
    bad = [k for k,v in original.items() if v and (k in forbidden or
           k.startswith(("LD_", "CARGO_ALIAS_", "RUST_TEST_", "SEMWRIGHT_")) or
           k.startswith("PYTHON") and safe_python.get(k) != v)]
    require(not bad, "audio-live-inherited-execution-override")
    root = Path(directory)
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
            "HOME": str(root / "home"), "TMPDIR": str(root / "tmp"), "TMP": str(root / "tmp"),
            "TEMP": str(root / "tmp"), "RUST_BACKTRACE": "0", "CARGO_TERM_COLOR": "never",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1"}


def audio_live_worker(L, function, deadline_ns, *, limit=1048576):
    """Retain one invocation and its uncertain prefix; never repeat it."""
    import time
    handle = None
    observation = {"status": "UNKNOWN", "automatic_retry": False, "value": None,
                   "result_prefix": b"", "original_process": None}
    try:
        handle = L._spawn_worker(function, deadline_ns)
        complete = L.observe_worker(handle, deadline_ns, limit=limit)
        raw = bytes(handle.prefix)
        observation["result_prefix"] = raw
        if complete:
            frame = L.strict_json(raw, limit)
            require(set(frame) == {"ok", "value"} and frame["ok"] is True,
                    "audio-live-worker-failed")
            observation.update(status="OBSERVED", value=frame["value"])
        else:
            handle.kill_once(time.monotonic_ns())
            L.observe_worker(handle, deadline_ns, limit=limit)
    except BaseException as error:
        if handle is None and isinstance(getattr(error, "custody", None), L.Child):
            handle = error.custody
        observation["error_type"] = type(error).__name__
        if handle is not None:
            handle.kill_once(time.monotonic_ns())
            L.observe_worker(handle, deadline_ns, limit=limit)
            observation["result_prefix"] = bytes(handle.prefix)
    finally:
        if handle is not None:
            observation["result_prefix"] = bytes(handle.prefix)
            handle.close()
            observation["original_process"] = handle.metadata()
            if handle.errors or time.monotonic_ns() >= deadline_ns:
                observation["status"] = "UNKNOWN"
    return observation


def audio_live_pidfd(L, pid, *, parent_pid, uid, namespace, ledger):
    """A retained non-child; owning its fd does not authenticate its target."""
    before = L._kernel_stamp(pid)
    item = {"pid": pid, "pidfd": L.OwnedFD(os.pidfd_open(pid), "audio-namespace-role"),
            "identity": before, "authenticated": False, "exit_ns": None, "signals": [], "errors": []}
    ledger.append(item)
    after = L._kernel_stamp(pid)
    require(L._same_stamp(before, after) and before["parent_pid"] == parent_pid
            and before["uid"] == uid and os.readlink(f"/proc/{pid}/ns/pid") == namespace,
            "audio-live-retained-pidfd-target-changed")
    item["authenticated"] = True
    return item


def audio_live_poll_pidfd(item, now_ns):
    import select
    if not item["authenticated"] or item["pidfd"].fd is None or item["exit_ns"] is not None:
        return
    poller = select.poll(); poller.register(item["pidfd"].fd, select.POLLIN)
    for _, mask in poller.poll(0):
        if mask & (select.POLLNVAL | select.POLLERR):
            item["errors"].append("PIDFD_UNAVAILABLE")
        elif mask & (select.POLLIN | select.POLLHUP):
            item["exit_ns"] = now_ns


def audio_live_signal_once(item, now_ns):
    import signal
    if not item["authenticated"] or item["signals"] or item["exit_ns"] is not None or item["pidfd"].fd is None:
        return
    action = {"signal": int(signal.SIGKILL), "monotonic_ns": now_ns, "outcome": "UNKNOWN"}
    item["signals"].append(action)
    try:
        signal.pidfd_send_signal(item["pidfd"].fd, signal.SIGKILL, None, 0)
        action["outcome"] = "SENT"
    except ProcessLookupError:
        action["outcome"] = "ALREADY_EXITED_OBSERVATION_REQUIRED"
    except OSError as error:
        action["error_type"] = type(error).__name__


def audio_live_role_record(item):
    return {key: value for key,value in item.items() if key != "pidfd"} | {
        "pidfd": item["pidfd"].metadata(), "original_wait": "NOT_OUR_CHILD_NOT_CLAIMED"}


def audio_live_proc_children(L, retained):
    require(retained["authenticated"] and retained["pidfd"].fd is not None,
            "audio-live-owned-init-query")
    pid = retained["pid"]
    before = L._kernel_stamp(pid)
    fd = os.open(f"/proc/{pid}/task/{pid}/children", os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        raw = os.read(fd, 4097)
        require(len(raw) <= 4096 and L._same_stamp(before, L._kernel_stamp(pid))
                and before["start_ticks"] == retained["identity"]["start_ticks"],
                "audio-live-init-changed-during-child-query")
        return raw
    finally:
        os.close(fd)


def audio_live_bwrap_argv(bwrap, python, argv, working, readonly, writable, channels, deadline_ns, *, preparation=False):
    require(set(channels) == {"status", "lifetime", "block", "gate", "ready"}
            and all(type(v) is int and v > 2 for v in channels.values())
            and len(set(channels.values())) == len(channels), "audio-live-channel-roster")
    require(type(argv) is list and argv and all(type(x) is str and "\0" not in x for x in argv)
            and all(Path(x).is_absolute() for x in (bwrap, python, working))
            and type(readonly) is list and readonly and len(set(readonly)) == len(readonly)
            and type(writable) is list and writable and len(set(writable)) == len(writable),
            "audio-live-namespace-command")
    require(type(preparation) is bool, "audio-live-phase-type")
    result = [bwrap, "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
              "--new-session", "--proc", "/proc", "--dev", "/dev",
              "--chdir", working, "--json-status-fd", str(channels["status"]),
              "--sync-fd", str(channels["lifetime"]), "--block-fd", str(channels["block"])]
    if not preparation:
        result.append("--unshare-net")
    for path in readonly:
        require(type(path) is str and Path(path).is_absolute() and path not in ("/", "/proc", "/run"),
                "audio-live-readonly-root")
        result.extend(("--ro-bind", path, path))
    for path in writable:
        require(type(path) is str and Path(path).is_absolute() and path not in ("/", "/usr", "/etc", "/proc"),
                "audio-live-writable-root")
        result.extend(("--bind", path, path))
    # EOF of --block-fd is not an authorization; this bootstrap requires G.
    result.extend(("--", python, "-I", "-B", "-X", "utf8", "-c", AUDIO_LIVE_BOOTSTRAP,
                   str(deadline_ns), str(channels["gate"]), str(channels["ready"]),
                   "0" if preparation else str(MAX_FILE), *argv))
    return result


def audio_live_namespace(L, *, bwrap, python, argv, working, readonly, writable, environment,
                         clock, effect_deadline_ns, uid, gid, expected_executable,
                         expected_python, expected_bwrap, python_binary, command_binary, preparation=False):
    """Observe existing Bubblewrap and L handles; no filesystem writes here."""
    import select
    import time
    require(effect_deadline_ns <= clock["work_deadline_ns"] and time.monotonic_ns() < effect_deadline_ns,
            "audio-live-no-renewed-effect-deadline")
    streams, owned, roles, monitor = {}, [], [], None
    init = command = None
    released = False; executable_seen = False; block_released = False
    errors, status_rows = [], []
    result = {"status": "UNKNOWN", "automatic_retry": False, "raw": {}, "roles": [],
              "deadline_unchanged": True, "clock": dict(clock)}
    try:
        for label, cap in AUDIO_LIVE_PIPE_CAPS.items():
            read, write = L._pipe(); read.label = label + "-read"; write.label = label + "-write"
            owned.extend((read, write))
            streams[label] = {"read": read, "write": write, "prefix": bytearray(),
                              "eof": False, "truncated": False, "cap": cap}
        block_r, block_w = L._pipe(); owned.extend((block_r, block_w))
        gate_r, gate_w = L._pipe(); owned.extend((gate_r, gate_w))
        channels = {"status": streams["status"]["write"].fd, "lifetime": streams["lifetime"]["write"].fd,
                    "ready": streams["ready"]["write"].fd, "block": block_r.fd, "gate": gate_r.fd}
        bargv = audio_live_bwrap_argv(bwrap, python, argv, working, readonly, writable, channels,
                                     effect_deadline_ns, preparation=preparation)
        keep = tuple([s["write"].fd for s in streams.values()] + [block_r.fd, gate_r.fd])
        def execute(_):
            if os.geteuid() == 0:
                os.setgroups([]); os.setgid(gid); os.setuid(uid)
            require(os.geteuid() == uid and os.getegid() == gid, "audio-live-process-owner")
            null = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
            os.dup2(null, 0)
            if null != 0:
                os.close(null)
            os.set_blocking(streams["stdout"]["write"].fd, True)
            os.set_blocking(streams["stderr"]["write"].fd, True)
            os.dup2(streams["stdout"]["write"].fd, 1)
            os.dup2(streams["stderr"]["write"].fd, 2)
            for label in ("stdout", "stderr"):
                os.close(streams[label]["write"].fd)
            for fd in channels.values():
                os.set_inheritable(fd, True)
                os.set_blocking(fd, True)
            os.execve(bwrap, bargv, environment)
        try:
            monitor = L._spawn_worker(execute, effect_deadline_ns, keep_fds=keep)
        except BaseException as error:
            if isinstance(getattr(error, "custody", None), L.Child):
                monitor = error.custody
            raise
        parent_closes = [s["write"].close() for s in streams.values()]
        parent_closes.extend((block_r.close(), gate_r.close()))
        require(all(parent_closes), "audio-live-inherited-fd-close-uncertain")
        parsed = 0
        while time.monotonic_ns() < clock["cleanup_deadline_ns"]:
            now = time.monotonic_ns()
            if now >= effect_deadline_ns:
                errors.append("ORIGINAL_EFFECT_CUTOFF_EXCEEDED")
            for label, stream in streams.items():
                if stream["eof"]:
                    continue
                try:
                    raw = os.read(stream["read"].fd, 65536)
                    if raw:
                        available = max(0, stream["cap"] - len(stream["prefix"]))
                        stream["prefix"].extend(raw[:available])
                        stream["truncated"] |= len(raw) > available
                    else:
                        stream["eof"] = True
                except BlockingIOError:
                    pass
                except OSError:
                    errors.append("PIPE_READ_UNCERTAIN:" + label)
            L._drain_child(monitor, 4096); monitor.poll(now)
            if streams["lifetime"]["prefix"]:
                errors.append("LIFETIME_CHANNEL_UNEXPECTED_BYTES")
            for role in roles:
                audio_live_poll_pidfd(role, now)
            raw_status = bytes(streams["status"]["prefix"])
            while b"\n" in raw_status[parsed:]:
                end = raw_status.index(b"\n", parsed) + 1
                row = L.strict_json(raw_status[parsed:end], 65536); parsed = end
                require(len(status_rows) < 2, "audio-live-extra-bwrap-status")
                if not status_rows:
                    require(type(row.get("child-pid")) is int and row["child-pid"] > 1
                            and set(row) <= {"child-pid", "cgroup-namespace", "ipc-namespace", "mnt-namespace",
                                             "net-namespace", "pid-namespace", "uts-namespace"}
                            and {"pid-namespace", "mnt-namespace"} <= set(row)
                            and all(type(value) is int and value > 0 for value in row.values()),
                            "audio-live-bwrap-init-status")
                    ns = "pid:[" + str(row["pid-namespace"]) + "]"
                    require(ns != os.readlink("/proc/self/ns/pid"), "audio-live-pid-namespace-missing")
                    init = audio_live_pidfd(L, row["child-pid"], parent_pid=monitor.pid,
                                           uid=uid, namespace=ns, ledger=roles)
                    monitor_stamp = L._kernel_stamp(monitor.pid)
                    require(monitor_stamp["start_ticks"] == monitor.identity["start_ticks"]
                            and monitor_stamp["parent_pid"] == os.getpid() and monitor_stamp["uid"] == uid
                            and os.readlink(f"/proc/{monitor.pid}/exe") == bwrap
                            and metadata_identity(os.stat(f"/proc/{monitor.pid}/exe")) == tuple(expected_bwrap)
                            and L._proc_bytes(monitor.pid, "cmdline", 65536) ==
                                b"\0".join(x.encode() for x in bargv) + b"\0",
                            "audio-live-monitor-exec-identity")
                    require(os.readlink(f"/proc/{init['pid']}/exe") == bwrap,
                            "audio-live-init-executable-identity")
                    require(metadata_identity(os.stat(f"/proc/{init['pid']}/exe")) == tuple(expected_bwrap),
                            "audio-live-init-executable-changed")
                    require(os.write(block_w.fd, b"B") == 1 and block_w.close(), "audio-live-block-release")
                    block_released = True
                else:
                    require(set(row) == {"exit-code"} and type(row["exit-code"]) is int
                            and 0 <= row["exit-code"] <= 255, "audio-live-bwrap-terminal")
                status_rows.append(row)
            require(not streams["status"]["eof"] or parsed == len(raw_status),
                    "audio-live-truncated-status-at-eof")
            if init is not None and not released and streams["ready"]["eof"]:
                require(bytes(streams["ready"]["prefix"]) == b"READY\n" and not streams["ready"]["truncated"],
                        "audio-live-bootstrap-ready-eof")
                raw_children = audio_live_proc_children(L, init)
                children = raw_children.split()
                require(len(children) == 1 and children[0].isdigit(), "audio-live-bootstrap-child-roster")
                command = audio_live_pidfd(L, int(children[0]), parent_pid=init["pid"], uid=uid,
                                          namespace="pid:[" + str(status_rows[0]["pid-namespace"]) + "]", ledger=roles)
                bootstrap_argv = [python, "-I", "-B", "-X", "utf8", "-c", AUDIO_LIVE_BOOTSTRAP,
                                  str(effect_deadline_ns), str(channels["gate"]), str(channels["ready"]),
                                  "0" if preparation else str(MAX_FILE), *argv]
                require(os.readlink(f"/proc/{command['pid']}/exe") == python_binary
                        and metadata_identity(os.stat(f"/proc/{command['pid']}/exe")) == tuple(expected_python)
                        and L._proc_bytes(command["pid"], "cmdline", 65536) ==
                            b"\0".join(x.encode() for x in bootstrap_argv) + b"\0"
                        and L._same_stamp(command["identity"], L._kernel_stamp(command["pid"])),
                        "audio-live-bootstrap-identity-before-release")
                require(time.monotonic_ns() < effect_deadline_ns and not errors and not monitor.errors
                        and all(not s["truncated"] for s in streams.values()),
                        "audio-live-no-release-after-uncertainty")
                require(os.write(gate_w.fd, b"G") == 1, "audio-live-exact-release-byte")
                released = True
                require(gate_w.close(), "audio-live-command-gate-close")
            if command is not None and command["exit_ns"] is None and not executable_seen:
                try:
                    executable = os.readlink(f"/proc/{command['pid']}/exe")
                    if executable == command_binary:
                        info = os.stat(f"/proc/{command['pid']}/exe")
                        require(metadata_identity(info) == tuple(expected_executable),
                                "audio-live-command-executable-changed")
                        require(L._proc_bytes(command["pid"], "cmdline", 65536) ==
                                b"\0".join(x.encode() for x in argv) + b"\0", "audio-live-command-argv-changed")
                        executable_seen = True
                except FileNotFoundError:
                    pass
            now = time.monotonic_ns()
            if now >= effect_deadline_ns:
                errors.append("ORIGINAL_EFFECT_CUTOFF_EXCEEDED")
            trouble = (now >= effect_deadline_ns or errors or monitor.errors or
                       any(s["truncated"] for s in streams.values()) or any(r["errors"] for r in roles))
            if trouble:
                for role in reversed(roles):
                    audio_live_signal_once(role, now)
                monitor.kill_once(now)
                block_w.close(); gate_w.close()
            if (monitor.wait is not None and monitor.exited_ns is not None and init is not None
                    and init["exit_ns"] is not None and command is not None and command["exit_ns"] is not None
                    and all(s["eof"] for s in streams.values())):
                break
            select.select([], [], [], min(.01, max(0, (clock["cleanup_deadline_ns"] - time.monotonic_ns()) / 1e9)))
    except BaseException as error:
        errors.append(type(error).__name__ + ":" + str(error)[:160])
    finally:
        # On every path, stop the retained roles once, drain without retrying
        # the operation, and retire every fd before reporting its close result.
        if monitor is not None:
            now = time.monotonic_ns()
            incomplete = (monitor.wait is None or monitor.exited_ns is None or monitor.wait_lost
                          or any(role["exit_ns"] is None for role in roles)
                          or not all(stream["eof"] for stream in streams.values()))
            if now >= clock["cleanup_deadline_ns"] or incomplete:
                errors.append("ORIGINAL_CLOSURE_NOT_OBSERVED")
            if errors or incomplete:
                for role in reversed(roles):
                    audio_live_signal_once(role, now)
                monitor.kill_once(now)
            for item in (locals().get("block_w"), locals().get("gate_w")):
                if item is not None:
                    item.close()
            while time.monotonic_ns() < clock["cleanup_deadline_ns"]:
                now = time.monotonic_ns(); monitor.poll(now)
                for role in roles:
                    audio_live_poll_pidfd(role, now)
                for stream in streams.values():
                    if stream["eof"] or stream["read"].fd is None:
                        continue
                    try:
                        raw = os.read(stream["read"].fd, 65536)
                        if not raw:
                            stream["eof"] = True
                        else:
                            available = max(0, stream["cap"] - len(stream["prefix"]))
                            stream["prefix"].extend(raw[:available]); stream["truncated"] |= len(raw) > available
                    except BlockingIOError:
                        pass
                    except OSError:
                        errors.append("CLEANUP_PIPE_READ_UNCERTAIN")
                if monitor.wait is not None and all(r["exit_ns"] is not None for r in roles) and all(s["eof"] for s in streams.values()):
                    break
                select.select([], [], [], .01)
            monitor.close()
            errors.extend(monitor.errors)
        close_results = [fd.close() for fd in owned]
        close_results.extend(role["pidfd"].close() for role in roles)
        if not all(close_results):
            errors.append("DESCRIPTOR_CLOSE_UNCERTAIN")
        finished = time.monotonic_ns()
        if finished >= clock["cleanup_deadline_ns"]:
            errors.append("ORIGINAL_CLEANUP_DEADLINE_EXCEEDED")
        result.update(raw={label: bytes(s["prefix"]) for label,s in streams.items()},
            roles=[audio_live_role_record(role) for role in roles],
            monitor=monitor.metadata() if monitor is not None else None,
            status_rows=status_rows, release_sent=released, namespace_block_released=block_released,
            complete_before_cleanup_deadline=finished < clock["cleanup_deadline_ns"],
            finished_ns=finished, namespace_init_authenticated=init is not None and init["authenticated"],
            namespace_init_exited=init is not None and init["exit_ns"] is not None,
            command_authenticated_before_release=released and command is not None and command["authenticated"],
            command_executable_observed=executable_seen,
            command_exited=command is not None and command["exit_ns"] is not None,
            monitor_wait_exit_code=monitor.wait["exit_code"] if monitor is not None and monitor.wait else None,
            bubblewrap_exit_code=status_rows[-1].get("exit-code") if len(status_rows) == 2 else None,
            sync_eof=streams.get("lifetime", {}).get("eof", False),
            stdout_eof=streams.get("stdout", {}).get("eof", False),
            stderr_eof=streams.get("stderr", {}).get("eof", False),
            status_eof=streams.get("status", {}).get("eof", False),
            monitor_result_eof=monitor is not None and monitor.eof,
            monitor_result_truncated=monitor is None or monitor.truncated,
            monitor_pidfd_exited=monitor is not None and monitor.exited_ns is not None,
            truncated=any(s["truncated"] for s in streams.values()),
            forced_cleanup=bool((monitor.signal_attempts if monitor is not None else []) or any(r["signals"] for r in roles)),
            errors=list(dict.fromkeys(errors)), descriptors=[fd.metadata() for fd in owned])
    result["status"] = "OBSERVED_NAMESPACE_TERMINAL" if audio_live_namespace_closed(result) else "UNKNOWN_NO_RETRY"
    return result


def audio_live_runtime_contract(value):
    """Reuse existing runtime/compiler intake; paths never supply authority."""
    require(type(value) is dict and set(value) == {"schema", "tools", "official_runtime", "compiler",
            "installation_effect"} and value["schema"] == "motionwright.audio-live-runtime/1"
            and value["installation_effect"] == "EXISTING_REPAIRED_MODULE_ADMISSION",
            "audio-live-runtime-contract")
    roles = {"python", "git", "node", "bash", "rustup", "bwrap", "mount", "gh", "melt", "ffprobe", "ffmpeg"}
    require(type(value["tools"]) is dict and set(value["tools"]) == roles,
            "audio-live-runtime-tool-roster")
    for name, row in value["tools"].items():
        require(type(row) is dict and set(row) == {"path", "sha256", "version"}
                and type(row["path"]) is str and row["path"].startswith("/")
                and 0 < len(row["path"]) <= 512 and "\0" not in row["path"]
                and type(row["sha256"]) is str and HEX.fullmatch(row["sha256"])
                and type(row["version"]) is str and 0 < len(row["version"]) <= 8192,
                "audio-live-runtime-tool-binding:" + name)
    require(value["tools"]["bwrap"]["version"].strip() == "bubblewrap 0.11.0"
            and value["tools"]["node"]["version"].startswith("v22.")
            and value["tools"]["gh"]["path"] == "/usr/bin/gh",
            "audio-live-runtime-protocol-version")
    return value


def audio_live_paths(gate, output, current, runner_temp):
    gate, output, runner_temp = map(Path, (gate, output, runner_temp))
    require(gate.is_absolute() and gate.resolve(strict=True) == gate
            and runner_temp.is_absolute() and runner_temp.resolve(strict=True) == runner_temp
            and runner_temp.is_dir() and not any(p.is_symlink() for p in (*gate.parents, *runner_temp.parents))
            and output == runner_temp / ("mw-audio-live-" + current["run_id"] + "-1")
            and not output.exists() and not output.is_symlink()
            and not output.is_relative_to(gate) and not gate.is_relative_to(output),
            "audio-live-fresh-owned-root-outside-source")
    return gate, output


def audio_live_fingerprint(path):
    path = Path(path)
    before = path.lstat()
    raw = audio_live_read(path, MAX_FILE)
    return {"path": str(path), "bytes": len(raw), "sha256": sha(raw),
            "identity": list(metadata_identity(before))}


def audio_live_bootstrap_inputs(gate, output, admitted, environment, clock, uid, gid):
    """Blocking bootstrap in one retained child; no SDK or native effect."""
    import time
    runtime = audio_live_runtime_contract(admitted["request"]["runtime"])
    require(os.geteuid() == 0 and type(uid) is int and type(gid) is int and uid > 0 and gid > 0,
            "audio-live-privileged-controller-normal-producer-owner")
    audio_live_paths(gate, output, admitted["context"], environment["RUNNER_TEMP"])
    selected.audio_live_clock(clock["started_ns"], time.monotonic_ns())
    audio_live_manifest_bound(admitted["plan"], output)
    output.mkdir(mode=0o700)
    os.chown(output, uid, gid)
    for name in ("control", "evidence", "export", "target", "cargo-home", "rustup-home", "home", "tmp", "volume"):
        (output / name).mkdir(mode=0o700); os.chown(output / name, uid, gid)
    (output / "evidence/commands").mkdir(mode=0o700); os.chown(output / "evidence/commands", uid, gid)
    new_bytes(output / "evidence/request.original.json", json_bytes(admitted["request"]), owner=(uid, gid))
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    require(re.fullmatch(r"[0-9a-f-]{36}", boot), "audio-live-monotonic-boot-binding")
    new_json(output / "control/clock.json", {**clock, "boot_id": boot, "context": admitted["context"]})
    for name, digest in AUDIO_LIVE_PROVIDER_PINS.items():
        require(sha(audio_live_read(gate / name, 1024 * 1024)) == digest,
                "audio-live-provider-source-changed:" + name)
    require(runtime["tools"]["bwrap"]["sha256"] == admitted["authority"]["bubblewrap_sha256"]
            and runtime["tools"]["mount"]["sha256"] == admitted["authority"]["mount_sha256"],
            "audio-live-authority-runtime-pins")
    tools = {}
    for name, descriptor in runtime["tools"].items():
        actual = audio_live_fingerprint(Path(descriptor["path"]))
        require(actual["sha256"] == descriptor["sha256"], "audio-live-tool-bytes:" + name)
        tools[name] = {**actual, "expected_version": descriptor["version"]}
    require(tools["python"]["path"] == str(Path(sys.executable).resolve(strict=True)),
            "audio-live-same-observed-interpreter")
    readonly = [str(path) for path in (Path("/usr"), Path("/bin"), Path("/sbin"), Path("/lib"), Path("/lib64"), Path("/etc")) if path.exists()]
    readonly.extend([str(gate), str(Path(sys.prefix))])
    readonly.extend(str(Path(row["path"]).parent) for row in tools.values()
                    if not Path(row["path"]).is_relative_to(Path("/usr")))
    readonly = sorted(set(readonly), key=lambda path: (len(Path(path).parts), path))
    readonly = [path for index,path in enumerate(readonly)
                if not any(Path(path).is_relative_to(Path(parent)) for parent in readonly[:index])]
    public_env = {k: environment[k] for k in ("GITHUB_ACTIONS", "CI", "GITHUB_EVENT_NAME", "GITHUB_RUN_ATTEMPT",
        "GITHUB_RUN_ID", "GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_REPOSITORY",
        "GITHUB_JOB", "GITHUB_WORKSPACE", "RUNNER_OS", "RUNNER_ARCH", "RUNNER_TEMP", "RUNNER_TOOL_CACHE",
        "MW_MLT_SCOPE", "MW_EXPECTED_GATE_SHA", "MW_EXPECTED_SDK_SHA") if k in environment}
    configuration = {"schema": "motionwright.audio-live-worker-input/1", "gate": str(gate),
        "output": str(output), "clock": clock, "uid": uid, "gid": gid,
        "request": admitted["request"], "environment": public_env, "tools": tools}
    raw = json_bytes(configuration)
    require(len(raw) <= MAX_JSON, "audio-live-worker-configuration-bound")
    new_bytes(output / "control/worker.json", raw, owner=(uid, gid), mode=0o400)
    return {"tools": tools, "python_launch": str(Path(sys.executable).absolute()), "readonly_roots": readonly,
            "configuration_sha256": sha(raw), "root_identity": list(metadata_identity(output.lstat())),
            "clock": clock, "boot_id": boot, "resource_before": audio_resource_preflight(output)}


def audio_live_remaining(clock, ceiling, now_ns, *, phase_deadline=None):
    require(type(ceiling) is int and 0 < ceiling <= 900 and type(now_ns) is int,
            "audio-live-command-cap-type")
    upper = clock["work_deadline_ns"] if phase_deadline is None else phase_deadline
    require(type(upper) is int and upper <= clock["work_deadline_ns"]
            and clock["started_ns"] <= now_ns < upper, "audio-live-original-budget-exhausted")
    remaining = (upper - now_ns) // 1_000_000_000
    require(remaining > 0, "audio-live-no-command-in-last-fraction")
    return min(ceiling, remaining)


def audio_live_prepare_producers(sdk_gate, barrier, cargo, sdk, environment, logs, context, target):
    specs = selected.audio_live_binary_specs()
    prepared = {"collections": {}, "steps": [], "clippy": "NOT_RUN"}
    clippy = [cargo, "clippy", "--locked", "--jobs", "1", "-p", "semwright-mlt-video-driver",
              "--features", "test-tools", "--bin", "semwright-mlt-video-driver", "--bin",
              "semwright-mlt-runtime-runner", "--test", "live", "--", "-D", "warnings"]
    row = barrier(clippy, sdk, environment, logs, "strict-clippy", 900, context)
    require(type(row["exit_code"]) is int and row["exit_code"] == 0, "audio-live-strict-clippy-failed")
    prepared["clippy"] = "PASS"
    for label, argv in selected.audio_live_producers(cargo):
        row = barrier(argv, sdk, environment, logs, "producer-" + label, 900, context)
        raw = sdk_gate.observer.original_stream(logs, row, "stdout")
        paths = sdk_gate.select_binary_paths(raw, row["exit_code"], sdk, target, specs[label], testing=label == "tests")
        require(all(Path(path).is_relative_to(target / "debug") for path in paths.values()),
                "audio-live-producer-left-owned-target")
        prepared["collections"][label] = {"command": row, "specs": specs[label], "testing": label == "tests"}
        prepared["steps"].append(label)
        if label == "normal":
            require(all(Path(path) == target / "debug" / name for name,path in paths.items()),
                    "audio-live-normal-bin-location")
            prepared["normal_prebuild"] = paths
        if label == "tests":
            actual_normal = sdk_gate.select_binary_paths(raw, row["exit_code"], sdk, target, specs["normal"], testing=False)
            require(actual_normal == prepared["normal_prebuild"], "audio-live-cargo-bin-exe-target-changed")
            prepared["collections"]["normal"] = {"command": row, "specs": specs["normal"], "testing": False}
    require(prepared["steps"] == ["normal", "tests", "sandbox"], "audio-live-producer-roster-incomplete")
    barrier.begin_sealing()
    sealed = sdk_gate.seal_compiled_role(prepared, sdk, target, logs, logs.parent, barrier)
    barrier.begin_observation()
    binary = sealed["tests"]["live"]["absolute_path"]
    rosters = {}
    for name, extra in (("all", []), ("ignored", ["--ignored"])):
        row = barrier([binary, "--list", *extra, "--format", "pretty"], sdk, environment,
                      logs, "list-live-" + name, 30, context)
        require(not sdk_gate.observer.original_stream(logs, row, "stderr"), "audio-live-list-stderr")
        rosters[name] = selected.audio_source_list(sdk_gate.observer.original_stream(logs, row, "stdout"),
                                                    row["exit_code"], "live", ignored_only=name == "ignored")
    return {"prepared": prepared, "sealed": sealed, "rosters": rosters,
            "barrier": barrier.phase, "producers_closed": True, "cases_run": 0}


def audio_live_source_bundle(gate, sdk, snapshots, destination):
    """Preserve full original bytes with a closed inner index, without a second crypto format."""
    content, total = [], 0
    for label, root in (("gate", gate), ("sdk", sdk)):
        for path, stamp in sorted(snapshots[label]["files"].items()):
            raw = audio_live_read(root / path, MAX_FILE)
            require(sha(raw) == stamp["sha256"] and len(raw) == stamp["bytes"], "audio-live-source-copy-changed")
            total += len(raw); require(total <= MAX_FILE, "audio-live-source-bundle-overflow")
            content.append((label + "/" + path, raw))
    content.append(("source-index.json", json_bytes(snapshots)))
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, raw in content:
            safe_member(name); member = tarfile.TarInfo(name); member.mode = 0o400; member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
    raw = stream.getvalue(); require(len(raw) <= MAX_FILE, "audio-live-source-tar-overflow")
    new_bytes(destination, raw)
    return {"bytes": len(raw), "sha256": sha(raw), "members": len(content)}


def audio_live_prepare_stage(config_path, expected_sha):
    """Executed only inside the observed preparation namespace before live release."""
    import shutil
    import time
    import ci_sdk_native_source as sdk_gate
    observer, store = sdk_gate.observer, sdk_gate.custody
    raw = audio_live_read(Path(config_path), MAX_JSON)
    require(sha(raw) == expected_sha, "audio-live-worker-input-changed")
    config = strict(raw)
    current = selected.audio_live_context(config["environment"], sys.platform)
    admitted = selected.audio_live_admit(config["request"], current)
    require(os.geteuid() == config["uid"] > 0 and os.getegid() == config["gid"] > 0 and os.getppid() == 1,
            "audio-live-preparation-requires-retained-namespace")
    clock = selected.audio_live_clock(config["clock"]["started_ns"], time.monotonic_ns())
    require(clock == config["clock"], "audio-live-worker-clock-changed")
    gate, root = Path(config["gate"]), Path(config["output"])
    sdk, evidence, logs = root / "sdk", root / "evidence", root / "evidence/commands"
    environment = audio_live_environment({}, root)
    environment.update(CARGO_HOME=str(root / "cargo-home"), RUSTUP_HOME=str(root / "rustup-home"),
        CARGO_TARGET_DIR=str(root / "target"), RUSTUP_TOOLCHAIN="1.98.1", CARGO_BUILD_JOBS="1",
        CARGO_INCREMENTAL="0", CARGO_PROFILE_DEV_DEBUG="0", CARGO_PROFILE_TEST_DEBUG="0",
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
        GIT_GRAFT_FILE=os.devnull, GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1",
        GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    tools = config["tools"]
    environment["PATH"] = os.pathsep.join(dict.fromkeys([str(Path(x["path"]).parent) for x in tools.values()] + ["/usr/bin", "/bin"]))
    observations = []
    report = {"schema": "motionwright.audio-live-preparation/1", "status": "INCOMPLETE", "context": current,
              "clock": clock, "automatic_retry": False, "commands": observations, "cases_run": 0,
              "full_sdk_g0": "NOT_CLAIMED", "physical_case": "NOT_RUN"}
    phase_deadline = min(clock["work_deadline_ns"], clock["started_ns"] + 900 * 1_000_000_000)
    def call(argv, cwd, env, folder, label, seconds, ctx):
        cap = audio_live_remaining(clock, seconds, time.monotonic_ns(), phase_deadline=phase_deadline)
        row = observer.command(argv, cwd, env, folder, label, cap, ctx, (evidence, observations))
        require(time.monotonic_ns() < phase_deadline, "audio-live-command-returned-after-deadline")
        return row
    def checked(argv, label, seconds=45, cwd=gate):
        row = call(argv, cwd, environment, logs, label, seconds, current)
        require(type(row["exit_code"]) is int and row["exit_code"] == 0,
                "audio-live-preparation-command-failed:" + label)
        return row
    try:
        report["resource_before"] = audio_resource_preflight(root)
        gate_paths = tuple(p for p in audio_input_paths(store, gate, current["source_sha"])
                           if p.startswith("tooling/") or p in ("SOURCE_LOCK.json", ".github/workflows/native-mlt-sequence-e2e.yml"))
        gate_before = store.source_snapshot(gate, current["source_sha"], gate_paths)
        require(all(gate_before["files"][p]["sha256"] == digest for p,digest in AUDIO_LIVE_PROVIDER_PINS.items()),
                "audio-live-shared-source-pin")
        for name, descriptor in tools.items():
            require(audio_live_fingerprint(Path(descriptor["path"])) == {k:descriptor[k] for k in ("path", "bytes", "sha256", "identity")},
                    "audio-live-runtime-tool-changed:" + name)
            flag = "-version" if name in {"ffmpeg", "ffprobe"} else "--version"
            row = checked([descriptor["path"], flag], "tool-" + name)
            version = observer.original_stream(logs, row, "stdout") + observer.original_stream(logs, row, "stderr")
            require(version.decode("utf-8").strip() == descriptor["expected_version"].strip(),
                    "audio-live-runtime-tool-version:" + name)
        python = str(Path(sys.executable).absolute())
        checked([python, "-I", "-B", "-X", "utf8", "-c",
                 "import sys,tomllib,jsonschema,yaml; assert sys.version_info>=(3,12); print('SDK_PREREQUISITES')"],
                "sdk-prerequisites")
        git = tools["git"]["path"]
        checked([git, "init", str(sdk)], "sdk-init")
        checked([git, "-C", str(sdk), "remote", "add", "origin", "https://github.com/seradotcom/semwright.git"], "sdk-origin")
        checked([git, "-C", str(sdk), "fetch", "--depth=2", "origin", selected.AUDIO_SOURCE_SHA], "sdk-fetch", 180)
        checked([git, "-C", str(sdk), "checkout", "--detach", selected.AUDIO_SOURCE_SHA], "sdk-checkout", 60)
        before = store.source_snapshot(sdk, selected.AUDIO_SOURCE_SHA,
                                      audio_input_paths(store, sdk, selected.AUDIO_SOURCE_SHA), selected.AUDIO_SOURCE_TREE)
        require(before["parents"] == [selected.AUDIO_SOURCE_PARENT]
                and all(before["files"][p]["sha256"] == digest for p,digest in selected.AUDIO_SOURCE_PINS.items()),
                "audio-live-exact-sdk-source")
        report["sources"] = {"gate": gate_before, "sdk": before}
        report["source_bundle"] = audio_live_source_bundle(gate, sdk, report["sources"], evidence / "sources.tar")
        rustup = tools["rustup"]["path"]
        checked([rustup, "toolchain", "install", "1.98.1", "--profile", "minimal", "--component", "rustfmt",
                 "--component", "clippy", "--no-self-update"], "owned-toolchain", 600, sdk)
        rust_tools = {}
        for name in ("cargo", "rustc", "rustdoc", "cargo-fmt", "rustfmt", "cargo-clippy", "clippy-driver"):
            row = checked([rustup, "which", "--toolchain", "1.98.1", name], "rust-path-" + name, cwd=sdk)
            path = Path(observer.original_stream(logs, row, "stdout").decode().strip())
            require(path.is_absolute() and path.resolve(strict=True) == path and path.is_relative_to(root / "rustup-home"),
                    "audio-live-rust-tool-outside-owned-root")
            version_row = checked([str(path), "-Vv" if name == "rustc" else "--version"], "rust-version-" + name, cwd=sdk)
            version = observer.original_stream(logs, version_row, "stdout").decode()
            require(version.strip() and (name != "rustc" or "release: 1.98.1\n" in version
                    and "host: x86_64-unknown-linux-gnu\n" in version), "audio-live-rust-version")
            rust_tools[name] = {**audio_live_fingerprint(path), "version": version}
        environment.update(RUSTC=rust_tools["rustc"]["path"], RUSTDOC=rust_tools["rustdoc"]["path"],
                           RUSTFMT=rust_tools["rustfmt"]["path"])
        environment["PATH"] = str(Path(rust_tools["cargo"]["path"]).parent) + os.pathsep + environment["PATH"]
        checked([python, "-I", "-B", "-X", "utf8", str(sdk / "scripts/verify-oss-hygiene.py")], "sdk-hygiene", 180, sdk)
        checked([python, "-I", "-B", "-X", "utf8", str(sdk / "scripts/verify-source.py")], "sdk-source", 300, sdk)
        checked([rust_tools["cargo-fmt"]["path"], "fmt", "--all", "--", "--check"], "sdk-format", 180, sdk)
        report["sdk_g0"] = "PASS_HYGIENE_SOURCE_FORMAT"
        barrier = sdk_gate.ProducerBarrier(call, rust_tools["cargo"]["path"])
        report["producer"] = audio_live_prepare_producers(sdk_gate, barrier, rust_tools["cargo"]["path"],
                                                         sdk, environment, logs, current, root / "target")
        require(store.source_snapshot(gate, current["source_sha"], gate_paths) == gate_before
                and store.source_snapshot(sdk, selected.AUDIO_SOURCE_SHA, tuple(before["files"]), selected.AUDIO_SOURCE_TREE) == before,
                "audio-live-preparation-source-postcheck")
        sdk_gate.verify_commands(evidence, observations)
        report["rust_tools"] = rust_tools
        report["status"] = "PASS_PREPARATION_ONLY"
    except (observer.UnknownExecution, store.UnknownObservation) as error:
        report.update(status="UNKNOWN_NO_RETRY", original_command=getattr(error, "command", None), error=str(error))
    except BaseException as error:
        report.update(status="FAIL_PREPARATION", error=type(error).__name__ + ":" + str(error))
    report["finished_ns"] = time.monotonic_ns()
    new_json(evidence / "preparation.json", report)
    # The enclosing original process/namespace terminal remains independently required.
    return 0 if report["status"] == "PASS_PREPARATION_ONLY" else 2


def audio_live_preparation_readback(root, expected_context, clock):
    raw = audio_live_read(root / "evidence/preparation.json", 8 * 1024**2)
    # The large source inventory stays in its original; IPC carries only the
    # already bounded roles needed by the controller.
    import startup_capture_launcher as L
    value = L.strict_json(raw, 8 * 1024**2)
    require(value.get("schema") == "motionwright.audio-live-preparation/1"
            and value.get("context") == expected_context and value.get("clock") == clock,
            "audio-live-preparation-original-binding")
    result = {"status": value.get("status"), "preparation_sha256": sha(raw),
              "cases_run": value.get("cases_run"), "sdk_g0": value.get("sdk_g0")}
    if value.get("status") == "PASS_PREPARATION_ONLY":
        require(value["producer"]["barrier"] == "OBSERVING"
                and value["producer"]["producers_closed"] is True
                and value["producer"]["prepared"]["clippy"] == "PASS"
                and value["cases_run"] == 0, "audio-live-producer-closure-not-sealed")
        result.update(sealed=value["producer"]["sealed"], rosters=value["producer"]["rosters"])
        result["kernel_identities"] = {label: {name: list(metadata_identity(Path(binary["absolute_path"]).lstat()))
            for name,binary in collection.items()} for label,collection in result["sealed"].items()}
    return result


def audio_live_runtime_and_volume(gate, root, admitted, bootstrap, environment, uid, gid):
    """Only the existing module installer and one newly owned quota mount."""
    import time
    import ci_sdk_native_source as sdk_gate
    clock = bootstrap["clock"]
    selected.audio_live_clock(clock["started_ns"], time.monotonic_ns())
    runtime = audio_live_runtime_contract(admitted["request"]["runtime"])
    dispatch = {"schema": SOURCE_DISPATCH_SCHEMA, "motionwright_sha": admitted["context"]["source_sha"],
        "semwright_sha": admitted["context"]["semwright_sha"], "official_runtime": runtime["official_runtime"],
        "compiler": runtime["compiler"], "recipient": admitted["recipient"]}
    validate_dispatch(json_bytes(dispatch), admitted["context"])
    intake = root / "evidence/runtime-intake"; intake.mkdir(mode=0o700)
    official = dispatch["official_runtime"]
    archive = download_artifact(official["artifact"], "official", intake)
    raw = zip_member(archive, "distribution/official-runtime-receipt.json", official["receipt_sha256"], MAX_JSON)
    g2.verify_official_runtime_bindings(strict(raw), official["known_original_module_sha256"], official["known_melt_sha256"])
    new_bytes(intake / "official-runtime-receipt.json", raw)
    intake_compiler(dispatch, gate, intake)
    # This environment authorizes nothing by itself; the exact closed row was
    # admitted before entering this worker. The original GitHub metadata stays.
    old_environment = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update({key: environment[key] for key in ("GITHUB_ACTIONS", "CI", "RUNNER_OS", "RUNNER_TEMP")})
        os.environ["MOTIONWRIGHT_APPROVED_MLT_G2"] = "1"
        proof = admission.preflight(intake / "original_c_source", intake / "repaired_c_source",
            intake / "compiled_module", g2.REPAIRED_MODULE_SHA256, intake / "compiler_receipt_path",
            g2.COMPILER_RECEIPT_SHA256, official["known_original_module_sha256"])
        installed = Path(environment["RUNNER_TEMP"]) / (root.name + "-installation")
        selected.audio_live_clock(clock["started_ns"], time.monotonic_ns())
        receipt = admission.install(proof, intake / "compiled_module", installed)
    finally:
        os.environ.clear(); os.environ.update(old_environment)
    for name in ("installation.json", "original_system_mlt_avformat.so"):
        new_bytes(intake / name, audio_live_read(installed / name, MAX_FILE))
    volume = root / "volume"
    require(volume.resolve(strict=True) == volume and not any(volume.iterdir())
            and volume.lstat().st_uid == uid, "audio-live-private-mountpoint-not-fresh")
    plan = admitted["plan"]
    mount = bootstrap["tools"]["mount"]
    require(audio_live_fingerprint(Path(mount["path"])) == {k:mount[k] for k in ("path", "bytes", "sha256", "identity")},
            "audio-live-mount-binary-changed")
    # Five extra inodes account for the volume, evidence, auxiliary/home/tmp.
    options = f"nodev,nosuid,mode=0700,uid={uid},gid={gid},size={plan['working_bytes']},nr_inodes={plan['inode_ceiling'] + 5}"
    logs = root / "evidence/commands"
    cap = audio_live_remaining(clock, 30, time.monotonic_ns())
    row = sdk_gate.observer.command([mount["path"], "-t", "tmpfs", "-o", options, "tmpfs", str(volume)],
        root, {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, logs, "mount-private-evidence", cap, admitted["context"])
    require(type(row["exit_code"]) is int and row["exit_code"] == 0, "audio-live-private-quota-mount-failed")
    actual = audio_live_mount_readback(volume, plan, uid, gid)
    for path in (volume / "evidence", volume / "auxiliary", volume / "auxiliary/home", volume / "auxiliary/tmp"):
        path.mkdir(mode=0o700); os.chown(path, uid, gid)
    live_root = volume / "evidence"
    require(not any(live_root.iterdir()), "audio-live-evidence-root-preused")
    return {"installation": receipt, "installation_original_root": str(installed), "mount": actual,
            "evidence_root": str(live_root), "evidence_identity": list(metadata_identity(live_root.lstat())),
            "auxiliary_root": str(volume / "auxiliary"), "resource_before_live": audio_resource_preflight(root),
            "cleanup_policy": "RETAIN_MOUNT_AND_ORIGINALS_FOR_INDEPENDENT_RECOVERY"}


def audio_live_mount_readback(volume, plan, uid, gid):
    raw = Path("/proc/self/mountinfo").read_bytes()
    require(len(raw) <= 1024 * 1024, "audio-live-mountinfo-bound")
    rows = []
    for line in raw.decode("utf-8").splitlines():
        left, delimiter, right = line.partition(" - ")
        require(delimiter, "audio-live-invalid-mountinfo")
        fields = left.split(); suffix = right.split()
        if len(fields) >= 6 and fields[4] == str(volume):
            rows.append((fields, suffix))
    require(len(rows) == 1 and rows[0][1][0] == "tmpfs"
            and {"rw", "nosuid", "nodev"} <= set(rows[0][0][5].split(",")),
            "audio-live-exact-private-tmpfs-required")
    info = volume.lstat(); fs = os.statvfs(volume)
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and info.st_gid == gid
            and stat.S_IMODE(info.st_mode) == 0o700
            and 0 < fs.f_blocks * fs.f_frsize <= plan["working_bytes"]
            and 0 < fs.f_files <= plan["inode_ceiling"] + 5,
            "audio-live-kernel-quota-not-observed")
    return {"mount_id": rows[0][0][0], "device": rows[0][0][2], "root_identity": list(metadata_identity(info)),
            "filesystem": "tmpfs", "byte_ceiling": fs.f_blocks * fs.f_frsize, "inode_ceiling": fs.f_files,
            "mountinfo_sha256": sha(raw), "volume": str(volume)}


def audio_live_inventory(root, plan):
    """Inspect the complete working root. Unplanned bytes remain in place."""
    root = Path(root); before = root.lstat()
    require(root.is_dir() and root.resolve(strict=True) == root and not root.is_symlink(), "audio-live-original-root-invalid")
    expected = {item["root"] + "/" + item["path"]: item for item in plan["entries"]}
    rows, failures, directories, total = {}, [], [], 0
    expected_directories = set(selected.AUDIO_LIVE_ROOTS)
    for name in expected:
        parts = name.split("/")
        expected_directories.update("/".join(parts[:index]) for index in range(1, len(parts)))
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs.sort(); files.sort()
        for name in [*dirs, *files]:
            path = Path(current) / name; relative_name = path.relative_to(root).as_posix()
            require(len(rows) + len(directories) < MAX_FILES * 4, "audio-live-namespace-overflow")
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode) and not path.is_symlink():
                directories.append(relative_name)
                if relative_name not in expected_directories:
                    failures.append({"path": relative_name, "status": "UNPLANNED_DIRECTORY"})
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                failures.append({"path": relative_name, "status": "NONREGULAR_OR_ALIAS_NOT_OPENED"})
                continue
            total += info.st_size
            row = expected.get(relative_name)
            status = ("INVENTORIED" if row is not None and info.st_size <= row["max_bytes"] else
                      "UNPLANNED_OR_OVERFLOW")
            rows[relative_name] = {"bytes": info.st_size, "identity": list(metadata_identity(info)), "status": status}
            if status != "INVENTORIED":
                failures.append({"path": relative_name, "status": status})
    for name, row in expected.items():
        if name not in rows:
            state = "REQUIRED_ABSENT" if row["required"] else "OPTIONAL_ABSENT"
            if row["required"]:
                failures.append({"path": name, "status": state})
            rows[name] = {"status": state}
    for name in selected.AUDIO_LIVE_ROOTS:
        if name not in directories:
            failures.append({"path": name, "status": "REQUIRED_ROOT_ABSENT"})
    require(metadata_identity(root.lstat()) == metadata_identity(before), "audio-live-root-namespace-changed")
    if (total > plan["working_bytes"] or len([r for r in rows.values() if "bytes" in r]) > plan["regular_files"]
            or len(directories) > plan["directories"]):
        failures.append({"status": "WHOLE_OPERATING_PLAN_EXCEEDED"})
    return {"root_identity": list(metadata_identity(before)), "rows": rows, "directories": directories,
            "total_bytes": total, "failures": failures, "complete": not failures,
            "truncation_or_omission_permitted": False}


def audio_live_pack_retention(root, destination, maximum):
    """One bounded package of every preparation/control original, no cache."""
    files = []
    for folder in (root / "evidence", root / "control"):
        for current, dirs, names in os.walk(folder, followlinks=False):
            dirs.sort(); names.sort()
            require(all(not (Path(current) / p).is_symlink() for p in dirs), "audio-live-retention-directory-alias")
            for name in names:
                path = Path(current) / name
                files.append(path)
                require(len(files) <= MAX_FILES, "audio-live-retention-file-count")
    stream = io.BytesIO(); inventory = []; total = 0
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for path in sorted(files):
            raw = audio_live_read(path, MAX_FILE); total += len(raw)
            require(total <= maximum, "audio-live-retention-byte-reserve-exceeded")
            name = path.relative_to(root).as_posix(); safe_member(name)
            member = tarfile.TarInfo(name); member.mode = 0o400; member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
            inventory.append({"path": name, "bytes": len(raw), "sha256": sha(raw)})
        index = json_bytes({"schema": "motionwright.audio-live-retained-original-index/1", "files": inventory})
        require(len(index) <= MAX_JSON, "audio-live-retention-index-bound")
        member = tarfile.TarInfo("retention-index.json"); member.mode = 0o400; member.size = len(index)
        archive.addfile(member, io.BytesIO(index))
    raw = stream.getvalue()
    require(len(raw) <= min(maximum, MAX_FILE), "audio-live-retention-tar-overhead")
    new_bytes(destination, raw)
    return {"bytes": len(raw), "sha256": sha(raw), "files": len(inventory)}


def audio_live_inner_archive(raw, index_name, *, maximum):
    """Read retained bytes without extracting paths or trusting a TAR's names."""
    require(type(raw) is bytes and len(raw) <= maximum <= MAX_FILE, "audio-live-inner-archive-bound")
    files = {}; total = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for member in archive:
            safe_member(member.name)
            require(member.name not in files and len(files) < MAX_FILES + 1 and member.isreg()
                    and member.type in (tarfile.REGTYPE, tarfile.AREGTYPE) and not member.pax_headers
                    and 0 <= member.size <= MAX_FILE, "audio-live-inner-archive-member")
            stream = archive.extractfile(member)
            require(stream is not None, "audio-live-inner-archive-unreadable")
            data = stream.read(member.size + 1)
            require(len(data) == member.size, "audio-live-inner-archive-short")
            total += len(data); require(total <= maximum, "audio-live-inner-archive-total")
            files[member.name] = data
    require(index_name in files, "audio-live-inner-index-missing")
    return files


def audio_live_preparation_intake(raw, record, expected_context):
    """Bind the existing producer/source originals before accepting a native PASS."""
    import hashlib
    expected = record["retained_preparation"]
    require(type(expected["bytes"]) is int and len(raw) == expected["bytes"]
            and sha(raw) == expected["sha256"], "audio-live-retained-preparation-binding")
    files = audio_live_inner_archive(raw, "retention-index.json", maximum=MAX_FILE)
    index = strict(files.pop("retention-index.json"))
    require(index.get("schema") == "motionwright.audio-live-retained-original-index/1"
            and type(index.get("files")) is list, "audio-live-retained-index-schema")
    rows = {}
    for row in index["files"]:
        require(type(row) is dict and set(row) == {"path", "bytes", "sha256"}
                and row["path"] not in rows and row["path"] in files
                and type(row["bytes"]) is int and row["bytes"] == len(files[row["path"]])
                and row["sha256"] == sha(files[row["path"]]), "audio-live-retained-original-changed")
        rows[row["path"]] = row
    require(set(rows) == set(files) and len(rows) == expected["files"], "audio-live-retained-roster")
    request = strict(files["evidence/request.original.json"])
    require(sha(json_bytes(request)) == record["request_sha256"], "audio-live-retained-request-changed")
    if record["native_status"] != "PASS_ONE_EXACT_IGNORED_METHOD":
        return {"status": "PARTIAL_OR_FAILURE_ORIGINALS_ONLY", "files": len(rows)}
    import ci_sdk_native_source as sdk_gate
    import startup_capture_launcher as L
    report = L.strict_json(files["evidence/preparation.json"], 8 * 1024**2)
    require(report["context"] == expected_context and report["clock"] == record["clock"]
            and report["status"] == "PASS_PREPARATION_ONLY" and report["sdk_g0"] == "PASS_HYGIENE_SOURCE_FORMAT"
            and report["producer"]["prepared"]["clippy"] == "PASS"
            and report["producer"]["barrier"] == "OBSERVING"
            and report["producer"]["prepared"]["steps"] == ["normal", "tests", "sandbox"],
            "audio-live-retained-preparation-not-passed")
    sources = audio_live_inner_archive(files["evidence/sources.tar"], "source-index.json", maximum=MAX_FILE)
    source_index = L.strict_json(sources.pop("source-index.json"), 8 * 1024**2)
    require(source_index == report["sources"] and set(source_index) == {"gate", "sdk"}
            and source_index["gate"]["sha"] == expected_context["source_sha"]
            and source_index["sdk"]["sha"] == selected.AUDIO_SOURCE_SHA
            and source_index["sdk"]["tree"] == selected.AUDIO_SOURCE_TREE
            and source_index["sdk"]["parents"] == [selected.AUDIO_SOURCE_PARENT],
            "audio-live-retained-source-context")
    roster = {}
    for label,snapshot in source_index.items():
        for path, identity in snapshot["files"].items():
            name = label + "/" + path; safe_member(name)
            require(name in sources and identity["sha256"] == sha(sources[name])
                    and identity["bytes"] == len(sources[name])
                    and identity["git_blob_oid"] == hashlib.sha1(b"blob " + str(len(sources[name])).encode()
                        + b"\0" + sources[name]).hexdigest(), "audio-live-retained-source-blob")
            roster[name] = identity
    require(set(roster) == set(sources), "audio-live-retained-source-roster")
    for path,digest in AUDIO_LIVE_PROVIDER_PINS.items():
        require(sha(sources["gate/" + path]) == digest, "audio-live-retained-provider-pin")
    for path,digest in selected.AUDIO_SOURCE_PINS.items():
        require(sha(sources["sdk/" + path]) == digest, "audio-live-retained-sdk-pin")
    observations = report["commands"]; commands = {}
    for item in observations:
        row = item["command"]; folder = "evidence/" + item["folder"] + "/"
        receipt_name = folder + Path(item["receipt"]["path"]).name
        require(receipt_name in files and sha(files[receipt_name]) == item["receipt"]["sha256"]
                and L.strict_json(files[receipt_name], 8 * 1024**2) == row
                and row["context"] == expected_context and row["observation"] == "DIRECT_PROCESS_EXIT_OBSERVED"
                and row["log_custody"] == "VERIFIED_SNAPSHOT" and type(row["exit_code"]) is int and row["exit_code"] == 0,
                "audio-live-retained-command-identity")
        for channel in ("stdout", "stderr"):
            channel_name = folder + row[channel]["path"]
            require(channel_name in files and len(files[channel_name]) == row[channel]["bytes"]
                    and sha(files[channel_name]) == row[channel]["sha256"], "audio-live-retained-command-stream")
        label = Path(receipt_name).name.removesuffix(".command.json")
        require(label not in commands, "audio-live-duplicate-command-receipt")
        commands[label] = (row, files[folder + row["stdout"]["path"]], files[folder + row["stderr"]["path"]])
    sdk = Path(source_index["sdk"]["checkout"])
    target = Path(report["producer"]["sealed"]["normal"]["semwright-mlt-video-driver"]["absolute_path"]).parent.parent
    cargo = report["rust_tools"]["cargo"]["path"]
    specs = selected.audio_live_binary_specs()
    for label, argv in selected.audio_live_producers(cargo):
        row, stdout, _ = commands["producer-" + label]
        require(row["argv"] == argv and row["cwd"] == str(sdk), "audio-live-retained-producer-argv")
        paths = sdk_gate.select_binary_paths(stdout, row["exit_code"], sdk, target, specs[label], testing=label == "tests")
        for name, path in paths.items():
            binary = report["producer"]["sealed"][label][name]
            original = files["evidence/binaries/" + binary["sha256"]]
            require(binary["absolute_path"] == path and len(original) == binary["bytes"]
                    and sha(original) == binary["sha256"], "audio-live-retained-compiled-binary")
    binary = report["producer"]["sealed"]["tests"]["live"]["absolute_path"]
    for label,extra in (("all", []), ("ignored", ["--ignored"])):
        row,stdout,stderr = commands["list-live-" + label]
        require(row["argv"] == [binary, "--list", *extra, "--format", "pretty"] and not stderr,
                "audio-live-retained-list-argv")
        actual = selected.audio_source_list(stdout, row["exit_code"], "live", ignored_only=label == "ignored")
        require(json_bytes(actual) == json_bytes(report["producer"]["rosters"][label]), "audio-live-retained-list-roster")
    require(json_bytes(report["producer"]["rosters"]["ignored"]) == json_bytes(record["compiled_ignored_roster"]),
            "audio-live-retained-ignored-roster-changed")
    return {"status": "SOURCE_PRODUCER_AND_BINARY_ORIGINALS_BOUND", "files": len(rows),
            "source_blob_count": len(roster), "native_reexecuted": False}


def audio_live_snapshot_readback(raw, *, expected_context, expected_plan, expected_request_sha):
    """Independent plaintext intake binds the original external plan, not its self-description."""
    require(type(raw) is bytes and 0 < len(raw) <= MAX_TOTAL + MAX_FILES * 4096,
            "audio-live-capsule-bound")
    payload = {}; total = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for member in archive:
            safe_member(member.name)
            require(member.name not in payload and len(payload) <= MAX_FILES
                    and member.isreg() and member.type in (tarfile.REGTYPE, tarfile.AREGTYPE)
                    and not member.pax_headers and 0 <= member.size <= MAX_FILE,
                    "audio-live-capsule-member")
            stream = archive.extractfile(member); require(stream is not None, "audio-live-member-unreadable")
            data = stream.read(member.size + 1); require(len(data) == member.size, "audio-live-member-size")
            total += len(data); require(total <= MAX_TOTAL + MAX_JSON, "audio-live-capsule-total")
            payload[member.name] = data
    manifest = strict(payload.pop("snapshot-manifest.json", b""))
    require(manifest.get("schema") == "motionwright.ci.private-snapshot/1"
            and type(manifest.get("selected_originals_complete")) is bool,
            "audio-live-existing-snapshot-schema")
    listed = {}
    for root_name, root in manifest["roots"].items():
        require(root_name in {"working", "retained"}, "audio-live-capsule-root")
        for row in root.get("files", []):
            if "observation" in row:
                name = row["archive_path"]; observed = row["observation"]
                require(name not in listed and name in payload and observed["sha256"] == sha(payload[name])
                        and observed["size"] == len(payload[name]), "audio-live-capsule-original-binding")
                listed[name] = observed
    require(set(payload) == set(listed), "audio-live-unlisted-or-missing-payload")
    record = strict(payload.get("retained/result.json", b""))
    require(record.get("schema") == "motionwright.audio-live-local-custody/1"
            and record.get("context") == expected_context
            and record.get("request_sha256") == expected_request_sha
            and record.get("operating_plan_sha256") == sha(json_bytes(expected_plan)),
            "audio-live-external-plan-context-required")
    plan = selected.audio_live_plan(expected_plan)
    roster = {"working/evidence/" + row["root"] + "/" + row["path"]: row for row in plan["entries"]}
    working = {name: data for name,data in payload.items() if name.startswith("working/")}
    complete = (manifest["selected_originals_complete"] and record.get("inventory_complete") is True
                and record.get("source_unchanged") is True and record.get("secondary_custody_errors") == [])
    complete &= all(name in roster and len(data) <= roster[name]["max_bytes"] for name,data in working.items())
    complete &= all(not row["required"] or name in working for name,row in roster.items())
    complete &= sum(map(len, working.values())) <= plan["working_bytes"] and len(working) <= plan["regular_files"]
    require(record.get("selected_originals_complete") is complete,
            "audio-live-inconsistent-completeness-claim")
    preparation = audio_live_preparation_intake(payload["retained/preparation.tar"], record, expected_context)
    require(len(payload["retained/preparation.tar"]) <= plan["retention_bytes"]
            and preparation["files"] + preparation.get("source_blob_count", 0) + 16 <= plan["retention_files"],
            "audio-live-retained-original-budget")
    admitted = None
    if record.get("native_status") == "PASS_ONE_EXACT_IGNORED_METHOD":
        require(complete, "audio-live-pass-with-incomplete-originals")
        native = record["native_observation"]
        for name, expected in native["raw"].items():
            original = payload["retained/live." + name + ".original"]
            require(type(expected["bytes"]) is int and len(original) == expected["bytes"]
                    and sha(original) == expected["sha256"], "audio-live-process-original-changed")
        require(payload["retained/live.lifetime.original"] == b""
                and payload["retained/live.ready.original"] == b"READY\n"
                and [strict(line) for line in payload["retained/live.status.original"].splitlines()]
                    == native["status_rows"], "audio-live-process-protocol-originals")
        terminal = selected.audio_live_terminal(payload["retained/live.stdout.original"], payload["retained/live.stderr.original"],
                                                native["bubblewrap_exit_code"], record["compiled_ignored_roster"])
        admitted = selected.audio_live_join(native, terminal)
    return {"schema": "motionwright.audio-live-offline-intake/1", "context": expected_context,
            "selected_originals_complete": complete, "native": admitted, "preparation": preparation,
            "native_status": record.get("native_status"), "automatic_retry": False,
            "formal_acceptance": False, "human_acceptance": False}


def audio_live_manifest_bound(plan, root):
    """Bound the real shared snapshot schema, including its three stat maps."""
    maximum = 2**127 - 1
    metadata = {key: maximum for key in ("device", "inode", "size", "mode", "uid", "gid", "mtime_ns", "ctime_ns")}
    entries = ["evidence/" + row["root"] + "/" + row["path"] for row in plan["entries"]]
    roots = {}
    for label, paths, prefix in (("working", entries, root / "volume"),
            ("retained", ["x" * 160 + str(i) for i in range(16)], root / "retained")):
        rows = []
        for path in paths:
            require(len(str(prefix / path).encode()) <= 1024, "audio-live-observation-path-bound")
            rows.append({"archive_path": label + "/" + path, "observation": {
                "path": str(prefix / path), "sha256": "f" * 64, "size": maximum,
                "before": metadata, "after": metadata, "namespace_after": metadata}})
        roots[label] = {"status": "CAPTURED", "files": rows}
    bound = json_bytes({"schema": "motionwright.ci.private-snapshot/1", "scope": "selected-original-files-at-read-time",
                       "roots": roots, "selected_originals_complete": False, "complete_owner_state_claimed": False})
    require(len(bound) <= MAX_JSON, "audio-live-snapshot-manifest-budget-before-effects")
    return len(bound)


def audio_live_retention_footprint(root, plan, preparation):
    files, size = [], 0
    for folder in (root / "control", root / "evidence"):
        for current, directories, names in os.walk(folder, followlinks=False):
            require(all(not (Path(current) / name).is_symlink() for name in directories),
                    "audio-live-retention-directory-alias")
            for name in names:
                path = Path(current) / name
                info = path.lstat()
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "audio-live-retention-nonregular")
                size += info.st_size; files.append(path)
    # The source TAR does not erase the count of original blobs inside it.
    source_count = sum(len(preparation["sources"][name]["files"]) for name in ("gate", "sdk"))
    reserve = 8 * 1024**2  # stdout/stderr maxima, controller records and TAR framing.
    require(size + reserve <= plan["retention_bytes"]
            and len(files) + source_count + 16 <= plan["retention_files"],
            "audio-live-retention-reserve-does-not-cover-actual-inputs")
    return {"observed_bytes": size, "observed_original_count": len(files) + source_count,
            "reserved_additional_bytes": reserve, "original_count_not_reduced_by_packaging": True}


def audio_live_worker_record(value):
    result = {key: item for key,item in value.items() if key not in {"result_prefix", "raw"}}
    if "result_prefix" in value:
        result["result_prefix"] = {"bytes": len(value["result_prefix"]), "sha256": sha(value["result_prefix"])}
    if "raw" in value:
        result["raw"] = {name: {"bytes": len(raw), "sha256": sha(raw)} for name,raw in value["raw"].items()}
    return result


def audio_live_seal(root, admitted, stages, native_status, compiled_roster, clock, uid, gid):
    """A bounded child retains partial originals, then calls the existing cipher once."""
    import time
    import ci_sdk_native_source as sdk_gate
    require(time.monotonic_ns() < clock["seal_deadline_ns"], "audio-live-no-seal-after-original-deadline")
    retained = root / "retained"; retained.mkdir(mode=0o700)
    for label, observation in stages.items():
        if "raw" in observation:
            for name, raw in observation["raw"].items():
                new_bytes(retained / (label + "." + name + ".original"), raw)
        if "result_prefix" in observation:
            new_bytes(root / "control" / (label + ".worker-prefix.original"), observation["result_prefix"])
    source_stable = False; errors = []
    report_path = root / "evidence/preparation.json"
    preparation = None
    if report_path.is_file():
        import startup_capture_launcher as L
        preparation = L.strict_json(audio_live_read(report_path, 8 * 1024**2), 8 * 1024**2)
        if "sources" in preparation:
            try:
                store = sdk_gate.custody
                for snapshot in preparation["sources"].values():
                    require(store.source_snapshot(Path(snapshot["checkout"]), snapshot["sha"], tuple(snapshot["files"]), snapshot["tree"]) == snapshot,
                            "audio-live-final-source-changed")
                if "producer" in preparation:
                    for collection in preparation["producer"]["sealed"].values():
                        sdk_gate.verify_binaries(collection, root / "target")
                config = strict(audio_live_read(root / "control/worker.json", MAX_JSON))
                for descriptor in config["tools"].values():
                    require(audio_live_fingerprint(Path(descriptor["path"])) ==
                            {key:descriptor[key] for key in ("path", "bytes", "sha256", "identity")},
                            "audio-live-final-runtime-tool-changed")
                for descriptor in preparation.get("rust_tools", {}).values():
                    require(audio_live_fingerprint(Path(descriptor["path"])) ==
                            {key:descriptor[key] for key in ("path", "bytes", "sha256", "identity")},
                            "audio-live-final-rust-tool-changed")
                if "runtime" in stages and stages["runtime"].get("status") == "OBSERVED":
                    installed = stages["runtime"]["value"]["installation"]
                    require(sha(audio_live_read(Path(installed["installed_path"]), MAX_FILE))
                            == installed["actual_installed_module_sha256"] == g2.REPAIRED_MODULE_SHA256,
                            "audio-live-final-installed-module-changed")
                sdk_gate.verify_commands(root / "evidence", preparation["commands"])
                source_stable = True
            except BaseException as error:
                errors.append(type(error).__name__ + ":" + str(error))
    inventory = {"complete": False, "failures": [{"status": "LIVE_ROOT_NOT_CREATED"}], "rows": {}}
    live_root = root / "volume/evidence"
    if live_root.is_dir():
        try:
            inventory = audio_live_inventory(live_root, admitted["plan"])
            # The auxiliary home/tmp namespace is also retained. Any leaf there
            # is outside the closed operating plan and prevents completeness.
            if any(p.is_file() or p.is_symlink() for p in (root / "volume/auxiliary").rglob("*")):
                inventory["complete"] = False
                inventory["failures"].append({"status": "UNPLANNED_AUXILIARY_ORIGINALS"})
        except BaseException as error:
            errors.append(type(error).__name__ + ":" + str(error))
            inventory["complete"] = False
    new_json(root / "control/final-inventory.json", inventory)
    new_json(root / "control/process-stages.json", {name: audio_live_worker_record(value) for name,value in stages.items()})
    packed = audio_live_pack_retention(root, retained / "preparation.tar", admitted["plan"]["retention_bytes"])
    complete = inventory["complete"] and source_stable and not errors
    # A durable prefix does not become a successful or complete native command.
    status = native_status if complete else "UNKNOWN_OR_INCOMPLETE_CUSTODY"
    record = {"schema": "motionwright.audio-live-local-custody/1", "context": admitted["context"], "clock": clock,
        "request_sha256": sha(json_bytes(admitted["request"])), "operating_plan_sha256": sha(json_bytes(admitted["request"]["plan"])),
        "native_status": status, "native_observation": audio_live_worker_record(stages.get("live", {})),
        "compiled_ignored_roster": compiled_roster, "inventory_complete": inventory["complete"],
        "selected_originals_complete": complete, "source_unchanged": source_stable,
        "secondary_custody_errors": errors, "retained_preparation": packed,
        "automatic_retry": False, "provider_graceful_shutdown": "NOT_INFERRED",
        "global_process_tree_closed": False, "formal_acceptance": False, "human_acceptance": False}
    new_json(retained / "result.json", record)
    roots = {"retained": retained}
    if (root / "volume/evidence").is_dir():
        roots["working"] = root / "volume"
    plaintext, snapshot_complete = gather_snapshot(roots, preserve_partial=True)
    # Validate the actual schema/manifest/payload before encryption. Incomplete
    # snapshots are preserved but cannot be promoted to the test verdict.
    if snapshot_complete is not True and complete:
        raise CustodyRejected("audio-live-snapshot-became-incomplete; originals-retained-no-retry")
    intake = audio_live_snapshot_readback(plaintext, expected_context=admitted["context"],
        expected_plan=admitted["request"]["plan"], expected_request_sha=sha(json_bytes(admitted["request"])))
    envelope = encrypt_snapshot(plaintext, admitted["recipient"], admitted["context"])
    path = root / "export/originals.mwci"
    import startup_capture_launcher as L
    L._write_new(path, envelope)
    require(audio_live_read(path, MAX_TOTAL + MAX_FILES * 4096 + 8256) == envelope,
            "audio-live-cipher-readback-changed")
    public = {"schema": "motionwright.audio-live-public/1", "context": admitted["context"], "clock": clock,
        "scope": selected.AUDIO_LIVE_SCOPE, "native_status": record["native_status"],
        "ciphertext_sha256": sha(envelope), "ciphertext_bytes": len(envelope),
        "recipient_spki_sha256": selected.AUDIO_LIVE_RECIPIENT,
        "selected_originals_complete": intake["selected_originals_complete"],
        "source_unchanged": source_stable, "delivery_verified": False,
        "automatic_retry": False, "human_acceptance": False, "release_authorized": False}
    owner = (uid, gid)
    require(all(type(value) is int and value > 0 for value in owner), "audio-live-export-owner")
    os.chown(path, *owner)
    new_bytes(root / "export/public.json", json_bytes(public), owner=owner, mode=0o400)
    require(time.monotonic_ns() < clock["seal_deadline_ns"], "audio-live-seal-returned-after-original-deadline")
    return public


def audio_live_namespace_closed(value):
    fields = ("deadline_unchanged", "complete_before_cleanup_deadline", "namespace_init_authenticated",
              "namespace_init_exited", "command_authenticated_before_release", "command_executable_observed",
              "command_exited", "sync_eof", "stdout_eof", "stderr_eof", "status_eof",
              "monitor_result_eof", "monitor_pidfd_exited")
    return (all(value.get(name) is True for name in fields)
            and type(value.get("monitor_wait_exit_code")) is int
            and type(value.get("bubblewrap_exit_code")) is int
            and value["monitor_wait_exit_code"] == value["bubblewrap_exit_code"]
            and value.get("truncated") is False and value.get("monitor_result_truncated") is False
            and value.get("forced_cleanup") is False and value.get("errors") == [])


def audio_live_ready(gate, root, admitted, prepared, mounted):
    import ci_sdk_native_source as sdk_gate
    L = sys.modules.get("startup_capture_launcher")
    require(L is not None, "audio-live-framework-must-be-loaded-before-original-clock")
    report = L.strict_json(audio_live_read(root / "evidence/preparation.json", 8 * 1024**2), 8 * 1024**2)
    require(sha(json_bytes(report)) == prepared["preparation_sha256"], "audio-live-preparation-bytes-changed")
    require(report["status"] == "PASS_PREPARATION_ONLY" and report["sdk_g0"] == "PASS_HYGIENE_SOURCE_FORMAT",
            "audio-live-own-sdk-g0-missing")
    for collection in prepared["sealed"].values():
        sdk_gate.verify_binaries(collection, root / "target")
    for snapshot in report["sources"].values():
        require(sdk_gate.custody.source_snapshot(Path(snapshot["checkout"]), snapshot["sha"], tuple(snapshot["files"]), snapshot["tree"]) == snapshot,
                "audio-live-source-changed-before-effect")
    live_root = Path(mounted["evidence_root"])
    require(live_root == root / "volume/evidence" and not any(live_root.iterdir())
            and list(metadata_identity(live_root.lstat())) == mounted["evidence_identity"],
            "audio-live-evidence-root-changed-before-effect")
    manifest_bound = audio_live_manifest_bound(admitted["plan"], root)
    footprint = audio_live_retention_footprint(root, admitted["plan"], report)
    return {"manifest_upper_bytes": manifest_bound, "retention_footprint": footprint,
            "resource_before_effect": audio_resource_preflight(root),
            "evidence_identity": mounted["evidence_identity"]}


def audio_live_run(gate, output, request_raw, *, started_ns, environment=None, uid=None, gid=None):
    """A single controller; SDK work is impossible without a reviewed exact row."""
    import time
    L = sys.modules.get("startup_capture_launcher")
    require(L is not None, "audio-live-framework-must-be-loaded-before-original-clock")
    env = dict(os.environ if environment is None else environment)
    current = selected.audio_live_context(env, sys.platform)
    clock = selected.audio_live_clock(started_ns, time.monotonic_ns())
    require(type(request_raw) is bytes and 0 < len(request_raw) <= 65536, "audio-live-request-byte-bound")
    request = strict(request_raw)
    admitted = selected.audio_live_admit(request, current)
    require(type(uid) is int and type(gid) is int and uid > 0 and gid > 0,
            "audio-live-explicit-normal-process-owner")
    # This pure boundary precedes path reads, mkdir, mount, worker creation,
    # any Cargo and any renderer. An empty catalogue stops here.
    require(Path(L.__file__).absolute() == Path(gate).absolute() / "tooling/startup_capture_launcher.py",
            "audio-live-generic-provider-location")
    gate, root = Path(gate).absolute(), Path(output).absolute()
    safe_environment = audio_live_environment(env, root)
    stages, compiled_roster = {}, None
    native_status = "NOT_RUN"
    held_root = None
    result = {"schema": "motionwright.audio-live-controller/1", "context": current, "clock": clock,
              "status": "UNKNOWN_NO_RETRY", "native_status": native_status, "automatic_retry": False,
              "whole_tree_closure_after_controller_crash": "NOT_CLAIMED"}
    def worker(label, operation, deadline):
        require(label not in stages, "audio-live-operation-cannot-be-replayed")
        observed = audio_live_worker(L, lambda _: operation(), deadline)
        stages[label] = observed
        require(observed["status"] == "OBSERVED", "audio-live-worker-unknown:" + label)
        return observed["value"]
    try:
        bootstrap = worker("bootstrap", lambda: audio_live_bootstrap_inputs(gate, root, admitted, env, clock, uid, gid),
                           min(clock["work_deadline_ns"], started_ns + 60 * 1_000_000_000))
        tools = bootstrap["tools"]
        # The inherited interpreter/crypto setup belongs to the gate bootstrap.
        # SDK checkout, toolchain provisioning, G0 and every producer are below
        # the original clock and inside this separate retained namespace.
        readonly = bootstrap["readonly_roots"]
        stage_code = ("import sys; sys.path.insert(0,sys.argv[1]); import native_mlt_g2_custody as m; "
                      "raise SystemExit(m.audio_live_prepare_stage(sys.argv[2],sys.argv[3]))")
        argv = [bootstrap["python_launch"], "-I", "-B", "-X", "utf8", "-c", stage_code,
                str(gate / "tooling"), str(root / "control/worker.json"), bootstrap["configuration_sha256"]]
        preparation_environment = {**safe_environment, **{key: env[key] for key in env if key.startswith("GITHUB_") and "TOKEN" not in key}}
        prep_observation = audio_live_namespace(L, bwrap=tools["bwrap"]["path"], python=bootstrap["python_launch"],
            argv=argv, working=str(gate), readonly=readonly, writable=[str(root)], environment=preparation_environment,
            clock=clock, effect_deadline_ns=started_ns + 900 * 1_000_000_000,
            uid=uid, gid=gid, expected_executable=tools["python"]["identity"],
            expected_python=tools["python"]["identity"], expected_bwrap=tools["bwrap"]["identity"],
            python_binary=tools["python"]["path"], command_binary=tools["python"]["path"], preparation=True)
        stages["preparation"] = prep_observation
        prepared = worker("preparation-readback", lambda: audio_live_preparation_readback(root, current, clock), clock["work_deadline_ns"])
        native_status = "NOT_RUN_PREPARATION_FAILED"
        require(audio_live_namespace_closed(prep_observation) and prep_observation["bubblewrap_exit_code"] == 0
                and prepared["status"] == "PASS_PREPARATION_ONLY", "audio-live-preparation-not-admissible")
        compiled_roster = prepared["rosters"]["ignored"]
        mounted = worker("runtime", lambda: audio_live_runtime_and_volume(gate, root, admitted, bootstrap, env, uid, gid),
                         clock["work_deadline_ns"])
        ready = worker("ready", lambda: audio_live_ready(gate, root, admitted, prepared, mounted), clock["work_deadline_ns"])
        held_root = L.OwnedFD(os.open(mounted["evidence_root"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC),
                              "audio-live-evidence-root")
        require(list(metadata_identity(os.fstat(held_root.fd))) == ready["evidence_identity"],
                "audio-live-root-identity-before-release")
        live_environment = audio_live_environment(env, root / "volume/auxiliary")
        live_environment.update(GITHUB_WORKSPACE=env["GITHUB_WORKSPACE"], CARGO_TARGET_DIR=str(root / "target"),
            CARGO_HOME=str(root / "cargo-home"), RUSTUP_HOME=str(root / "rustup-home"),
            SEMWRIGHT_TEST_MLT_VIDEO="1", SEMWRIGHT_TEST_MLT_VIDEO_EVIDENCE_ROOT=mounted["evidence_root"],
            SEMWRIGHT_TEST_SANDBOX_HELPER=prepared["sealed"]["sandbox"]["semwright-sandbox"]["absolute_path"],
            SEMWRIGHT_TEST_MELT=tools["melt"]["path"], SEMWRIGHT_TEST_FFPROBE=tools["ffprobe"]["path"],
            SEMWRIGHT_TEST_FFMPEG=tools["ffmpeg"]["path"], SEMWRIGHT_TEST_BWRAP=tools["bwrap"]["path"])
        binary = prepared["sealed"]["tests"]["live"]
        native_status = "UNKNOWN_NO_RETRY"
        native = audio_live_namespace(L, bwrap=tools["bwrap"]["path"], python=bootstrap["python_launch"],
            argv=selected.audio_live_argv(binary["absolute_path"]), working=str(root / "sdk"),
            readonly=[*readonly, str(root / "sdk"), str(root / "target")],
            writable=[str(root / "volume/evidence"), str(root / "volume/auxiliary")], environment=live_environment,
            clock=clock, effect_deadline_ns=clock["work_deadline_ns"], uid=uid, gid=gid,
            expected_executable=prepared["kernel_identities"]["tests"]["live"], expected_python=tools["python"]["identity"],
            expected_bwrap=tools["bwrap"]["identity"], python_binary=tools["python"]["path"],
            command_binary=binary["absolute_path"])
        stages["live"] = native
        require(list(metadata_identity(os.fstat(held_root.fd)))[:5] == ready["evidence_identity"][:5],
                "audio-live-root-replaced-after-operation")
        if audio_live_namespace_closed(native):
            native_status = "FAIL_RUST_TERMINAL"
            terminal = selected.audio_live_terminal(native["raw"]["stdout"], native["raw"]["stderr"],
                                                   native["bubblewrap_exit_code"], compiled_roster)
            result["native"] = selected.audio_live_join(native, terminal)
            native_status = terminal["status"]
    except BaseException as error:
        result["error"] = type(error).__name__ + ":" + str(error)[:256]
    finally:
        if held_root is not None:
            if not held_root.close():
                native_status = "UNKNOWN_ROOT_CLOSE_NO_RETRY"
            result["held_root"] = held_root.metadata()
        result["native_status"] = native_status
        if "bootstrap" in stages:
            sealed = audio_live_worker(L, lambda _: audio_live_seal(root, admitted, stages, native_status, compiled_roster, clock, uid, gid),
                                       clock["seal_deadline_ns"], limit=MAX_JSON)
            stages["seal"] = sealed
            if sealed["status"] == "OBSERVED":
                result.update(status="LOCAL_CUSTODY_READY_NOT_DELIVERED", public=sealed["value"])
            else:
                result.update(status="INCOMPLETE_CUSTODY_NO_RETRY", export_ready=False)
        result["stages"] = {name: audio_live_worker_record(value) for name,value in stages.items()}
        result["finished_ns"] = time.monotonic_ns()
        result["deadline_unchanged"] = True
        if result["status"] == "LOCAL_CUSTODY_READY_NOT_DELIVERED":
            persisted = audio_live_worker(L, lambda _: (new_json(root / "control/controller-observed.json", result),
                                                        {"record_sha256": sha(json_bytes(result))})[1],
                                           clock["seal_deadline_ns"], limit=MAX_JSON)
            result["controller_record"] = audio_live_worker_record(persisted)
            if persisted["status"] != "OBSERVED":
                result["status"] = "INCOMPLETE_CONTROLLER_RECORD_NO_RETRY"
    return result


def audio_live_delivery_window(root, current, now_ns, boot_id):
    value = strict(audio_live_read(Path(root) / "control/clock.json", MAX_JSON))
    clock = {key: value[key] for key in ("started_ns", "work_deadline_ns", "cleanup_deadline_ns", "seal_deadline_ns",
                                        "upload_deadline_ns", "deadline_ns")}
    expected = selected.audio_live_clock(clock["started_ns"], clock["started_ns"])
    require(clock == expected and value["context"] == current and value["boot_id"] == boot_id
            and type(now_ns) is int and clock["started_ns"] <= now_ns
            and now_ns + 270 * 1_000_000_000 < clock["deadline_ns"],
            "audio-live-upload-and-readback-reserve-exhausted")
    public = strict(audio_live_read(Path(root) / "export/public.json", MAX_JSON))
    controller = strict(audio_live_read(Path(root) / "control/controller-observed.json", MAX_JSON))
    require(controller["status"] == "LOCAL_CUSTODY_READY_NOT_DELIVERED" and controller["context"] == current
            and controller["clock"] == clock and controller["stages"]["seal"]["status"] == "OBSERVED"
            and controller["stages"]["seal"]["original_process"]["original_wait"]["exit_code"] == 0
            and controller["stages"]["seal"]["original_process"]["result_eof"] is True
            and controller["stages"]["seal"]["original_process"]["errors"] == [],
            "audio-live-seal-original-wait-and-eof-required")
    require(public["clock"] == clock and public["context"] == current
            and set(p.name for p in (Path(root) / "export").iterdir()) == {"public.json", "originals.mwci"},
            "audio-live-export-roster")
    raw = audio_live_read(Path(root) / "export/originals.mwci", MAX_TOTAL + MAX_FILES * 4096 + 8256)
    require(sha(raw) == public["ciphertext_sha256"] and len(raw) == public["ciphertext_bytes"],
            "audio-live-export-original-changed")
    return public


def audio_live_verify_delivery_zip(raw, public, envelope):
    require(type(raw) is bytes and len(raw) <= MAX_TOTAL + MAX_FILES * 4096 + 1024 * 1024,
            "audio-live-delivery-zip-bound")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        rows = archive.infolist()
        require(len(rows) == 2 and {x.filename for x in rows} == {"public.json", "originals.mwci"}
                and all(not x.is_dir() and stat.S_IFMT(x.external_attr >> 16) in (0, stat.S_IFREG) for x in rows)
                and sum(x.file_size for x in rows) <= len(envelope) + MAX_JSON and archive.testzip() is None,
                "audio-live-delivery-exact-roster")
        require(archive.read("public.json") == json_bytes(public)
                and archive.read("originals.mwci") == envelope
                and sha(envelope) == public["ciphertext_sha256"], "audio-live-delivery-bytes-changed")
    return {"status": "CIPHERTEXT_DELIVERED_OFFLINE_INTAKE_REQUIRED", "context": public["context"],
            "ciphertext_sha256": sha(envelope), "zip_sha256": sha(raw), "automatic_retry": False,
            "decryption_performed": False, "formal_acceptance": False}


def audio_live_delivery_readback(root, current, artifact_id, zip_digest):
    """Same GitHub API/ZIP channel; no new transport or private-key upload."""
    import resource
    import time
    clock = strict(audio_live_read(root / "control/clock.json", MAX_JSON))
    require(clock["context"] == current and clock["boot_id"] == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            and type(artifact_id) is int and artifact_id > 0 and type(zip_digest) is str and HEX.fullmatch(zip_digest),
            "audio-live-delivery-context")
    folder = root / "delivery"; folder.mkdir(mode=0o700)
    def api(route, name, maximum):
        remaining = (clock["deadline_ns"] - time.monotonic_ns()) / 1_000_000_000
        require(remaining > 3, "audio-live-delivery-deadline")
        argv = ["/usr/bin/gh", "api", route]
        new_json(folder / (name + ".intent.json"), {"argv": argv, "original_deadline_ns": clock["deadline_ns"], "automatic_retry": False})
        before_limit = resource.getrlimit(resource.RLIMIT_FSIZE)
        try:
            resource.setrlimit(resource.RLIMIT_FSIZE, (maximum, before_limit[1]))
            with (folder / (name + ".stdout.original")).open("xb") as out, (folder / (name + ".stderr.original")).open("xb") as err:
                process = subprocess.run(argv, stdout=out, stderr=err, timeout=min(20, remaining - 2), check=False)
        finally:
            resource.setrlimit(resource.RLIMIT_FSIZE, before_limit)
        new_json(folder / (name + ".terminal.json"), {"exit_code": process.returncode, "automatic_retry": False})
        require(process.returncode == 0 and time.monotonic_ns() < clock["deadline_ns"], "audio-live-delivery-api-failed")
        return audio_live_read(folder / (name + ".stdout.original"), maximum)
    prefix = "repos/" + REPO + "/actions/artifacts/" + str(artifact_id)
    metadata = strict(api(prefix, "artifact-metadata", MAX_JSON))
    require(type(metadata.get("id")) is int and metadata.get("id") == artifact_id and metadata.get("expired") is False
            and type(metadata.get("workflow_run", {}).get("id")) is int
            and metadata.get("workflow_run", {}).get("id") == int(current["run_id"])
            and metadata.get("workflow_run", {}).get("head_sha") == current["source_sha"]
            and metadata.get("digest") == "sha256:" + zip_digest
            and type(metadata.get("size_in_bytes")) is int
            and 0 < metadata["size_in_bytes"] <= MAX_TOTAL + MAX_FILES * 4096 + 1024 * 1024,
            "audio-live-delivery-artifact-identity")
    raw = api(prefix + "/zip", "artifact-zip", MAX_TOTAL + MAX_FILES * 4096 + 1024 * 1024)
    require(len(raw) == metadata["size_in_bytes"] and sha(raw) == zip_digest, "audio-live-delivery-zip-identity")
    public = strict(audio_live_read(root / "export/public.json", MAX_JSON))
    envelope = audio_live_read(root / "export/originals.mwci", MAX_TOTAL + MAX_FILES * 4096 + 8256)
    result = audio_live_verify_delivery_zip(raw, public, envelope)
    require(time.monotonic_ns() < clock["deadline_ns"], "audio-live-delivery-returned-after-H")
    new_json(folder / "receipt.json", result)
    return result


def audio_live_arguments(argv):
    class SilentParser(argparse.ArgumentParser):
        def error(self, message):
            raise CustodyRejected("audio-live-argument-contract")
    parser = SilentParser(add_help=False)
    parser.add_argument("action", choices=("audio-live", "audio-live-window", "audio-live-delivery"))
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    for name in ("uid", "gid", "started-ns", "artifact-id"):
        parser.add_argument("--" + name, type=int)
    parser.add_argument("--artifact-digest")
    return parser.parse_args(argv)


def audio_live_cli(args, original_started_ns, environment):
    import time
    L = sys.modules.get("startup_capture_launcher")
    require(L is not None, "audio-live-framework-bootstrap-missing")
    code = 2
    lines = []
    deadline = original_started_ns + 1800 * 1_000_000_000
    try:
        require(args.gate is not None and args.output is not None, "audio-live-paths-required")
        if args.action == "audio-live":
            result = audio_live_run(args.gate, args.output, environment.get("MW_AUDIO_LIVE_REQUEST", "").encode(),
                                    started_ns=original_started_ns, environment=environment, uid=args.uid, gid=args.gid)
            ready = result["status"] == "LOCAL_CUSTODY_READY_NOT_DELIVERED"
            lines = ["started_ns=" + str(original_started_ns), "custody_ready=" + str(ready).lower()]
            code = 0 if ready else 2
        else:
            require(type(args.started_ns) is int and 0 < args.started_ns <= original_started_ns,
                    "audio-live-original-followup-clock-required")
            deadline = args.started_ns + 1800 * 1_000_000_000
            current = selected.audio_live_context(environment, sys.platform)
            if args.action == "audio-live-window":
                observed = audio_live_worker(L, lambda _: audio_live_delivery_window(args.output, current,
                    time.monotonic_ns(), Path("/proc/sys/kernel/random/boot_id").read_text().strip()),
                    args.started_ns + 1530 * 1_000_000_000, limit=MAX_JSON)
                ready = observed["status"] == "OBSERVED"
                lines = ["upload_ready=" + str(ready).lower()]
                code = 0 if ready else 2
            else:
                observed = audio_live_worker(L, lambda _: audio_live_delivery_readback(args.output, current,
                    args.artifact_id, args.artifact_digest), deadline, limit=MAX_JSON)
                delivered = observed["status"] == "OBSERVED"
                lines = ["delivered=" + str(delivered).lower(), "offline_intake_required=true"]
                code = 0 if delivered else 2
    except BaseException:
        lines = ["custody_ready=false", "upload_ready=false", "delivered=false", "automatic_retry=false"]
    try:
        os.set_blocking(1, False)
        L._write_pipe(1, ("\n".join(lines) + "\n").encode(), deadline)
    except BaseException:
        code = 2
    # No Python flush, another attempt, or a new clock after the original H.
    os._exit(code)

def main(argv=None):
    import time
    original_started_ns = time.monotonic_ns()
    tokens = list(sys.argv[1:] if argv is None else argv)
    if tokens and tokens[0] in {"audio-live", "audio-live-window", "audio-live-delivery"}:
        try:
            parsed = audio_live_arguments(tokens)
        except BaseException:
            parsed = argparse.Namespace(action="audio-live", gate=None, output=None)
        return audio_live_cli(parsed, original_started_ns, dict(os.environ))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "guard", "handoff", "inputs", "begin", "exit", "seal", "audio-source-g1"))
    parser.add_argument("--uid", type=int)
    parser.add_argument("--gid", type=int)
    parser.add_argument("--exit-code", type=int)
    parser.add_argument("--job-status")
    parser.add_argument("--gate", type=Path)
    parser.add_argument("--sdk", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    env = os.environ
    if args.action == "audio-source-g1":
        require(args.gate is not None and args.sdk is not None and args.output is not None, "audio-source-paths-required")
        result = audio_source_run(args.gate, args.sdk, args.output)
        print(json.dumps({"status": result["status"], "actual_cases": len(result["results"])}))
        return 0 if result["status"] == "PASS_EXACT_AUDIO_SOURCE_G1" else 2
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
