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
            and env.get("MW_MLT_SCOPE") in {"repaired-g2", FILM_SCOPE, "fixedtxt-v4-one-fixture"},
            "closed_manual_selection_required")
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
    if scenario == "fixedtxt-v4-one-fixture":
        from native_fixedtxt_g2_admission import validate_dispatch as fixedtxt_dispatch
        return fixedtxt_dispatch(raw, current)
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
    require(scenario in {"repaired-g2", FILM_SCOPE, "fixedtxt-v4-one-fixture"}, "source_scenario")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull,
               GIT_CONFIG_GLOBAL=os.devnull, GIT_GRAFT_FILE=os.devnull,
               GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    def git(*argv):
        return subprocess.check_output(["/usr/bin/git", "--no-replace-objects", "-C", str(repo),
                                        "-c", "core.fsmonitor=false", *argv], env=env, timeout=20)
    require(git("rev-parse", "HEAD").decode().strip() == current["source_sha"], "source_HEAD_changed")
    require(not git("diff", "--name-only", "HEAD", "--"), "tracked_source_changed")
    if scenario == "fixedtxt-v4-one-fixture":
        from native_fixedtxt_g2_admission import source_paths
        selected_paths = source_paths()
    else:
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


def runtime_preparation_dispatch(raw, current, *, scenario):
    """The existing external runtime permit precedes FixedTxt P/R/Q review.

    This accepts the original Film runtime-only dispatch for preparation; it
    never admits a FixedTxt wrapper or substitutes for its external commitment.
    Native FixedTxt admission still uses validate_dispatch and its full P/R/Q.
    """
    return validate_dispatch(raw, current,
        scenario=FILM_SCOPE if scenario == "fixedtxt-v4-one-fixture" else scenario)


