#!/usr/bin/env python3
"""One explicitly admitted ALab metadata observation through existing custody.

The frozen-admission interface requires an externally reviewed exact source,
tree, SDK and existing RSA-3072 recipient binding before dispatch. This caller
never infers origin, decodes frames, grants
rights, applies a cut, launches an NLE, or retries an uncertain operation.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import http.client
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tooling/otio-media"))

import alab_readonly as owner
import ci_private_evidence as custody
import kdenlive_gui_e2e as gui
from mlt_official_runtime_intake import run_tool

gui.custody = custody
SCOPE = "external-alab-readonly"
JOB = "admitted-native-kdenlive-gui"
ADMISSION_SCHEMA = "motionwright.otio.external-alab-readonly-admission/1"
ADMISSION_KEYS = {
    "schema", "authority", "admission_reference", "scope", "job_name",
    "source_sha", "source_tree", "semwright_sha", "run_attempt",
    "recipient_spki_sha256", "existing_recipient", "request_contract_sha256",
    "metadata_only", "automatic_retry", "source_origin",
    "decoded_frames_verified", "candidate_admitted", "human_acceptance",
    "release_authorized",
}
ADDITIONAL_SOURCE_PATHS = (
    "tooling/ci_otio_alab_readonly.py", "tooling/tests/test_ci_otio_alab_readonly.py",
    "tooling/otio-media/alab_readonly.py", "tooling/otio-media/test_bridge.py",
    "tooling/ci_test_runner.py", "tooling/ci_alab_source_control_custody.py",
    "tooling/tests/test_ci_alab_source_control_custody.py",
)
FALSE_CLAIMS = ("decoded_frames_verified", "candidate_admitted", "human_acceptance",
                "release_authorized", "automatic_retry")
RESULT_KEYS = {
    "schema", "request_sha256", "context", "source_tree", "job_name", "status",
    "execution_status", "intake", "tools", "media", "originals",
    "prohibited_effects", "source_origin", *FALSE_CLAIMS,
}
OBSERVED = "READONLY_METADATA_ORIGINALS_RETAINED"
UNKNOWN = "UNKNOWN_NO_RETRY"
FFPROBE = Path("/usr/bin/ffprobe")
REQUIRED_CALLER_ORIGINALS = {
    "recipient.original.json", "admission.original.json", "request.original.json",
    "source-before.json", "source-after.json", "outer-argv.original.json",
    "outer-terminal.original.json", "stdout.original.bin", "stderr.original.bin",
    *{label + "-identity-" + phase + ".json" for label in ("python", "env", "ffprobe")
      for phase in ("before", "after")},
}


class Rejected(ValueError):
    pass


def require(value, message):
    if not value:
        raise Rejected(message)


def exact(actual, expected, label):
    """Closed JSON equality also rejects bool-as-int and float-as-int aliases."""
    require(type(actual) is type(expected), label + ":type")
    if type(expected) is dict:
        require(set(actual) == set(expected), label + ":fields")
        for key in expected:
            exact(actual[key], expected[key], label + "." + key)
    elif type(expected) is list:
        require(len(actual) == len(expected), label + ":length")
        for index, item in enumerate(expected):
            exact(actual[index], item, label + f"[{index}]")
    else:
        require(actual == expected, label + ":value")


def static_contract():
    return copy.deepcopy({
        "schema": owner.REQUEST_SCHEMA, "mode": SCOPE, "job_name": JOB,
        "archive": owner.ARCHIVE, "members": owner.MEMBERS,
        "limits": owner.LIMITS, "prohibited_effects": owner.PROHIBITED_EFFECTS,
    })


def contract_sha256():
    return custody.sha(custody.json_bytes(static_contract()))


def validate_dispatch(environment, platform, uid, source_sha):
    require(platform == "linux" and type(uid) is int and uid > 0,
            "requires_disposable_nonroot_linux")
    for key, value in {
        "GITHUB_ACTIONS": "true", "CI": "true", "RUNNER_OS": "Linux",
        "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_JOB": JOB, "MOTIONWRIGHT_KDENLIVE_GUI_SCOPE": SCOPE,
        "GITHUB_SHA": source_sha,
    }.items():
        exact(environment.get(key), value, "dispatch." + key)
    require(type(source_sha) is str and re.fullmatch(r"[0-9a-f]{40}", source_sha),
            "source_sha_required")
    require(type(environment.get("GITHUB_RUN_ID")) is str
            and re.fullmatch(r"[1-9][0-9]{0,23}", environment["GITHUB_RUN_ID"]),
            "run_id_required")


def source_snapshot(source_sha):
    """Reuse the existing exact-blob observer, adding only this caller's closure."""
    observed = gui.source_snapshot(source_sha)
    def git(*args):
        return subprocess.run(["git", "-C", str(ROOT), *args], check=True,
                              capture_output=True, timeout=20).stdout
    require(not git("status", "--porcelain", "--untracked-files=no").strip(),
            "tracked_checkout_is_dirty")
    tree = git("rev-parse", "HEAD^{tree}").decode("ascii").strip()
    require(re.fullmatch(r"[0-9a-f]{40}", tree), "source_tree_required")
    for relative in ADDITIONAL_SOURCE_PATHS:
        expected = git("show", source_sha + ":" + relative)
        raw, _ = custody.regular(ROOT / relative, 2 * 1024 * 1024)
        require(raw == expected, "alab_source_blob_changed:" + relative)
        observed["files"][relative] = {"bytes": len(raw), "sha256": custody.sha(raw)}
    raw, _ = custody.regular(ROOT / "SOURCE_LOCK.json", 2 * 1024 * 1024)
    lock = custody.strict(raw)
    semwright = lock["dependencies"]["semwright"]["revision"]
    require(type(semwright) is str and re.fullmatch(r"[0-9a-f]{40}", semwright),
            "sdk_lock_sha_required")
    observed.update(tree=tree, semwright_sha=semwright)
    return observed


