#!/usr/bin/env python3
"""Offline, independent pre-effect review of one authenticated Graph R74 carrier.

This program does not run inside GitHub Actions, and never writes GitHub commit
statuses, runs Native/Host/Studio or grants G2. The reviewer must independently
hold the private key and trusted Git clones; neither belongs on the owner runner.
"""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import ci_private_evidence as seal

MAX_ARCHIVE = seal.MAX_TOTAL + seal.MAX_FILES * 4096 + 8192 + 64
INDEX_SCHEMA = "motionwright.private.graph-producer-reference-index/0"
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class ReviewRejected(ValueError):
    pass


def require(value, why):
    if not value:
        raise ReviewRejected("GRAPH_REVIEW_" + why)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def json_original(raw, bound=1024 * 1024):
    require(isinstance(raw, bytes) and 0 < len(raw) <= bound, "JSON_BYTES")
    def pairs(seq):
        result = {}
        for key, value in seq:
            require(key not in result, "JSON_DUPLICATE_KEY")
            result[key] = value
        return result
    result = json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ReviewRejected("JSON_CONSTANT")))
    require(type(result) is dict, "JSON_ROOT")
    return result


def private_key_owner(path):
    """Read a platform-protected independent key without copying it to the product."""
    from review_private_key import read_private_key_bytes
    try:
        raw = read_private_key_bytes(path)
    except (ValueError, OSError) as error:
        raise ReviewRejected("REVIEW_PRIVATE_KEY_CUSTODY") from error
    key = serialization.load_pem_private_key(raw, password=None)
    require(isinstance(key, rsa.RSAPrivateKey) and key.key_size == 3072
            and key.public_key().public_numbers().e == 65537,
            "PRIVATE_KEY_RSA3072")
    public = key.public_key().public_bytes(serialization.Encoding.DER,
                 serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, sha(public)


def trusted_git(root, *args):
    root = Path(root)
    require(root.is_absolute() and root.is_dir()
            and not root.is_symlink(), "TRUSTED_REPO_PATH")
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          timeout=12, check=False)
    require(proc.returncode == 0, "TRUSTED_GIT_" + str(args)[:120])
    return proc.stdout


def clean_repo_path(name):
    require(type(name) is str and 1 <= len(name) <= 1024
            and "\\" not in name and ":" not in name
            and not name.startswith("/")
            and all(p not in ("", ".", "..") for p in name.split("/"))
            and str(PurePosixPath(name)) == name, "SOURCE_RELATIVE_PATH")
    return name


def verify_sources(repo, revision, listing, required):
    require(type(listing) is dict and required <= set(listing), "SOURCE_REQUIRED_FILES")
    tree = trusted_git(repo, "rev-parse", revision + "^{tree}").decode().strip()
    result = {}
    for name, ref in sorted(listing.items()):
        clean_repo_path(name)
        require(type(ref) is dict and set(ref) == {"bytes", "sha256"}
                and type(ref["bytes"]) is int and 0 < ref["bytes"] <= seal.MAX_FILE
                and type(ref["sha256"]) is str and HEX64.fullmatch(ref["sha256"]),
                "SOURCE_REF_" + name)
        raw = trusted_git(repo, "show", revision + ":" + name)
        require(len(raw) == ref["bytes"] and sha(raw) == ref["sha256"],
                "SOURCE_GIT_BYTES_" + name)
        result[name] = ref
    return tree, result


def source_member(entries, name, expected):
    require(name in entries and type(expected) is dict
            and set(expected) == {"path", "sha256", "size_bytes"}
            and type(expected["path"]) is str
            and expected["path"].endswith("/" + name.removeprefix("producer/"))
            and expected["size_bytes"] == len(entries[name])
            and expected["sha256"] == sha(entries[name]),
            "ORIGINAL_REF_" + name)
    return entries[name]


