#!/usr/bin/env python3
"""Read-only runner delivery of one fixed Graph build capsule.

Reuse the Manim recipient/draft/AEAD protocol, with an explicit Graph input-only
schema. The runner never needs draft visibility or a contents-write token. A
separate same-workflow courier carries an encrypted read ticket. Its signed
asset URL authorizes one exact ciphertext download, without a GitHub credential.
The source-bound Graph52 TAR is not an owner admission or a Native result.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import ssl
import stat
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import native_graph_build_reuse as reuse

REPOSITORY = "seradotcom/motionwright"
WORKFLOW = ".github/workflows/studio-service-verification.yml"
JOB = "real-service"
COURIER_SELECTION = "graph-input-delivery"
ASSET_NAME = "graph52-build-inputs.mwseal"
MAX_CIPHERTEXT = reuse.CAPSULE["bytes"] + 16384
MAX_TICKET = 32768
MAX_TICKET_PLAINTEXT = 24576
MAX_COURIER_ZIP = 65536
MAX_PUBLIC = 32768
POLL_SECONDS = 900
DOWNLOAD_SECONDS = 180
RECIPIENT_SECONDS = 2190
MAGIC = b"MWCISEAL1\n"
require, encoded, sha, strict = reuse.require, reuse.encoded, reuse.sha, reuse.strict


def seal_module():
    import ci_private_evidence
    return ci_private_evidence


def timestamp(value):
    require(type(value) is str and len(value) <= 40, "ticket_utc")
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(instant.tzinfo is not None and instant.utcoffset() == timedelta(0), "ticket_utc_offset")
    return instant


def now():
    return datetime.now(timezone.utc)


def context(current):
    require(type(current) is dict and set(current) == {"source_sha", "semwright_sha", "run_id", "run_attempt"}
            and type(current["source_sha"]) is str and re.fullmatch(r"[0-9a-f]{40}", current["source_sha"])
            and current["semwright_sha"] == reuse.ORIGIN["semwright_sha"]
            and type(current["run_id"]) is str and re.fullmatch(r"[1-9][0-9]*", current["run_id"])
            and current["run_id"] not in ("42356210001", "42372165893") and current["run_attempt"] == "1",
            "input_context")
    return current


def input_contract(current):
    context(current)
    return {"schema": "motionwright.graph.original-build-input-contract/1", "origin": reuse.ORIGIN,
            "target": current, "capsule": reuse.CAPSULE, "manifest_sha256": reuse.MANIFEST_SHA,
            "members": reuse.MEMBERS, "selected_roles": list(reuse.ROLES), "selector": reuse.SELECTOR,
            "owner_authority": False, "native_calls": 0, "new_builds": 0, "automatic_retry": False}


def recipient_record(recipient, current, actor, issued, expires, deadline_sha):
    context(current); seal_module().public_key(recipient)
    require(type(actor) is int and actor > 0 and re.fullmatch(r"[0-9a-f]{64}", deadline_sha)
            and timedelta(0) < timestamp(expires) - timestamp(issued) <= timedelta(seconds=RECIPIENT_SECONDS),
            "recipient_original_clock_actor")
    return {"schema": "motionwright.graph.original-build-recipient/1", "context": current,
            "repository": REPOSITORY, "workflow": WORKFLOW, "job": JOB, "actor_id": actor,
            "recipient": recipient, "issued_at": issued, "expires_at": expires,
            "deadline_original_sha256": deadline_sha, "input_contract_sha256": sha(encoded(input_contract(current))),
            "input_contract": input_contract(current), "private_key_retained_only_on_this_runner": True,
            "historical_private_key_requested": False, "owner_authority": False,
            "native_calls": 0, "automatic_retry": False}


def validate_recipient(value, current, actor, deadline_sha, *, instant=None):
    require(type(value) is dict, "recipient_object")
    expected = recipient_record(value.get("recipient"), current, actor,
                                value.get("issued_at"), value.get("expires_at"), deadline_sha)
    require(encoded(value) == encoded(expected), "recipient_exact_binding")
    instant = instant or now()
    require(timestamp(value["issued_at"]) - timedelta(seconds=5) <= instant < timestamp(value["expires_at"]),
            "recipient_expired_or_future")
    return value["recipient"]


def paths(root):
    root = Path(root)
    origin = root / "e/graph-producer/origin-transport"
    return {"root": origin, "public": root / "graph-build-inputs-public",
            "private": root / "graph-build-inputs-key", "intake": origin / "intake"}


def create_recipient(root, current, actor, deadline_sha, remaining):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    scopes = paths(root)
    require(all(not scopes[name].exists() and not scopes[name].is_symlink()
                for name in ("root", "public", "private")), "recipient_once_only")
    for name in ("root", "public", "private"):
        scopes[name].mkdir(mode=0o700)
    private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    reuse.new_bytes(scopes["private"] / "recipient.pkcs8.pem", pem)
    der = private.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    public = {"spki_der_base64": base64.b64encode(der).decode(), "sha256": sha(der)}
    begin = now().replace(microsecond=0)
    require(type(remaining) is int and remaining > POLL_SECONDS + 60, "input_remaining_clock")
    end = begin + timedelta(seconds=min(RECIPIENT_SECONDS, remaining))
    value = recipient_record(public, current, actor, begin.isoformat(), end.isoformat(), deadline_sha)
    reuse.new_json(scopes["public"] / "recipient.json", value)
    return value


def tag(current):
    context(current)
    return "mw-graph-build-input-" + current["run_id"] + "-" + current["source_sha"][:12]


def release_body(public):
    return encoded({"schema": "motionwright.graph.original-build-draft-link/1", "context": public["context"],
        "recipient_sha256": public["recipient"]["sha256"], "input_contract_sha256": public["input_contract_sha256"],
        "deadline_original_sha256": public["deadline_original_sha256"], "recipient_issued_at": public["issued_at"],
        "recipient_expires_at": public["expires_at"], "capsule": reuse.CAPSULE,
        "owner_authority": False, "native_calls": 0, "automatic_retry": False}).decode("ascii")


def validate_release_header(value, public):
    current = public["context"]
    require(type(value) is dict and type(value.get("id")) is int and value["id"] > 0
            and value.get("draft") is True and value.get("prerelease") is True and value.get("published_at") is None
            and value.get("tag_name") == value.get("name") == tag(current)
            and value.get("target_commitish") == current["source_sha"] and value.get("body") == release_body(public)
            and value.get("author", {}).get("id") == public["actor_id"], "draft_exact_binding")


def validate_release(value, public):
    validate_release_header(value, public)
    assets = value.get("assets")
    require(type(assets) is list and len(assets) == 1, "one_ciphertext_asset")
    row = assets[0]
    require(type(row.get("id")) is int and row["id"] > 0 and row.get("name") == ASSET_NAME
            and row.get("state") == "uploaded" and type(row.get("size")) is int
            and reuse.CAPSULE["bytes"] < row["size"] <= MAX_CIPHERTEXT
            and re.fullmatch(r"sha256:[0-9a-f]{64}", row.get("digest", ""))
            and row.get("content_type") == "application/octet-stream"
            and row.get("uploader", {}).get("id") == public["actor_id"], "asset_id_digest_bound")
    require(timestamp(public["issued_at"]) <= timestamp(row["created_at"])
            <= timestamp(row["updated_at"]) < timestamp(public["expires_at"]), "asset_upload_window")
    return {"release_id": value["id"], "asset_id": row["id"], "bytes": row["size"], "sha256": row["digest"][7:]}


def signed_url(value, *, instant=None):
    require(type(value) is str and 0 < len(value) <= 8192 and "\r" not in value and "\n" not in value, "signed_url_length")
    url = urllib.parse.urlsplit(value)
    require(url.scheme == "https" and url.hostname == "release-assets.githubusercontent.com"
            and url.port in (None, 443) and url.username is None and url.password is None and not url.fragment
            and re.fullmatch(r"/github-production-release-asset/[1-9][0-9]*/[0-9A-Fa-f-]+", url.path), "signed_url_origin_path")
    pairs = urllib.parse.parse_qsl(url.query, strict_parsing=True)
    query = dict(pairs)
    require(len(query) == len(pairs) and query.get("sp") == "r" and query.get("spr") == "https"
            and query.get("sr") == "b" and query.get("sig") and query.get("se"), "read_only_signed_resource")
    expires = timestamp(query["se"])
    instant = instant or now()
    require(instant + timedelta(seconds=DOWNLOAD_SECONDS + 10) < expires
            <= instant + timedelta(hours=2), "signed_url_expiry_bound")
    return expires


def read_ticket(public, asset, url, release_raw, *, instant=None):
    instant = instant or now(); expires = signed_url(url, instant=instant)
    require(validate_release(strict(release_raw), public) == asset, "ticket_original_release")
    effective = min(expires, timestamp(public["expires_at"]), instant + timedelta(seconds=480))
    require(instant + timedelta(seconds=DOWNLOAD_SECONDS + 10) < effective, "ticket_full_download_window")
    return {"schema": "motionwright.graph.build-ciphertext-read-ticket/1", "context": public["context"],
        "recipient_sha256": public["recipient"]["sha256"], "recipient_original_sha256": sha(encoded(public)),
        "input_contract_sha256": public["input_contract_sha256"], "asset": asset,
        "release_original_sha256": sha(release_raw), "release_original_base64": base64.b64encode(release_raw).decode(),
        "url": url, "url_sha256": sha(url.encode()), "url_expires_at": expires.isoformat(),
        "issued_at": instant.isoformat(), "expires_at": effective.isoformat(),
        "maximum_gets": 1, "maximum_download_seconds": DOWNLOAD_SECONDS,
        "github_token_forwarded": False, "owner_authority": False, "native_calls": 0, "automatic_retry": False}


def validate_ticket(value, public, *, instant=None):
    instant = instant or now()
    require(type(value) is dict and set(value) == {"schema", "context", "recipient_sha256", "recipient_original_sha256",
        "input_contract_sha256", "asset", "release_original_sha256", "release_original_base64", "url", "url_sha256",
        "url_expires_at", "issued_at", "expires_at", "maximum_gets", "maximum_download_seconds",
        "github_token_forwarded", "owner_authority", "native_calls", "automatic_retry"}
            and len(encoded(value)) <= MAX_TICKET_PLAINTEXT
            and value.get("schema") == "motionwright.graph.build-ciphertext-read-ticket/1"
            and value.get("context") == public["context"]
            and value.get("recipient_sha256") == public["recipient"]["sha256"]
            and value.get("recipient_original_sha256") == sha(encoded(public))
            and value.get("input_contract_sha256") == public["input_contract_sha256"]
            and value.get("url_sha256") == sha(value["url"].encode())
            and type(value.get("maximum_gets")) is int and value["maximum_gets"] == 1
            and type(value.get("maximum_download_seconds")) is int and value["maximum_download_seconds"] == DOWNLOAD_SECONDS
            and value.get("github_token_forwarded") is False and value.get("owner_authority") is False
            and type(value.get("native_calls")) is int and value["native_calls"] == 0
            and value.get("automatic_retry") is False, "read_ticket_binding")
    original = base64.b64decode(value["release_original_base64"], validate=True)
    require(len(original) <= 16 * 1024 and base64.b64encode(original).decode() == value["release_original_base64"]
            and validate_release(strict(original), public) == value["asset"]
            and sha(original) == value["release_original_sha256"], "read_ticket_asset_original")
    expires = signed_url(value["url"], instant=instant)
    require(expires == timestamp(value["url_expires_at"])
            and timestamp(value["issued_at"]) - timedelta(seconds=5) <= instant
            and instant + timedelta(seconds=DOWNLOAD_SECONDS + 10) < timestamp(value["expires_at"])
            <= min(expires, timestamp(public["expires_at"]), timestamp(value["issued_at"]) + timedelta(seconds=480)),
            "read_ticket_expired_or_extended")
    return value["asset"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        return None


def opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect,
                                      urllib.request.HTTPSHandler(context=ssl.create_default_context()))


def api_get(endpoint, token, maximum, *, artifact_zip=False):
    prefix = "/repos/" + REPOSITORY
    allowed = (re.fullmatch(re.escape(prefix) + r"/actions/runs/[1-9][0-9]*(?:/jobs\?per_page=100)?", endpoint)
               or re.fullmatch(re.escape(prefix) + r"/actions/artifacts(?:/[1-9][0-9]*(?:/zip)?|\?name=[A-Za-z0-9_-]+&per_page=100)", endpoint))
    require(allowed and type(token) is str and token, "closed_actions_read_endpoint")
    headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Motionwright-Graph-build-inputs"}
    request = urllib.request.Request("https://api.github.com" + endpoint, headers=headers, method="GET")
    transport = opener()
    try:
        response = transport.open(request, timeout=20)
    except urllib.error.HTTPError as error:
        if not artifact_zip or error.code != 302:
            raise
        target = urllib.parse.urlsplit(error.headers.get("Location", ""))
        require(target.scheme == "https" and target.username is None and target.password is None
                and target.port in (None, 443) and type(target.hostname) is str and (target.hostname == "productionresultssa0.blob.core.windows.net"
                    or target.hostname.endswith(".blob.core.windows.net") and target.hostname.startswith("productionresults")),
                "courier_artifact_redirect_origin")
        response = transport.open(urllib.request.Request(target.geturl(), method="GET"), timeout=20)
    with response:
        require(response.status == 200, "actions_read_status")
        length = response.headers.get("Content-Length")
        require(length is None or length.isdecimal() and 0 < int(length) <= maximum, "actions_read_length")
        data = response.read(maximum + 1)
        require(0 < len(data) <= maximum, "actions_read_bound")
    return data


def courier_name(current, recipient_sha):
    context(current); require(re.fullmatch(r"[0-9a-f]{64}", recipient_sha), "courier_recipient_digest")
    return "graph-build-delivery-" + current["run_id"] + "-" + recipient_sha[:16]


def create_courier_file(output, environment):
    """No key/dependency installation: only the opaque once-delivered ticket."""
    current = {"source_sha": environment["GITHUB_SHA"], "semwright_sha": reuse.ORIGIN["semwright_sha"],
               "run_id": environment["GRAPH_BUILD_TARGET_RUN"], "run_attempt": "1"}
    context(current)
    require(environment["GITHUB_REPOSITORY"] == REPOSITORY and environment["GITHUB_EVENT_NAME"] == "workflow_dispatch"
            and environment["GITHUB_RUN_ATTEMPT"] == "1" and environment["GITHUB_JOB"] == "graph-input-delivery"
            and environment["GITHUB_ACTOR_ID"].isdecimal(), "courier_original_dispatch")
    raw = base64.b64decode(environment["GRAPH_BUILD_TICKET"], validate=True)
    require(base64.b64encode(raw).decode() == environment["GRAPH_BUILD_TICKET"] and len(raw) <= MAX_TICKET
            and sha(raw) == environment["GRAPH_BUILD_TICKET_SHA256"] and raw.startswith(MAGIC), "opaque_ticket_input")
    offset = len(MAGIC); size = int.from_bytes(raw[offset:offset + 4], "big")
    require(0 < size <= 8192 and len(raw) >= offset + 4 + size + 16, "opaque_ticket_header_size")
    header = strict(raw[offset + 4:offset + 4 + size])
    require(header["context"] == current and header["recipient_spki_sha256"] == environment["GRAPH_BUILD_RECIPIENT_SHA256"]
            and type(header["plaintext_bytes"]) is int and 0 < header["plaintext_bytes"] <= MAX_TICKET_PLAINTEXT,
            "opaque_ticket_context")
    output = Path(output); output.mkdir(mode=0o700, exist_ok=False)
    reuse.new_bytes(output / "read-ticket.mwseal", raw)
    return courier_name(current, environment["GRAPH_BUILD_RECIPIENT_SHA256"])


def checked_courier_artifact(item, current, public, source_run):
    require(type(item) is dict and type(item.get("id")) is int and item["id"] > 0
            and item.get("name") == courier_name(current, public["recipient"]["sha256"])
            and item.get("expired") is False and type(item.get("size_in_bytes")) is int
            and 0 < item["size_in_bytes"] <= MAX_COURIER_ZIP
            and re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("digest", ""))
            and item.get("workflow_run", {}).get("head_sha") == current["source_sha"]
            and type(item["workflow_run"].get("id")) is int and item["workflow_run"]["id"] > 0
            and item["workflow_run"]["id"] != int(current["run_id"]), "courier_artifact_identity")
    run = source_run
    require(run["id"] == item["workflow_run"]["id"] and run.get("head_sha") == current["source_sha"]
            and type(run.get("run_attempt")) is int and run["run_attempt"] == 1 and run.get("event") == "workflow_dispatch"
            and run.get("status") == "completed" and run.get("conclusion") == "success"
            and run.get("repository", {}).get("full_name") == REPOSITORY
            and run.get("head_repository", {}).get("full_name") == REPOSITORY
            and run.get("path", "").split("@", 1)[0] == WORKFLOW and run.get("workflow_id") == 391230122
            and run.get("actor", {}).get("id") == public["actor_id"] and run["actor"].get("type") == "User",
            "courier_authenticated_actor_workflow")
    require(timestamp(public["issued_at"]) <= timestamp(item["created_at"])
            <= timestamp(item["updated_at"]) < timestamp(public["expires_at"]), "courier_artifact_window")
    return item


def read_courier_zip(raw, artifact):
    require(len(raw) == artifact["size_in_bytes"] and "sha256:" + sha(raw) == artifact["digest"], "courier_zip_identity")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(len(entries) == 1 and not archive.comment, "courier_one_ticket")
        row = entries[0]
        require(row.filename == row.orig_filename == "read-ticket.mwseal" and not row.is_dir() and not row.flag_bits & 1
                and not row.comment and row.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                and 0 <= row.header_offset < len(raw) and 0 < row.compress_size <= len(raw)
                and 0 < row.file_size <= MAX_TICKET
                and stat.S_IFMT(row.external_attr >> 16) in (0, stat.S_IFREG), "courier_ticket_regular")
        data = archive.read(row)
        require(len(data) == row.file_size, "courier_ticket_size")
    return data


def download_ciphertext_once(ticket, public, output, guard):
    asset = validate_ticket(ticket, public)
    output = Path(output)
    reuse.new_json(output / "asset-download-intent.original.json", {"asset": asset,
        "url_sha256": ticket["url_sha256"], "context": public["context"], "maximum_gets": 1,
        "maximum_seconds": DOWNLOAD_SECONDS, "github_token_forwarded": False, "automatic_retry": False})
    target = output / ASSET_NAME
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    started, count, hashed = time.monotonic(), 0, hashlib.sha256()
    try:
        # No Authorization header, cookies, proxy, or redirect to another origin.
        request = urllib.request.Request(ticket["url"], headers={"Accept": "application/octet-stream"}, method="GET")
        with os.fdopen(fd, "wb", closefd=False) as stream, opener().open(request, timeout=20) as response:
            require(response.status == 200, "ciphertext_response_status")
            length = response.headers.get("Content-Length")
            require(length is None or length.isdecimal() and int(length) == asset["bytes"], "ciphertext_response_length")
            while block := response.read(reuse.CHUNK):
                guard(); require(time.monotonic() - started < DOWNLOAD_SECONDS and now() < timestamp(ticket["expires_at"]),
                                 "ciphertext_download_expired")
                count += len(block); require(count <= asset["bytes"], "ciphertext_download_bound")
                require(stream.write(block) == len(block), "ciphertext_short_write"); hashed.update(block)
            stream.flush(); os.fsync(fd)
        require({"bytes": count, "sha256": hashed.hexdigest()} == {key: asset[key] for key in ("bytes", "sha256")},
                "ciphertext_exact_download")
    finally:
        os.close(fd)
    require(reuse.identity(target, MAX_CIPHERTEXT, guard) == {key: asset[key] for key in ("bytes", "sha256")},
            "ciphertext_physical_readback")
    reuse.new_json(output / "asset-download-terminal.original.json", {"status": "ONE_CIPHERTEXT_GET_RETAINED",
        "asset": asset, "elapsed_seconds": time.monotonic() - started, "automatic_retry": False})
    return target


def encrypt_capsule(source, target, recipient, current, guard):
    """Streaming implementation of the existing ci_private_evidence envelope."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    engine = seal_module(); public = engine.public_key(recipient); context(current)
    require(reuse.identity(source, reuse.CAPSULE["bytes"], guard) == reuse.CAPSULE, "host_original_capsule")
    secret, nonce = os.urandom(32), os.urandom(12)
    wrapped = public.encrypt(secret, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=engine.MAGIC))
    header = {"schema": engine.SEAL_SCHEMA, "algorithm": "RSA-OAEP-SHA256+AES-256-GCM",
        "recipient_spki_sha256": recipient["sha256"], "context": current, "nonce": base64.b64encode(nonce).decode(),
        "wrapped_key": base64.b64encode(wrapped).decode(), "plaintext_sha256": reuse.CAPSULE["sha256"],
        "plaintext_bytes": reuse.CAPSULE["bytes"]}
    aad = engine.json_bytes(header); encrypted = Cipher(algorithms.AES(secret), modes.GCM(nonce)).encryptor()
    encrypted.authenticate_additional_data(aad)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with reuse.stable_reader(source, reuse.CAPSULE["bytes"]) as (incoming, _), os.fdopen(fd, "wb", closefd=False) as output:
            output.write(engine.MAGIC + len(aad).to_bytes(4, "big") + aad)
            observed, hashed = 0, hashlib.sha256()
            while block := incoming.read(reuse.CHUNK):
                guard(); observed += len(block); hashed.update(block)
                value = encrypted.update(block); require(output.write(value) == len(value), "encrypted_short_write")
            require({"bytes": observed, "sha256": hashed.hexdigest()} == reuse.CAPSULE, "capsule_changed_during_encryption")
            output.write(encrypted.finalize()); output.write(encrypted.tag); output.flush(); os.fsync(fd)
    finally:
        os.close(fd)
    return reuse.identity(target, MAX_CIPHERTEXT, guard)