def context_for(environment, source):
    value = {"source_sha": source["checkout"], "semwright_sha": source["semwright_sha"],
             "run_id": environment["GITHUB_RUN_ID"],
             "run_attempt": environment["GITHUB_RUN_ATTEMPT"]}
    return custody.validate_context(value)


def validate_admission(raw, expected_digest, recipient, source, context):
    require(type(expected_digest) is str
            and re.fullmatch(r"[0-9a-f]{64}", expected_digest),
            "frozen_admission_sha256_required")
    require(type(raw) is bytes and 0 < len(raw) <= 16384
            and custody.sha(raw) == expected_digest, "frozen_admission_digest_differs")
    value = custody.strict(raw)
    require(set(value) == ADMISSION_KEYS, "frozen_admission_fields")
    reference = value["admission_reference"]
    require(type(reference) is str and 0 < len(reference) <= 4096
            and reference.strip() == reference
            and not any(ord(char) < 32 or ord(char) == 127 for char in reference),
            "external_admission_reference_required")
    expected = {
        "schema": ADMISSION_SCHEMA, "authority": "release-integrator", "admission_reference": reference,
        "scope": SCOPE, "job_name": JOB, "source_sha": context["source_sha"],
        "source_tree": source["tree"], "semwright_sha": context["semwright_sha"],
        "run_attempt": 1, "recipient_spki_sha256": recipient["sha256"],
        "existing_recipient": True, "request_contract_sha256": contract_sha256(),
        "metadata_only": True, "automatic_retry": False, "source_origin": "NOT_OBSERVED",
        "decoded_frames_verified": False, "candidate_admitted": False,
        "human_acceptance": False, "release_authorized": False,
    }
    exact(value, expected, "frozen_admission")
    custody.public_key(recipient)
    return value


def request_for(admission, context):
    request = static_contract()
    request.update(context={**context, "run_attempt": 1},
                   source_tree=admission["source_tree"],
                   recipient_spki_sha256=admission["recipient_spki_sha256"],
                   admission_reference=admission["admission_reference"])
    owner.validate_request(request)
    return request


def command_argv(request_path, evidence_root, context):
    """run_tool deliberately clears the environment; carry only the exact seam."""
    return ["/usr/bin/env", "GITHUB_SHA=" + context["source_sha"],
            "GITHUB_RUN_ID=" + context["run_id"],
            "GITHUB_RUN_ATTEMPT=" + context["run_attempt"], "GITHUB_JOB=" + JOB,
            "PYTHONDONTWRITEBYTECODE=1", sys.executable, "-B",
            str(ROOT / "tooling/otio-media/bridge.py"), SCOPE,
            "--request", str(request_path), "--evidence-root", str(evidence_root),
            "--ffprobe", str(FFPROBE)]


