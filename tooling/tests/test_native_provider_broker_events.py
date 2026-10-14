"""Source-safe, read-only Broker event replay for the v0.1 provider issue #49."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_provider_broker_events as probe  # noqa: E402

SECRET = "PRIVATE_OWNER_TOKEN_123"
SOURCE = "driver:motion-canvas"


def event(seq=1, *, kind="provider.connected", provider=SOURCE, generation=23,
          revision=24, source="semwright-core", payload=None, untrusted=False):
    return json.dumps({
        "sequence": seq,
        "event": {
            "kind": kind,
            "source": source,
            "provider": provider,
            "generation": generation,
            "revision": revision,
            "timestamp_ms": 1780000000000,
            "untrusted_payload": untrusted,
            "payload": payload,
            "attributes": {"token": SECRET},
        },
    }).encode() + b"\n"


class BrokerProviderEventTests(unittest.TestCase):
    def assert_private_never_exported(self, report):
        output = json.dumps(report)
        self.assertNotIn(SECRET, output)
        self.assertNotIn('"payload":', output)
        self.assertNotIn("timestamp_ms", output)
        self.assertNotIn("/tmp", output)
        self.assertFalse(report["original_render_retry_permitted"])
        self.assertFalse(report["terminal_native_result_verified"])
        self.assertEqual(report["provider_generation_root_cause"], "NOT_PROVEN")
        self.assertEqual(report["installed_owner_recovery"], "NOT_RUN")
        self.assertFalse(report["public_owner_payloads_copied"])

    def test_pinned_native_lifecycle_events_are_ordered_and_only_allowlisted(self):
        stream = (
            event(1, kind="provider.connected", payload={"ref": SECRET})
            + event(4, kind="provider.generation.invalidated")
            + event(7, kind="provider.disconnected")
        )
        result = probe.summarize_broker_watch_bytes(stream)
        self.assertEqual(result["schema"], probe.SCHEMA)
        self.assertEqual(result["observation"], "BROKER_LIFECYCLE_OBSERVED")
        self.assertEqual(result["trusted_lifecycle_event_count"], 3)
        self.assertEqual([row["kind"] for row in result["events"]],
                         ["provider.connected", "provider.generation.invalidated",
                          "provider.disconnected"])
        self.assertEqual(result["events"][0]["generation"], 23)
        self.assert_private_never_exported(result)

    def test_ignores_other_providers_and_non_lifecycle_telemetry(self):
        stream = (
            event(1, provider="driver:blender")
            + event(2, kind="job.progress")
            + event(3, source="untrusted-app")
            + event(4, kind="provider.connected")
        )
        result = probe.summarize_broker_watch_bytes(stream)
        self.assertEqual(result["trusted_lifecycle_event_count"], 1)
        self.assertEqual(result["events"][0]["kind"], "provider.connected")
        self.assert_private_never_exported(result)

    def test_an_absent_event_is_not_proof_provider_stayed_alive(self):
        result = probe.summarize_broker_watch_bytes(b"")
        self.assertEqual(result["observation"], "NO_TRUSTED_EVENT_REPLAY")
        self.assertFalse(result["terminal_native_result_verified"])
        self.assert_private_never_exported(result)

    def test_duplicate_sequence_refused_without_partial_verified_history(self):
        report = probe.summarize_broker_watch_bytes(event(4) + event(4, kind="provider.removed"))
        self.assertEqual(report["observation"], "UNVERIFIED_EVENT_ORDER")
        self.assertEqual(report["events"], [])
        self.assert_private_never_exported(report)

    def test_out_of_order_sequences_fail_closed(self):
        report = probe.summarize_broker_watch_bytes(event(8) + event(7))
        self.assertEqual(report["observation"], "UNVERIFIED_EVENT_ORDER")

    def test_forged_provider_source_does_not_inherit_identity(self):
        report = probe.summarize_broker_watch_bytes(event(1, provider="attacker"))
        self.assertEqual(report["observation"], "NO_TRUSTED_EVENT_REPLAY")
        self.assertEqual(report["trusted_lifecycle_event_count"], 0)

    def test_malformed_doubled_or_partial_json_refuses_untrusted_data(self):
        invalid = (
            b'{"sequence":1,"sequence":2,"event":{}}\n',
            b'{"sequence":1,"event":{not valid json}}\n',
            b'[1,2,3]\n',
            b'{"sequence":"1","event":{}}\n',
        )
        for blob in invalid:
            with self.subTest(blob=blob):
                report = probe.summarize_broker_watch_bytes(blob)
                self.assertTrue(report["observation"].startswith("UNVERIFIED"))
                self.assertEqual(report["trusted_lifecycle_event_count"], 0)
                self.assert_private_never_exported(report)

    def test_only_partial_unframed_broker_data_is_not_empty_history(self):
        result = probe.summarize_broker_watch_bytes(event(1)[:-1])
        self.assertEqual(result["observation"], "UNVERIFIED_PARTIAL_STREAM")
        self.assertEqual(result["events"], [])
        self.assert_private_never_exported(result)

    def test_truncated_unterminated_last_line_does_not_fabricate_event(self):
        report = probe.summarize_broker_watch_bytes(event(1) + event(2)[:-1])
        self.assertEqual(report["trusted_lifecycle_event_count"], 1)
        self.assertEqual(report["events"][0]["kind"], "provider.connected")

    def test_wrong_generation_revision_or_timestamp_refused(self):
        for invalid in (
            {"generation": -1}, {"generation": True},
            {"revision": "24"}, {"revision": None},
        ):
            with self.subTest(case=invalid):
                report = probe.summarize_broker_watch_bytes(event(1, **invalid))
                self.assertEqual(report["observation"], "UNVERIFIED_PROVIDER_PROVENANCE")
        forged = json.loads(event(1))
        forged["event"]["timestamp_ms"] = None
        report = probe.summarize_broker_watch_bytes(
            json.dumps(forged).encode() + b"\n"
        )
        self.assertEqual(report["observation"], "UNVERIFIED_PROVIDER_PROVENANCE")

    def test_untrusted_lifecycle_payload_is_not_treated_as_authoritative(self):
        report = probe.summarize_broker_watch_bytes(
            event(1, untrusted=True, payload={"code": SECRET})
        )
        self.assertEqual(report["observation"], "UNVERIFIED_PROVIDER_PROVENANCE")
        self.assert_private_never_exported(report)
        forged = json.loads(event(1))
        forged["event"]["untrusted_payload"] = 1
        result = probe.summarize_broker_watch_bytes(
            json.dumps(forged).encode() + b"\n"
        )
        self.assertEqual(result["observation"], "UNVERIFIED_PROVIDER_PROVENANCE")

    def test_oversize_stream_or_line_refused(self):
        self.assertEqual(
            probe.summarize_broker_watch_bytes(b"x" * (probe.MAX_CAPTURE_BYTES + 1))
            ["observation"],
            "UNVERIFIED_BOUNDED_STREAM",
        )
        forged = json.loads(event(1))
        forged["event"]["payload"] = "x" * 20000
        self.assertEqual(
            probe.summarize_broker_watch_bytes(json.dumps(forged).encode() + b"\n")
            ["observation"],
            "UNVERIFIED_BOUNDED_STREAM",
        )

    def test_exact_one_read_only_owner_watch_subscription(self):
        with patch.object(probe.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(
                [], 0, event(1, kind="provider.connected"),
            )
            result = probe.read_owner_broker_event_history(
                Path("/trusted/semwright"),
                Path("/owner/broker.sock"),
                Path("/owner/client.session"),
                env={"CI": "true"},
            )
        self.assertEqual(result["observation"], "BROKER_LIFECYCLE_OBSERVED")
        self.assertEqual(run.call_count, 1)
        args = run.call_args.args[0]
        self.assertEqual(args[-3:], ["watch", "--after", "0"])
        self.assertIn("--session-file", args)
        self.assertNotIn("execute", args)
        self.assertNotIn("render.start", args)
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["timeout"], probe.WATCH_SECONDS)
        self.assert_private_never_exported(result)

    def test_persistent_watch_timeout_admits_only_typed_replay(self):
        with patch.object(probe.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired(
                              "watch", probe.WATCH_SECONDS,
                              output=event(1, kind="provider.disconnected"),
                              stderr=SECRET.encode(),
                          )):
            result = probe.read_owner_broker_event_history(
                Path("/trusted/semwright"),
                Path("/owner/broker.sock"),
                Path("/owner/client.session"), env={},
            )
        self.assertEqual(result["observation"], "BROKER_LIFECYCLE_OBSERVED")
        self.assertEqual(result["events"][0]["kind"], "provider.disconnected")
        self.assert_private_never_exported(result)

    def test_unavailable_stream_does_not_upgrade_native_job(self):
        with patch.object(probe.subprocess, "run",
                          side_effect=OSError(SECRET)):
            result = probe.read_owner_broker_event_history(
                Path("/trusted/semwright"), Path("/private.sock"),
                Path("/owner.session"), env={},
            )
        self.assertEqual(result["observation"], "BROKER_WATCH_UNAVAILABLE")
        self.assert_private_never_exported(result)
        with patch.object(probe.subprocess, "run") as run:
            result = probe.read_owner_broker_event_history(
                Path("/trusted/semwright"), Path("/private.sock"),
                Path("/owner.session"), env={}, deadline_seconds=99,
            )
        self.assertEqual(result["observation"], "UNVERIFIED_WATCH_DEADLINE")
        run.assert_not_called()

    def test_failed_watch_exit_is_never_a_verified_provider_event(self):
        with patch.object(probe.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 3, event(1))
            result = probe.read_owner_broker_event_history(
                Path("/trusted/semwright"), Path("/private.sock"),
                Path("/owner.session"), env={},
            )
        self.assertEqual(result["observation"], "BROKER_WATCH_UNAVAILABLE")
        self.assertEqual(result["events"], [])

    def test_baseline_ci_only_opt_in_and_no_mutating_retry_contract(self):
        worktree = Path(__file__).resolve().parents[2]
        yml = (worktree / ".github/workflows/native-av-master-e2e.yml").read_text()
        runner = (worktree / "tooling/native_av_master_e2e.py").read_text()
        self.assertIn("MOTIONWRIGHT_V01_PROVIDER_EVENT_REPLAY: '1'", yml)
        self.assertIn("test_native_provider_broker_events.py", yml)
        self.assertIn("native-provider-broker-events.json", runner)
        self.assertIn('FIXTURE == "baseline"', runner)
        self.assertNotIn("driver.motion-canvas.render.start", (worktree
            / "tooling/native_provider_broker_events.py").read_text())

    def test_startup_failure_never_echoes_original_daemon_log(self):
        from unittest.mock import Mock
        import native_av_master_e2e as av
        process = Mock()
        process.poll.return_value = 7
        socket = Mock()
        private_log = Mock()
        private_log.read_text.side_effect = AssertionError(SECRET)
        with self.assertRaises(AssertionError) as raised:
            av.wait_for_socket(process, socket, private_log)
        self.assertNotIn(SECRET, str(raised.exception))
        private_log.read_text.assert_not_called()

    def test_empty_or_other_provider_event_is_unconfirmed(self):
        with patch.object(probe.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(
                [], 0, event(1, provider="driver:mlt-video")
            )
            result = probe.read_owner_broker_event_history(
                Path("/trusted/semwright"), Path("/owner.sock"),
                Path("/owner.session"), env={},
            )
        self.assertEqual(result["observation"], "NO_TRUSTED_EVENT_REPLAY")


if __name__ == "__main__":
    unittest.main()
