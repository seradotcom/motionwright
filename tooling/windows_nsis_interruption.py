#!/usr/bin/env python3
"""Compile NSIS fixtures and read checkpoint custody; never execute an installer.

Structural reader results are not physical evidence. Historical lifecycle/v1 is
independent. There is no installer launch or recovery command in this module.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
from typing import Any
import uuid

GATE_PATHS = (".github/workflows/windows-nsis-interruption.yml",
              "tooling/windows_nsis_interruption.py",
              "tooling/tests/test_windows_nsis_interruption.py", "tooling/ci_test_runner.py")
PRODUCT_PATHS = (
    "apps/desktop/src-tauri/windows/installer.nsi",
    "apps/desktop/src-tauri/windows/uninstall-ownership.nsh",
    "apps/desktop/src-tauri/tauri.conf.json", "apps/desktop/src-tauri/build.rs",
    "apps/desktop/src-tauri/Cargo.toml", "docs/reliability/windows-upgrade-interruption.md",
    "tooling/nsis_owned_files.py", "apps/studio/package.json", "apps/studio/package-lock.json",
    "Cargo.toml", "Cargo.lock", "SOURCE_LOCK.json", "rust-toolchain.toml")
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
GUID = r"\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}"
CHECKPOINT = re.compile(
    r"motionwright\.nsis-checkpoint/1\|id=(?P<id>" + GUID + r")"
    r"\|pid=(?P<pid>[1-9][0-9]{0,9})"
    r"\|creation_filetime=(?P<creation_filetime>[1-9][0-9]{0,19})"
    r"\|phase=main-withdrawn-before-publication"
    r"\|stage=(?P<stage>mw-stage-" + GUID + r"\\payload\.bin)"
    r"\|candidate_sha256=(?P<candidate_sha256>[0-9a-f]{64})"
    r"\|candidate_size=(?P<candidate_size>[1-9][0-9]{0,18})\r\n\Z")
MAX_RENDER_BYTES = 4 * 1024 * 1024
MAX_INCLUDE_BYTES = 64 * 1024
MAX_PAYLOAD_BYTES = 256 * 1024 * 1024
MAX_DIAGNOSTIC_BYTES = 512 * 1024 * 1024
MAX_DIAGNOSTIC_FILES = 4096
OPT_IN_PREFIX = b"!define MWINST_CI_INTERRUPTION\n"
DEFINE = re.compile(r"^\s*!define\s+MWINST_CI_INTERRUPTION(?:\s|$)", re.MULTILINE)


class ContractError(ValueError):
    pass


def require(value: Any, reason: str) -> None:
    if not value:
        raise ContractError(reason)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def positive_decimal(value: Any, maximum: int, name: str) -> str:
    require(isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value), name)
    require(len(value) <= 20 and int(value) <= maximum, name)
    return value


def canonical_guid(value: Any) -> str:
    require(isinstance(value, str) and re.fullmatch(GUID, value), "guid_encoding")
    parsed = uuid.UUID(value[1:-1])
    require(parsed.int != 0 and "{" + str(parsed).upper() + "}" == value, "guid_canonical")
    return value


def parse_checkpoint(raw: bytes) -> dict[str, Any]:
    require(type(raw) is bytes and 0 < len(raw) <= 2048 and len(raw) % 2 == 0,
            "checkpoint_byte_length")
    require(not raw.startswith((b"\xff\xfe", b"\xfe\xff")), "checkpoint_bom")
    try:
        text = raw.decode("utf-16-le", errors="strict")
    except UnicodeError as error:
        raise ContractError("checkpoint_utf16le") from error
    match = CHECKPOINT.fullmatch(text)
    require(match is not None, "checkpoint_closed_schema")
    result = match.groupdict()
    canonical_guid(result["id"])
    canonical_guid(result["stage"][len("mw-stage-"):-len("\\payload.bin")])
    positive_decimal(result["pid"], 2**32 - 1, "checkpoint_pid")
    positive_decimal(result["creation_filetime"], 2**64 - 1, "checkpoint_filetime")
    positive_decimal(result["candidate_size"], 2**63 - 1, "checkpoint_size")
    result.update({"schema": "motionwright.nsis-checkpoint/1",
                   "phase": "main-withdrawn-before-publication",
                   "original_sha256": digest(raw), "original_bytes": len(raw)})
    return result


def validate_live_context(checkpoint: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Validate producer assertions; this reader never manufactures a live handle."""
    require(context.get("schema") == "motionwright.nsis-live-context/1", "context_schema")
    require(context.get("state") == "LIVE", "context_not_live")
    for field in ("original_handle_retained", "same_handle_identity_observed",
                  "private_directory_admitted", "predecessor_profile_admitted"):
        require(context.get(field) is True, field)
    for field in ("id", "pid", "creation_filetime", "candidate_sha256", "candidate_size"):
        require(type(context.get(field)) is str and context[field] == checkpoint[field],
                "context_mismatch_" + field)
    for field in ("setup_sha256", "predecessor_profile_sha256"):
        require(type(context.get(field)) is str and SHA256.fullmatch(context[field]), field)
    for field in ("gate_sha", "product_sha"):
        require(type(context.get(field)) is str and SHA40.fullmatch(context[field]), field)
    for field in ("run_id", "run_attempt"):
        positive_decimal(context.get(field), 2**63 - 1, field)
    require(isinstance(context.get("job"), str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", context["job"]), "job")
    require(context.get("launch_count") == 1 and type(context["launch_count"]) is int,
            "launch_count")
    return {"schema": "motionwright.nsis-checkpoint-structure/1",
            "result": "STRUCTURALLY_ADMITTED", "checkpoint_sha256": checkpoint["original_sha256"],
            "physical_execution": "NOT_RUN", "human_acceptance": "NOT_RUN"}


def validate_termination(checkpoint: dict[str, Any], context: dict[str, Any],
                         terminal: dict[str, Any]) -> dict[str, str]:
    """No TerminateProcess, wait, or recovery is performed by this reader."""
    validate_live_context(checkpoint, context)
    require(terminal.get("schema") == "motionwright.nsis-terminal/1", "terminal_schema")
    for field in ("id", "pid", "creation_filetime", "setup_sha256", "predecessor_profile_sha256",
                  "gate_sha", "product_sha", "run_id", "run_attempt", "job"):
        require(field in context and terminal.get(field) == context[field],
                "terminal_mismatch_" + field)
    require(terminal.get("state") == "TERMINATED", "terminal_not_known")
    require(terminal.get("termination_attempts") == 1
            and type(terminal["termination_attempts"]) is int, "termination_count")
    require(terminal.get("same_original_handle") is True, "termination_handle")
    require(terminal.get("terminate_returned_success") is True, "terminate_return")
    require(terminal.get("wait_result") == "WAIT_OBJECT_0", "wait_not_terminal")
    require(type(terminal.get("exit_code")) is int and 0 <= terminal["exit_code"] <= 2**32 - 1,
            "terminal_exit_code")
    require(terminal["exit_code"] != 4, "expired_rendezvous_exit_4")
    require(terminal.get("ambiguous") is False, "terminal_ambiguous")
    return {"result": "STRUCTURAL_TERMINAL_RECEIPT_ONLY", "recovery": "NOT_AUTHORIZED_BY_READER",
            "physical_execution": "NOT_RUN", "human_acceptance": "NOT_RUN"}


def regular_file(path: Path, maximum: int | None = None) -> dict[str, Any]:
    path = path.absolute()
    for ancestor in (path, *path.parents):
        observed = ancestor.lstat()
        require(not stat.S_ISLNK(observed.st_mode)
                and not getattr(observed, "st_file_attributes", 0) & 0x400,
                "link_or_reparse:" + str(ancestor))
    before = path.stat()
    require(stat.S_ISREG(before.st_mode), "not_regular:" + str(path))
    if maximum is not None:
        require(0 <= before.st_size <= maximum, "file_size_bound:" + str(path))
    sha, observed_bytes = hashlib.sha256(), 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            observed_bytes += len(block)
            require(observed_bytes <= before.st_size, "file_grew_during_read:" + str(path))
            sha.update(block)
    require(observed_bytes == before.st_size, "file_truncated_during_read:" + str(path))
    after = path.stat()
    require(all(getattr(before, key) == getattr(after, key)
                for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns")),
            "file_changed:" + str(path))
    return {"bytes": before.st_size, "sha256": sha.hexdigest()}


def bounded_read(path: Path, maximum: int) -> tuple[bytes, dict[str, Any]]:
    before = regular_file(path, maximum)
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    require(len(raw) == before["bytes"] and digest(raw) == before["sha256"]
            and regular_file(path, maximum) == before, "bounded_read_changed:" + str(path))
    return raw, before


def pe_header_only(path: Path, expected: dict[str, Any] | None = None) -> dict[str, Any]:
    """Check two bytes only; this is not a PE version or signature observation."""
    before = regular_file(path, MAX_PAYLOAD_BYTES)
    if expected is not None:
        require(before == expected, "pe_input_identity_changed")
    with path.open("rb") as stream:
        header = stream.read(2)
    require(header == b"MZ", "compiled_artifact_not_mz")
    require(regular_file(path, MAX_PAYLOAD_BYTES) == before, "pe_header_input_changed")
    return before


def retain_built_application(main: Path, output: Path,
                             expected: dict[str, Any]) -> dict[str, Any]:
    """Keep the real built bytes before a bundle can fail prior to MWINST_CAPTURE."""
    require(regular_file(main, MAX_PAYLOAD_BYTES) == expected, "built_application_changed")
    folder = output / "inputs/compiled-application"
    folder.mkdir(parents=True)
    retained = folder / main.name
    copied, sha = 0, hashlib.sha256()
    with main.open("rb") as source, retained.open("xb") as destination:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            require(copied + len(block) <= expected["bytes"], "built_application_grew")
            require(destination.write(block) == len(block), "built_application_short_write")
            copied += len(block)
            sha.update(block)
        destination.flush()
        os.fsync(destination.fileno())
    require({"bytes": copied, "sha256": sha.hexdigest()} == expected
            and regular_file(main, MAX_PAYLOAD_BYTES) == expected
            and regular_file(retained, MAX_PAYLOAD_BYTES) == expected,
            "built_application_capture_changed")
    record = {"schema": "motionwright.nsis-built-application-custody/1",
              "original_path": str(main.absolute()), "retained_path": str(retained.absolute()),
              "identity": expected, "physical_pe_version": "NOT_OBSERVED",
              "installer_execution": "NOT_RUN"}
    write_json(folder / "receipt.json", record)
    return record


def file_bound(path: Path) -> int:
    if path.suffix.lower() == ".nsi":
        return MAX_RENDER_BYTES
    if path.suffix.lower() == ".nsh":
        return MAX_INCLUDE_BYTES
    return MAX_PAYLOAD_BYTES


def archive_inventory(root: Path) -> dict[str, int]:
    """Bound every name and byte before opening any payload for hashing."""
    result, total = {}, 0
    for ancestor in (root, *root.parents):
        observed = ancestor.lstat()
        require(stat.S_ISDIR(observed.st_mode) and not stat.S_ISLNK(observed.st_mode)
                and not getattr(observed, "st_file_attributes", 0) & 0x400,
                "archive_reparse_directory")
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            observed = (Path(current) / name).lstat()
            require(not stat.S_ISLNK(observed.st_mode)
                    and not getattr(observed, "st_file_attributes", 0) & 0x400,
                    "archive_reparse_directory")
        for name in files:
            require(len(result) < MAX_DIAGNOSTIC_FILES, "artifact_file_bound")
            path = Path(current) / name
            observed = path.lstat()
            require(stat.S_ISREG(observed.st_mode)
                    and not getattr(observed, "st_file_attributes", 0) & 0x400,
                    "archive_nonregular_file")
            require(0 <= observed.st_size <= file_bound(path), "artifact_member_size_bound")
            require(total + observed.st_size <= MAX_DIAGNOSTIC_BYTES,
                    "artifact_total_size_bound")
            total += observed.st_size
            result[path.relative_to(root).as_posix()] = observed.st_size
    return result


def diagnostic_budget(output: Path) -> dict[str, Any]:
    """Admit the explicit upload scope, without scanning Cargo caches or deps."""
    total, count = 0, 0
    for relative in ("commands", "inputs", "original-source", "variants",
                     "target/release/nsis", "target/release/bundle/nsis"):
        root = output / relative
        if not root.exists():
            continue
        inventory = archive_inventory(root)
        total += sum(inventory.values())
        count += len(inventory)
        require(total <= MAX_DIAGNOSTIC_BYTES, "diagnostic_total_size_bound")
        require(count <= MAX_DIAGNOSTIC_FILES, "diagnostic_file_count_bound")
    for name in ("source-before.json", "compile-summary.json"):
        path = output / name
        if path.exists():
            observed = path.lstat()
            require(stat.S_ISREG(observed.st_mode) and observed.st_size <= MAX_RENDER_BYTES,
                    "diagnostic_projection_size_bound")
            total += observed.st_size
            count += 1
    require(total <= MAX_DIAGNOSTIC_BYTES and count <= MAX_DIAGNOSTIC_FILES,
            "diagnostic_aggregate_bound")
    return {"bytes": total, "files": count, "maximum_bytes": MAX_DIAGNOSTIC_BYTES,
            "maximum_files": MAX_DIAGNOSTIC_FILES,
            "scope": "explicit_compilation_diagnostics_at_observation",
            "unknown_outputs_stable": False}


def write_new(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def write_json(path: Path, value: Any) -> None:
    write_new(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def git(repo: Path, *arguments: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *arguments], check=True,
                            capture_output=True, timeout=30)
    return result.stdout


def source_observation(repo: Path, expected: str, paths: tuple[str, ...]) -> dict[str, Any]:
    require(SHA40.fullmatch(expected), "source_sha_not_full")
    require(git(repo, "rev-parse", "HEAD").decode().strip() == expected, "source_head_mismatch")
    require(not git(repo, "status", "--porcelain=v1", "--untracked-files=no"), "tracked_source_dirty")
    git(repo, "ls-files", "--error-unmatch", "--", *paths)
    files = {}
    for relative in paths:
        blob = git(repo, "show", expected + ":" + relative)
        path = repo / relative
        identity = regular_file(path)
        checkout = path.read_bytes()
        require(identity == {"bytes": len(checkout), "sha256": digest(checkout)},
                "checkout_changed_during_source_read:" + relative)
        exact = checkout == blob
        crlf_only = b"\r" not in blob and checkout == blob.replace(b"\n", b"\r\n")
        require(exact or crlf_only, "checkout_bytes_not_git_or_exact_crlf:" + relative)
        files[relative] = {"git_bytes": len(blob), "git_sha256": digest(blob), "checkout": identity,
                           "checkout_encoding": "EXACT_GIT_BYTES" if exact else "EXACT_LF_TO_CRLF"}
    return {"sha": expected, "tree": git(repo, "rev-parse", "HEAD^{tree}").decode().strip(),
            "files": files}


def command(root: Path, label: str, argv: list[str], cwd: Path, environment: dict[str, str],
            timeout: int) -> dict[str, Any]:
    """One command, original streams, append-free intent/terminal, no retry."""
    folder = root / "commands" / label
    folder.mkdir(parents=True)
    write_json(folder / "intent.json", {"argv": argv, "cwd": str(cwd),
               "observed_at_unix_ns": str(time.time_ns()), "attempt": 1})
    record: dict[str, Any] = {"state": "NOT_STARTED", "exit_code": None, "attempt": 1}
    try:
        with (folder / "stdout.bin").open("xb") as stdout, (folder / "stderr.bin").open("xb") as stderr:
            result = subprocess.run(argv, cwd=cwd, env=environment, stdout=stdout, stderr=stderr,
                                    timeout=timeout, check=False)
        record.update(state="ACK" if result.returncode == 0 else "ERROR", exit_code=result.returncode)
    except subprocess.TimeoutExpired:
        record.update(state="UNKNOWN", reason="command_timeout_no_retry")
    except OSError as error:
        record.update(state="NOT_STARTED", reason=type(error).__name__)
    finally:
        record["observed_at_unix_ns"] = str(time.time_ns())
        record["stream_custody_complete"] = True
        for name in ("stdout.bin", "stderr.bin"):
            if (folder / name).is_file():
                try:
                    record[name] = regular_file(folder / name, MAX_PAYLOAD_BYTES)
                except (ContractError, OSError) as error:
                    record["stream_custody_complete"] = False
                    record[name] = {"state": "RETAINED_NOT_ADMITTED",
                                    "error_type": type(error).__name__, "reason": str(error)}
        write_json(folder / "terminal.json", record)
    require(record["state"] == "ACK", "command_failed:" + label + ":" + record["state"])
    require(record["stream_custody_complete"], "command_stream_custody_incomplete:" + label)
    return record


def opt_in_template(raw: bytes) -> bytes:
    require(not raw.startswith(b"\xef\xbb\xbf"), "unexpected_source_template_bom")
    text = raw.decode("utf-8", errors="strict")
    require(not DEFINE.search(text), "source_already_opted_in")
    require("!ifdef MWINST_CI_INTERRUPTION" in text
            and "Function MWINST_ArmInterruption" in text
            and "Function MWINST_InterruptMainAfterWithdrawal" in text,
            "source_lacks_checkpoint_contract")
    return OPT_IN_PREFIX + raw


def tree_manifest(root: Path) -> dict[str, Any]:
    inventory = archive_inventory(root)
    result = {relative: regular_file(root / relative, file_bound(root / relative))
              for relative in inventory}
    require({name: item["bytes"] for name, item in result.items()} == inventory
            and archive_inventory(root) == inventory, "archive_inventory_changed")
    return result


def retain_variant(target: Path, folder: Path) -> dict[str, Any]:
    """Move only this job's fresh, terminal compiler outputs, including failed partials."""
    folder.mkdir(parents=True, exist_ok=True)
    sources = [(name, source) for name, source in (
        ("render", target / "release/nsis"),
        ("bundle", target / "release/bundle/nsis")) if source.exists()]
    inventories = {name: archive_inventory(source) for name, source in sources}
    require(sum(sum(files.values()) for files in inventories.values()) <= MAX_DIAGNOSTIC_BYTES,
            "variant_total_size_bound")
    require(sum(len(files) for files in inventories.values()) <= MAX_DIAGNOSTIC_FILES,
            "variant_file_count_bound")
    output: dict[str, Any] = {"schema": "motionwright.nsis-variant-custody/1", "trees": {}}
    for name, source in sources:
        destination = folder / name
        require(not destination.exists(), "variant_output_already_exists")
        before = tree_manifest(source)
        require({key: value["bytes"] for key, value in before.items()} == inventories[name],
                "variant_output_changed_before_move")
        original_root = str(source.absolute())
        source.rename(destination)
        require(tree_manifest(destination) == before, "variant_archive_changed")
        output["trees"][name] = {"original_root": original_root,
                                "retained_root": str(destination.absolute()), "files": before}
    write_json(folder / "retained-files.json", output)
    return output


def nsis_literal(value: str) -> str:
    # Same bounded CI path alphabet as the existing package-candidate reader.
    require(value and not any(char in value for char in '$"\r\n\x00'),
            "unsupported_nsis_literal")
    return value


def rendered_define(text: str, name: str) -> str:
    candidates = re.findall(r"(?m)^[ \t]*!define[ \t]+" + re.escape(name)
                            + r"(?:[ \t].*|)\r?$", text)
    matches = re.findall(r'(?m)^[ \t]*!define[ \t]+' + re.escape(name)
                         + r'[ \t]+"([^"\r\n]*)"[ \t]*\r?$', text)
    require(len(candidates) == len(matches) == 1, "rendered_definition_missing_or_duplicated:" + name)
    return nsis_literal(matches[0])


def inspect_variant(folder: Path, variant: str, main_identity: dict[str, Any],
                    expected_defines: dict[str, str],
                    application: dict[str, Any]) -> dict[str, Any]:
    renders = list((folder / "render").rglob("installer.nsi"))
    setups = list((folder / "bundle").glob("*-setup.exe"))
    require(len(renders) == len(setups) == 1, "compile_artifact_cardinality")
    require(set(expected_defines) == {"PRODUCTNAME", "BUNDLEID", "ARCH", "INSTALLMODE",
                                     "VERSION", "MAINBINARYNAME", "MAINBINARYSRCPATH"},
            "expected_render_identity_roster")
    require(expected_defines["PRODUCTNAME"] == "Motionwright"
            and expected_defines["BUNDLEID"] == "com.seradotcom.motionwright"
            and expected_defines["VERSION"] == "0.1.1" and expected_defines["ARCH"] == "x64"
            and expected_defines["INSTALLMODE"] == "currentUser"
            and expected_defines["MAINBINARYNAME"] == "motionwright-desktop",
            "expected_compile_fixture_identity")
    main = Path(nsis_literal(expected_defines["MAINBINARYSRCPATH"]))
    require(main.is_absolute() and application.get("original_path") == str(main)
            and application.get("identity") == main_identity
            and application.get("schema") == "motionwright.nsis-built-application-custody/1",
            "retained_application_binding")
    require(regular_file(Path(application["retained_path"]), MAX_PAYLOAD_BYTES) == main_identity,
            "retained_application_changed")
    render = renders[0]
    raw, render_identity = bounded_read(render, MAX_RENDER_BYTES)
    require(raw.startswith(b"\xef\xbb\xbf"), "rendered_template_bom_missing")
    text = raw.decode("utf-8-sig", errors="strict")
    require("{{" not in text, "unexpanded_template")
    require(variant in ("normal", "opt-in"), "unknown_compile_variant")
    require(len(DEFINE.findall(text)) == (1 if variant == "opt-in" else 0), "define_variant_mismatch")
    observed_defines = {name: rendered_define(text, name) for name in expected_defines}
    for name, expected in expected_defines.items():
        if name == "MAINBINARYSRCPATH":
            require(Path(observed_defines[name]) == main, "rendered_main_source_mismatch")
        else:
            require(observed_defines[name] == expected, "rendered_identity_mismatch:" + name)
    require(setups[0].name == f'{expected_defines["PRODUCTNAME"]}_0.1.1_x64-setup.exe',
            "setup_filename_identity_mismatch")

    custody_raw, custody_identity = bounded_read(folder / "retained-files.json", MAX_RENDER_BYTES)
    custody = json.loads(custody_raw)
    require(custody.get("schema") == "motionwright.nsis-variant-custody/1"
            and set(custody.get("trees", {})) == {"render", "bundle"}, "variant_custody_schema")
    for name, original in (("render", main.parent / "nsis"),
                           ("bundle", main.parent / "bundle/nsis")):
        record = custody["trees"][name]
        require(Path(record["original_root"]) == original
                and Path(record["retained_root"]) == (folder / name).absolute()
                and tree_manifest(folder / name) == record["files"], "variant_custody_changed")
    original_render = (Path(custody["trees"]["render"]["original_root"])
                       / render.relative_to(folder / "render"))
    original_capture = original_render.parent / "mw-owned-MW_MAIN.bin"
    capture = render.parent / "mw-owned-MW_MAIN.bin"
    include = render.parent / "mw-owned-MW_MAIN.nsh"
    require(regular_file(capture, MAX_PAYLOAD_BYTES) == main_identity, "compiled_main_capture_mismatch")
    include_raw, include_identity = bounded_read(include, MAX_INCLUDE_BYTES)
    require(include_raw.startswith(b"\xef\xbb\xbf"), "capture_include_bom_missing")
    include_text = include_raw.decode("utf-8-sig", errors="strict").replace("\r\n", "\n")
    match = re.fullmatch(
        r'!define MW_MAIN_SOURCE "([^"\r\n\x00]+)"\n'
        r'!define MW_MAIN_SHA256 "([0-9a-f]{64})"\n'
        r'!define MW_MAIN_SIZE ([1-9][0-9]{0,18})\n', include_text)
    require(match is not None, "capture_include_closed_schema")
    require(Path(nsis_literal(match[1])) == original_capture, "capture_include_source_mismatch")
    require(match[2] == main_identity["sha256"] and int(match[3]) == main_identity["bytes"],
            "capture_include_payload_mismatch")
    setup_identity = pe_header_only(setups[0])
    return {"variant": variant, "result": "COMPILE_ONLY", "setup": setup_identity,
            "render": render_identity, "render_defines": observed_defines,
            "main_capture": regular_file(capture, MAX_PAYLOAD_BYTES),
            "capture_include": include_identity, "variant_custody": custody_identity,
            "original_capture_path": str(original_capture),
            "application_custody": application, "physical_pe_version": "NOT_OBSERVED",
            "installer_execution": "NOT_RUN", "predecessor_profile": "NOT_ADMITTED"}


def compile_fixtures(args: argparse.Namespace) -> int:
    require(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true",
            "disposable_windows_actions_required")
    gate, product, output = (Path(args.gate_repo).absolute(), Path(args.product_repo).absolute(),
                             Path(args.output).absolute())
    require(gate != product, "gate_product_checkout_must_be_separate")
    output.mkdir(parents=True)
    report: dict[str, Any] = {
        "schema": "motionwright.nsis-interruption-compile/1",
        "gate_sha": args.gate_sha, "product_sha": args.product_sha,
        "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "job": os.environ.get("GITHUB_JOB"), "compile": "NOT_RUN",
        "physical_interruption": "NOT_RUN", "recovery": "NOT_RUN",
        "installed_edit_reopen": "NOT_RUN", "human_acceptance": "NOT_RUN",
        "predecessor_profile": "NOT_ADMITTED", "release_published": False, "variants": []}
    exit_code = 1
    try:
        before = {"gate": source_observation(gate, args.gate_sha, GATE_PATHS),
                  "product": source_observation(product, args.product_sha, PRODUCT_PATHS)}
        report["source_before"] = before
        write_json(output / "source-before.json", before)
        for label, repo, identity, paths in (("gate", gate, args.gate_sha, GATE_PATHS),
                                            ("product", product, args.product_sha, PRODUCT_PATHS)):
            for relative in paths:
                write_new(output / "original-source" / label / relative,
                          git(repo, "show", identity + ":" + relative))
        config = json.loads((product / "apps/desktop/src-tauri/tauri.conf.json").read_bytes())
        require(config["version"] == "0.1.0", "reviewed_source_version_required")
        require(config["productName"] == "Motionwright"
                and config["identifier"] == "com.seradotcom.motionwright"
                and config["bundle"]["windows"]["nsis"]["installMode"] == "currentUser",
                "reviewed_application_identity_required")
        source_template = product / "apps/desktop/src-tauri/windows/installer.nsi"
        fixture_template = output / "inputs/opt-in-installer.nsi"
        write_new(fixture_template, opt_in_template(source_template.read_bytes()))
        version_overlay = output / "inputs/version-overlay.json"
        write_json(version_overlay, {"version": "0.1.1"})
        environment = dict(os.environ)
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "MOTIONWRIGHT_NSIS_INTERRUPT_ID",
                    "MW_NSIS_PREDECESSOR_MANIFEST", "MW_NSIS_PREDECESSOR_MANIFEST_SHA256",
                    "CARGO_BUILD_TARGET"):
            environment.pop(key, None)
        for key, relative in (("TEMP", "tmp"), ("TMP", "tmp"), ("LOCALAPPDATA", "local-app-data"),
                              ("APPDATA", "app-data"), ("npm_config_cache", "npm-cache")):
            directory = output / relative
            directory.mkdir(exist_ok=True)
            environment[key] = str(directory)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        target = output / "target"
        environment["CARGO_TARGET_DIR"] = str(target)
        node = Path(shutil.which("node.exe") or "")
        npm_cmd = Path(shutil.which("npm.cmd") or "")
        npm_cli = npm_cmd.parent / "node_modules/npm/bin/npm-cli.js"
        require(node.is_file() and npm_cli.is_file(), "node_npm_cli_missing")
        report["tools_before"] = {"node": regular_file(node), "npm_cli": regular_file(npm_cli),
                                   "python": regular_file(Path(sys.executable))}
        command(output, "npm-ci", [str(node), str(npm_cli), "--prefix", str(product / "apps/studio"),
                "ci", "--no-audit", "--no-fund"], product, environment, 600)
        cli_root = product / "apps/studio/node_modules/@tauri-apps"
        cli = cli_root / "cli/tauri.js"
        require(json.loads((cli_root / "cli/package.json").read_bytes())["version"] == "2.12.1",
                "reviewed_tauri_cli_required")
        cli_paths = ("cli/package.json", "cli/tauri.js", "cli-win32-x64-msvc/package.json",
                     "cli-win32-x64-msvc/cli.win32-x64-msvc.node")
        cli_before = {name: regular_file(cli_root / name) for name in cli_paths}
        report["tauri_cli"] = cli_before
        command(output, "cli-help", [str(node), str(cli), "bundle", "--help"],
                product / "apps/desktop", environment, 30)
        require(b"--no-binary-patching" in (output / "commands/cli-help/stdout.bin").read_bytes(),
                "cli_no_binary_patching_missing")
        for label, executable, arguments in (("rustc-version", "rustc", ["-vV"]),
                                              ("cargo-version", "cargo", ["--version"])):
            resolved = shutil.which(executable, path=environment.get("PATH"))
            require(resolved is not None, "required_build_tool_missing:" + executable)
            report.setdefault("resolved_build_tools", {})[executable] = {
                "path": resolved, "identity": regular_file(Path(resolved), MAX_PAYLOAD_BYTES)}
            command(output, label, [resolved, *arguments], product, environment, 30)
        disk = shutil.disk_usage(output)
        report["disk_before_build"] = {"total_bytes": disk.total, "used_bytes": disk.used,
                                      "free_bytes": disk.free, "minimum_free_bytes": None,
                                      "admission": "OBSERVATION_ONLY_NO_APPROVED_BUILD_FLOOR"}
        write_json(output / "inputs/disk-before-build.json", report["disk_before_build"])
        report["diagnostics_before_build"] = diagnostic_budget(output)
        command(output, "build-real-application", [str(node), str(cli), "build", "--no-bundle",
                "--config", str(version_overlay), "--", "--locked"],
                product / "apps/desktop", environment, 2700)
        main = target / "release/motionwright-desktop.exe"
        main_identity = pe_header_only(main)
        report["real_application"] = main_identity
        application = retain_built_application(main, output, main_identity)
        report["retained_application"] = application
        report["diagnostics_after_application"] = diagnostic_budget(output)
        expected_defines = {"PRODUCTNAME": config["productName"], "BUNDLEID": config["identifier"],
                            "ARCH": "x64", "INSTALLMODE": "currentUser", "VERSION": "0.1.1",
                            "MAINBINARYNAME": main.stem, "MAINBINARYSRCPATH": str(main)}
        report["makensis_observation"] = {"state": "NOT_RESOLVED_BEFORE_BUNDLE",
                                          "actual_bundler_compiler_identity": "NOT_OBSERVED"}
        makensis = shutil.which("makensis.exe", path=environment.get("PATH"))
        if makensis is not None:
            report["makensis_observation"] = {"state": "RESOLVED_ON_PATH",
                "path": makensis, "identity": regular_file(Path(makensis), MAX_PAYLOAD_BYTES),
                "actual_bundler_compiler_identity": "NOT_PROVEN_BY_PATH_LOOKUP"}
            command(output, "makensis-version", [makensis, "/VERSION"], product, environment, 30)
        for variant in ("normal", "opt-in"):
            require(not (target / "release/nsis").exists()
                    and not (target / "release/bundle/nsis").exists(), "render_output_not_fresh")
            template = source_template if variant == "normal" else fixture_template
            overlay = output / ("inputs/" + variant + "-overlay.json")
            write_json(overlay, {"version": "0.1.1", "bundle": {"windows": {"nsis": {
                "template": str(template)}}}})
            folder = output / "variants" / variant
            try:
                command(output, "compile-" + variant, [str(node), str(cli), "bundle", "--bundles",
                        "nsis", "--no-binary-patching", "--config", str(overlay)],
                        product / "apps/desktop", environment, 720)
            finally:
                terminal_path = output / "commands" / ("compile-" + variant) / "terminal.json"
                state = (json.loads(terminal_path.read_bytes()).get("state")
                         if terminal_path.is_file() else "UNKNOWN")
                if state in ("ACK", "ERROR", "NOT_STARTED"):
                    retain_variant(target, folder)
                else:
                    folder.mkdir(parents=True, exist_ok=True)
                    write_json(folder / "retention-state.json", {
                        "state": "UNKNOWN", "stable_snapshot": "NOT_ADMITTED",
                        "outputs_moved": False, "retry": "PROHIBITED",
                        "original_paths": ["target/release/nsis", "target/release/bundle/nsis"]})
            require(regular_file(main) == main_identity, "main_changed_during_bundle")
            report["variants"].append(inspect_variant(
                folder, variant, main_identity, expected_defines, application))
            report["diagnostics_after_" + variant] = diagnostic_budget(output)
        after = {"gate": source_observation(gate, args.gate_sha, GATE_PATHS),
                 "product": source_observation(product, args.product_sha, PRODUCT_PATHS)}
        report["source_after"] = after
        require(after == before, "source_changed_during_compile")
        require({name: regular_file(cli_root / name) for name in cli_paths} == cli_before,
                "tauri_tool_bytes_changed")
        require(regular_file(node) == report["tools_before"]["node"], "node_changed")
        report["compile"] = "TWO_VARIANTS_COMPILED"
        exit_code = 0
    except (ContractError, OSError, subprocess.SubprocessError, UnicodeError,
            KeyError, json.JSONDecodeError) as error:
        report["compile"] = "FAILED_OR_INCOMPLETE"
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        try:
            budget = diagnostic_budget(output)
            report["diagnostics_before_summary"] = budget
            summary = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
            require(len(summary) <= MAX_RENDER_BYTES
                    and budget["bytes"] + len(summary) <= MAX_DIAGNOSTIC_BYTES
                    and budget["files"] + 1 <= MAX_DIAGNOSTIC_FILES,
                    "final_diagnostic_size_bound")
        except (ContractError, OSError) as error:
            report["compile"] = "FAILED_OR_INCOMPLETE"
            report["diagnostic_admission"] = {"state": "NOT_ADMITTED",
                "reason": str(error), "originals_removed": False,
                "raw_failure_uploads_bounded": False}
            exit_code = 1
        write_json(output / "compile-summary.json", report)
    print(json.dumps({"compile": report["compile"], "physical_interruption": "NOT_RUN",
                      "gate_sha": args.gate_sha, "product_sha": args.product_sha}))
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    compile_parser = subcommands.add_parser("compile", help="Compile only; never execute a setup")
    for name in ("gate-repo", "gate-sha", "product-repo", "product-sha", "output"):
        compile_parser.add_argument("--" + name, required=True)
    return compile_fixtures(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
