"""Original-consumer publication regressions; native effects remain test doubles."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_mlt_g2_observation as g2
from tooling.tests import test_native_mlt_host_tool_observation as fixtures


class OriginalConsumerPublicationTests(unittest.TestCase):
    def test_private_selection_preserves_the_exact_layout_rejections(self):
        cwd = "/tmp/semwright-video-" + "a"*32
        output = cwd + "/partial.mp4"
        def argv(path):
            return ("/plugin/tools/melt\0timeline.mlt\0-consumer\0avformat:" + path + "\0").encode()
        self.assertEqual(g2.host_tool_consumer(argv(output), cwd), output)
        for path in (
            "/tmp/unrelated/partial.mp4",
            "/tmp/semwright-video-" + "a"*31 + "/partial.mp4",
            "/tmp/semwright-video-" + "A"*32 + "/partial.mp4",
            cwd + "/child/partial.mp4",
        ):
            with self.subTest(path=path), self.assertRaises(g2.G2Unverified):
                g2.host_tool_consumer(argv(path), cwd)
        self.assertIsNone(g2.host_tool_consumer(argv(cwd + "/partial.wav"), cwd))

    def test_publication_binding_must_equal_the_original_ack_before_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            raw = b"original"
            expected = g2.sha(raw)
            for index,change in enumerate((
                {"published_output_sha256": g2.sha(b"replaced")},
                {"published_output_bytes": len(raw)+1},
                {"published_output_bytes": str(len(raw))},
            )):
                with self.subTest(change=change):
                    directory = root/str(index)
                    directory.mkdir()
                    master = directory/"master.mp4"
                    master.write_bytes(raw)
                    evidence = directory/"evidence"
                    evidence.mkdir()
                    (evidence/"original-native-master.mp4").write_bytes(raw)
                    session = g2.G2Session({}, evidence, b"", {"test_double": True})
                    session.native_result = {"native_mlt_multisegment_e2e": "PASS",
                                             "master_artifact_sha256": expected,
                                             "master_artifact_bytes": len(raw)}
                    session.master_sha = expected
                    session.observer = mock.Mock()
                    session.observer.require_one.return_value = {
                        "publication_binding": "SAME_ORIGINAL_OPEN_DESCRIPTOR_BYTES",
                        "published_output_sha256": expected, "published_output_bytes": len(raw),
                        **change,
                    }
                    session.retain_master(master, expected, len(raw))
                    self.assertIsNone(session.live_proof)
                    self.assertIn("original_live_module_witness", session.secondary_failures)
                    self.assertFalse((evidence/"original-live-module.json").exists())
                    self.assertTrue((evidence/"original-live-module-error.json").is_file())
                    session.observer.require_one.assert_called_once_with(master)

    def test_real_same_inode_append_during_descriptor_read_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"partial.mp4"
            original = b"original"
            path.write_bytes(original)
            descriptor = os.open(path, os.O_RDONLY|getattr(os,"O_BINARY",0))
            opened = os.fstat(descriptor)
            real_read = os.read
            changed = False
            def mutate(fd,limit):
                nonlocal changed
                block = real_read(fd,limit)
                if not changed:
                    changed = True
                    with path.open("ab") as writer:
                        writer.write(b"changed")
                return block
            try:
                with mock.patch.object(g2.os,"read",side_effect=mutate):
                    with self.assertRaisesRegex(g2.G2Unverified,"changed_during_read"):
                        g2.read_held_output(descriptor,opened)
            finally:
                os.close(descriptor)

    def test_pid_generation_change_after_capture_closes_the_original_descriptor(self):
        fixture = fixtures.NamespaceObservationTests("runTest")
        fixture.setUp()
        try:
            def change(*_args,**_kwargs):
                fixture.raw["41/stat"] = fixtures.proc_stat(41,20,301)
                return {"test_double": True}
            fixture.attest.side_effect = change
            with self.assertRaisesRegex(g2.G2Unverified,"process_context_changed"):
                fixture.observe()
            self.assertEqual(len(fixture.opened_fds),1)
            with self.assertRaises(OSError):
                fixture.original_fstat(fixture.opened_fds[0])
        finally:
            fixture.doCleanups()

    def test_original_descriptor_bytes_only_bind_a_publication_below_the_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root/"partial.mp4"
            source.write_bytes(b"source-only publication fixture")
            output = root/"output"
            output.mkdir()
            master = output/"master.mp4"
            master.write_bytes(source.read_bytes())
            evidence = root/"evidence"
            evidence.mkdir()
            contract = {"output_path":str(output)}
            descriptor = os.open(source,os.O_RDONLY|getattr(os,"O_BINARY",0))
            held = g2.NamespaceMaster((41,10),descriptor,os.fstat(descriptor),{
                "output_path":"/tmp/semwright-video-"+"a"*32+"/partial.mp4",
                "owner_contract":contract,"live_module":{"test_double":True},
            })
            try:
                held.retain(evidence)
                with mock.patch.object(g2,"process_identity",side_effect=FileNotFoundError()), \
                     mock.patch.object(g2,"host_tool_contract",return_value=contract):
                    proof = held.bind_master(master)
                    self.assertEqual(proof["published_output_sha256"],g2.sha(master.read_bytes()))
                    self.assertEqual(proof["published_output_bytes"],master.stat().st_size)
                    self.assertEqual(proof["output_path"],str(master))
                    self.assertFalse(proof["human_acceptance"])
                    foreign = root/"foreign.mp4"
                    foreign.write_bytes(master.read_bytes())
                    with self.assertRaisesRegex(g2.G2Unverified,"source_contract_changed"):
                        held.bind_master(foreign)
            finally:
                held.close()

    def test_descriptor_close_failure_blocks_success_and_preserves_reported_native_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary).resolve()
            session = g2.G2Session({},evidence,b"",{"test_double":True})
            session.native_result = {"native_mlt_multisegment_e2e":"PASS","test_double":True}
            session.completed = True
            session.observer_diagnostic_attempted = True
            session.observer = mock.Mock()
            original = OSError("original descriptor close failure")
            session.observer.close.side_effect = original
            with self.assertRaisesRegex(g2.G2Unverified,"descriptor_cleanup_failed") as caught:
                with session:
                    pass
            self.assertIs(caught.exception.native_result,session.native_result)
            self.assertIs(caught.exception.__cause__,original)
            terminal = json.loads((evidence/"final-custody.json").read_bytes())
            self.assertEqual(terminal["native_terminal"],"SUCCESS_REPORTED")
            self.assertFalse(terminal["source_content_and_live_module_verified"])
            session.observer.close.assert_called_once()

    def test_descriptor_close_failure_never_replaces_original_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary).resolve()
            session = g2.G2Session({},evidence,b"",{"test_double":True})
            original = RuntimeError("original native UNKNOWN")
            session.native_error = original
            session.observer_diagnostic_attempted = True
            session.observer = mock.Mock()
            session.observer.close.side_effect = OSError("secondary close failure")
            with self.assertRaises(RuntimeError) as caught:
                with session:
                    raise original
            self.assertIs(caught.exception,original)
            terminal = json.loads((evidence/"final-custody.json").read_bytes())
            self.assertEqual(terminal["native_terminal"],"ERROR_OR_UNKNOWN")
            self.assertFalse(terminal["source_content_and_live_module_verified"])
            self.assertFalse(terminal["native_operation_retried"])
            session.observer.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