def prepare(env):
    require(sys.platform == "linux" and os.geteuid() != 0, "prepare_requires_normal_Linux_runner")
    current = context(env)
    dispatch = runtime_preparation_dispatch(env.get("MW_MLT_G2_ADMISSION", "").encode(), current,
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
    if env.get("MW_MLT_SCOPE", "repaired-g2") not in {FILM_SCOPE, "fixedtxt-v4-one-fixture"}:
        original = {"artifact_id": 12109323512, "run_id": 39054798740, "run_attempt": 1,
                    "source_sha": g2.REFERENCE_PR_HEAD, "zip_sha256": g2.REFERENCE_ZIP_SHA256}
        archive = download_artifact(original, "reference", control / "intake")
        g2.reference_source(archive)
        new_bytes(control / "intake/original-artifact.zip", archive)
    elif env.get("MW_MLT_SCOPE") == "fixedtxt-v4-one-fixture":
        new_json(control / "records/fixedtxt-source-intent.json", {
            "schema": "motionwright.native.fixedtxt-source-intent/1",
            "origin": "EXTERNAL_AUTHORED_SOURCE_PENDING_R28_COMMITMENT",
            "frame_count": 180, "frame_rate": 30,
            "historical_reference_WAV_reused": False, "native_effect_started": False,
        })
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
    dispatch = runtime_preparation_dispatch(regular(control / "records/dispatch.json", MAX_JSON)[0], context(env),
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
    dispatch = runtime_preparation_dispatch(regular(control / "records/dispatch.json", MAX_JSON)[0], current,
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
    if env.get("MW_MLT_SCOPE", "repaired-g2") in {FILM_SCOPE, "fixedtxt-v4-one-fixture"}:
        data["schema"] = ("motionwright.native.fixedtxt-runtime-preparation/1"
                          if env.get("MW_MLT_SCOPE") == "fixedtxt-v4-one-fixture"
                          else FILM_INPUT_SCHEMA)
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
    require(env.get("MW_MLT_SCOPE", "repaired-g2") in {"repaired-g2", FILM_SCOPE},
            "fixedtxt_requires_its_original_external_owner")
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
    if manifest["schema"]==selected_core.MANIFEST_SCHEMA_V3 and scenario==FILM_SCOPE:
        from native_film180_command_custody import contract as command_contract
        expected=command_contract(selected.checked_context(current,FILM_SCOPE))
        require(manifest["diagnostic_contract"]==expected and diagnostic_admission is None,
                "film-public-command-roster")
        value.update(schema="motionwright.native.film180-g2-public/3",
            custody_scope="explicit-source-bound-selected-layers/3",
            selected_evidence_schema=selected_core.MANIFEST_SCHEMA_V3,
            selected_lost_scope=list(selected_core.LOST_SCOPE_V3),
            command_originals_contract_sha256=sha(selected_core.canonical(expected)),
            command_originals_intake_verified=diagnostic_verified,
            command_originals_roster_complete=manifest["diagnostic_roster_complete"],
            raw_diagnostics_included=manifest["raw_diagnostics_included"],
            diagnostic_eof_claimed=False,diagnostic_history_complete_claimed=False,
            diagnostic_payloads_redacted=False)
    elif manifest["schema"]==selected_core.MANIFEST_SCHEMA_V3:
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
    if (scenario == FILM_SCOPE or (diagnostic_admission is not None and custody_valid)) and source_unchanged:
        # Raw originals remain useful even when a failed/unknown native terminal
        # cannot satisfy the independent success policy. This performs no replay.
        try:
            selected.verify_decrypted_selection(raw,current=current,scenario=scenario,
                expected_diagnostic_admission=diagnostic_admission,require_complete=False)
            diagnostic_intake_verified=True
        except Exception:
            # The same captured bytes remain sealed; no second capture or retry.
            diagnostic_intake_verified=False
    intake_verified = False
    if (manifest["required_layers_complete"] and custody_valid and not ffv1_stage_plan_absent
            and ((diagnostic_admission is None and scenario != FILM_SCOPE) or diagnostic_intake_verified)):
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

FIXEDTXT_RUNTIME_MEMBER = "source-input.original.json"
FIXEDTXT_CYCLE_MEMBER = "cycle.original.json"


def fixedtxt_owner_context(env):
    """Use the actual owner environment, never fields read from R or a capsule."""
    selected.fixedtxt_owner_selection_guard()
    from native_film180_evidence_selection import FIXEDTXT_SCOPE
    require(env.get("GITHUB_ACTIONS") == "true" and env.get("CI") == "true"
            and env.get("RUNNER_OS") == "Linux"
            and env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and env.get("GITHUB_RUN_ATTEMPT") == "1"
            and env.get("MW_MLT_SCOPE") == FIXEDTXT_SCOPE
            and env.get("MOTIONWRIGHT_APPROVED_FIXEDTXT_V4_G2") == "1"
            and env.get("MOTIONWRIGHT_APPROVED_MLT_G2") == "1",
            "fixedtxt-explicit-single-owner-context")
    current = {"source_sha": env.get("GITHUB_SHA"), "semwright_sha": g2.SEMWRIGHT_PIN,
               "run_id": env.get("GITHUB_RUN_ID"), "run_attempt": "1"}
    from ci_private_evidence import validate_context
    validate_context(current)
    return current


def fixedtxt_owner_layout(*, current, runner_temp):
    """Pure closed owner layout; no path resolution, reads or directory creation.

    These are new source-owned names for this 043c successor. They are not a
    recovered frozen-R3 layout. The caller supplies independently verified
    RUNNER_TEMP; neither an input's parent nor R can choose any root/member.
    """
    selected.fixedtxt_owner_selection_guard()
    from ci_private_evidence import validate_context
    from native_film180_evidence_selection import FIXEDTXT_PLAN_PATH
    validate_context(current)
    require(current["run_attempt"] == "1", "fixedtxt-first-owner-attempt")
    require(type(runner_temp) is str and 0 < len(runner_temp) <= 768
            and "\\" not in runner_temp and all(32 <= ord(ch) < 127 for ch in runner_temp),
            "fixedtxt-owner-temp-syntax")
    temp = PurePosixPath(runner_temp)
    require(temp.is_absolute() and temp != PurePosixPath("/")
            and str(temp) == runner_temp and ".." not in temp.parts,
            "fixedtxt-owner-temp-canonical")
    suffix = current["run_id"] + "-1"
    control = temp / ("mw-fixedtxt-g2-control-" + suffix)
    inputs = temp / ("mw-fixedtxt-g2-inputs-" + suffix)
    return {"control_root": str(control), "inputs_root": str(inputs),
            "plan": str(control / FIXEDTXT_PLAN_PATH),
            "runtime": str(inputs / FIXEDTXT_RUNTIME_MEMBER),
            "cycle": str(inputs / FIXEDTXT_CYCLE_MEMBER),
            "observation_root": str(temp / ("mw-fixedtxt-g2-original-" + suffix))}


def fixedtxt_current_selection(current):
    """Select one externally reviewed row by the independently held context."""
    selected.fixedtxt_owner_selection_guard()
    from native_film180_evidence_selection import FIXEDTXT_SCOPE
    require(len(selected.FIXEDTXT_ATTEMPTS) <= 128, "fixedtxt-catalogue-bound")
    matches = [identifier for identifier, row in selected.FIXEDTXT_ATTEMPTS.items()
               if type(row) is dict and row.get("context") == current
               and row.get("scope") == FIXEDTXT_SCOPE]
    require(len(matches) == 1, "fixedtxt-one-independent-current-row")
    identifier = matches[0]
    row = selected._fixedtxt_reviewed_row(identifier)
    require(type(row["recipient_sha256"]) is str and HEX.fullmatch(row["recipient_sha256"]),
            "fixedtxt-independent-recipient-SPKI")
    return identifier, row["recipient_sha256"]


def fixedtxt_owner_input_paths(*, current, runner_temp, control_root, inputs_root,
                              plan_input_path, runtime_input_path, cycle_input_path):
    """Compare all supplied paths with the one source-owned layout before I/O."""
    selected.fixedtxt_owner_selection_guard()
    layout = fixedtxt_owner_layout(current=current, runner_temp=runner_temp)
    for key, value in (("control_root", control_root), ("inputs_root", inputs_root),
                       ("plan", plan_input_path), ("runtime", runtime_input_path),
                       ("cycle", cycle_input_path)):
        require(type(value) is str and value == layout[key], "fixedtxt-owner-input-location")
    return layout


def fixedtxt_prepare_owner_invocation(
    plan_raw, runtime_raw, *, current, admission_id, recipient_sha256,
    plan_input_path, runtime_input_path, cycle_input_path, cycle_raw,
    control_root, inputs_root, source_root, python_executable, environment,
):
    """Pure same-buffer admission for the existing wrapper's exact argv.

    No supplied callback can replace the source guard. No path is opened,
    resolved or serialized here; the owner has captured P/R/Q already.
    """
    selected.fixedtxt_owner_selection_guard()
    require(fixedtxt_owner_context(environment) == current, "fixedtxt-independent-current-context")
    require(fixedtxt_current_selection(current) == (admission_id, recipient_sha256),
            "fixedtxt-independent-selected-row")
    paths = fixedtxt_owner_input_paths(current=current, runner_temp=environment.get("RUNNER_TEMP"),
        control_root=control_root, inputs_root=inputs_root, plan_input_path=plan_input_path,
        runtime_input_path=runtime_input_path, cycle_input_path=cycle_input_path)
    plan, runtime = selected.admit_fixedtxt_runtime_original(
        plan_raw, runtime_raw, current=current, admission_id=admission_id,
        recipient_sha256=recipient_sha256, plan_input_path=paths["plan"])
    descriptor = selected._fixedtxt_runtime_descriptor(selected._fixedtxt_reviewed_row(admission_id))
    require(descriptor["relative_path"] == FIXEDTXT_RUNTIME_MEMBER
            and runtime["private_evidence_dir"] == paths["observation_root"],
            "fixedtxt-owner-runtime-independent-members")
    require(type(cycle_raw) is bytes and 0 < len(cycle_raw) <= 1024 * 1024
            and sha(cycle_raw) == plan["cycle_input_sha256"], "fixedtxt-owner-cycle-original")
    members = [member for member in plan["members"]
               if (member["root"], member["relative_path"]) == ("inputs", FIXEDTXT_CYCLE_MEMBER)]
    require(len(members) == 1 and members[0]["role"] == "input-original"
            and members[0]["expected_sha256"] == sha(cycle_raw)
            and len(cycle_raw) <= members[0]["maximum"], "fixedtxt-owner-cycle-planned-member")
    # Q has its own 1 MiB bound above; use the existing strict parser without
    # borrowing the carrier's smaller metadata-manifest bound.
    cycle = selected_core.strict(cycle_raw)
    require(cycle.get("schema") == "motionwright.fixed-text-native-cycle-inputs/4"
            and cycle.get("arm") == plan["arm"] and cycle.get("decoder_sha256") == plan["decoder_sha256"]
            and type(cycle.get("runtime")) is dict
            and cycle["runtime"].get("semwright_revision") == current["semwright_sha"]
            and type(cycle["runtime"].get("native_source_sha256")) is dict
            and cycle["runtime"]["native_source_sha256"].get(plan["arm"])
            == plan["arm_sources"][plan["arm"]]["native_source_sha256"],
            "fixedtxt-owner-cycle-selected-bindings")
    require(type(source_root) is str and source_root == environment.get("GITHUB_WORKSPACE")
            and PurePosixPath(source_root).is_absolute() and str(PurePosixPath(source_root)) == source_root
            and ".." not in PurePosixPath(source_root).parts and "\\" not in source_root,
            "fixedtxt-owner-source-root")
    require(type(python_executable) is str and PurePosixPath(python_executable).is_absolute()
            and str(PurePosixPath(python_executable)) == python_executable
            and ".." not in PurePosixPath(python_executable).parts and "\\" not in python_executable,
            "fixedtxt-observed-python-required")
    return [python_executable, "-B", "-E", "-s",
            str(PurePosixPath(source_root) / "tooling/native_film180_original_g2.py"),
            "--g2-inputs", paths["runtime"], "--fixed-text-v4-inputs", paths["cycle"],
            "--fixed-text-v4-fixture", plan["fixture"]]


def fixedtxt_acquire_owner_originals(env, *, runtime_input_path, cycle_input_path,
                                    selected_fixture, source_root, python_executable):
    """Acquire the three exact owner inputs once with the existing openat reader.

    R's small routing parse happens only after the source guard and independently
    owned layout. Its hints are compared before P is opened. The held P/R/Q
    buffers are passed unchanged to the pure preparer and returned to the wrapper.
    """
    selected.fixedtxt_owner_selection_guard()
    from native_film180_evidence_selection import FIXEDTXT_PLAN_PATH, FIXEDTXT_MAX_JSON, FIXEDTXT_KEYS
    current = fixedtxt_owner_context(env)
    layout = fixedtxt_owner_layout(current=current, runner_temp=env.get("RUNNER_TEMP"))
    require(type(runtime_input_path) is str and runtime_input_path == layout["runtime"]
            and type(cycle_input_path) is str and cycle_input_path == layout["cycle"]
            and selected_fixture in FIXEDTXT_KEYS, "fixedtxt-CLI-inputs-differ-from-owner")
    require(type(source_root) is str and source_root == env.get("GITHUB_WORKSPACE"),
            "fixedtxt-owner-source-root")
    identifier, recipient = fixedtxt_current_selection(current)
    # Same common disposable-runtime guard; no new exception for this family.
    runner = admission.disposable_linux_runner()
    require(str(runner) == env.get("RUNNER_TEMP"), "fixedtxt-observed-runner-root")
    handles = selected_core.RootHandles({"control": layout["control_root"], "inputs": layout["inputs_root"]})
    primary = None
    try:
        runtime_raw, runtime_identity = handles.read("inputs", selected_core.member_path(FIXEDTXT_RUNTIME_MEMBER), 65536)
        routing = strict(runtime_raw)
        reference = routing.get("fixedtxt_plan")
        require(type(reference) is dict and reference.get("path") == layout["plan"]
                and routing.get("fixedtxt_admission_id") == identifier
                and routing.get("recipient_sha256") == recipient,
                "fixedtxt-routing-hints-differ-from-independent-owner")
        plan_raw, plan_identity = handles.read("control", selected_core.member_path(FIXEDTXT_PLAN_PATH), FIXEDTXT_MAX_JSON)
        source_plan = selected.admit_fixedtxt_attempt(plan_raw, current=current,
            admission_id=identifier, recipient_sha256=recipient)
        # The existing verifier requires and validates the collector's source
        # binding as well as the reviewed wrapper. Do not use a wrapper-only
        # pseudo-roster or self-computed collector pin before this step.
        selected_core.verify_source_bindings(source_root, current["source_sha"], source_plan["source_bindings"])
        plan, runtime = selected.admit_fixedtxt_runtime_original(
            plan_raw, runtime_raw, current=current, admission_id=identifier,
            recipient_sha256=recipient, plan_input_path=layout["plan"])
        require(plan["fixture"] == selected_fixture, "fixedtxt-owner-fixture-changed")
        cycle_raw, cycle_identity = handles.read("inputs", selected_core.member_path(FIXEDTXT_CYCLE_MEMBER), 1024 * 1024)
        identities = (runtime_identity, plan_identity, cycle_identity)
        require(len({identity[:2] for identity in identities}) == 3, "fixedtxt-aliased-owner-inputs")
        argv = fixedtxt_prepare_owner_invocation(plan_raw, runtime_raw,
            current=current, admission_id=identifier, recipient_sha256=recipient,
            plan_input_path=layout["plan"], runtime_input_path=layout["runtime"],
            cycle_input_path=layout["cycle"], cycle_raw=cycle_raw,
            control_root=layout["control_root"], inputs_root=layout["inputs_root"],
            source_root=source_root, python_executable=python_executable, environment=env)
        require(plan == source_plan and handles.stable_roots(), "fixedtxt-owner-input-roots-changed")
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            handles.close()
        except BaseException:
            if primary is None:
                raise
            primary.add_note("FixedTXT input-handle close also failed; no read or close was retried.")
    return {"plan": plan, "runtime": runtime, "plan_raw": plan_raw, "runtime_raw": runtime_raw,
            "cycle_raw": cycle_raw, "current": current, "admission_id": identifier,
            "recipient_sha256": recipient, "layout": layout, "argv": argv}


def _fixedtxt_inventory(roots, plan, *, source_root, runner_temp):
    """Bound the complete declared roots, without opening any selected payload."""
    from native_film180_evidence_selection import FIXEDTXT_ROOTS, FIXEDTXT_MAX_JSON
    require(type(roots) is dict and set(roots) == set(FIXEDTXT_ROOTS), "fixedtxt-private-root-roster")
    temp = Path(runner_temp)
    source = Path(source_root)
    require(temp.is_absolute() and temp.is_dir() and temp.resolve(strict=True) == temp
            and not any(path.is_symlink() for path in (temp, *temp.parents)), "fixedtxt-private-temp")
    require(source.is_absolute() and source.is_dir() and source.resolve(strict=True) == source,
            "fixedtxt-source-root")
    paths, root_ids = {}, set()
    for label, raw in roots.items():
        root = Path(raw)
        require(root.is_absolute() and root.is_relative_to(temp) and root != temp
                and not root.is_relative_to(source) and not source.is_relative_to(root)
                and root.resolve(strict=True) == root and root.is_dir()
                and not any(path.is_symlink() for path in (root, *root.parents)), "fixedtxt-private-root-location")
        mark = root.lstat()
        require(stat.S_ISDIR(mark.st_mode) and (mark.st_dev, mark.st_ino) not in root_ids,
                "fixedtxt-private-root-identity")
        require(not any(root.is_relative_to(other) or other.is_relative_to(root) for other in paths.values()),
                "fixedtxt-overlapping-roots")
        root_ids.add((mark.st_dev, mark.st_ino)); paths[label] = root
    expected = {(row["root"], row["relative_path"]): row for row in plan["members"]}
    seen, aliases, listing, count, total = {}, set(), {}, 0, 0
    for label, root in sorted(paths.items()):
        captured, pending, namespaces = [], [root], 0
        while pending:
            parent = pending.pop()
            with os.scandir(parent) as iterator:
                for entry in iterator:
                    namespaces += 1
                    require(namespaces <= MAX_FILES * 4, "fixedtxt-namespace-count")
                    path = Path(entry.path)
                    # DirEntry.stat omits dev/inode/nlink on Windows.
                    # Keep the fresh no-follow identity and hardlink guard.
                    mark = path.lstat()
                    require(not entry.is_symlink() and stat.S_ISREG(mark.st_mode) or
                            not entry.is_symlink() and stat.S_ISDIR(mark.st_mode), "fixedtxt-nonregular-original")
                    if stat.S_ISDIR(mark.st_mode):
                        pending.append(path)
                        continue
                    relative = path.relative_to(root).as_posix()
                    key = (label, relative)
                    require(key in expected and key not in seen, "fixedtxt-unplanned-original")
                    require(0 <= mark.st_size <= expected[key]["maximum"], "fixedtxt-original-exceeds-plan")
                    identity = (mark.st_dev, mark.st_ino)
                    require(identity not in aliases and mark.st_nlink == 1, "fixedtxt-aliased-original")
                    aliases.add(identity)
                    require(len(str(path)) <= 1024, "fixedtxt-physical-path-bound")
                    count += 1; total += mark.st_size
                    require(count <= plan["limits"]["files"] and total <= MAX_TOTAL,
                            "fixedtxt-inventory-budget")
                    seen[key] = metadata_identity(mark)
                    physical = dict(device=mark.st_dev, inode=mark.st_ino, size=mark.st_size,
                        mode=mark.st_mode, uid=mark.st_uid, gid=mark.st_gid,
                        mtime_ns=mark.st_mtime_ns, ctime_ns=mark.st_ctime_ns)
                    captured.append(dict(archive_path=label + "/" + relative,
                        observation=dict(path=str(path), sha256="f" * 64, size=mark.st_size,
                            before=physical, after=physical, namespace_after=physical)))
        listing[label] = dict(status="CAPTURED", files=sorted(captured, key=lambda row: row["archive_path"]))
    # Same compact JSON and three physical observations as gather_snapshot.
    # The actual TAR is checked again before encryption; this is not permission
    # to grow a manifest or drop rows if a failure makes it larger.
    manifest = dict(schema="motionwright.ci.private-snapshot/1",
        scope="selected-original-files-at-read-time", roots=listing,
        selected_originals_complete=True, complete_owner_state_claimed=False)
    require(len(json_bytes(manifest)) <= FIXEDTXT_MAX_JSON, "fixedtxt-snapshot-manifest-bound")
    return paths, seen


def capture_fixedtxt_capsule(plan_raw, *, current, admission_id, recipient,
                            roots, source_root, runner_temp, plan_input_path):
    """Reuse one existing snapshot and one existing cipher; no native effects.

    The source/run catalogue is empty. No workflow routes here before the
    owner's one-fixture ABI, complete inputs and finite runtime are admitted.
    This function does not provide a watchdog or native process supervision.
    """
    from native_film180_evidence_selection import (
        FIXEDTXT_PLAN_PATH, FIXEDTXT_SCOPE, fixedtxt_frames,
    )
    require(type(recipient) is dict and type(recipient.get("sha256")) is str,
            "fixedtxt-recipient-reference")
    plan = selected.admit_fixedtxt_attempt(plan_raw, current=current, admission_id=admission_id,
                                           recipient_sha256=recipient["sha256"])
    selected._fixedtxt_plan_input_path(plan_input_path)
    public_key(recipient)
    paths, before = _fixedtxt_inventory(roots, plan, source_root=source_root, runner_temp=runner_temp)
    require(str(paths["control"] / FIXEDTXT_PLAN_PATH) == plan_input_path,
            "fixedtxt-owner-plan-input-location")
    # Reuse the existing single-owner receipt and its physical identity checks.
    owner = checked_owner_workspace({"RUNNER_TEMP": str(runner_temp)}, current,
                                     paths["observation"] / plan["fixture"])
    require(owner == paths["owner"], "fixedtxt-owner-differs-from-planned-root")
    require(regular(paths["control"] / FIXEDTXT_PLAN_PATH, MAX_JSON)[0] == plan_raw,
            "fixedtxt-independent-plan-original")
    selected_core.verify_source_bindings(source_root, current["source_sha"], plan["source_bindings"])
    plaintext, snapshot_complete = gather_snapshot(paths, preserve_partial=True)
    selected_core.verify_source_bindings(source_root, current["source_sha"], plan["source_bindings"])
    _, after = _fixedtxt_inventory(paths, plan, source_root=source_root, runner_temp=runner_temp)
    require(before == after, "fixedtxt-selected-namespace-changed")
    result = selected.verify_fixedtxt_snapshot(plaintext, expected_plan=plan, current=current,
        plan_raw=plan_raw, plan_input_path=plan_input_path,
        admission_id=admission_id, recipient_sha256=recipient["sha256"])
    require(result["originals"].get("control/" + FIXEDTXT_PLAN_PATH) == plan_raw,
            "fixedtxt-capture-plan-original-absent-or-changed")
    require(type(snapshot_complete) is bool and snapshot_complete == result["snapshot_capture_complete"],
            "fixedtxt-snapshot-completeness-mismatch")
    require(0 < len(plaintext) <= plan["limits"]["capsule_bytes"] - MAX_JSON - 8192 - 64,
            "fixedtxt-snapshot-before-encryption-bound")
    encrypted = encrypt_snapshot(plaintext, recipient, current)
    public = dict(schema=selected.FIXEDTXT_PUBLIC_SCHEMA, scope=FIXEDTXT_SCOPE,
        context=current, arm=plan["arm"], fixture=plan["fixture"], plan_sha256=sha(plan_raw),
        recipient_spki_sha256=recipient["sha256"], sealed_originals_sha256=sha(encrypted),
        sealed_originals_bytes=len(encrypted), single_owner=1,
        verified_decode_count=len(fixedtxt_frames(plan["fixture"])) if result["decoder_originals_verified"] else 0,
        native_success_verified=False, pair_comparison_performed=False,
        decoder_invoked_by_intake=False, native_operation_retried=False,
        physical_acceptance=False, human_acceptance=False, release_authorized=False,
        upload_authorization="NOT_GRANTED_BY_COLLECTOR")
    for name in ("snapshot_capture_complete", "planned_originals_complete", "decoder_originals_verified",
                 "retained_file_count", "missing_file_count"):
        public[name] = result[name]
    selected.validate_fixedtxt_public(public, expected_plan=plan, current=current)
    require(len(encrypted) + len(json_bytes(public)) <= plan["limits"]["capsule_bytes"],
            "fixedtxt-two-file-capsule-bound")
    return encrypted, public


def write_fixedtxt_capsule(directory, envelope, public, *, expected_plan, current, admission_id, plan_raw):
    """Use the existing exclusive writer and exact existing two-file container."""
    selected.fixedtxt_owner_selection_guard()
    plan = selected.admit_fixedtxt_attempt(plan_raw, current=current,
        admission_id=admission_id, recipient_sha256=public["recipient_spki_sha256"])
    require(plan == expected_plan, "fixedtxt-writer-held-plan-differs")
    selected.validate_fixedtxt_public(public, expected_plan=expected_plan, current=current)
    require(type(envelope) is bytes and (sha(envelope), len(envelope)) == (
        public["sealed_originals_sha256"], public["sealed_originals_bytes"]), "fixedtxt-writer-original-cipher")
    root = Path(directory)
    require(root.is_absolute() and root.is_dir() and root.resolve(strict=True) == root
            and not any(path.is_symlink() for path in (root, *root.parents))
            and not list(root.iterdir()), "fixedtxt-fresh-output-root")
    # Never retry a failed fsync/write/readback. Its original files stay in place.
    new_bytes(root / "sealed-originals.bin", envelope)
    new_json(root / "public-evidence.json", public)
    require({path.name for path in root.iterdir()} == {"sealed-originals.bin", "public-evidence.json"},
            "fixedtxt-output-roster")
    return {"sealed_originals_sha256": sha(envelope), "public_evidence_sha256": sha(json_bytes(public)),
            "artifact_uploaded": False, "native_success_verified": False}


def verify_fixedtxt_artifact(original_zip, private_key, *, current, expected_plan,
                             expected_artifact, plan_raw, plan_input_path,
                             admission_id, recipient_sha256, require_complete=True):
    """Independent descriptor and plan; neither is recovered as authority from ZIP."""
    from native_film180_evidence_selection import fixedtxt_validate_plan
    plan = selected.admit_fixedtxt_attempt(plan_raw, current=current,
        admission_id=admission_id, recipient_sha256=recipient_sha256)
    require(json_bytes(fixedtxt_validate_plan(expected_plan, current=current)) == plan_raw,
            "fixedtxt-independent-plan-argument")
    selected._fixedtxt_plan_input_path(plan_input_path)
    expected = artifact_descriptor(strict(json_bytes(expected_artifact)))
    require(expected["source_sha"] == current["source_sha"]
            and str(expected["run_id"]) == current["run_id"]
            and str(expected["run_attempt"]) == current["run_attempt"], "fixedtxt-artifact-context")
    require(type(original_zip) is bytes and 0 < len(original_zip) <= MAX_FILE
            and sha(original_zip) == expected["zip_sha256"], "fixedtxt-original-zip-identity")
    with zipfile.ZipFile(io.BytesIO(original_zip)) as archive:
        entries = archive.infolist()
        require(len(entries) == 2 and {entry.filename for entry in entries} == {
            "sealed-originals.bin", "public-evidence.json"}, "fixedtxt-two-file-artifact-roster")
        require(sum(entry.file_size for entry in entries) <= plan["limits"]["capsule_bytes"],
                "fixedtxt-artifact-total-bound")
        for entry in entries:
            limit = MAX_JSON if entry.filename == "public-evidence.json" else MAX_FILE
            require(not entry.is_dir() and not entry.flag_bits & 1
                    and stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG)
                    and 0 < entry.file_size <= limit, "fixedtxt-artifact-member")
        require(archive.testzip() is None, "fixedtxt-artifact-crc")
        public = strict(archive.read("public-evidence.json"))
    selected.validate_fixedtxt_public(public, expected_plan=plan, current=current)
    envelope = zip_member(original_zip, "sealed-originals.bin", public["sealed_originals_sha256"], MAX_FILE)
    return selected.verify_fixedtxt_sealed_selection(envelope, private_key, public,
        expected_plan=plan, current=current, plan_raw=plan_raw,
        plan_input_path=plan_input_path, admission_id=admission_id,
        recipient_sha256=recipient_sha256, require_complete=require_complete)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        # The original raw evidence remains private. No exception payload,
        # owner path, input envelope or recovery key is printed to CI logs.
        print("MLT G2 custody gate failed; no automatic retry.", file=sys.stderr)
        raise SystemExit(2)
