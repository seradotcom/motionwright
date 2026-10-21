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


def regular_file(path: Path) -> dict[str, Any]:
    path = path.absolute()
    for ancestor in (path, *path.parents):
        observed = ancestor.lstat()
        require(not stat.S_ISLNK(observed.st_mode)
                and not getattr(observed, "st_file_attributes", 0) & 0x400,
                "link_or_reparse:" + str(ancestor))
    before = path.stat()
    require(stat.S_ISREG(before.st_mode), "not_regular:" + str(path))
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    after = path.stat()
    require(all(getattr(before, key) == getattr(after, key)
                for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns")),
            "file_changed:" + str(path))
    return {"bytes": before.st_size, "sha256": sha.hexdigest()}


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
        for name in ("stdout.bin", "stderr.bin"):
            if (folder / name).is_file():
                record[name] = regular_file(folder / name)
        write_json(folder / "terminal.json", record)
    require(record["state"] == "ACK", "command_failed:" + label + ":" + record["state"])
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
    result = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            observed = (Path(current) / name).lstat()
            require(not stat.S_ISLNK(observed.st_mode)
                    and not getattr(observed, "st_file_attributes", 0) & 0x400,
                    "archive_reparse_directory")
        for name in files:
            path = Path(current) / name
            result[path.relative_to(root).as_posix()] = regular_file(path)
    require(len(result) <= 4096, "artifact_file_bound")
    return result


def retain_variant(target: Path, folder: Path) -> dict[str, Any]:
    """Move only this job's fresh, terminal compiler outputs, including failed partials."""
    folder.mkdir(parents=True, exist_ok=True)
    output = {}
    for name, source in (("render", target / "release/nsis"),
                         ("bundle", target / "release/bundle/nsis")):
        if source.exists():
            destination = folder / name
            require(not destination.exists(), "variant_output_already_exists")
            before = tree_manifest(source)
            source.rename(destination)
            require(tree_manifest(destination) == before, "variant_archive_changed")
            output[name] = before
    write_json(folder / "retained-files.json", output)
    return output


def inspect_variant(folder: Path, variant: str, main_identity: dict[str, Any]) -> dict[str, Any]:
    renders = list((folder / "render").rglob("installer.nsi"))
    setups = list((folder / "bundle").glob("*-setup.exe"))
    require(len(renders) == len(setups) == 1, "compile_artifact_cardinality")
    render = renders[0]
    text = render.read_bytes().decode("utf-8-sig", errors="strict")
    require("{{" not in text, "unexpanded_template")
    require(len(DEFINE.findall(text)) == (1 if variant == "opt-in" else 0), "define_variant_mismatch")
    capture = render.parent / "mw-owned-MW_MAIN.bin"
    include = render.parent / "mw-owned-MW_MAIN.nsh"
    require(regular_file(capture) == main_identity, "compiled_main_capture_mismatch")
    require(include.is_file(), "main_capture_include_missing")
    require(setups[0].read_bytes()[:2] == b"MZ", "compiled_setup_not_pe")
    return {"variant": variant, "result": "COMPILE_ONLY", "setup": regular_file(setups[0]),
            "render": regular_file(render), "main_capture": regular_file(capture),
            "capture_include": regular_file(include), "installer_execution": "NOT_RUN",
            "predecessor_profile": "NOT_ADMITTED"}


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
        command(output, "build-real-application", [str(node), str(cli), "build", "--no-bundle",
                "--config", str(version_overlay), "--", "--locked"],
                product / "apps/desktop", environment, 2700)
        main = target / "release/motionwright-desktop.exe"
        main_identity = regular_file(main)
        require(main.read_bytes()[:2] == b"MZ", "real_application_not_pe")
        report["real_application"] = main_identity
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
            report["variants"].append(inspect_variant(folder, variant, main_identity))
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