def decrypt_capsule(source, target, private_key, public, asset, guard):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    engine = seal_module()
    require(reuse.identity(source, MAX_CIPHERTEXT, guard) == {key: asset[key] for key in ("bytes", "sha256")}, "encrypted_capsule_identity")
    with reuse.stable_reader(source, MAX_CIPHERTEXT) as (incoming, size):
        require(incoming.read(len(MAGIC)) == MAGIC, "capsule_seal_magic")
        length = int.from_bytes(incoming.read(4), "big"); require(0 < length <= 8192, "capsule_header_size")
        aad = incoming.read(length); header = engine.strict(aad)
        require(set(header) == {"schema", "algorithm", "recipient_spki_sha256", "context", "nonce", "wrapped_key", "plaintext_sha256", "plaintext_bytes"}
                and engine.json_bytes(header) == aad and header["schema"] == engine.SEAL_SCHEMA
                and header["algorithm"] == "RSA-OAEP-SHA256+AES-256-GCM" and header["context"] == public["context"]
                and header["recipient_spki_sha256"] == public["recipient"]["sha256"]
                and {"bytes": header["plaintext_bytes"], "sha256": header["plaintext_sha256"]} == reuse.CAPSULE,
                "capsule_authenticated_header")
        der = private_key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        require(sha(der) == header["recipient_spki_sha256"], "ephemeral_key_changed")
        secret = private_key.decrypt(base64.b64decode(header["wrapped_key"], validate=True),
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=MAGIC))
        nonce = base64.b64decode(header["nonce"], validate=True)
        require(len(secret) == 32 and len(nonce) == 12 and size - len(MAGIC) - 4 - length - 16 == reuse.CAPSULE["bytes"], "capsule_ciphertext_length")
        start = incoming.tell(); incoming.seek(-16, os.SEEK_END); tag_bytes = incoming.read(16); incoming.seek(start)
        decoder = Cipher(algorithms.AES(secret), modes.GCM(nonce, tag_bytes)).decryptor(); decoder.authenticate_additional_data(aad)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        count, hashed, remaining = 0, hashlib.sha256(), reuse.CAPSULE["bytes"]
        try:
            with os.fdopen(fd, "wb", closefd=False) as output:
                while remaining:
                    guard(); block = incoming.read(min(reuse.CHUNK, remaining)); require(block, "ciphertext_truncated")
                    remaining -= len(block); decoded = decoder.update(block); count += len(decoded); hashed.update(decoded)
                    require(output.write(decoded) == len(decoded), "decrypted_short_write")
                tail = decoder.finalize(); require(not tail, "unexpected_GCM_tail")
                require({"bytes": count, "sha256": hashed.hexdigest()} == reuse.CAPSULE, "authenticated_capsule_identity")
                output.flush(); os.fsync(fd)
        finally:
            os.close(fd)
    require(reuse.identity(target, reuse.CAPSULE["bytes"], guard) == reuse.CAPSULE, "authenticated_capsule_readback")
    return target


