#!/usr/bin/env python3
"""Attest a repaired-only MLT 7.22.0 consumer build for runtime admission.

The attestation binds the repaired source, compiler and linker commands,
object, module bytes and independent format/silence controls to the same
execution attempt. A combined diagnostic or instrumented source cannot
stand in for the repaired-only source. This module reads existing build
evidence; it never compiles, installs, renders or retries a native effect.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys

ORIGINAL_SHA = "c1c8850ff049a8775bb3d24c64be9b9365659773ce019902585c056f5be2f910"
REPAIRED_SHA = "246e84b02d25d0fe5ea08d59d5abb03c6dfd42d5d15dabeb464c8051f8ed8de0"
COMBINED_DIAGNOSTIC_SHA = "579eba9bb5a128e78cf078e0ae47f72a6ddbeb9147cf4451a4900541ed9eb56c"
HEX = re.compile(r"[0-9a-f]{64}\Z")
HEAD = re.compile(r"[0-9a-f]{40}\Z")
MAX_SOURCE = 256 * 1024
MAX_OBJECT = 32 * 1024 * 1024
MAX_MODULE = 64 * 1024 * 1024
MAX_JSON = 8 * 1024 * 1024


class CompilerReceiptRejected(ValueError):
    """No admissible new production ELF or source-bound G1 compiler proof."""


def require(value: object, label: str) -> None:
    if not value:
        raise CompilerReceiptRejected(label)


def sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def checked(path: Path, budget: int) -> bytes:
    require(path.is_absolute() and path.is_file()
            and not path.is_symlink()
            and not any(parent.is_symlink() for parent in path.parents),
            "repaired_only_input_not_an_absolute_regular_file")
    initial = path.stat()
    require(0 < initial.st_size <= budget, "repaired_only_input_byte_budget")
    raw = path.read_bytes()
    after = path.stat()
    require(initial.st_size == len(raw)
            and (initial.st_ino, initial.st_size, initial.st_mtime_ns)
            == (after.st_ino, after.st_size, after.st_mtime_ns),
            "repaired_only_compiler_input_changed_during_read")
    return raw


def unique_json(raw: bytes) -> object:
    def no_duplicate(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            require(key not in result, "repaired_only_G1_receipt_duplicate_key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=no_duplicate,
                      parse_constant=lambda _x: require(False, "nonfinite_receipt"))


def compiler_object_binding(compiles: object, compiled_c: Path,
                            object_path: Path) -> dict:
    require(isinstance(compiles, list), "repaired_only_compile_commands_not_an_array")
    expected_file = str(compiled_c.resolve(strict=True))
    expected_output = str(object_path.resolve(strict=True))
    entries = []
    for row in compiles:
        if not isinstance(row, dict):
            continue
        file = row.get("file")
        if not isinstance(file, str):
            continue
        workdir = row.get("directory")
        require(isinstance(workdir, str) and Path(workdir).is_absolute()
                and Path(workdir).is_dir()
                and not Path(workdir).is_symlink(),
                "compiler_CMake_working_directory_not_original_absolute")
        cwd = Path(workdir).resolve(strict=True)
        source = Path(file)
        if not source.is_absolute():
            source = cwd / source
        if str(source.resolve(strict=True)) != expected_file:
            continue
        arguments = row.get("arguments")
        if arguments is None:
            cmd = row.get("command")
            require(isinstance(cmd, str), "compiler_command_not_source_bound")
            arguments = shlex.split(cmd, posix=True)
        require(isinstance(arguments, list)
                and len(arguments) > 4
                and all(isinstance(arg, str) for arg in arguments),
                "compiler_arguments_are_not_a_pinned_vector")
        require("-c" in arguments and "-o" in arguments
                and arguments.index("-c") + 1 < len(arguments)
                and arguments.index("-o") + 1 < len(arguments),
                "consumer_compiler_must_compile_an_original_C_object")
        actual_input = Path(arguments[arguments.index("-c") + 1])
        if not actual_input.is_absolute():
            actual_input = cwd / actual_input
        require(str(actual_input.resolve(strict=True)) == expected_file,
                "actual_compiler_C_argument_not_the_repaired_only_source")
        target = arguments[arguments.index("-o") + 1]
        target_path = Path(target)
        if not target_path.is_absolute():
            target_path = cwd / target_path
        require(str(target_path.resolve(strict=True)) == expected_output,
                "compiled_consumer_object_is_not_the_source_command_target")
        entries.append({
            "consumer_command_sha256": sha(
                json.dumps(arguments, separators=(",", ":")).encode("utf-8")
            ),
            "original_compiled_C_file": expected_file,
            "consumer_object": expected_output,
            "compiler_workdir": str(cwd),
        })
    require(len(entries) == 1,
            "one_and_only_one_repaired_only_compiler_command_required")
    return entries[0]


def linker_object_binding(raw_link_command: bytes, consumer_object: Path,
                          module: Path, link_workdir: Path) -> dict:
    """Bind one actual repaired C object to one original candidate shared ELF.

    Use the original CMake link.txt (not a hand-authored claim). The approved
    G1 runner must separately persist this original linker command and the
    resulting ELF bytes in its signed job attempt.
    """
    try:
        raw = raw_link_command.decode("utf-8").strip()
        require(raw and "\x00" not in raw and "\n" not in raw
                and "\r" not in raw, "original_linker_command_invalid")
        argv = shlex.split(raw, posix=True)
    except (ValueError, UnicodeDecodeError) as exc:
        raise CompilerReceiptRejected("original_linker_command_malformed") from exc
    require(len(argv) >= 5 and "-shared" in argv and "-o" in argv
            and argv.index("-o") + 1 < len(argv),
            "real_MLT_repaired_shared_link_command_missing")
    # Do not infer response-file contents or accept chained shell mutations.
    require(not any(token.startswith("@") or token in {";", "|", "&&", "||"}
                    for token in argv),
            "linker_input_object_identity_ambiguous")
    require(link_workdir.is_absolute() and link_workdir.is_dir()
            and not link_workdir.is_symlink(),
            "original_CMake_link_workdir_not_trusted")
    # CMake launches link.txt from its *compile_commands directory*, e.g.
    # build/src/modules/avformat. The .so lives in build/out/lib/mlt.
    # Anchoring relative -o or .o arguments at module.parent is incorrect.
    cwd = link_workdir.resolve(strict=True)

    def absolute_arg(token: str) -> str:
        name = Path(token)
        if not name.is_absolute():
            name = cwd / name
        return str(name.resolve(strict=True))

    result = absolute_arg(argv[argv.index("-o") + 1])
    require(result == str(module.resolve(strict=True)),
            "linked_MLT_module_not_the_admitted_candidate")
    matches = sum(absolute_arg(token) == str(consumer_object.resolve(strict=True))
                  for token in argv if token.endswith((".o", ".obj")))
    require(matches == 1,
            "exact_repaired_consumer_object_not_linked_once")
    return {
        "original_link_command_sha256": sha(raw_link_command),
        "linked_module": result,
        "linked_consumer_object": str(consumer_object.resolve(strict=True)),
        "repaired_consumer_object_in_link": True,
        "original_CMake_link_workdir": str(cwd),
    }


def build_receipt(original: Path, repaired: Path, compiled_source: Path,
                  object_path: Path, module: Path, compile_commands: Path,
                  link_command: Path, oracle_file: Path, source_head: str,
                  run_id: str, run_attempt: str) -> dict:
    require(HEAD.fullmatch(source_head) is not None
            and run_id.isdecimal() and run_attempt.isdecimal()
            and int(run_id) > 0 and int(run_attempt) > 0,
            "repaired_only_compiler_GitHub_provenance_missing")
    before = checked(original, MAX_SOURCE)
    candidate = checked(repaired, MAX_SOURCE)
    actual_built_source = checked(compiled_source, MAX_SOURCE)
    object_bytes = checked(object_path, MAX_OBJECT)
    module_bytes = checked(module, MAX_MODULE)
    commands_bytes = checked(compile_commands, MAX_JSON)
    link_bytes = checked(link_command, MAX_JSON)
    oracle_bytes = checked(oracle_file, MAX_JSON)
    require(len(before) == 96690 and sha(before) == ORIGINAL_SHA,
            "original_Ubuntu_MLT_consumer_not_source_locked")
    require(len(candidate) == 98847 and sha(candidate) == REPAIRED_SHA
            and actual_built_source == candidate
            and sha(actual_built_source) != COMBINED_DIAGNOSTIC_SHA,
            "built_consumer_not_the_uninstrumented_repaired_only_source")
    require(object_bytes.startswith(b"\x7fELF")
            and module_bytes.startswith(b"\x7fELF"),
            "compiled_repaired_only_C_and_module_not_actual_ELF")
    compile_entry = compiler_object_binding(
        unique_json(commands_bytes), compiled_source, object_path,
    )
    link_entry = linker_object_binding(
        link_bytes, object_path, module,
        Path(compile_entry["compiler_workdir"]),
    )
    oracle = unique_json(oracle_bytes)
    require(isinstance(oracle, dict)
            and oracle.get("schema") == "motionwright.native.mlt.repaired-only-G1-C-oracle/1"
            and oracle.get("source_head") == source_head
            and str(oracle.get("run_id")) == run_id
            and str(oracle.get("run_attempt")) == run_attempt
            and oracle.get("original_source_sha256") == ORIGINAL_SHA
            and oracle.get("repaired_source_sha256") == REPAIRED_SHA
            and oracle.get("compiled_consumer_object_sha256") == sha(object_bytes)
            and oracle.get("compiled_module_sha256") == sha(module_bytes)
            and oracle.get("source_constructor_ASAN_UBSAN") == "PASS"
            and oracle.get("source_frame_oracle_result") == "PASS"
            and oracle.get("legacy_U8P_zero_padding_negative") == "CORRECTLY_REJECTED"
            and oracle.get("complete_MLT_consumer_compiler") == "PASS"
            and oracle.get("actual_native_effect_performed") is False,
            "separate_exact_new_repaired_only_C_G1_oracle_missing_or_red")
    return {
        "schema": "motionwright.native.mlt.repaired-only-compiler-receipt/1",
        "original_source_sha256": ORIGINAL_SHA,
        "repaired_source_sha256": REPAIRED_SHA,
        "compiled_source_sha256": sha(actual_built_source),
        "instrumented_combined_source_compiled": False,
        "compiled_consumer_object_sha256": sha(object_bytes),
        "compiled_module_sha256": sha(module_bytes),
        "compiled_module_bytes": len(module_bytes),
        "compiled_consumer_object_linked": True,
        "linker_consumer_object_sha256": sha(object_bytes),
        "linker_command": link_entry,
        "linker_command_sha256": sha(link_bytes),
        "compiler_command": compile_entry,
        "compiler_commands_sha256": sha(commands_bytes),
        "G1_oracle_receipt_sha256": sha(oracle_bytes),
        "source_head": source_head,
        "run_id": run_id, "run_attempt": run_attempt,
        "MLT_version": "7.22.0",
        "compiler_result": "PASS",
        "source_sanitizers_result": "PASS",
        "source_frame_oracle_result": "PASS",
        "source_constructor_ASAN_UBSAN": "PASS",
        "complete_MLT_consumer_compiler": "PASS",
        "actual_native_effect_performed": False,
        "module_installed": False,
        "source_G1_attestation_not_a_loaded_module_or_media_PASS": True,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ("original-source", "repaired-source", "compiled-source",
                 "consumer-object", "compiled-module", "compile-commands",
                 "link-command", "oracle-receipt", "receipt"):
        ap.add_argument("--" + name, type=Path, required=True)
    args = ap.parse_args()
    require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("CI") == "true"
            and os.environ.get("RUNNER_OS") == "Linux",
            "repaired_only_compiler_receipt_requires_a_real_GitHub_Linux_run")
    head = os.environ.get("GITHUB_SHA", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    result = build_receipt(
        args.original_source, args.repaired_source,
        args.compiled_source, args.consumer_object,
        args.compiled_module, args.compile_commands,
        args.link_command, args.oracle_receipt, head, run_id, attempt,
    )
    destination = args.receipt
    require(destination.is_absolute() and destination.parent.is_dir()
            and not destination.exists() and not destination.is_symlink(),
            "compiler_receipt_must_use_new_owner_private_file")
    data = (json.dumps(result, sort_keys=True, indent=2) + "\n").encode()
    require(len(data) < 16384, "compiler_receipt_outside_byte_budget")
    with destination.open("xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    print(json.dumps({
        "schema": result["schema"],
        "source_head": head,
        "repaired_source_sha256": REPAIRED_SHA,
        "compiled_module_sha256": result["compiled_module_sha256"],
        "compiler_receipt_sha256": sha(data),
        "native_effect_started": False,
        "whole_master_accepted": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
