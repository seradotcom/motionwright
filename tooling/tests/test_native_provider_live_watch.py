"""Adversarial v0.1 read-only live Broker diagnostics. No native mutations."""
from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_provider_live_watch as live  # noqa: E402

SECRET = "owner-private-session-token-never-export"
WATCH = ["broker", "--socket", "private.sock", "--session-file", "owner.session",
         "--json", "watch", "--after", "0"]


def typed_event(seq=1, *, kind="provider.connected", provider="driver:motion-canvas"):
    return (json.dumps({
        "sequence": seq,
        "event": {
            "source": "semwright-core",
            "provider": provider,
            "kind": kind,
            "generation": 12,
            "revision": 1,
            "timestamp_ms": 1780000000000,
            "untrusted_payload": False,
            "payload": {"owner": SECRET, "private_path": "/never/publish"},
        },
    }) + "\n").encode()


class FakeWatchProcess:
    def __init__(self, data=b"", *, early=False, fail_wait_once=False,
                 fail_read=False):
        self.stdout = io.BytesIO(data) if not fail_read else FailingPipe()
        self.early = early
        self.running = not early
        self.terminated = 0
        self.killed = 0
        self.fail_wait_once = fail_wait_once
        self.waits = 0

    def poll(self):
        return None if self.running else 0

    def terminate(self):
        self.terminated += 1
        self.running = False

    def kill(self):
        self.killed += 1
        self.running = False

    def wait(self, timeout=None):
        self.waits += 1
        if self.fail_wait_once and self.waits == 1:
            raise subprocess.TimeoutExpired("watch", timeout)
        self.running = False
        return 0


class FailingPipe:
    def read(self, count):
        raise OSError(SECRET)

    def close(self):
        pass


