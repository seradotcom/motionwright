#!/usr/bin/env python3
"""Retain an authenticated raw Graph review ZIP without decrypting its contents."""
import argparse
import hashlib
import io
from pathlib import Path
import re
import stat
import shutil
import subprocess
import zipfile

from ci_private_evidence import MAX_FILE, strict
from review_original_io import new_bytes, new_json, read_bytes


REPOSITORY = "seradotcom/motionwright"
WORKFLOW = ".github/workflows/studio-service-verification.yml"
PREFIX = "repos/" + REPOSITORY + "/actions/"
MAX_ZIP = MAX_FILE
MAX_ARTIFACTS = 100


def require(condition, reason):
    """Reject an incomplete or mismatched transport observation."""
    if not condition:
        raise ValueError("graph_review_artifact_" + reason)


def gh_json_or_bytes(route, name, directory):
    """Retain one read-only API response using the host's installed GitHub CLI."""
    executable = shutil.which("gh")
    require(executable is not None, "github_cli_unavailable")
    command = [executable, "api", route]
    new_json(directory / (name + ".intent.json"), {"argv": command, "automatic_retry": False})
    with (directory / (name + ".stdout")).open("xb") as output, \
            (directory / (name + ".stderr")).open("xb") as error:
        try:
            result = subprocess.run(command, stdout=output, stderr=error,
                                    check=False, timeout=120)
        except subprocess.TimeoutExpired as failure:
            new_json(directory / (name + ".exit.json"),
                     {"exit_code": None, "timed_out": True, "automatic_retry": False})
            raise ValueError("graph_review_artifact_api_timeout") from failure
    new_json(directory / (name + ".exit.json"), {"exit_code": result.returncode})
    require(result.returncode == 0, "api_failed_no_retry")
    return read_bytes(directory / (name + ".stdout"), MAX_FILE)

def checked_run(value, source, run_id):
    """Bind a live manual run to the expected repository and first attempt."""
    repository = value.get("repository", {})
    head = value.get("head_repository", {})
    require(value.get("id") == int(run_id)
            and type(value.get("run_attempt")) is int and value["run_attempt"] == 1
            and value.get("head_sha") == source
            and value.get("event") == "workflow_dispatch"
            and value.get("path") == WORKFLOW
            and value.get("status") == "in_progress"
            and type(value.get("workflow_id")) is int and value["workflow_id"] > 0
            and repository.get("full_name") == REPOSITORY
            and head.get("full_name") == REPOSITORY
            and type(repository.get("id")) is int and repository["id"] > 0
            and head.get("id") == repository["id"], "run_context")
    return {key: value[key] for key in ("id", "run_attempt", "head_sha", "event", "path", "workflow_id")} | {
        "repository_id": repository["id"], "head_repository_id": head["id"]}


def checked_artifact(value, name, run):
    """Bind one immutable artifact descriptor to the independently observed run."""
    origin = value.get("workflow_run", {})
    require(type(value.get("id")) is int and value["id"] > 0
            and value.get("name") == name and value.get("expired") is False
            and type(value.get("size_in_bytes")) is int
            and 0 < value["size_in_bytes"] <= MAX_ZIP
            and type(value.get("digest")) is str
            and re.fullmatch(r"sha256:[0-9a-f]{64}", value["digest"])
            and origin.get("id") == run["id"]
            and origin.get("head_sha") == run["head_sha"]
            and origin.get("repository_id") == run["repository_id"]
            and origin.get("head_repository_id") == run["head_repository_id"], "artifact_context")
    return {key: value[key] for key in ("id", "name", "size_in_bytes", "digest")} | {
        "run": {key: origin[key] for key in ("id", "head_sha", "repository_id", "head_repository_id")}}


def checked_members(raw):
    """Check the closed ZIP roster and CRCs without extracting or decrypting."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        rows = archive.infolist()
        require(len(rows) == 2 and {row.filename for row in rows} == {
                    "producer-originals.mwseal", "carrier.original.json"}
                and all(not row.is_dir() and 0 < row.file_size <= MAX_ZIP
                        and 0 < row.compress_size <= MAX_ZIP
                        and stat.S_IFMT(row.external_attr >> 16) in (0, stat.S_IFREG)
                        and not row.flag_bits & 1
                        and row.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        for row in rows)
                and sum(row.file_size for row in rows) <= MAX_ZIP, "zip_members")
        require(archive.testzip() is None, "zip_crc")


def download(source_sha, run_id, output):
    """Retain one run-bound raw ZIP and API originals in a fresh private directory.

    Success verifies transport provenance only. Independent decryption, source
    review and any owner admission remain separate operations.
    """
    require(type(source_sha) is str and re.fullmatch(r"[0-9a-f]{40}", source_sha)
            and type(run_id) is str and re.fullmatch(r"[1-9][0-9]*", run_id), "expected_identity")
    output = Path(output)
    require(output.is_absolute() and not output.exists() and not output.is_symlink()
            and output.parent.resolve(strict=True) == output.parent, "new_private_directory")
    output.mkdir(mode=0o700)
    name = "graph-originals-review-" + source_sha + "-" + run_id + "-1"
    run_route = PREFIX + "runs/" + run_id
    before = checked_run(strict(gh_json_or_bytes(run_route, "run-before", output)), source_sha, run_id)
    listing = strict(gh_json_or_bytes(run_route + "/artifacts?per_page=100&page=1", "artifacts", output))
    rows = listing.get("artifacts")
    require(type(rows) is list and type(listing.get("total_count")) is int
            and len(rows) == listing["total_count"] and len(rows) <= MAX_ARTIFACTS, "complete_artifact_listing")
    matches = [row for row in rows if type(row) is dict and row.get("name") == name]
    require(len(matches) == 1, "unique_named_artifact")
    selected = checked_artifact(matches[0], name, before)
    route = PREFIX + "artifacts/" + str(selected["id"])
    detail = checked_artifact(strict(gh_json_or_bytes(route, "artifact-before", output)), name, before)
    require(detail == selected, "artifact_selection_changed")
    raw = gh_json_or_bytes(route + "/zip", "artifact-zip", output)
    # Preserve the actual response even when its declared digest is wrong.
    new_bytes(output / "artifact.original.zip", raw, mode=0o600)
    digest = hashlib.sha256(raw).hexdigest()
    require(len(raw) == selected["size_in_bytes"] and digest == selected["digest"][7:], "zip_digest_or_size")
    checked_members(raw)
    after = checked_run(strict(gh_json_or_bytes(run_route, "run-after", output)), source_sha, run_id)
    require(after == before, "run_changed")
    final = checked_artifact(strict(gh_json_or_bytes(route, "artifact-after", output)), name, after)
    require(final == selected, "artifact_changed")
    result = {"schema": "motionwright.graph-review-artifact-transport/1",
              "decision": "TRANSPORT_VERIFIED_REQUIRES_INDEPENDENT_REVIEW",
              "repository": REPOSITORY, "workflow": WORKFLOW,
              "source_sha": source_sha, "run_id": run_id, "run_attempt": 1,
              "artifact_id": selected["id"], "artifact_name": name,
              "zip_sha256": digest, "zip_bytes": len(raw),
              "archive": str(output / "artifact.original.zip"),
              "decrypted": False, "github_status_written": False}
    new_json(output / "transport-result.original.json", result)
    return result


def main(argv=None):
    """Fetch a bounded Graph review artifact using the installed read-only gh CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = download(args.source_sha, args.run_id, args.output)
    print(result["archive"])


if __name__ == "__main__":
    main()
