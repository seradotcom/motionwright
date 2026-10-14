#!/usr/bin/env python3
"""Optional v0.1 read-only replay of typed Semwright Broker provider events.

The pinned Semwright CLI supports watch --after 0: its subscribe frame is a
read-only stream of session-filtered historical events. Never copy untrusted
event payloads, session tickets, provider output or owner filesystem paths.
A lifecycle event can narrow an incident but cannot prove what killed a
specific original render, let alone authorize a retry.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

SCHEMA = "motionwright-ci-native-provider-broker-events/1"
ALLOWED_EVENTS = frozenset({
    "provider.connected",
    "provider.disconnected",
    "provider.capabilities.changed",
    "provider.generation.invalidated",
    "provider.removed",
})
SOURCE = "semwright-core"
PROVIDER = "driver:motion-canvas"
MAX_CAPTURE_BYTES = 256_000
MAX_EVENT_LINE_BYTES = 16_384
MAX_STREAM_EVENTS = 256
WATCH_SECONDS = 2.5


def _report(observation: str, events: list[dict] | None = None) -> dict[str, Any]:
    events = events if events is not None else []
    return {
        "schema": SCHEMA,
        "observation": observation,
        "events": events,
        "trusted_lifecycle_event_count": len(events),
        "read_only_broker_replay": True,
        "original_render_retry_permitted": False,
        "terminal_native_result_verified": False,
        "provider_generation_root_cause": "NOT_PROVEN",
        "installed_owner_recovery": "NOT_RUN",
        "public_owner_payloads_copied": False,
    }


def _strict_object(pairs: list[tuple[str, object]]) -> dict:
    obj: dict = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("Duplicate native event JSON authority field")
        obj[key] = value
    return obj


def summarize_broker_watch_bytes(blob: object) -> dict[str, Any]:
    """Fail closed for malformed/oversized streams, without exposing content."""
    if not isinstance(blob, bytes) or len(blob) > MAX_CAPTURE_BYTES:
        return _report("UNVERIFIED_BOUNDED_STREAM")
    if not blob:
        return _report("NO_TRUSTED_EVENT_REPLAY")
    events: list[dict] = []
    lines = blob.split(b"\n")
    if len(lines) > MAX_STREAM_EVENTS + 1:
        return _report("UNVERIFIED_BOUNDED_STREAM")
    if len(lines) == 1:
        # A non-empty stream without even one complete framed JSON line
        # cannot establish that provider lifecycle events were absent.
        return _report("UNVERIFIED_PARTIAL_STREAM")
    previous_sequence = 0
    for line in lines[:-1]:
        if not line:
            continue
        if len(line) > MAX_EVENT_LINE_BYTES:
            return _report("UNVERIFIED_BOUNDED_STREAM")
        try:
            row = json.loads(line.decode("utf-8"), object_pairs_hook=_strict_object)
        except (ValueError, UnicodeError):
            return _report("UNVERIFIED_MALFORMED_EVENT")
        if not isinstance(row, dict):
            return _report("UNVERIFIED_MALFORMED_EVENT")
        sequence = row.get("sequence")
        event = row.get("event")
        if type(sequence) is not int or sequence <= previous_sequence or not isinstance(event, dict):
            return _report("UNVERIFIED_EVENT_ORDER")
        previous_sequence = sequence
        if event.get("source") != SOURCE or event.get("provider") != PROVIDER:
            # The native Broker also emits ordinary desktop/job events.
            # They must never be admitted as Motion Canvas lifetime facts.
            continue
        kind = event.get("kind")
        if kind not in ALLOWED_EVENTS:
            continue
        generation = event.get("generation")
        revision = event.get("revision")
        timestamp_ms = event.get("timestamp_ms")
        if (type(generation) is not int or generation < 0
            or type(revision) is not int or revision < 0
            or type(timestamp_ms) is not int or timestamp_ms <= 0
            or type(event.get("untrusted_payload", False)) is not bool
            or event.get("untrusted_payload") is True):
            return _report("UNVERIFIED_PROVIDER_PROVENANCE")
        # Copy only allowlisted provenance, never arbitrary attributes, ref,
        # untrusted event payload, original job ID or wall-clock timestamp.
        events.append({
            "kind": kind, "generation": generation, "revision": revision,
        })
    return _report(
        "BROKER_LIFECYCLE_OBSERVED" if events else "NO_TRUSTED_EVENT_REPLAY", events
    )


def read_owner_broker_event_history(
    cli: Path, socket: Path, session_file: Path, *, env: dict[str, str],
    deadline_seconds: float = WATCH_SECONDS,
) -> dict[str, Any]:
    """One bounded read-only subscription, using the existing owner session.

    The watch command is intentionally persistent; hitting the fixed timeout
    after replay is expected. subprocess.run kills the subscriber on timeout.
    No connection reset, provider restart, status replay or effect is issued.
    """
    if deadline_seconds <= 0 or deadline_seconds > WATCH_SECONDS:
        return _report("UNVERIFIED_WATCH_DEADLINE")
    argv = [
        str(cli), "--socket", str(socket), "--session-file",
        str(session_file), "--json", "watch", "--after", "0",
    ]
    try:
        result = subprocess.run(
            argv,
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=deadline_seconds,
        )
        if result.returncode != 0:
            return _report("BROKER_WATCH_UNAVAILABLE")
        output = result.stdout
    except subprocess.TimeoutExpired as error:
        # A persistent subscription normally times out. Python already
        # terminated the CLI and exposes only its bounded partial stdout.
        output = error.stdout
    except (OSError, subprocess.SubprocessError):
        return _report("BROKER_WATCH_UNAVAILABLE")
    try:
        return summarize_broker_watch_bytes(output)
    except (OSError, TypeError, ValueError):
        return _report("UNVERIFIED_BROKER_EVENT_REPLAY")