def snapshot_files(packed):
    require(type(packed) is bytes and len(packed) <= custody.MAX_TOTAL
            + custody.MAX_FILES * 4096, "snapshot_not_bounded_immutable_bytes")
    files, total = {}, 0
    with tarfile.open(fileobj=io.BytesIO(packed), mode="r:") as archive:
        for member in archive:
            name = custody.snapshot_member(member.name)
            require(name not in files and member.isfile() and not member.sparse
                    and len(files) <= custody.MAX_FILES
                    and 0 <= member.size <= custody.MAX_FILE,
                    "snapshot_member_not_unique_regular_bounded")
            total += member.size
            require(total <= custody.MAX_TOTAL, "snapshot_total_size")
            stream = archive.extractfile(member)
            require(stream is not None, "snapshot_member_unreadable")
            with stream:
                raw = stream.read(member.size + 1)
            require(len(raw) == member.size, "snapshot_member_size_differs")
            files[name] = raw
    return files


def descriptor_bytes(files, descriptor):
    require(type(descriptor) is dict and set(descriptor) == {"path", "bytes", "sha256"},
            "original_descriptor_fields")
    name = custody.snapshot_member(descriptor["path"])
    require(":" not in name and all(
        not part.endswith((".", " "))
        and re.fullmatch(r"(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part) is None
        for part in name.split("/")), "original_path_alias")
    require(type(descriptor["bytes"]) is int and descriptor["bytes"] >= 0
            and type(descriptor["sha256"]) is str
            and re.fullmatch(r"[0-9a-f]{64}", descriptor["sha256"]),
            "original_descriptor_types")
    raw = files.get("alab/" + name)
    require(type(raw) is bytes and len(raw) == descriptor["bytes"]
            and custody.sha(raw) == descriptor["sha256"], "original_bytes_differ")
    return raw


def verify_process(record, files, argv, cwd):
    require(type(record) is dict and set(record) == {"terminal", "originals", "custody_errors"}
            and record["custody_errors"] == [], "process_custody_contract")
    originals = record["originals"]
    require(type(originals) is dict and set(originals) == {"argv", "stdout", "stderr", "terminal"},
            "process_originals_incomplete")
    raw = {key: descriptor_bytes(files, value) for key, value in originals.items()}
    terminal = owner.strict_object(raw["terminal"], "retained process terminal")
    exact(record["terminal"], terminal, "process_terminal_original")
    require(set(terminal) == {
        "spawned", "exit_code", "execution_status", "timed_out", "overflow", "forced",
        "reader_errors", "cleanup_errors", "primary_error", "elapsed_seconds", "pid",
        "stdout", "stderr"}, "process_terminal_fields")
    require(type(terminal["pid"]) is int and terminal["pid"] > 0
            and type(terminal["elapsed_seconds"]) in (int, float)
            and math.isfinite(terminal["elapsed_seconds"])
            and 0 <= terminal["elapsed_seconds"] <= owner.LIMITS["inner_total_wall_seconds_max"],
            "process_identity_or_elapsed_invalid")
    require(terminal.get("spawned") is True
            and terminal.get("execution_status") == "TERMINAL_OBSERVED"
            and type(terminal.get("exit_code")) is int and terminal["exit_code"] == 0
            and terminal.get("timed_out") is False and terminal.get("overflow") is False
            and terminal.get("forced") is False and terminal.get("reader_errors") == []
            and terminal.get("cleanup_errors") == [] and terminal.get("primary_error") is None,
            "process_terminal_not_known_success")
    for label in ("stdout", "stderr"):
        state = terminal.get(label)
        require(type(state) is dict and set(state) == {"observed_bytes", "retained_bytes", "complete"}
                and state.get("complete") is True
                and type(state.get("observed_bytes")) is int
                and type(state.get("retained_bytes")) is int
                and state["observed_bytes"] == state["retained_bytes"] == len(raw[label])
                and len(raw[label]) <= owner.MAX_JSON, "process_stream_incomplete")
    original_argv = owner.strict_object(raw["argv"], "retained argv")
    require(set(original_argv) == {"argv", "cwd", "automatic_retry"}
            and original_argv["automatic_retry"] is False
            and original_argv["cwd"] == str(cwd), "process_argv_contract")
    exact(original_argv["argv"], argv, "process_argv")
    return raw


def verify_intake(intake, files):
    require(type(intake) is dict and set(intake) == {
        "requests", "requested_bytes", "observed_bytes", "ranges", "members",
        "source_tree_and_recipient_admission", "elapsed_seconds", "descriptor_adjustment"},
        "intake_fields_or_failure")
    require(intake["source_tree_and_recipient_admission"] == "EXTERNAL_CALLER_PRE_POST_PROOF"
            and type(intake["elapsed_seconds"]) in (int, float)
            and math.isfinite(intake["elapsed_seconds"])
            and 0 <= intake["elapsed_seconds"] <= owner.LIMITS["inner_total_wall_seconds_max"]
            and type(intake["descriptor_adjustment"]) is str,
            "intake_admission_or_elapsed")
    for key, limit in (("requests", owner.LIMITS["all_range_requests_max"]),
                       ("requested_bytes", owner.LIMITS["range_bytes_budget"]),
                       ("observed_bytes", owner.LIMITS["range_bytes_budget"])):
        require(type(intake[key]) is int and 0 < intake[key] <= limit,
                "intake_budget_or_integer")
    ranges, members = intake["ranges"], intake["members"]
    require(type(ranges) is list and len(ranges) == intake["requests"]
            and type(members) is list and len(members) == len(owner.MEMBERS) == 5,
            "intake_roster_incomplete")
    cumulative, intervals = 0, set()
    for index, row in enumerate(ranges, 1):
        require(type(row) is dict and set(row) == {
            "request_number", "start", "length", "end_inclusive", "url", "etag",
            "automatic_retry", "complete", "validation_status", "response", "header_capture",
            "body_capture", "observed_bytes_total", "retained_body_bytes", "custody_errors",
            "headers", "body", "terminal"}, "range_fields_or_failure")
        for key in ("request_number", "start", "length", "end_inclusive",
                    "observed_bytes_total", "retained_body_bytes"):
            require(type(row[key]) is int, "range_exact_integer:" + key)
        start, length = row["start"], row["length"]
        require(row["request_number"] == index and 0 <= start < owner.ARCHIVE["bytes"]
                and 0 < length <= owner.LIMITS["one_request_max"]
                and row["end_inclusive"] == start + length - 1 < owner.ARCHIVE["bytes"]
                and (start, length) not in intervals, "range_identity_or_retry")
        intervals.add((start, length))
        cumulative += length
        require(row["url"] == owner.ARCHIVE["url"] and row["etag"] == owner.ARCHIVE["etag"]
                and row["automatic_retry"] is False and row["complete"] is True
                and row["validation_status"] == "VALIDATED_RANGE" and row["custody_errors"] == []
                and row["observed_bytes_total"] == cumulative
                and row["retained_body_bytes"] == length, "range_incomplete")
        header_raw = descriptor_bytes(files, row["headers"])
        body_raw = descriptor_bytes(files, row["body"])
        require(0 < len(header_raw) <= 65536 and len(body_raw) == length, "range_raw_size")
        for key, raw in (("header_capture", header_raw), ("body_capture", body_raw)):
            exact(row[key], {"observed_bytes": len(raw), "retained_bytes": len(raw), "complete": True},
                  "range." + key)
        metadata = row["response"]
        require(type(metadata) is dict and set(metadata) == {"status", "headers"}
                and type(metadata["status"]) is int and metadata["status"] == 206,
                "range_http_status")
        headers = owner._header_map(metadata)
        require(headers.get("etag") == owner.ARCHIVE["etag"]
                and headers.get("content-range") == f"bytes {start}-{start + length - 1}/{owner.ARCHIVE['bytes']}"
                and headers.get("content-length") == str(length)
                and headers.get("content-encoding", "identity") == "identity"
                and "transfer-encoding" not in headers, "range_http_headers")
        class RetainedHeaders:
            def makefile(self, *_args, **_kwargs):
                return io.BytesIO(header_raw)
        response = http.client.HTTPResponse(RetainedHeaders())
        response.begin()
        try:
            exact({"status": response.status,
                   "headers": [list(pair) for pair in response.headers.raw_items()]},
                  metadata, "range_retained_headers")
            require(response.fp is not None and response.fp.tell() == len(header_raw),
                    "range_header_tail")
        finally:
            response.close()
        original = {key: value for key, value in row.items() if key != "terminal"}
        original.update(complete=False, validation_status="NOT_CLASSIFIED")
        exact(owner.strict_object(descriptor_bytes(files, row["terminal"]), "HTTP original terminal"),
              original, "range_original_before_classification")
        intent = {key: original[key] for key in (
            "request_number", "start", "length", "end_inclusive", "url", "etag",
            "automatic_retry", "complete", "validation_status")}
        exact(owner.strict_object(files[f"alab/http/range-{index:02d}.request.json"], "HTTP original request"),
              intent, "range_original_request")
    require(cumulative == intake["requested_bytes"] == intake["observed_bytes"],
            "intake_incomplete_bytes")
    for row, pin in zip(members, owner.MEMBERS):
        require(type(row) is dict and set(row) == {
            "path", "bytes", "sha256", "crc32", "name", "descriptor", "local_header_flags"},
            "member_fields")
        for key, value in {"path": pin["retained_path"], "bytes": pin["bytes"],
                           "sha256": pin["sha256"], "crc32": pin["crc32"], "name": pin["name"]}.items():
            exact(row[key], value, "member." + key)
        flags = row["local_header_flags"]
        require(type(flags) is int and flags in (0, 8, 2048, 2056), "member_flags")
        if flags & 8:
            descriptor = row["descriptor"]
            require(type(descriptor) is dict and type(descriptor.get("signed")) is bool,
                    "member_descriptor_required")
            signed = descriptor["signed"]
            peek = descriptor.get("adjacent_peek_hex")
            require(type(peek) is str and (peek == "" if signed else re.fullmatch(r"[0-9a-f]{8}", peek)),
                    "member_descriptor_peek")
            exact(descriptor, {
                "signed": signed, "bytes": 16 if signed else 12, "crc32": pin["crc32"],
                "compressed_bytes": pin["compressed_bytes"], "uncompressed_bytes": pin["bytes"],
                "adjacent_peek_hex": peek, "adjacent_peek_interpreted": False,
            }, "member_descriptor")
        else:
            require(row["descriptor"] is None, "member_unexpected_descriptor")


def verify_snapshot(packed, request_raw, result_raw, evidence_root, expected_caller):
    """Validate the one immutable collected snapshot, without rereading live files."""
    files = snapshot_files(packed)
    require(type(expected_caller) is dict and set(expected_caller) == REQUIRED_CALLER_ORIGINALS
            and all(type(raw) is bytes for raw in expected_caller.values()),
            "caller_original_roster_incomplete")
    manifest = custody.strict(files["snapshot-manifest.json"])
    require(manifest.get("schema") == "motionwright.ci.private-snapshot/1"
            and manifest.get("selected_originals_complete") is True
            and manifest.get("complete_owner_state_claimed") is False
            and type(manifest.get("roots")) is dict
            and set(manifest["roots"]) == {"alab", "caller"}
            and all(row.get("status") == "CAPTURED" for row in manifest["roots"].values()),
            "snapshot_collection_not_complete")
    for name, raw in expected_caller.items():
        require(files.get("caller/" + name) == raw, "caller_original_changed:" + name)
    require(files.get("alab/result.json") == result_raw, "result_original_changed")
    request = owner.strict_object(request_raw, "request")
    result = owner.strict_object(result_raw, "result")
    require(set(result) == RESULT_KEYS, "result_fields")
    outer = custody.strict(files["caller/outer-terminal.original.json"])
    exact(outer, {"execution_status": "TERMINAL_OBSERVED", "exit_code": 0,
                  "automatic_retry": False}, "outer_terminal")
    exact(owner.strict_object(files["caller/stdout.original.bin"], "outer stdout"),
          result, "outer_stdout_result")
    for key, value in {
        "schema": owner.RESULT_SCHEMA, "request_sha256": custody.sha(request_raw),
        "context": request["context"], "source_tree": request["source_tree"],
        "job_name": JOB, "status": OBSERVED, "execution_status": "TERMINAL_OBSERVED",
        "prohibited_effects": owner.PROHIBITED_EFFECTS, "source_origin": "NOT_OBSERVED",
        **{key: False for key in FALSE_CLAIMS},
    }.items():
        exact(result[key], value, "result." + key)
    originals = result["originals"]
    require(type(originals) is list and 0 < len(originals) <= custody.MAX_FILES,
            "original_roster_missing")
    names = []
    for descriptor in originals:
        descriptor_bytes(files, descriptor)
        names.append(descriptor["path"])
    require(len(names) == len(set(names))
            and {"alab/" + name for name in names}
                == {name for name in files if name.startswith("alab/")}
                   - {"alab/result.json"}, "original_roster_not_complete_unique")
    require(files.get("alab/request.original.json") == request_raw,
            "owner_request_original_changed")
    for member in owner.MEMBERS:
        descriptor_bytes(files, {key: member[key] for key in ("bytes", "sha256")}
                         | {"path": member["retained_path"]})
    verify_intake(result["intake"], files)
    tools = result["tools"]
    require(type(tools) is list and len(tools) == 1 and type(tools[0]) is dict
            and set(tools[0]) == {"name", "identity_before", "identity_after", "version"}
            and tools[0]["name"] == "ffprobe", "ffprobe_tool_roster")
    exact(tools[0]["identity_before"], tools[0]["identity_after"], "ffprobe_identity")
    identity = tools[0]["identity_before"]
    require(type(identity) is dict and set(identity) == {
        "path", "bytes", "sha256", "device", "inode", "mtime_ns"}
            and all(type(identity[key]) is int for key in ("bytes", "device", "inode", "mtime_ns"))
            and identity["bytes"] > 0 and type(identity.get("sha256")) is str
            and re.fullmatch(r"[0-9a-f]{64}", identity["sha256"]), "ffprobe_identity_absent")
    caller_identity = custody.strict(files["caller/ffprobe-identity-before.json"])
    require(identity.get("path") == str(FFPROBE)
            and identity["bytes"] == caller_identity["size"]
            and identity["sha256"] == caller_identity["sha256"]
            and all(identity.get(key) == caller_identity["before"][key]
                    for key in ("device", "inode", "mtime_ns")),
            "ffprobe_identity_not_caller_observation")
    version = verify_process(tools[0]["version"], files, [str(FFPROBE), "-version"], evidence_root)
    require(version["stdout"].startswith(b"ffprobe version "), "ffprobe_version_original")
    members = [row for row in owner.MEMBERS if row["kind"] == "video-original"]
    media = result["media"]
    require(type(media) is list and len(media) == len(members) == 2, "media_roster_incomplete")
    for row, member in zip(media, members):
        require(type(row) is dict and set(row) == {
            "path", "bytes", "sha256", "crc32", "status", "process", "observations",
            "source_origin", "decoded_frames_verified"}, "media_fields")
        for key, value in {"path": member["retained_path"], "bytes": member["bytes"],
                           "sha256": member["sha256"], "crc32": member["crc32"],
                           "status": "METADATA_ORIGINALS_RETAINED",
                           "source_origin": "NOT_OBSERVED", "decoded_frames_verified": False}.items():
            exact(row[key], value, "media." + key)
        argv = [str(FFPROBE), "-v", "error", "-protocol_whitelist", "file",
                "-threads", "1", "-max_alloc", "268435456", "-show_streams",
                "-show_format", "-of", "json", str(evidence_root / member["retained_path"])]
        raw = verify_process(row["process"], files, argv, evidence_root)
        metadata = owner.strict_object(raw["stdout"], "ffprobe metadata original")
        require(type(metadata.get("streams")) is list
                and all(type(stream) is dict for stream in metadata["streams"])
                and type(metadata.get("format")) is dict, "all_streams_metadata_required")
        exact(row["observations"], metadata, "ffprobe_original_observations")
    return True


def preserve_unknown(status, replacement):
    return UNKNOWN if status == UNKNOWN else replacement


def seal_and_publish(caller, owner_root, public_dir, recipient, context, source_tree,
                     status, request_raw, result_raw, expected_caller, admission_digest):
    sealed, complete, bound = None, False, False
    custody_status = "FAILED"
    try:
        packed, complete = custody.gather_snapshot(
            {"caller": caller, "alab": owner_root}, preserve_partial=True)
        if status == OBSERVED and complete:
            try:
                bound = verify_snapshot(packed, request_raw, result_raw, owner_root, expected_caller)
                require(bound is True, "snapshot_binding_not_verified")
            except Exception:
                status = "SNAPSHOT_BINDING_FAILED"
        if not complete:
            status = preserve_unknown(status, "CUSTODY_INCOMPLETE")
        candidate = custody.encrypt_snapshot(packed, recipient, context)
        custody.new_bytes(public_dir / "original-evidence.mwseal", candidate)
        sealed = candidate
        custody_status = "COMPLETE" if complete else "INCOMPLETE"
    except Exception:
        status = preserve_unknown(status, "CUSTODY_FAILED")
    projection = {
        "schema": "motionwright.otio.external-alab-readonly-projection/1",
        "scope": SCOPE, "source": context, "source_tree": source_tree, "job_name": JOB,
        "status": status, "frozen_admission_sha256": admission_digest,
        "ciphertext_sha256": None if sealed is None else custody.sha(sealed),
        "custody_status": custody_status, "snapshot_collection_complete": complete,
        "snapshot_binding_verified": bound,
        "source_origin": "NOT_OBSERVED", "metadata_only": True,
        **{key: False for key in FALSE_CLAIMS},
        "other_workflow_selections": "NOT_RUN",
    }
    custody.new_json(public_dir / "projection.json", projection)
    return projection


def execute_once(argv, records):
    records.emit({"step": SCOPE, "state": "INTENT", "argv": argv,
                  "outer_wall_seconds": 120, "automatic_retry": False})
    terminal = {"execution_status": UNKNOWN, "exit_code": None, "automatic_retry": False}
    try:
        code = run_tool(argv, records.root / "stdout.original.bin",
                        records.root / "stderr.original.bin", records.root)
        if type(code) is int:
            terminal.update(execution_status="TERMINAL_OBSERVED", exit_code=code)
        else:
            terminal["invalid_exit_type"] = type(code).__name__
    except BaseException as error:
        terminal["original_error"] = {"type": type(error).__name__, "message": str(error)}
    records.document("outer-terminal.original.json", terminal)
    records.emit({"step": SCOPE, "state": terminal["execution_status"],
                  "exit_code": terminal["exit_code"], "automatic_retry": False})
    return terminal


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--admission-sha256", required=True)
    for name in ("recipient", "admission", "evidence-root", "public-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    validate_dispatch(os.environ, sys.platform, os.getuid(), args.source_sha)
    require(Path.cwd().resolve(strict=True) == ROOT, "caller_cwd_not_source_root")
    runner = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    require(all(path.is_absolute() and path.parent == runner for path in
                (args.recipient, args.admission, args.evidence_root, args.public_dir)),
            "inputs_and_outputs_must_be_direct_runner_children")
    recipient_raw, _ = custody.regular(args.recipient, 8192)
    admission_raw, _ = custody.regular(args.admission, 16384)
    recipient = custody.strict(recipient_raw)
    before = source_snapshot(args.source_sha)
    context = context_for(os.environ, before)
    admission = validate_admission(admission_raw, args.admission_sha256, recipient, before, context)
    request_raw = custody.json_bytes(request_for(admission, context))
    old_umask = os.umask(0o077)
    try:
        gui.fresh_directory(args.evidence_root)
        gui.fresh_directory(args.public_dir)
        caller, owner_root = args.evidence_root / "caller", args.evidence_root / "alab"
        gui.fresh_directory(caller)
        (caller / "tmp").mkdir(mode=0o700)
        records = gui.Recorder(caller)
        status, result_raw, expected, source_after = "NOT_STARTED", b"", {}, None
        try:
            for name, raw in {
                "recipient.original.json": recipient_raw, "admission.original.json": admission_raw,
                "request.original.json": request_raw, "source-before.json": custody.json_bytes(before),
            }.items():
                custody.new_bytes(caller / name, raw)
                expected[name] = raw
            request_path = caller / "request.original.json"
            command = command_argv(request_path, owner_root, context)
            original_command = {"argv": command, "cwd": str(ROOT), "automatic_retry": False}
            records.document("outer-argv.original.json", original_command)
            expected["outer-argv.original.json"] = custody.json_bytes(original_command)
            for label, path in (("python", Path(sys.executable).resolve(strict=True)),
                                ("env", Path("/usr/bin/env")), ("ffprobe", FFPROBE)):
                _, identity = custody.regular(path, 256 * 1024 * 1024)
                name = label + "-identity-before.json"
                records.document(name, identity)
                expected[name] = custody.json_bytes(identity)
            # A later recording exception cannot erase uncertainty once invocation begins.
            status = UNKNOWN
            terminal = execute_once(command, records)
            status = UNKNOWN if terminal["execution_status"] == UNKNOWN else "FAILED"
            expected["outer-terminal.original.json"] = custody.json_bytes(terminal)
            for name in ("stdout.original.bin", "stderr.original.bin"):
                expected[name] = custody.regular(caller / name, 128 * 1024 * 1024)[0]
            if status != UNKNOWN:
                try:
                    result_raw, _ = custody.regular(owner_root / "result.json", owner.MAX_JSON)
                    result = owner.strict_object(result_raw, "ALab owner result")
                    if result.get("status") == UNKNOWN or result.get("execution_status") == UNKNOWN:
                        status = UNKNOWN
                    elif terminal["exit_code"] == 0 and result.get("status") == OBSERVED:
                        status = OBSERVED
                except Exception:
                    status = UNKNOWN
        except BaseException as error:
            status = preserve_unknown(status, "FAILED")
            try:
                records.document("caller-error.original.json", {
                    "type": type(error).__name__, "message": str(error), "automatic_retry": False})
            except BaseException:
                status = preserve_unknown(status, "LOCAL_RECORDING_FAILED")
        finally:
            try:
                validate_dispatch(os.environ, sys.platform, os.getuid(), args.source_sha)
                require(Path.cwd().resolve(strict=True) == ROOT, "caller_cwd_changed")
                source_after = source_snapshot(args.source_sha)
                require(source_after == before and context_for(os.environ, source_after) == context,
                        "source_or_execution_context_changed")
                require(custody.regular(args.recipient, 8192)[0] == recipient_raw
                        and custody.regular(args.admission, 16384)[0] == admission_raw,
                        "recipient_or_frozen_admission_changed")
                for label, path in (("python", Path(sys.executable).resolve(strict=True)),
                                    ("env", Path("/usr/bin/env")), ("ffprobe", FFPROBE)):
                    _, identity = custody.regular(path, 256 * 1024 * 1024)
                    require(custody.json_bytes(identity)
                            == expected[label + "-identity-before.json"],
                            "executable_identity_changed:" + label)
                    name = label + "-identity-after.json"
                    records.document(name, identity)
                    expected[name] = custody.json_bytes(identity)
                records.document("source-after.json", source_after)
                expected["source-after.json"] = custody.json_bytes(source_after)
            except BaseException as error:
                status = preserve_unknown(status, "SOURCE_OR_ADMISSION_CHANGED")
                try:
                    records.document("source-after-error.original.json", {
                        "type": type(error).__name__, "message": str(error), "automatic_retry": False})
                except BaseException:
                    status = preserve_unknown(status, "LOCAL_RECORDING_FAILED")
            recorded = gui.finalize_records(records, status, {
                "owner_result_sha256": custody.sha(result_raw) if result_raw else None,
                "snapshot_binding_still_required": True,
                "source_origin": "NOT_OBSERVED", "decoded_frames_verified": False,
            }, context)
            if recorded != status:
                status = preserve_unknown(status, recorded)
        projection = seal_and_publish(caller, owner_root, args.public_dir, recipient, context,
                                      before["tree"], status, request_raw, result_raw, expected,
                                      args.admission_sha256)
        print(json.dumps(projection, sort_keys=True))
        return 0 if projection["status"] == OBSERVED and projection["snapshot_binding_verified"] else 1
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    raise SystemExit(main())