def original_zip(path):
    """Read the exact two original files from a raw ZIP or gh-extracted directory.

    gh run download extracts artifact ZIP contents, rather than leaving a ZIP.
    Both transports retain the same authenticated envelope and carrier bytes.
    """
    path = Path(path)
    require(path.is_absolute() and not path.is_symlink()
            and path.resolve(strict=True) == path, "ARTIFACT_CANONICAL_PATH")
    required = {"producer-originals.mwseal", "carrier.original.json"}
    if path.is_dir():
        members = list(path.iterdir())
        require(len(members) == 2 and {x.name for x in members} == required,
                "ARTIFACT_TWO_ORIGINAL_MEMBERS")
        original = {}
        for member in members:
            require(not member.is_symlink() and member.is_file(),
                    "ARTIFACT_REGULAR_FILE")
            fd = os.open(member, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                before = os.fstat(fd)
                require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                        and 0 < before.st_size <= MAX_ARCHIVE,
                        "ARTIFACT_REGULAR_FILE")
                with os.fdopen(os.dup(fd), "rb") as stream:
                    raw = stream.read(MAX_ARCHIVE + 1)
                after = os.fstat(fd)
                require(len(raw) == before.st_size
                        and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
                        "ARTIFACT_FILE_STABLE")
                original[member.name] = raw
            finally:
                os.close(fd)
        return original
    require(path.is_file() and path.stat().st_size <= MAX_ARCHIVE + (1 << 20),
            "ARTIFACT_ZIP_BYTES")
    with zipfile.ZipFile(path) as z:
        members = z.infolist()
        require({x.filename for x in members} == required
                and len(members) == 2
                and all(not x.is_dir() and 0 < x.file_size <= MAX_ARCHIVE
                        and x.compress_size <= MAX_ARCHIVE for x in members),
                "ARTIFACT_TWO_ORIGINAL_MEMBERS")
        original = {x.filename: z.read(x) for x in members}
    return original


def tar_originals(plaintext):
    entries = {}
    with tarfile.open(fileobj=io.BytesIO(plaintext), mode="r:") as archive:
        members = archive.getmembers()
        require(0 < len(members) <= seal.MAX_FILES + 1,
                "SNAPSHOT_MEMBER_COUNT")
        for row in members:
            name = row.name
            require(row.isfile() and row.size <= seal.MAX_FILE
                    and (name == "snapshot-manifest.json" or
                         name.startswith("producer/") and
                         all(p not in ("", ".", "..") for p in name.split("/")))
                    and name not in entries, "SNAPSHOT_MEMBER_" + name[:80])
            entries[name] = archive.extractfile(row).read()
    manifest = json_original(entries["snapshot-manifest.json"])
    require(manifest.get("schema") == "motionwright.ci.private-snapshot/1"
            and manifest.get("scope") == "selected-original-files-at-read-time"
            and manifest.get("selected_originals_complete") is True
            and manifest.get("complete_owner_state_claimed") is False
            and type(manifest.get("roots")) is dict
            and set(manifest["roots"]) == {"producer"}
            and manifest["roots"]["producer"].get("status") == "CAPTURED",
            "ORIGINAL_MANIFEST_COMPLETE")
    captured = manifest["roots"]["producer"]["files"]
    require(type(captured) is list, "MANIFEST_INVENTORY")
    recorded = {}
    for row in captured:
        require(type(row) is dict and type(row.get("archive_path")) is str,
                "MANIFEST_ROW")
        if "observation" in row:
            name = row["archive_path"]
            require(name in entries and name not in recorded, "MANIFEST_MEMBER")
            recorded[name] = 1
        else:
            require(row.get("kind", "").startswith("metadata-only-"),
                    "MANIFEST_UNKNOWN_OR_PARTIAL")
    require(set(entries) == set(recorded) | {"snapshot-manifest.json"},
            "SNAPSHOT_ORIGINAL_ROSTER")
    return entries