class BoundedLiveProviderTests(unittest.TestCase):
    def watcher(self):
        return live.PassiveLiveBrokerWatch(
            Path("broker"), Path("private.sock"), Path("owner.session"), env={"CI": "true"}
        )

    def test_one_read_only_subscription_and_bounded_positive_events(self):
        proc = FakeWatchProcess(typed_event(1) + typed_event(2, kind="provider.disconnected"))
        with patch.object(live.subprocess, "Popen", return_value=proc) as spawn:
            watch = self.watcher()
            self.assertTrue(watch.start())
            result = watch.finish()
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(spawn.call_args.args[0], WATCH)
        self.assertEqual(spawn.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(spawn.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(spawn.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(result["observation"], "BROKER_LIFECYCLE_OBSERVED")
        self.assertEqual(result["trusted_lifecycle_event_count"], 2)
        self.assertTrue(result["started_before_render"])
        self.assertTrue(result["fully_framed_observed_bytes"])
        self.assertFalse(result["history_exhaustive"])
        self.assertFalse(result["subscriber_exited_before_finalize"])
        self.assertFalse(result["native_provider_causation_proven"])
        self.assertEqual(proc.terminated, 1)
        self.assertNotIn(SECRET, str(result))
        self.assertFalse(result["original_render_retry_permitted"])
        self.assertFalse(result["terminal_native_result_verified"])

    def test_early_broker_disappearance_preserves_typed_positive_event(self):
        proc = FakeWatchProcess(typed_event(1, kind="provider.generation.invalidated"),
                                early=True)
        with patch.object(live.subprocess, "Popen", return_value=proc):
            watch = self.watcher()
            watch.start()
            result = watch.finish()
        self.assertEqual(result["observation"], "BROKER_LIFECYCLE_OBSERVED")
        self.assertTrue(result["subscriber_exited_before_finalize"])
        self.assertEqual(result["events"][0]["kind"], "provider.generation.invalidated")
        self.assertFalse(result["terminal_native_result_verified"])

    def test_early_exit_without_events_never_implies_healthy_provider(self):
        with patch.object(live.subprocess, "Popen",
                          return_value=FakeWatchProcess(early=True)):
            watch = self.watcher()
            watch.start()
            result = watch.finish()
        self.assertEqual(result["observation"], "UNVERIFIED_LIVE_SUBSCRIBER_EARLY_EXIT")
        self.assertEqual(result["events"], [])
        self.assertFalse(result["history_exhaustive"])

    def test_large_stream_fails_closed_without_logging_any_content(self):
        with patch.object(live, "MAX_CAPTURE_BYTES", 40), \
             patch.object(live.subprocess, "Popen",
                          return_value=FakeWatchProcess(typed_event())):
            watch = self.watcher()
            watch.start()
            result = watch.finish()
        self.assertEqual(result["observation"], "UNVERIFIED_BOUNDED_LIVE_STREAM")
        self.assertFalse(result["capture_within_byte_limit"])
        self.assertNotIn(SECRET, str(result))
        self.assertEqual(result["events"], [])

    def test_incomplete_last_line_must_never_prove_exhaustive_history(self):
        with patch.object(live.subprocess, "Popen",
                          return_value=FakeWatchProcess(typed_event() + b'{"sequence":2')):
            watch = self.watcher()
            watch.start()
            result = watch.finish()
        self.assertFalse(result["fully_framed_observed_bytes"])
        self.assertFalse(result["history_exhaustive"])
        self.assertEqual(result["trusted_lifecycle_event_count"], 1)
        self.assertNotIn("sequence", str(result))

    def test_failed_start_is_an_unverified_observation(self):
        with patch.object(live.subprocess, "Popen",
                          side_effect=FileNotFoundError(SECRET)):
            watch = self.watcher()
            self.assertFalse(watch.start())
            result = watch.finish()
        self.assertEqual(result["observation"], "UNVERIFIED_LIVE_WATCH_NOT_STARTED")
        self.assertFalse(result["started_before_render"])
        self.assertNotIn(SECRET, str(result))

    def test_reader_error_is_redacted_and_does_not_verify_native_result(self):
        with patch.object(live.subprocess, "Popen",
                          return_value=FakeWatchProcess(fail_read=True)):
            watch = self.watcher()
            watch.start()
            result = watch.finish()
        self.assertEqual(result["observation"], "UNVERIFIED_LIVE_WATCH_READ_OR_STOP")
        self.assertFalse(result["terminal_native_result_verified"])
        self.assertNotIn(SECRET, str(result))

    def test_timeout_stops_only_subscriber_without_mutating_provider(self):
        proc = FakeWatchProcess(typed_event(), fail_wait_once=True)
        with patch.object(live.subprocess, "Popen", return_value=proc):
            watch = self.watcher()
            watch.start()
            result = watch.finish()
        self.assertEqual(proc.terminated, 1)
        self.assertEqual(proc.killed, 1)
        self.assertEqual(result["trusted_lifecycle_event_count"], 1)
        self.assertFalse(result["original_render_retry_permitted"])

    def test_duplicate_start_or_finish_not_permitted(self):
        watch = self.watcher()
        with patch.object(live.subprocess, "Popen",
                          return_value=FakeWatchProcess()):
            self.assertTrue(watch.start())
            with self.assertRaises(RuntimeError):
                watch.start()
            watch.finish()
            with self.assertRaises(RuntimeError):
                watch.finish()

    def test_non_lifecycle_or_other_provider_cannot_be_admitted(self):
        with patch.object(live.subprocess, "Popen", return_value=FakeWatchProcess(
            typed_event(1, provider="driver:blender") +
            typed_event(2, kind="job.progress"),
        )):
            watch = self.watcher()
            watch.start()
            result = watch.finish()
        self.assertEqual(result["trusted_lifecycle_event_count"], 0)
        self.assertFalse(result["history_exhaustive"])

    def test_public_log_aggregates_are_closed_enum_and_redacted(self):
        private = live.summarize_broker_watch_bytes(
            typed_event(1) + typed_event(2, kind="provider.generation.invalidated")
        )
        counts = live.public_lifecycle_kind_counts(private)
        self.assertEqual(set(counts), set(live.ALLOWED_EVENTS))
        self.assertEqual(counts["provider.connected"], 1)
        self.assertEqual(counts["provider.generation.invalidated"], 1)
        self.assertEqual(sum(counts.values()), 2)
        self.assertNotIn(SECRET, str(counts))
        private["events"][0]["kind"] = SECRET
        with self.assertRaises(ValueError):
            live.public_lifecycle_kind_counts(private)
        private["events"][0]["kind"] = "provider.connected"
        private["trusted_lifecycle_event_count"] = 200
        with self.assertRaises(ValueError):
            live.public_lifecycle_kind_counts(private)

    def test_native_render_lane_observes_broker_during_original_job(self):
        root = Path(__file__).resolve().parents[2]
        runner = (root / "tooling/native_render_e2e.py").read_text()
        workflow = (root / ".github/workflows/native-render-e2e.yml").read_text()
        from native_render_public_artifact import FILES
        self.assertIn("PassiveLiveBrokerWatch", runner)
        self.assertIn("public_lifecycle_kind_counts(live_events)", runner)
        self.assertIn("live_broker_watch.start()", runner)
        self.assertIn("live_broker_watch.finish()", runner)
        self.assertIn("native-provider-live-broker-events.json", runner)
        replay_key = "MOTIONWRIGHT_V01_PROVIDER_EVENT_REPLAY:"
        self.assertEqual(workflow.count(replay_key), 2)

        def workflow_step(name):
            marker = "      - name: " + name + "\n"
            self.assertEqual(workflow.count(marker), 1)
            return workflow.split(marker, 1)[1].split("\n      - ", 1)[0]

        original_step = workflow_step("Render application-owned revision through real Broker and Driver Host")
        repeat_step = workflow_step("Repeat native render with a fresh owner Broker and Driver Host (v0.1 diagnostic only)")
        # The ordinary render keeps replay enabled; the finite N83 selection
        # disables it and cannot enter the separate repeat step.
        self.assertEqual(
            [line.strip() for line in original_step.splitlines() if replay_key in line],
            [replay_key + " ${{ (inputs.native_scope == 'n83-preview' || inputs.native_scope == 'editable-source-cycle') && '0' || '1' }}"],
        )
        self.assertEqual(
            [line.strip() for line in repeat_step.splitlines() if replay_key in line],
            [replay_key + " '1'"],
        )
        self.assertEqual(
            [line.strip() for line in repeat_step.splitlines() if line.strip().startswith("if:")],
            ["if: ${{ inputs.native_scope != 'n83-preview' && inputs.native_scope != 'editable-source-cycle' && "
             "((github.event_name == 'workflow_dispatch' && inputs.v01_provider_repeat) || "
             "(github.event_name == 'pull_request' && startsWith(github.head_ref, "
             "'feat/v01-provider-repeat-ci'))) }}"],
        )
        self.assertIn("test_native_provider_live_watch.py", workflow)
        self.assertIn(
            'test -s "verification/native-render-e2e/${GITHUB_SHA}/native-provider-live-broker-events.json"',
            workflow,
        )
        self.assertIn('native_provider_causation_proven"] is False', workflow)
        self.assertIn('original_render_retry_permitted"] is False', workflow)
        self.assertNotIn("native-provider-live-broker-events.json", FILES)
        self.assertNotIn('native-render-e2e/${GITHUB_SHA}/native-provider-live-broker-events.json', (
            root / ".github/workflows/native-av-master-e2e.yml"
        ).read_text())

    def test_ci_wiring_is_baseline_only_no_native_retry(self):
        root = Path(__file__).resolve().parents[2]
        runner = (root / "tooling/native_av_master_e2e.py").read_text()
        workflow = (root / ".github/workflows/native-av-master-e2e.yml").read_text()
        self.assertIn('if FIXTURE == "baseline"', runner)
        self.assertIn("PassiveLiveBrokerWatch", runner)
        self.assertIn("public_lifecycle_kind_counts(live_events)", runner)
        self.assertIn('live_broker_watch.start()', runner)
        self.assertIn("native-provider-live-broker-events.json", runner)
        self.assertIn("test_native_provider_live_watch.py", workflow)
        self.assertIn('test -s "$root/native-provider-live-broker-events.json"', workflow)
        self.assertIn("path: verification/native-av-master-public", workflow)
        from native_av_public_artifact import ALLOWED_MEDIA
        self.assertNotIn("native-provider-live-broker-events.json", ALLOWED_MEDIA)
        self.assertNotIn("render.start", (root / "tooling/native_provider_live_watch.py").read_text()
                         .replace("native render.start", ""))


if __name__ == "__main__":
    unittest.main()
