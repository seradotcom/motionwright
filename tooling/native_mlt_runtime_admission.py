#!/usr/bin/env python3
"""Source-bound MLT 7.22.0 module admission for disposable GHA runners only.

A1/A6 must explicitly approve a selected new G2 original before --admit.
This is not a Semwright runtime, a provider grant, or evidence that audio
was repaired. Once installed, the separate live /proc attestor and an
independent whole-master stereo PCM comparison are mandatory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ORIGINAL_SHA = "c1c8850ff049a8775bb3d24c64be9b9365659773ce019902585c056f5be2f910"
REPAIRED_SHA = "246e84b02d25d0fe5ea08d59d5abb03c6dfd42d5d15dabeb464c8051f8ed8de0"
INSTALLED = Path("/usr/lib/x86_64-linux-gnu/mlt-7/libmltavformat.so")
MAX_ELF_BYTES = 64 * 1024 * 1024
MAX_SOURCE_BYTES = 256 * 1024
HEX = re.compile(r"[0-9a-f]{64}\Z")
FORBIDDEN_ENV = {
    "LD_LIBRARY_PATH", "LD_PRELOAD", "LD_AUDIT",
    "MLT_REPOSITORY", "MLT_DATA", "MLT_PROFILES_PATH",
}


class AdmissionRejected(ValueError):
    pass


def require(ok: object, reason: str) -> None:
    if not ok:
        raise AdmissionRejected(reason)


def checked_bytes(path: Path, budget: int) -> bytes:
    require(path.is_absolute() and path.is_file() and not path.is_symlink(),
            "candidate_or_source_not_an_absolute_regular_file")
    require(not any(parent.is_symlink() for parent in path.parents),
            "candidate_or_source_has_a_symlink_ancestor")
    before = path.stat()
    require(0 < before.st_size <= budget, "candidate_or_source_exceeds_byte_budget")
    data = path.read_bytes()
    after = path.stat()
    require(len(data) == before.st_size
            and (before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_ino, after.st_size, after.st_mtime_ns),
            "candidate_or_source_changed_during_read")
    return data


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def source_compiler_closure(original: Path, repaired: Path,
                            candidate: Path, candidate_sha: str,
                            compiler_receipt: Path, compiler_receipt_sha: str) -> dict:
    require(HEX.fullmatch(candidate_sha) is not None
            and HEX.fullmatch(compiler_receipt_sha) is not None,
            "G1_compiler_hash_is_not_authoritative")
    a = checked_bytes(original, MAX_SOURCE_BYTES)
    b = checked_bytes(repaired, MAX_SOURCE_BYTES)
    elf = checked_bytes(candidate, MAX_ELF_BYTES)
    raw = checked_bytes(compiler_receipt, 32 * 1024)
    require(len(a) == 96690 and sha(a) == ORIGINAL_SHA,
            "original_MLT_722_source_not_the_frozen_original")
    require(len(b) == 98847 and sha(b) == REPAIRED_SHA,
            "repaired_MLT_U8P_source_not_the_frozen_candidate")
    require(elf.startswith(b"\x7fELF") and sha(elf) == candidate_sha,
            "compiled_MLT_candidate_not_exact_expected_ELF")
    require(sha(raw) == compiler_receipt_sha,
            "compiler_receipt_bytes_not_authenticated")

    def no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
        obj = {}
        for key, value in pairs:
            require(key not in obj, "compiler_receipt_duplicate_key")
            obj[key] = value
        return obj
    proof = json.loads(raw, object_pairs_hook=no_duplicate_keys)
    require(type(proof) is dict
            and proof.get("original_source_sha256") == ORIGINAL_SHA
            and proof.get("repaired_source_sha256") == REPAIRED_SHA
            and proof.get("compiled_module_sha256") == candidate_sha
            and proof.get("compiled_module_bytes") == len(elf)
            and proof.get("MLT_version") == "7.22.0"
            and proof.get("source_constructor_ASAN_UBSAN") == "PASS"
            and proof.get("complete_MLT_consumer_compiler") == "PASS"
            and proof.get("actual_native_effect_performed") is False,
            "compiler_receipt_does_not_bind_original_repaired_MLT")
    return {
        "original_MLT_source_sha256": ORIGINAL_SHA,
        "repaired_MLT_source_sha256": REPAIRED_SHA,
        "compiled_module_sha256": candidate_sha,
        "compiled_module_bytes": len(elf),
        "compiler_receipt_sha256": compiler_receipt_sha,
    }


def disposable_linux_runner() -> Path:
    require(sys.platform == "linux"
            and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_OS") == "Linux"
            and os.environ.get("CI") == "true"
            and os.environ.get("MOTIONWRIGHT_APPROVED_MLT_G2") == "1",
            "installed_module_admission_requires_explicit_disposable_GHA")
    require(not FORBIDDEN_ENV.intersection(os.environ),
            "untrusted_module_loader_environment_forbidden")
    value = os.environ.get("RUNNER_TEMP", "")
    temp = Path(value)
    require(value and temp.is_absolute() and temp.is_dir()
            and not temp.is_symlink() and temp != Path("/")
            and temp.as_posix().startswith("/home/runner/work/_temp")
            and not any(parent.is_symlink() for parent in temp.parents),
            "temporary_evidence_root_not_owned_by_ephemeral_runner")
    return temp


def installed_original(expected_sha: str) -> dict:
    require(HEX.fullmatch(expected_sha) is not None,
            "original_installed_system_module_sha256_missing")
    require(INSTALLED.as_posix() ==
            "/usr/lib/x86_64-linux-gnu/mlt-7/libmltavformat.so",
            "unexpected_system_module_path")
    data = checked_bytes(INSTALLED, MAX_ELF_BYTES)
    require(data.startswith(b"\x7fELF") and sha(data) == expected_sha,
            "installed_original_MLT_module_does_not_match_frozen_distro_ELF")
    return {
        "original_module_sha256": expected_sha,
        "original_module_bytes": len(data),
        "original_inode": INSTALLED.stat().st_ino,
    }


def live_melt_pids() -> list[int]:
    result = []
    for pid in Path("/proc").iterdir():
        if pid.name.isdecimal() and (pid / "comm").is_file():
            try:
                name = (pid / "comm").read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                continue
            if name == "melt":
                result.append(int(pid.name))
    return result


def preflight(original: Path, repaired: Path, candidate: Path,
              candidate_sha: str, compiler_receipt: Path,
              compiler_sha: str, installed_sha: str) -> dict:
    root = disposable_linux_runner()
    closure = source_compiler_closure(
        original, repaired, candidate, candidate_sha, compiler_receipt, compiler_sha
    )
    original_module = installed_original(installed_sha)
    query = subprocess.run(
        ["dpkg-query", "-W", "-f=" + "$" + "{Version}", "libmlt7"],
        text=True, capture_output=True, timeout=20, check=True,
    )
    require(query.stdout.strip().startswith("7.22.0"),
            "distro_MLT_version_not_7_22_0")
    require(not live_melt_pids(),
            "melt_process_exists_before_approved_module_admission")
    return {
        "schema": "motionwright.native.mlt-module-admission-preflight/1",
        "runner_temp": str(root), "installed_path": str(INSTALLED),
        "MLT_distro_version": query.stdout.strip(),
        **closure, **original_module,
        "module_replaced": False, "original_native_effect_started": False,
        "repaired_AAC_right_channel_verified": False,
    }


def install(preflight_receipt: dict, candidate: Path, new_evidence_dir: Path) -> dict:
    root = disposable_linux_runner()
    require(os.geteuid() == 0, "module_install_requires_runner_root")
    require(new_evidence_dir.is_absolute()
            and new_evidence_dir.parent == root
            and not new_evidence_dir.exists()
            and not new_evidence_dir.is_symlink(),
            "installation_receipt_requires_fresh_runner_private_directory")
    require(preflight_receipt["installed_path"] == str(INSTALLED)
            and not live_melt_pids(),
            "MLT_module_install_preflight_no_longer_valid")
    before = checked_bytes(INSTALLED, MAX_ELF_BYTES)
    require(sha(before) == preflight_receipt["original_module_sha256"],
            "installed_original_module_changed_since_preflight")
    binary = checked_bytes(candidate, MAX_ELF_BYTES)
    require(sha(binary) == preflight_receipt["compiled_module_sha256"],
            "repaired_ELF_changed_since_G1_compiler")
    new_evidence_dir.mkdir(mode=0o700)
    backup = new_evidence_dir / "original_system_mlt_avformat.so"
    with backup.open("xb") as f:
        f.write(before)
        f.flush()
        os.fsync(f.fileno())
    require(sha(checked_bytes(backup, MAX_ELF_BYTES))
            == preflight_receipt["original_module_sha256"],
            "original_distro_module_backup_not_byte_identical")
    require(not live_melt_pids(), "melt_started_before_one_shot_runtime_change")
    fd, temporary_path = tempfile.mkstemp(prefix=".mw-reviewed-avformat-",
                                          dir=INSTALLED.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(binary)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temporary_path, 0o644)
        require(sha(checked_bytes(Path(temporary_path), MAX_ELF_BYTES))
                == preflight_receipt["compiled_module_sha256"],
                "installed_candidate_staging_sha256_changed")
        # One explicit runner-only, privileged mutation; NEVER retry UNKNOWN.
        os.replace(temporary_path, INSTALLED)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
    require(sha(checked_bytes(INSTALLED, MAX_ELF_BYTES))
            == preflight_receipt["compiled_module_sha256"],
            "installed_MLT_module_readback_differs_from_compiler")
    receipt = {
        **preflight_receipt,
        "schema": "motionwright.native.mlt-module-installed/1",
        "module_replaced": True,
        "actual_installed_module_sha256": sha(binary),
        "actual_installed_module_inode": INSTALLED.stat().st_ino,
        "original_backup_sha256": sha(before),
        "original_backup_runner_private_path": str(backup),
        "new_native_master_issued": False,
        "actual_loaded_PID_and_module_verified": False,
        "whole_master_right_channel_verified": False,
        "human_acceptance": False,
    }
    with (new_evidence_dir / "installation.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return receipt


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    for arg in ("original-source", "repaired-source", "compiled-module",
                "compiler-receipt"):
        p.add_argument("--" + arg, type=Path, required=True)
    for arg in ("compiled-module-sha256", "compiler-receipt-sha256",
                "installed-original-sha256"):
        p.add_argument("--" + arg, required=True)
    p.add_argument("--admit", action="store_true")
    p.add_argument("--new-runner-private-evidence", type=Path)
    args = p.parse_args()
    check = preflight(
        args.original_source, args.repaired_source,
        args.compiled_module, args.compiled_module_sha256,
        args.compiler_receipt, args.compiler_receipt_sha256,
        args.installed_original_sha256,
    )
    if args.admit:
        require(args.new_runner_private_evidence is not None,
                "actual_install_needs_separate_runner_private_receipt")
        check = install(check, args.compiled_module,
                        args.new_runner_private_evidence)
    else:
        require(args.new_runner_private_evidence is None,
                "read_only_preflight_has_no_written_receipt")
    print(json.dumps(check, sort_keys=True))


if __name__ == "__main__":
    main()