def review(archive, key_path, product, semwright, source_sha, sem_sha, run_id):
    require(os.environ.get("GITHUB_ACTIONS") != "true"
            and not os.environ.get("MOTIONWRIGHT_GRAPH_PRODUCER_RUN"),
            "INDEPENDENT_ONLY_NEVER_RUNNER")
    key_real = Path(key_path).resolve(strict=True)
    for trusted in (product, semwright):
        require(not key_real.is_relative_to(Path(trusted).resolve(strict=True)),
                "PRIVATE_KEY_INSIDE_OWNER_CHECKOUT")
    require(HEX40.fullmatch(source_sha) and HEX40.fullmatch(sem_sha)
            and type(run_id) is str and re.fullmatch(r"[1-9][0-9]*", run_id),
            "REVIEWER_EXPECTED_IDENTITY")
    key, spki_sha = private_key_owner(key_path)
    z = original_zip(archive)
    carrier = json_original(z["carrier.original.json"])
    require(carrier.get("source_sha") == source_sha
            and carrier.get("semwright_sha") == sem_sha
            and carrier.get("run_id") == run_id
            and carrier.get("run_attempt") == 1
            and carrier.get("recipient_spki_sha256") == spki_sha,
            "CARRIER_CONTEXT")
    envelope_ref = carrier.get("envelope")
    require(type(envelope_ref) is dict
            and envelope_ref.get("filename") == "producer-originals.mwseal"
            and envelope_ref.get("size_bytes") == len(z["producer-originals.mwseal"])
            and envelope_ref.get("sha256") == sha(z["producer-originals.mwseal"]),
            "CARRIER_BYTES")
    plaintext, context = seal.decrypt_snapshot(z["producer-originals.mwseal"], key)
    require(context == {"source_sha": source_sha, "semwright_sha": sem_sha,
            "run_id": run_id, "run_attempt": "1"}, "ENCRYPTED_CONTEXT")
    entries = tar_originals(plaintext)
    index_raw = entries["producer/producer-index.original.json"]
    index = json_original(index_raw)
    require(index.get("schema") == INDEX_SCHEMA, "INDEX_SCHEMA")
    source = index["source"]
    require(source["sha"] == source_sha and source["sdk_sha"] == sem_sha
            and source["run_id"] == run_id and source["run_attempt"] == 1,
            "INDEX_SOURCE_AND_RUN")
    require(carrier.get("index_sha256") == sha(index_raw),
            "INDEX_CARRIER_DIGEST")
    request = json_original(entries["producer/review-request.original.json"])
    require(request["source"] == source
            and request["execution"] == index["contract"]["execution"]
            and request["contexts"] == [
                "mw-owner/phase1-native-graph/" + run_id + "/1/graph-flow"],
            "REVIEW_REQUEST_SOURCE")
    source_member(entries, "producer/producer-index.original.json", request["index"])
    execution = index["contract"]["execution"]
    require(execution.get("scope") == "phase1-native-graph"
            and execution.get("job") == "real-service"
            and execution.get("selected_case") == "graph-flow",
            "REVIEWED_ONECASE_SCOPE")
    req_product = {"SOURCE_LOCK.json", "Cargo.lock", "tooling/ci_canvas_service.py",
                   "tooling/ci_private_evidence.py",
                   "tooling/native_graph_browser_owner.py",
                   "crates/native/examples/native-graph-review-session.rs",
                   "apps/studio/tests/semantic-graph-service.spec.ts"}
    req_sdk = {"Cargo.lock", "Cargo.toml",
               "scripts/dev/ci-driver-bwrap-profile.sh"}
    product_tree, product_rows = verify_sources(product, source_sha,
           index["contract"]["source_files"], req_product)
    sdk_tree, sdk_rows = verify_sources(semwright, sem_sha,
           index["contract"]["sdk_files"], req_sdk)
    require(source["tree"] == product_tree
            and index["contract"]["sdk_tree"] == sdk_tree,
            "TRUSTED_GIT_TREES")
    copies = request["outside_original_copies"]
    require(type(copies) is list and len(copies) >= 2, "REVIEW_OUTSIDE_COPY_ROSTER")
    seen = set()
    for item in copies:
        retained, original = item["retained"], item["original"]
        name = "producer/review-originals/" + Path(retained["path"]).name
        require(name not in seen and
                retained["sha256"] == original["sha256"] and
                retained["size_bytes"] == original["size_bytes"],
                "REVIEW_COPY_ORIGINAL")
        source_member(entries, name, retained)
        seen.add(name)
    return {
      "schema": "motionwright.graph-independent-pre-effect-review/1",
      "decision": "SOURCE_CARRIER_AUTHENTICATED_NO_OWNER_ADMISSION_NO_G2",
      "run_id": run_id, "source_sha": source_sha, "semwright_sha": sem_sha,
      "index_sha256": sha(index_raw), "recipient_spki_sha256": spki_sha,
      "ciphertext_sha256": sha(z["producer-originals.mwseal"]),
      "original_members": len(entries), "independent_product_sources": len(product_rows),
      "independent_semwright_sources": len(sdk_rows),
      "source_tree": product_tree, "semwright_tree": sdk_tree,
      "case": "graph-flow", "native_effects_observed": False,
      "github_status_written": False, "Graph6": "0/6",
      "G2": "NOT_GRANTED", "G3": "STOP",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-path", "--artifact-zip", dest="artifact_path",
                        type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--trusted-product", type=Path, required=True)
    parser.add_argument("--trusted-semwright", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--semwright-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    require(args.output.is_absolute() and args.output.parent.is_dir()
            and not args.output.exists(), "NEW_RECEIPT_ONLY")
    receipt = review(args.artifact_path, args.private_key, args.trusted_product,
         args.trusted_semwright, args.source_sha, args.semwright_sha, args.run_id)
    data = seal.json_bytes(receipt)
    seal.new_bytes(args.output, data, mode=0o600)
    print(json.dumps({"decision": receipt["decision"], "receipt_sha256": sha(data),
       "index_sha256":receipt["index_sha256"],"case":"graph-flow",
       "G2":"NOT_GRANTED"},sort_keys=True))


if __name__ == "__main__":
    main()
