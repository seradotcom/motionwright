#!/usr/bin/env python3
"""v0.1 owner-side, read-only verification of one unsigned installer candidate.

Require all three independent links of a delivered artifact:
  exact clean Motionwright source -> SHA/pin-bound candidate receipt ->
  actual local installer SHA-256 -> GitHub Sigstore signer/source verification.

This does not install, launch, sign, notarize, connect a Broker, authenticate a
human reviewer, or approve the v0.1 release. All failure text is redacted.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from typing import Callable

from candidate_attestation_input import (
    AttestationInputError, _bounded_receipt, BASENAME, EXPECTED_KINDS,
    MAX_RECEIPT_BYTES, SHA40, SHA64, UNSIGNED_CLAIMS,
)
from package_receipt import CandidateError, classify, verified_artifact
from release_manifest import (
    ManifestError, _checkout, _discard_new_output_on_source_change,
    _stable_read, _strict_object, write_new_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "seradotcom/motionwright"
SIGNER_WORKFLOW = REPOSITORY + "/.github/workflows/package-candidate.yml"
SCHEMA = "motionwright.v01-owner-candidate-provenance/1"
KIND_TO_PLATFORM = {
    "appimage": "linux", "deb": "linux",
    "nsis": "windows", "dmg": "macos",
}
ARCH = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
GH_TIMEOUT_SECONDS = 30


class OwnerCandidateError(ValueError):
    """Fixed public reason codes, never a real owner path or GitHub stderr."""


def need(ok: bool, code: str) -> None:
    if not ok:
        raise OwnerCandidateError(code)


def verify_gh_attestation(path: Path, source_sha: str) -> bool:
    """Verify signed provenance of THIS file, workflow and source digest.

    The GitHub CLI uses its own verified Sigstore trust roots. Ignore all
    untrusted stdout/stderr, never run a shell, never import a signing key.
    """
    argv = [
        "gh", "attestation", "verify", str(path),
        "--repo", REPOSITORY,
        "--signer-workflow", SIGNER_WORKFLOW,
        "--source-digest", source_sha,
    ]
    try:
        response = subprocess.run(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=GH_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False
    return response.returncode == 0


def validate_owner_candidate(
    root: Path, source_sha: str, kind: str, artifact: Path,
    receipt_path: Path, *,
    attest: Callable[[Path, str], bool] = verify_gh_attestation,
) -> dict:
    """Fail closed on every source, receipt, binary and signer mismatch."""
    need(isinstance(source_sha, str) and SHA40.fullmatch(source_sha) is not None,
         "owner_source_sha_invalid")
    need(kind in KIND_TO_PLATFORM, "owner_bundle_kind_invalid")
    platform = KIND_TO_PLATFORM[kind]

    try:
        _checkout(root, source_sha)
        lock = json.loads(
            _stable_read(root, "SOURCE_LOCK.json"),
            object_pairs_hook=_strict_object,
        )
        pinned = lock["dependencies"]["semwright"]["revision"]
        cargo = tomllib.loads(_stable_read(root, "Cargo.toml").decode("utf-8"))
        version = cargo["workspace"]["package"]["version"]
        need(isinstance(pinned, str) and SHA40.fullmatch(pinned) is not None,
             "owner_semwright_pin_invalid")
        need(isinstance(version, str) and bool(version),
             "owner_source_version_invalid")
    except (ManifestError, KeyError, TypeError, UnicodeError,
            ValueError, tomllib.TOMLDecodeError) as exc:
        if isinstance(exc, OwnerCandidateError):
            raise
        raise OwnerCandidateError("owner_checkout_unverified") from exc

    need(artifact.is_absolute() and receipt_path.is_absolute(),
         "owner_candidate_path_not_absolute")
    need(artifact != receipt_path, "owner_candidate_input_alias")
    try:
        # Bound hostile receipt inputs BEFORE the shared signature-hash helper
        # runs; a receipt is kilobytes, never a multi-gigabyte installer.
        receipt_size = receipt_path.lstat().st_size
        need(0 < receipt_size <= MAX_RECEIPT_BYTES,
             "owner_receipt_size_invalid")
        receipt = _bounded_receipt(receipt_path, root)
    except (AttestationInputError, CandidateError, OSError) as exc:
        raise OwnerCandidateError("owner_receipt_unverified") from exc
    need(isinstance(receipt, dict)
         and set(receipt) == {"schema_version", "candidate", "artifacts", "claims"}
         and type(receipt["schema_version"]) is int
         and receipt["schema_version"] == 1,
         "owner_receipt_schema_invalid")
    meta = receipt["candidate"]
    need(isinstance(meta, dict) and set(meta) == {
        "application", "version", "source_revision",
        "semwright_revision", "platform", "arch",
    } and meta["application"] == "Motionwright"
         and meta["source_revision"] == source_sha
         and meta["semwright_revision"] == pinned
         and meta["platform"] == platform
         and meta["version"] == version
         and isinstance(meta["arch"], str)
         and ARCH.fullmatch(meta["arch"]) is not None,
         "owner_receipt_source_mismatch")
    need(receipt["claims"] == UNSIGNED_CLAIMS,
         "owner_receipt_release_claims_invalid")

    listed = receipt["artifacts"]
    need(isinstance(listed, list)
         and len(listed) == len(EXPECTED_KINDS[platform]),
         "owner_receipt_artifact_count_invalid")
    seen_names: set[str] = set()
    seen_kinds: set[str] = set()
    matched = None
    for row in listed:
        need(isinstance(row, dict)
             and set(row) == {"filename", "bundle_kind", "bytes", "sha256"},
             "owner_receipt_artifact_row_invalid")
        name = row["filename"]
        need(isinstance(name, str) and BASENAME.fullmatch(name) is not None
             and name not in (".", "..")
             and name.casefold() not in seen_names,
             "owner_receipt_artifact_name_invalid")
        seen_names.add(name.casefold())
        try:
            found = classify(platform, Path(name))
        except (ValueError, KeyError) as exc:
            raise OwnerCandidateError("owner_receipt_artifact_type_invalid") from exc
        need(found == row["bundle_kind"], "owner_receipt_artifact_kind_mismatch")
        seen_kinds.add(found)
        need(type(row["bytes"]) is int and row["bytes"] > 0
             and isinstance(row["sha256"], str)
             and SHA64.fullmatch(row["sha256"]) is not None,
             "owner_receipt_artifact_digest_invalid")
        if found == kind:
            need(name == artifact.name, "owner_artifact_filename_mismatch")
            matched = row
    need(seen_kinds == EXPECTED_KINDS[platform],
         "owner_receipt_artifact_set_invalid")
    need(matched is not None, "owner_requested_kind_not_in_receipt")

    try:
        _, length, digest = verified_artifact(root, artifact)
    except (CandidateError, OSError, ValueError) as exc:
        raise OwnerCandidateError("owner_artifact_unavailable_or_changed") from exc
    need(length == matched["bytes"] and digest == matched["sha256"],
         "owner_artifact_hash_mismatch")
    try:
        before_artifact = artifact.stat()
        before_receipt = receipt_path.stat()
    except OSError as exc:
        raise OwnerCandidateError("owner_input_identity_unavailable") from exc

    # Do not report cryptographic success from a receipt's self-asserted flag.
    # Require a successful external signature verification against the exact
    # on-disk bytes, correct repository, signer workflow and source SHA.
    try:
        verified = attest(artifact, source_sha)
    except Exception as exc:
        raise OwnerCandidateError("owner_gh_attestation_unverified") from exc
    need(verified is True, "owner_gh_attestation_unverified")

    # The CLI independently hashes this pathname for signature admission.
    # Refuse TOCTOU substitutions or rewrites while it runs, even if a second
    # legitimate signed candidate of the same Git source was swapped in.
    try:
        _, size_after, digest_after = verified_artifact(root, artifact)
        after_artifact = artifact.stat()
        after_receipt = receipt_path.stat()
        current_receipt = _bounded_receipt(receipt_path, root)
    except (CandidateError, AttestationInputError, OSError, ValueError) as exc:
        raise OwnerCandidateError("owner_input_changed_during_attestation") from exc
    identity = lambda st: (
        st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_nlink,
    )
    need(size_after == length and digest_after == digest
         and identity(before_artifact) == identity(after_artifact)
         and identity(before_receipt) == identity(after_receipt)
         and current_receipt == receipt,
         "owner_input_changed_during_attestation")
    try:
        _checkout(root, source_sha)
    except ManifestError as exc:
        raise OwnerCandidateError("owner_checkout_changed") from exc

    return {
        "schema": SCHEMA,
        "application": "Motionwright",
        "source_sha": source_sha,
        "semwright_source_pin": pinned,
        "candidate": {
            "platform": platform,
            "arch": meta["arch"],
            "bundle_kind": kind,
            "filename": artifact.name,
            "bytes": length,
            "sha256": digest,
        },
        "github_attestation": {
            "status": "VERIFIED_SIGSTORE_WORKFLOW_PROVENANCE",
            "repository": REPOSITORY,
            "signer_workflow": SIGNER_WORKFLOW,
            "source_digest": source_sha,
        },
        "claims": {
            "installer_distribution_signature": "NOT_CLAIMED",
            "macos_notarization": "NOT_CLAIMED",
            "owner_installed_binary": "NOT_TESTED",
            "owner_broker_session": "NOT_TESTED",
            "owner_native_render": "NOT_RUN",
            "independent_human_acceptance": "NOT_RUN",
            "v01_release_authorized": False,
        },
    }


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--bundle-kind", choices=sorted(KIND_TO_PLATFORM), required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--candidate-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        report = validate_owner_candidate(
            root, args.source_sha, args.bundle_kind,
            args.artifact, args.candidate_receipt,
        )
        output = args.output
        need(output.is_absolute(), "owner_output_path_not_absolute")
        # Private review support receipts never belong in the public checkout.
        need(not output.is_relative_to(root.resolve()),
             "owner_output_must_be_outside_source")
        write_new_manifest(root, output, report)
        try:
            _checkout(root, args.source_sha)
        except ManifestError:
            _discard_new_output_on_source_change(output, report)
            raise OwnerCandidateError("owner_checkout_changed_after_write") from None
    except OwnerCandidateError as exc:
        print("owner-candidate-provenance=REFUSED code=" + str(exc), file=sys.stderr)
        return 2
    except (ManifestError, OSError, ValueError):
        print("owner-candidate-provenance=REFUSED code=owner_output_refused",
              file=sys.stderr)
        return 2

    print("owner-candidate-provenance=VERIFIED kind=" + args.bundle_kind
          + " source=" + args.source_sha
          + " distribution_signature=NOT_CLAIMED"
          + " human_acceptance=NOT_RUN release=BLOCKED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
