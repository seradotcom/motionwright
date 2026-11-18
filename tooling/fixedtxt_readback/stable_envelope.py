"""Stable retained-ciphertext I/O for the existing bounded authenticator.

Root and ACL authorization, source pins, key custody and memory admission remain
with the private caller. The existing decrypt_stream owns every cryptographic
predicate and allocation. No archive is copied, written, retried or parsed here.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from stable_file_stream import open_stable

POLICY = "motionwright.fixedtxt-stable-envelope/1"
CHUNK = 65536
_REFERENCE_KEYS = {
    "path", "bytes", "sha256", "namespace_identity", "retained_external_original",
    "copied", "retention_dependency",
}


class StableEnvelopeRejected(ValueError):
    pass


def need(value, code):
    if not value:
        raise StableEnvelopeRejected(code)


def envelope_reference(source, expected_sha256, *, maximum):
    """Hash one retained original with bounded reads from a held descriptor."""
    need(type(expected_sha256) is str
         and re.fullmatch(r"[0-9a-f]{64}", expected_sha256),
         "envelope_digest_shape")
    source = Path(source)
    digest, total = hashlib.sha256(), 0
    with open_stable(source, maximum) as held:
        need(held.namespace_identity[4] > 0, "envelope_budget")
        for block in iter(lambda: held.stream.read(CHUNK), b""):
            total += len(block)
            need(total <= maximum, "envelope_budget")
            digest.update(block)
        need(total == held.namespace_identity[4], "envelope_read_size")
        namespace = list(held.namespace_identity)
    need(digest.hexdigest() == expected_sha256, "envelope_digest_differs")
    return {
        "path": str(source), "bytes": total, "sha256": digest.hexdigest(),
        "namespace_identity": namespace, "retained_external_original": True,
        "copied": False, "retention_dependency": "keep_this_complete_ciphertext",
    }


def decrypt_retained(path, expected_reference, header, private_key, seal, *,
                     bounded, maximum):
    """Return authenticated memory only after the held file also passes close.

    A namespace/descriptor failure after authentication still wipes the snapshot.
    The qualified stable-stream policy compares unchanged regular-file identity,
    allows only the Windows lstat/fstat ctime distinction, and keeps ancestor
    dev/inode/type/attributes without comparing directory-content metadata.
    """
    path = Path(path)
    reference = expected_reference
    need(type(reference) is dict and set(reference) == _REFERENCE_KEYS
         and reference["path"] == str(path)
         and type(reference["bytes"]) is int and 0 < reference["bytes"] <= maximum
         and type(reference["sha256"]) is str
         and re.fullmatch(r"[0-9a-f]{64}", reference["sha256"])
         and type(reference["namespace_identity"]) is list
         and len(reference["namespace_identity"]) == 8
         and all(type(value) is int for value in reference["namespace_identity"])
         and reference["namespace_identity"][4] == reference["bytes"]
         and reference["retained_external_original"] is True
         and reference["copied"] is False
         and reference["retention_dependency"] == "keep_this_complete_ciphertext",
         "stream_reference_binding")
    snapshot = None
    try:
        with open_stable(path, maximum) as held:
            need(list(held.namespace_identity) == reference["namespace_identity"],
                 "stream_namespace_changed")
            snapshot = bounded.decrypt_stream(
                held.stream, reference["bytes"], reference["sha256"], header,
                private_key, seal, reserve_guard=bounded.require_reserve)
        result, snapshot = snapshot, None
        return result
    finally:
        if snapshot is not None:
            snapshot.close()
