#!/usr/bin/env python3
"""Closed five-file input handoff for the two remaining Manim Native phases.

Only a new runner recipient is public. The historical recipient private key is
never an input. HTTP operations in this module are GETs; creating a draft release
and uploading its one ciphertext remain explicit, separately reviewed host work.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import re
import stat
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import ci_private_evidence as engine

REPOSITORY = "seradotcom/motionwright"
REF = "refs/heads/fix/a4-manim-recovery-minimal-20261107"
JOB = "native-manim-resume"
RESUME_SCENARIO = "editable-source-resume"
RECOVERY_SCENARIO = "recovery-runtime-cycle"
ORIGIN_SDK = "acc0a089e2f0e3e9936632fb2a4aa0c2fab56199"
SDK = "0b2406bee57fc15280abbdf25f1b89527bd8987c"
ORIGIN = {"source_sha": "b3eaccd0695e91aefb504e3bd23e0e74fdaedfcb",
          "semwright_sha": ORIGIN_SDK, "run_id": "42340139012", "run_attempt": "1"}
SOURCE_RECOVERY = {
    "source_sha": "091791717563d0e85d94431d302ec0a170cafe12",
    "tool_run_id": "42349348642",
    "tool_sha256": "edf69fd96501a9a175635fad26d2abdb168aa572271119974fbb94fd1b6c1e8f",
    "report_sha256": "59bc7c6144957c4a66c138702b0626a294067d4bc349d3074854b371c0b57e8b",
    "export_sha256": "4159664b11e202b99c68cd88bb6a5cfd019f073e9a0b35e4990cc75a16610af7",
    "export_bytes": 768529,
}
INPUTS = {
    "reopen-original-reference.sqlite3": (241664, "f4efbf99dfc2d098f635cfd2fc1ba6fb44246dde2f4e042a80cb04e881e38a76"),
    "edited-original.sqlite3": (274432, "89dcdc86f053b26cfae98c4f39aae88a6e7322bbf66e4d47531dbd45fe365b3e"),
    "original-source.mwedit.zip": (768513, "5a8acf05e9efcc30099f6625ca4abc3a21503e0c161a90e979a1f576c580dc14"),
    "edited-source.mwedit.zip": (768527, "1f03cf21c2d8a4db0e29fa73f5fe11f96141bf393a8d9cb9299a3f8812947c33"),
    "source-edit-intent.original.json": (591, "e5be09f83106cf04d66d7103530033d7932dac5d94fe6670cedf30d37f29d024"),
}
MANIFEST = "resume-input-manifest.json"
MAX_PLAINTEXT = 4 * 1024 * 1024
MAX_CIPHERTEXT = MAX_PLAINTEXT + 16384
ASSET_NAME = "source-inputs.mwseal"
POLL_SECONDS = 300
require, sha = engine.require, engine.sha


def selected_scenario(value=None):
    value = os.environ.get("MANIM_NATIVE_SCENARIO", RESUME_SCENARIO) if value is None else value
    require(value in (RESUME_SCENARIO, RECOVERY_SCENARIO), "resume_closed_scenario_selection")
    return value


def input_files():
    from native_manim_retained_reference import CAPSULE, CAPSULE_IDENTITY, selected
    result = dict(INPUTS)
    if selected():
        require(selected_scenario() == RECOVERY_SCENARIO, "retained_reference_transfer_scenario")
        result[CAPSULE] = CAPSULE_IDENTITY
    return result


def selected_source_gate(current, root=None):
    if selected_scenario() == RECOVERY_SCENARIO:
        from native_manim_recovery_gate import check_source_gate
    else:
        from native_manim_resume_focals import check_source_gate
    return check_source_gate(current, root)


def selected_public_gate_files():
    original = ("source-gate.json", "source-gate.stdout.log", "source-gate.stderr.log")
    if selected_scenario() == RESUME_SCENARIO:
        return original
    return original + ("reference-gate.stdout.log", "reference-gate.stderr.log",
                       "runtime-receipt.json", "runtime-wheel-lock.json",
                       "runtime-installed-files.json", "runtime-artifact.original.json", "runtime-reuse.json")


def now():
    return datetime.now(timezone.utc)


def timestamp(value):
    require(type(value) is str and len(value) <= 40, "resume_utc_required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None and result.utcoffset() == timedelta(0), "resume_utc_offset")
    return result


def hosted_context():
    from native_manim_editable_outer import current_context
    current = current_context()
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY
            and os.environ.get("GITHUB_REF") == REF
            and os.environ.get("GITHUB_JOB") == JOB
            and os.environ.get("MANIM_NATIVE_SCENARIO") in (RESUME_SCENARIO, RECOVERY_SCENARIO)
            and os.environ.get("MW_MANIM_DEVELOPMENT_NATIVE") == "true"
            and os.environ.get("MW_MANIM_RESUME_EXPECTED_SHA") == current["source_sha"]
            and current["semwright_sha"] == SDK and current["run_attempt"] == "1"
            and os.geteuid() != 0, "resume_explicit_original_hosted_selection")
    input_files()  # Reject a foreign continuation before any key or Native owner.
    return current


def paths(current):
    root = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    suffix = current["source_sha"] + "-" + current["run_id"] + "-" + current["run_attempt"]
    return {"key": root / ("manim-resume-key-" + suffix),
            "public": root / ("manim-resume-recipient-" + suffix),
            "inputs": root / ("manim-resume-inputs-" + suffix)}


def tag(current):
    engine.validate_context(current)
    return "mw-manim-resume-" + current["run_id"] + "-" + current["source_sha"][:12]


def manifest(current, recipient_sha256):
    engine.validate_context(current)
    require(current["semwright_sha"] == SDK and current["run_attempt"] == "1"
            and engine.HEX.fullmatch(recipient_sha256), "resume_manifest_context")
    value = {"schema": "motionwright.manim.resume.input-manifest/1",
            "origin": ORIGIN, "target": current, "recipient_sha256": recipient_sha256,
            "files": {name: {"bytes": size, "sha256": digest}
                      for name, (size, digest) in sorted(input_files().items())},
            "source_recovery": SOURCE_RECOVERY,
            "old_phases": ["original", "reopen-original"],
            "remaining_phases": ["edited", "reopen-edited"],
            "project_changes_authorized": 0, "remaining_native_effect_invocations": 2,
            "old_native_results_revalidated_by_transfer": False,
            "automatic_retry": False}
    if selected_scenario() == RECOVERY_SCENARIO:
        value.update(schema="motionwright.manim.recovery-runtime.input-manifest/1",
                     scenario=RECOVERY_SCENARIO,
                     remaining_phases=["original-reference", "reopen-original-reference", "edited", "reopen-edited"],
                     remaining_native_effect_invocations=4,
                     additional_reference_renders=2, equivalence_to_old_runtime=False)
        from native_manim_retained_reference import PHASES, contract, selected
        if selected():
            value.update(schema="motionwright.manim.recovery-runtime.retained-reference-input-manifest/1",
                         retained_reference=contract(), remaining_phases=list(PHASES),
                         remaining_native_effect_invocations=3, additional_reference_renders=1)
    return value


def recipient_record(recipient, current, actor_id, issued, expires, source_gate_sha256):
    engine.public_key(recipient)
    engine.validate_context(current)
    require(type(actor_id) is str and re.fullmatch(r"[1-9][0-9]*", actor_id), "resume_actor_id")
    require(type(source_gate_sha256) is str and engine.HEX.fullmatch(source_gate_sha256),
            "resume_actual_source_gate_digest")
    require(timedelta(0) < timestamp(expires) - timestamp(issued) <= timedelta(seconds=2190),
            "resume_key_lifetime")
    value = {"schema": "motionwright.manim.resume.recipient/1", "context": current,
            "repository": REPOSITORY, "ref": REF, "job": JOB, "actor_id": actor_id,
            "recipient": recipient, "issued_at": issued, "expires_at": expires,
            "source_gate_sha256": source_gate_sha256,
            "draft_discovery": "bounded-list-then-release-id",
            "repository_push_permission_observed": False,
            "draft_visibility_already_observed": False,
            "input_contract_sha256": sha(engine.json_bytes(manifest(current, recipient["sha256"]))),
            "private_key_retained_only_on_this_runner": True,
            "historical_private_key_requested": False, "automatic_retry": False}
    if selected_scenario() == RECOVERY_SCENARIO:
        value.update(schema="motionwright.manim.recovery-runtime.recipient/1",
                     scenario=RECOVERY_SCENARIO, native_effect_invocations=4,
                     equivalence_to_old_runtime=False)
        from native_manim_retained_reference import contract, selected
        if selected():
            value.update(schema="motionwright.manim.recovery-runtime.retained-reference-recipient/1",
                         retained_reference=contract(), native_effect_invocations=3)
    return value


def validate_recipient(value, current, actor_id, source_gate_sha256, *, clock=None):
    require(type(value) is dict, "resume_recipient_object")
    expected = recipient_record(value.get("recipient"), current, actor_id,
                                value.get("issued_at"), value.get("expires_at"), source_gate_sha256)
    require(engine.json_bytes(value) == engine.json_bytes(expected), "resume_recipient_exact_context")
    instant = clock or now()
    require(timestamp(value["issued_at"]) - timedelta(seconds=5) <= instant
            < timestamp(value["expires_at"]), "resume_recipient_expired_or_future")
    return value["recipient"]


def make_plaintext(files, current, recipient_sha256):
    inputs = input_files()
    require(type(files) is dict and set(files) == set(inputs), "resume_closed_input_file_roster")
    for name, raw in files.items():
        size, digest = inputs[name]
        require(type(raw) is bytes and len(raw) == size and sha(raw) == digest,
                "resume_original_input_changed:" + name)
    entries = {**files, MANIFEST: engine.json_bytes(manifest(current, recipient_sha256))}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, raw in sorted(entries.items()):
            member = tarfile.TarInfo(name)
            member.mode, member.mtime, member.uid, member.gid = 0o600, 0, 0, 0
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
    result = output.getvalue()
    require(0 < len(result) <= MAX_PLAINTEXT, "resume_plaintext_budget")
    return result


def inspect_plaintext(raw, current, recipient_sha256):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_PLAINTEXT, "resume_plaintext_budget")
    files, inputs = {}, input_files()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        rows = archive.getmembers()
        require(len(rows) == len(inputs) + 1 and {row.name for row in rows} == set(inputs) | {MANIFEST},
                "resume_closed_archive_roster")
        for row in rows:
            expected_size = inputs[row.name][0] if row.name in inputs else 16384
            require(row.isfile() and not row.pax_headers and not row.linkname
                    and row.mode == 0o600 and row.uid == row.gid == row.mtime == 0
                    and row.uname == row.gname == "" and 0 < row.size <= expected_size,
                    "resume_regular_canonical_member")
            stream = archive.extractfile(row)
            require(stream is not None, "resume_member_unreadable")
            files[row.name] = stream.read(expected_size + 1)
            require(len(files[row.name]) == row.size, "resume_member_size_changed")
    expected = engine.json_bytes(manifest(current, recipient_sha256))
    require(files.pop(MANIFEST) == expected, "resume_manifest_exact_binding")
    # Canonical serialization rejects alternate headers, hidden trailing data,
    # sparse entries and duplicate files without calling tar.extractall.
    require(make_plaintext(files, current, recipient_sha256) == raw, "resume_archive_noncanonical")
    return files, expected


def check_repository_identity(raw, expected_id):
    # Installation-token repository metadata does not establish draft visibility.
    repository = engine.strict(raw)
    require(type(repository) is dict and repository.get("full_name") == REPOSITORY
            and type(expected_id) is int and expected_id > 0
            and type(repository.get("id")) is int and repository["id"] == expected_id,
            "resume_repository_identity_preflight_required")


def create_recipient():
    from native_manim_editable_outer import load_deadline
    from native_manim_resume_focals import folder
    current, deadline = hosted_context(), load_deadline()
    deadline.admit_work()
    _, gate_sha = selected_source_gate(current)
    scopes = paths(current)
    require(all(not p.exists() and not p.is_symlink() for p in scopes.values()),
            "resume_key_or_input_owner_already_exists_no_retry")
    scopes["public"].mkdir(mode=0o700)
    repository_raw = github_get("/repos/" + REPOSITORY, token=os.environ["GH_TOKEN"], limit=128 * 1024)
    engine.new_bytes(scopes["public"] / "repository-preflight.original.json", repository_raw)
    check_repository_identity(repository_raw, int(os.environ["GITHUB_REPOSITORY_ID"]))
    # Source gate + context authorize only this public recipient. Receiving the
    # exact draft and asset later is the observation that admits private inputs.
    scopes["key"].mkdir(mode=0o700)
    private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    engine.new_bytes(scopes["key"] / "recipient.pkcs8.pem", pem)
    der = private.public_key().public_bytes(serialization.Encoding.DER,
                                            serialization.PublicFormat.SubjectPublicKeyInfo)
    recipient = {"spki_der_base64": base64.b64encode(der).decode(), "sha256": sha(der)}
    instant = now().replace(microsecond=0)
    expires = instant + timedelta(seconds=min(2190, int(deadline.remaining("work"))))
    record = recipient_record(recipient, current, os.environ["GITHUB_ACTOR_ID"],
                              instant.isoformat(), expires.isoformat(), gate_sha)
    engine.new_json(scopes["public"] / "recipient.json", record)
    for name in selected_public_gate_files():
        engine.new_bytes(scopes["public"] / name, engine.regular(folder() / name, 2 * 1024 * 1024)[0])
    print(json.dumps({"status": "PUBLIC_RECIPIENT_READY", "context": current,
                      "recipient_sha256": recipient["sha256"], "private_key_exposed": False,
                      "repository_push_permission_observed": False, "draft_visibility_already_observed": False}))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        return None


def github_get(endpoint, *, token, limit, binary=False, missing=False):
    base = "/repos/" + REPOSITORY
    listed = base + "/releases?per_page=100&page=1"
    require(type(endpoint) is str and (endpoint == base or endpoint == listed
            or re.fullmatch(re.escape(base) + r"/releases/(?:assets/)?[1-9][0-9]*", endpoint)),
            "resume_api_closed_repository_and_bounded_query")
    headers = {"Accept": "application/octet-stream" if binary else "application/vnd.github+json",
               "Authorization": "Bearer " + token, "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "Motionwright-Manim-resume"}
    request = urllib.request.Request("https://api.github.com" + endpoint, headers=headers, method="GET")
    opener = urllib.request.build_opener(NoRedirect)
    try:
        response = opener.open(request, timeout=30)
    except urllib.error.HTTPError as error:
        if missing and error.code == 404:
            return None
        if not binary or error.code != 302:
            raise
        location = error.headers.get("Location", "")
        target = urllib.parse.urlsplit(location)
        require(target.scheme == "https" and target.hostname == "release-assets.githubusercontent.com"
                and target.port in (None, 443) and target.username is None and target.password is None,
                "resume_asset_redirect_host")
        # No GitHub credential is forwarded to the signed download URL.
        response = opener.open(urllib.request.Request(location, method="GET"), timeout=30)
    with response:
        length = response.headers.get("Content-Length")
        require(length is None or (length.isdecimal() and int(length) <= limit), "resume_http_size")
        data = response.read(limit + 1)
        require(0 < len(data) <= limit, "resume_http_budget")
    return data


def release_body(public, current):
    return engine.json_bytes({
        "schema": "motionwright.manim.resume.draft-link/1", "context": current,
        "recipient_sha256": public["recipient"]["sha256"],
        "input_contract_sha256": public["input_contract_sha256"],
        "source_gate_sha256": public["source_gate_sha256"],
        "recipient_issued_at": public["issued_at"], "recipient_expires_at": public["expires_at"],
        "source_original_count": len(input_files()), "automatic_retry": False,
        "native_authority_granted_by_release": False,
    }).decode("ascii")


def validate_release_header(value, public, current):
    require(type(value) is dict and type(value.get("id")) is int and value["id"] > 0
            and value.get("draft") is True and value.get("prerelease") is True
            and value.get("published_at") is None and value.get("tag_name") == tag(current)
            and value.get("target_commitish") == current["source_sha"]
            and value.get("name") == tag(current)
            and value.get("body") == release_body(public, current)
            and str(value.get("author", {}).get("id")) == public["actor_id"],
            "resume_draft_release_binding")
    # GitHub's release.created_at can describe the target commit. Retain it as
    # API metadata; freshness is the recipient-bound body and actual upload time.


def uploaded_asset(value, public, current):
    validate_release_header(value, public, current)
    assets = value.get("assets")
    require(type(assets) is list and len(assets) <= 1, "resume_exactly_one_encrypted_asset")
    if not assets:
        return None
    asset = assets[0]
    require(type(asset) is dict and type(asset.get("id")) is int and asset["id"] > 0
            and asset.get("name") == ASSET_NAME
            and str(asset.get("uploader", {}).get("id")) == public["actor_id"]
            and asset.get("state") in ("new", "open", "starter", "uploaded"),
            "resume_pending_asset_identity_or_state")
    if asset["state"] != "uploaded":
        return None
    return validate_release(value, public, current)


def validate_release(value, public, current):
    validate_release_header(value, public, current)
    assets = value.get("assets")
    require(type(assets) is list and len(assets) == 1, "resume_exactly_one_encrypted_asset")
    asset = assets[0]
    require(type(asset) is dict and type(asset.get("id")) is int and asset["id"] > 0
            and asset.get("name") == ASSET_NAME and asset.get("state") == "uploaded"
            and type(asset.get("size")) is int and 0 < asset["size"] <= MAX_CIPHERTEXT
            and re.fullmatch(r"sha256:[0-9a-f]{64}", asset.get("digest", ""))
            and asset.get("content_type") == "application/octet-stream"
            and str(asset.get("uploader", {}).get("id")) == public["actor_id"],
            "resume_asset_identity_digest_or_budget")
    require(timestamp(public["issued_at"]) <= timestamp(asset.get("created_at"))
            <= timestamp(asset.get("updated_at")) < timestamp(public["expires_at"]),
            "resume_asset_upload_window")
    return {"release_id": value["id"], "asset_id": asset["id"], "bytes": asset["size"],
            "sha256": asset["digest"][7:], "tag": value["tag_name"]}


def find_draft(raw, public, current):
    require(type(raw) is bytes and 0 < len(raw) <= 2 * 1024 * 1024, "resume_release_list_budget")
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, "resume_duplicate_api_json_key")
            result[key] = value
        return result
    rows = json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite API JSON")))
    require(type(rows) is list and len(rows) <= 100 and all(type(row) is dict for row in rows),
            "resume_bounded_release_list")
    matches = [row for row in rows if row.get("tag_name") == tag(current)]
    require(len(matches) <= 1, "resume_ambiguous_draft_tag_no_retry")
    if not matches:
        return None
    validate_release_header(matches[0], public, current)
    return matches[0]


def receive_inputs():
    from native_manim_editable_outer import load_deadline
    current, deadline = hosted_context(), load_deadline()
    deadline.admit_work()
    scopes = paths(current)
    public = engine.strict(engine.regular(scopes["public"] / "recipient.json", 16384)[0])
    _, gate_sha = selected_source_gate(current)
    recipient = validate_recipient(public, current, os.environ["GITHUB_ACTOR_ID"], gate_sha)
    key_path = scopes["key"] / "recipient.pkcs8.pem"
    info = key_path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o600, "resume_owned_ephemeral_private_key")
    key = serialization.load_pem_private_key(engine.regular(key_path, 8192)[0], password=None)
    require(not scopes["inputs"].exists() and not scopes["inputs"].is_symlink(),
            "resume_input_intake_already_attempted_no_retry")
    scopes["inputs"].mkdir(mode=0o700)
    engine.new_json(scopes["inputs"] / "intake-intent.original.json", {
        "context": current, "recipient_sha256": recipient["sha256"], "tag": tag(current),
        "maximum_poll_seconds": POLL_SECONDS, "maximum_files": len(input_files()) + 1,
        "maximum_ciphertext_bytes": MAX_CIPHERTEXT, "automatic_retry": False})
    token = os.environ["GH_TOKEN"]
    endpoint = None
    list_endpoint = "/repos/" + REPOSITORY + "/releases?per_page=100&page=1"
    stop = time.monotonic() + min(POLL_SECONDS, deadline.remaining("work"))
    original, first_release, first_asset_id = None, False, None
    while time.monotonic() < stop:
        deadline.admit_work()
        validate_recipient(public, current, os.environ["GITHUB_ACTOR_ID"], gate_sha)
        if endpoint is None:
            listed = github_get(list_endpoint, token=token, limit=2 * 1024 * 1024)
            selected = find_draft(listed, public, current)
            if selected is not None:
                engine.new_bytes(scopes["inputs"] / "release-list.original.json", listed)
                endpoint = "/repos/" + REPOSITORY + "/releases/" + str(selected["id"])
                engine.new_json(scopes["inputs"] / "draft-discovery.original.json", {
                    "release_id": selected["id"], "draft_visibility_observed": True,
                    "context": current, "automatic_retry": False})
        if endpoint is not None:
            original = github_get(endpoint, token=token, limit=256 * 1024)
            candidate = engine.strict(original)
            validate_release_header(candidate, public, current)
            require(endpoint.endswith("/" + str(candidate["id"])), "resume_discovered_release_id_changed")
            if not first_release:
                engine.new_bytes(scopes["inputs"] / "release-first-read.original.json", original)
                first_release = True
            ready = uploaded_asset(candidate, public, current)
            if candidate["assets"]:
                observed_id = candidate["assets"][0]["id"]
                if first_asset_id is None:
                    first_asset_id = observed_id
                    engine.new_bytes(scopes["inputs"] / "release-first-asset.original.json", original)
                require(observed_id == first_asset_id, "resume_original_upload_asset_id_changed")
            if ready is not None:
                break
        time.sleep(min(2, max(0, stop - time.monotonic())))
    require(original is not None, "resume_input_not_delivered_before_deadline")
    release = engine.strict(original)
    identity = validate_release(release, public, current)
    engine.new_bytes(scopes["inputs"] / "release.original.json", original)
    engine.new_json(scopes["inputs"] / "download-intent.original.json", identity)
    envelope = github_get("/repos/" + REPOSITORY + "/releases/assets/" + str(identity["asset_id"]),
                          token=token, limit=MAX_CIPHERTEXT, binary=True)
    engine.new_bytes(scopes["inputs"] / ASSET_NAME, envelope)
    require(len(envelope) == identity["bytes"] and sha(envelope) == identity["sha256"],
            "resume_downloaded_asset_digest_changed")
    readback = github_get(endpoint, token=token, limit=256 * 1024)
    engine.new_bytes(scopes["inputs"] / "release-readback.original.json", readback)
    require(validate_release(engine.strict(readback), public, current) == identity,
            "resume_asset_identity_changed_after_download")
    validate_recipient(public, current, os.environ["GITHUB_ACTOR_ID"], gate_sha)
    plaintext, observed_context = engine.decrypt_snapshot(envelope, key)
    require(observed_context == current, "resume_authenticated_context_changed")
    files, manifest_raw = inspect_plaintext(plaintext, current, recipient["sha256"])
    destination = scopes["inputs"] / "originals"
    destination.mkdir(mode=0o700)
    for name, raw in sorted(files.items()):
        engine.new_bytes(destination / name, raw)
    engine.new_bytes(destination / MANIFEST, manifest_raw)
    from native_manim_retained_reference import selected as retained_selected
    intake_status = "SOURCE_AND_RETAINED_REFERENCE_INPUTS" if retained_selected() else "SOURCE_INPUTS_ONLY"
    engine.new_json(scopes["inputs"] / "intake-result.original.json", {
        "schema": "motionwright.manim.resume.input-intake/1", "status": intake_status,
        "context": current, "origin": ORIGIN, "asset": identity,
        "recipient_sha256": recipient["sha256"], "manifest_sha256": sha(manifest_raw),
        "input_contract_sha256": public["input_contract_sha256"],
        "original_file_count": len(input_files()), "plaintext_bytes": len(plaintext),
        "project_changes_applied": 0, "native_effect_invocations": 0,
        "historical_private_key_received": False, "automatic_retry": False})
    print(json.dumps({"status": intake_status, "context": current,
                      "asset_id": identity["asset_id"], "sha256": identity["sha256"]}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("create-recipient", "receive-inputs"))
    args = parser.parse_args()
    (create_recipient if args.command == "create-recipient" else receive_inputs)()


if __name__ == "__main__":
    main()
