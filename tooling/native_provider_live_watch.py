#!/usr/bin/env python3
"""Passive bounded live Broker watch for the original v0.1 provider-loss issue.

The post-render replay may be unavailable if the Broker exits while rendering.
Start one read-only owner-session watch before render, retain at most 256 KiB
inside this disposable CI process, and stop it before intentional teardown.
Raw stream bytes, CLI paths, session tickets and native refs are never exported.
This does NOT resolve a job, restart a provider or establish upstream causation.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import threading
from typing import Any

from native_provider_broker_events import (
    MAX_CAPTURE_BYTES, _report, summarize_broker_watch_bytes,
)


class PassiveLiveBrokerWatch:
    """One optional CI-only, read-only CLI subscription with bounded capture."""

    def __init__(self, cli: Path, socket: Path, session: Path,
                 *, env: dict[str, str]) -> None:
        self.argv = [
            str(cli), "--socket", str(socket), "--session-file",
            str(session), "--json", "watch", "--after", "0",
        ]
        self.env = env
        self.process: subprocess.Popen[bytes] | None = None
        self.worker: threading.Thread | None = None
        self._captured = bytearray()
        self._overflow = False
        self._read_failed = False
        self._started = False
        self._finished = False

    def start(self) -> bool:
        if self._started:
            raise RuntimeError("Broker live observer started twice")
        self._started = True
        try:
            self.process = subprocess.Popen(
                self.argv, env=self.env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            if self.process.stdout is None:
                raise OSError("No watch stdout")
        except (OSError, ValueError):
            self.process = None
            return False
        self.worker = threading.Thread(
            target=self._reader, name="v01-read-only-broker-watch", daemon=True
        )
        self.worker.start()
        return True

    def _reader(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while True:
                data = self.process.stdout.read(8192)
                if not data:
                    return
                if len(self._captured) + len(data) > MAX_CAPTURE_BYTES:
                    self._overflow = True
                    return
                self._captured.extend(data)
        except (OSError, ValueError):
            self._read_failed = True

    def finish(self) -> dict[str, Any]:
        if self._finished:
            raise RuntimeError("Broker live observer finalized twice")
        self._finished = True
        proc = self.process
        early_exit = proc is not None and proc.poll() is not None
        shutdown_verified = True
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()  # only this read-only CLI subscriber
                proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                    proc.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    shutdown_verified = False
            if self.worker is not None:
                self.worker.join(timeout=3)
            if self.worker is None or self.worker.is_alive():
                shutdown_verified = False
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
            except OSError:
                shutdown_verified = False

        if proc is None:
            result = _report("UNVERIFIED_LIVE_WATCH_NOT_STARTED")
        elif self._overflow:
            result = _report("UNVERIFIED_BOUNDED_LIVE_STREAM")
        elif self._read_failed or not shutdown_verified:
            result = _report("UNVERIFIED_LIVE_WATCH_READ_OR_STOP")
        else:
            result = summarize_broker_watch_bytes(bytes(self._captured))
            if early_exit and not result["events"]:
                result = _report("UNVERIFIED_LIVE_SUBSCRIBER_EARLY_EXIT")
        # These are observations of a finite window, NOT a complete historical
        # event log or verified native result. No private process data is copied.
        result.update({
            "capture_mode": "bounded-read-only-owner-session-live-watch",
            "started_before_render": proc is not None,
            "subscriber_exited_before_finalize": bool(early_exit),
            "fully_framed_observed_bytes": (
                bool(self._captured) and self._captured.endswith(b"\n")
                and not self._overflow and not self._read_failed
                and shutdown_verified
            ),
            "history_exhaustive": False,
            "capture_within_byte_limit": not self._overflow,
            "native_provider_causation_proven": False,
        })
        return result