def receive_inputs(root, public, token, guard):
    from cryptography.hazmat.primitives import serialization
    scopes = paths(root); destination = scopes["intake"]
    validate_recipient(public, public["context"], public["actor_id"], public["deadline_original_sha256"])
    destination.mkdir(mode=0o700, exist_ok=False)
    reuse.new_json(destination / "intake-intent.original.json", {"context": public["context"],
        "recipient_sha256": public["recipient"]["sha256"], "input_contract": input_contract(public["context"]),
        "maximum_poll_seconds": POLL_SECONDS, "maximum_ciphertext_gets": 1,
        "runner_github_permissions": {"contents": "read", "actions": "read", "statuses": "read"},
        "owner_authority": False, "automatic_retry": False})
    try:
        started = time.monotonic(); artifact = None
        prefix = "/repos/" + REPOSITORY
        while time.monotonic() - started < POLL_SECONDS - DOWNLOAD_SECONDS - 30:
            guard(); validate_recipient(public, public["context"], public["actor_id"], public["deadline_original_sha256"])
            raw = api_get(prefix + "/actions/artifacts?name=" + courier_name(public["context"], public["recipient"]["sha256"]) + "&per_page=100", token, 256 * 1024)
            value = strict(raw)
            require(type(value.get("artifacts")) is list and value.get("total_count") == len(value["artifacts"]) <= 1,
                    "unique_current_courier_artifact")
            if value["artifacts"]:
                item = value["artifacts"][0]
                run_raw = api_get(prefix + "/actions/runs/" + str(item["workflow_run"]["id"]), token, 128 * 1024)
                run = strict(run_raw)
                if run.get("status") == "completed":
                    artifact = checked_courier_artifact(item, public["context"], public, run)
                    reuse.new_bytes(destination / "courier-artifacts.original.json", raw)
                    reuse.new_bytes(destination / "courier-run.original.json", run_raw)
                    break
            time.sleep(min(2, max(0, POLL_SECONDS - DOWNLOAD_SECONDS - 30 - (time.monotonic() - started))))
        require(artifact is not None, "courier_not_delivered")
        reuse.new_json(destination / "courier-download-intent.original.json", {"artifact_id": artifact["id"], "automatic_retry": False})
        zipped = api_get(prefix + "/actions/artifacts/" + str(artifact["id"]) + "/zip", token, MAX_COURIER_ZIP, artifact_zip=True)
        reuse.new_bytes(destination / "courier.original.zip", zipped)
        ticket_envelope = read_courier_zip(zipped, artifact)
        key_path = scopes["private"] / "recipient.pkcs8.pem"; state = key_path.lstat()
        require(stat.S_IMODE(state.st_mode) == 0o600 and state.st_uid == os.geteuid(), "ephemeral_key_permissions")
        key = serialization.load_pem_private_key(reuse.read_small(key_path, 8192), password=None)
        ticket_raw, observed_context = seal_module().decrypt_snapshot(ticket_envelope, key)
        require(observed_context == public["context"] and len(ticket_raw) <= MAX_TICKET_PLAINTEXT, "ticket_authenticated_context")
        ticket = strict(ticket_raw); asset = validate_ticket(ticket, public)
        # The URL is a capability. Keep its original outside the public-fixture
        # producer tree; host PRIVATE retains the issued original as well. The
        # exact encrypted courier remains in the producer for custody.
        reuse.new_bytes(scopes["private"] / "read-ticket.original.json", ticket_raw)
        reuse.new_json(destination / "read-ticket-binding.original.json", {
            "context": public["context"], "ticket_sha256": sha(ticket_raw), "ticket_bytes": len(ticket_raw),
            "encrypted_ticket_sha256": sha(ticket_envelope), "url_sha256": ticket["url_sha256"],
            "url_expires_at": ticket["url_expires_at"], "asset": asset,
            "plaintext_capability_retained_only_in_private_runner_and_host": True,
            "github_token_forwarded": False, "owner_authority": False, "automatic_retry": False})
        encrypted = download_ciphertext_once(ticket, public, destination, guard)
        capsule = decrypt_capsule(encrypted, destination / "graph52-build-inputs.original.tar", key, public, asset, guard)
        reuse.new_json(destination / "intake-terminal.original.json", {"status": "AUTHENTICATED_ORIGINAL_BUILD_INPUTS",
            "context": public["context"], "origin": reuse.ORIGIN, "capsule": reuse.CAPSULE,
            "asset": asset, "courier_artifact_id": artifact["id"], "capsule_path": str(capsule),
            "recipient_sha256": public["recipient"]["sha256"], "new_builds": 0, "native_calls": 0,
            "owner_authority": False, "automatic_retry": False})
        return capsule
    except BaseException as error:
        try:
            reuse.new_json(destination / "intake-negative.original.json", {"status": "UNKNOWN_OR_REJECTED_NO_RETRY",
                "error_type": type(error).__name__, "owner_authority": False, "automatic_retry": False})
        except BaseException:
            pass
        raise
