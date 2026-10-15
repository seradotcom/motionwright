#!/usr/bin/env python3
"""Independent v0.1 CI verifier for unsigned candidate attestation subjects.

This validates *downloaded* GitHub CI candidate packages against the exact
source/pinned Semwright candidate receipts before signing metadata. It never
signs an installer, claims a trusted publisher or upgrades product acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

from package_receipt import classify, verified_artifact
from release_manifest import ManifestError, _checkout, _strict_object, _stable_read

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "motionwright.v01-candidate-attestation-input/1"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
BASENAME = re.compile(r"[^\x00-\x1f/\\]{1,200}\Z")
MAX_RECEIPT_BYTES = 256 * 1024
PLATFORMS = ("linux", "windows", "macos")
RECEIPTS = {name: f"{name}-candidate.json" for name in PLATFORMS}
EXPECTED_KINDS = {
    "linux": {"appimage", "deb"},
    "windows": {"nsis"},
    "macos": {"dmg"},
}
# Artifact-upload jobs enumerate only these non-package files. Reject any
# raw owner/native receipt or unexpected path from a downloaded archive,
# even when it is not selected as an attestation subject.
KNOWN_EVIDENCE_FILES = {
    "linux": {
        "linux-candidate.json", "linux-deb-smoke.json",
        "linux-appimage-smoke.json", "linux-appimage-window.json",
    },
    "windows": {
        "windows-candidate.json", "windows-nsis-smoke.json",
        "windows-nsis-cold-relaunch.json",
    },
    "macos": {"macos-candidate.json", "macos-dmg-smoke.json"},
}
UNSIGNED_CLAIMS = {
    "published": False,
    "distribution_signature": "NOT_CLAIMED",
    "notarization": "NOT_CLAIMED",
    "human_install_acceptance": "NOT_RUN",
    "runtime_renderer_acceptance": "SEPARATE_GATE",
}


class AttestationInputError(ValueError):
    """Only static codes, never private native receipt bytes or filesystem paths."""


def need(ok: bool, code: str) -> None:
    if not ok:
        raise AttestationInputError(code)


def _bounded_receipt(path: Path, source_root: Path) -> dict:
    try:
        _, size, digest = verified_artifact(source_root, path)
        need(size <= MAX_RECEIPT_BYTES, "attest_receipt_too_large")
        raw = path.read_bytes()
        need(hashlib.sha256(raw).hexdigest() == digest, "attest_receipt_changed")
        data = json.loads(raw, object_pairs_hook=_strict_object)
        need(isinstance(data, dict), "attest_receipt_not_object")
        return data
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise AttestationInputError("attest_receipt_unavailable") from exc
    except ManifestError as exc:
        raise AttestationInputError("attest_receipt_malformed") from exc


def _all_files(folder: Path) -> list[Path]:
    need(folder.is_dir() and not folder.is_symlink(), "attest_download_root_invalid")
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        parent = Path(dirpath)
        for name in dirnames + filenames:
            part = parent / name
            try:
                info = part.lstat()
                reparse = bool(
                    getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                )
                need(not stat.S_ISLNK(info.st_mode)
                     and not part.is_symlink() and not reparse,
                     "attest_download_link_refused")
                need(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode),
                     "attest_download_invalid_entry")
            except OSError as exc:
                raise AttestationInputError("attest_download_unavailable") from exc
            if len(found) + len(dirnames) + len(filenames) > 256:
                raise AttestationInputError("attest_download_unbounded")
        for filename in filenames:
            p = parent / filename
            need(p.is_file(), "attest_download_not_regular")
            found.append(p)
    need(len(found) <= 256, "attest_download_unbounded")
    return found


def verify_candidates(root: Path, exact_sha: str,
                      download_root: Path) -> dict:
    need(isinstance(exact_sha, str) and SHA40.fullmatch(exact_sha) is not None,
         "attest_source_sha_invalid")
    try:
        _checkout(root, exact_sha)
        locked = json.loads(_stable_read(root, "SOURCE_LOCK.json"),
                            object_pairs_hook=_strict_object)
        pinned = locked["dependencies"]["semwright"]["revision"]
        need(isinstance(pinned, str) and SHA40.fullmatch(pinned) is not None,
             "attest_semwright_pin_invalid")
    except (ManifestError, KeyError, TypeError, UnicodeError, ValueError) as exc:
        if isinstance(exc, AttestationInputError):
            raise
        raise AttestationInputError("attest_source_unverified") from exc

    subjects: list[dict] = []
    for platform in PLATFORMS:
        folder = download_root / platform
        candidates = _all_files(folder)
        receipts = [path for path in candidates
                    if path.name == RECEIPTS[platform]]
        need(len(receipts) == 1, "attest_receipt_count_invalid")
        receipt = _bounded_receipt(receipts[0], root)
        need(set(receipt) == {"schema_version", "candidate", "artifacts", "claims"}
             and type(receipt.get("schema_version")) is int
             and receipt["schema_version"] == 1,
             "attest_receipt_schema_invalid")
        candidate = receipt.get("candidate")
        need(isinstance(candidate, dict)
             and set(candidate) == {
                 "application", "version", "source_revision",
                 "semwright_revision", "platform", "arch",
             } and candidate["application"] == "Motionwright"
             and candidate["source_revision"] == exact_sha
             and candidate["semwright_revision"] == pinned
             and candidate["platform"] == platform
             and isinstance(candidate["version"], str)
             and bool(candidate["version"])
             and isinstance(candidate["arch"], str)
             and bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", candidate["arch"])),
             "attest_candidate_source_mismatch")
        need(receipt.get("claims") == UNSIGNED_CLAIMS,
             "attest_release_claims_not_unsigned")
        listed = receipt.get("artifacts")
        need(isinstance(listed, list)
             and len(listed) == len(EXPECTED_KINDS[platform]),
             "attest_bundle_count_invalid")
        seen_names: set[str] = set()
        seen_kinds: set[str] = set()
        allowed_paths: set[Path] = set()
        for row in listed:
            need(isinstance(row, dict)
                 and set(row) == {"filename", "bundle_kind", "bytes", "sha256"},
                 "attest_bundle_metadata_invalid")
            filename = row.get("filename")
            need(isinstance(filename, str)
                 and BASENAME.fullmatch(filename) is not None
                 and filename not in (".", ".."),
                 "attest_bundle_filename_invalid")
            need(filename.casefold() not in seen_names,
                 "attest_bundle_filename_reused")
            seen_names.add(filename.casefold())
            try:
                expected_kind = classify(platform, Path(filename))
            except (KeyError, ValueError) as exc:
                raise AttestationInputError("attest_bundle_type_invalid") from exc
            need(row["bundle_kind"] == expected_kind,
                 "attest_bundle_kind_mismatch")
            seen_kinds.add(expected_kind)
            paths = [p for p in candidates if p.name == filename]
            need(len(paths) == 1, "attest_bundle_path_ambiguous")
            package = paths[0]
            allowed_paths.add(package)
            _, size, digest = verified_artifact(root, package)
            need(type(row["bytes"]) is int and row["bytes"] == size
                 and isinstance(row["sha256"], str)
                 and SHA64.fullmatch(row["sha256"]) is not None
                 and row["sha256"] == digest,
                 "attest_candidate_digest_mismatch")
            subjects.append({
                "path": package.relative_to(download_root).as_posix(),
                "sha256": digest,
                "bytes": size,
                "platform": platform,
                "bundle_kind": expected_kind,
            })
        need(seen_kinds == EXPECTED_KINDS[platform],
             "attest_bundle_kind_set_invalid")
        # No secret/raw native receipt may be smuggled into the downloaded
        # archive. Even non-package files must be from the documented smoke
        # evidence allowlist; signing package bytes never sanitizes uploads.
        for p in candidates:
            if p not in allowed_paths:
                if p.name.lower().endswith((".appimage", ".deb", ".exe", ".dmg")):
                    raise AttestationInputError("attest_extra_bundle_not_in_receipt")
                need(p.name in KNOWN_EVIDENCE_FILES[platform],
                     "attest_unexpected_downloaded_content")

    need(len(subjects) == 4, "attest_subject_count_invalid")
    need(len({row["path"] for row in subjects}) == 4,
         "attest_duplicate_subject_path")
    try:
        _checkout(root, exact_sha)
    except ManifestError as exc:
        raise AttestationInputError("attest_source_changed") from exc
    return {
        "schema": SCHEMA,
        "motionwright_source_sha": exact_sha,
        "semwright_source_pin": pinned,
        "subjects": sorted(subjects, key=lambda item: item["path"]),
        "checks": {
            "candidate_receipts_source_and_pin_bound": True,
            "downloaded_packages_digest_verified": True,
            "gh_attestation_signature_verified": False,
            "owner_installed_acceptance": "NOT_RUN",
            "distribution_signature": "NOT_CLAIMED",
            "notarization": "NOT_CLAIMED",
            "release_ready": False,
        },
    }


def write_input_files(output_dir: Path, report: dict) -> None:
    need(output_dir.is_dir() and not output_dir.is_symlink(),
         "attest_output_directory_invalid")
    manifest = output_dir / "verified-candidate-inputs.json"
    paths = output_dir / "verified-subject-paths.txt"
    for path in (manifest, paths):
        need(not path.exists() and not path.is_symlink(),
             "attest_output_exists")
    payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    subject_lines = "".join("candidates/" + row["path"] + "\n"
                            for row in report["subjects"]).encode()
    created = []
    try:
        for target, data in ((manifest, payload), (paths, subject_lines)):
            with target.open("xb") as output:
                created.append(target)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
    except OSError as exc:
        for path in created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise AttestationInputError("attest_output_write_refused") from exc


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--download-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = verify_candidates(root, args.source_sha, args.download_root)
        write_input_files(args.output_dir, report)
    except (AttestationInputError, OSError, TypeError, ValueError) as exc:
        code = str(exc) if isinstance(exc, AttestationInputError) else "attest_input_unavailable"
        print("v01-candidate-attestation-input=REFUSED code=" + code, file=sys.stderr)
        return 2
    print("v01-candidate-attestation-input=PASS subjects=4"
          + " source=" + report["motionwright_source_sha"]
          + " signing=NOT_CLAIMED release=BLOCKED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
