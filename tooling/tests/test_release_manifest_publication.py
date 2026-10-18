"""Real-file publication outcomes after one attempted exclusive manifest link.

These controls compose the existing synthetic Git fixture without inheriting or
re-exporting its TestCase. They never contact a runtime or signature service.
"""
from __future__ import annotations

import contextlib
import errno
import json
import os
import unittest
from unittest.mock import patch

if __package__:
    from . import test_release_manifest_integrity as fixture_module
else:
    import test_release_manifest_integrity as fixture_module

manifest = fixture_module.manifest


class ManifestPublicationOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.DeliveryIntegrityTests(
            methodName="test_clean_exact_checkout_is_bound_and_unreleased"
        )
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        document = manifest.build_manifest(self.fixture.root, self.fixture.sha)
        self.expected = (
            json.dumps(document, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")

    def _exercise_post_link_failure(self, *, fail_link_result):
        real_link = manifest.os.link
        real_stat = manifest.os.stat
        real_lstat = manifest.os.lstat
        real_unlink = manifest.os.unlink
        real_remove = manifest.os.remove
        link_calls = []
        committed = []
        observation_faults = []
        removed_final = []

        def is_final(path, directory_fd):
            if not committed or isinstance(path, int):
                return False
            destination, destination_fd = committed[0]
            return (
                directory_fd == destination_fd
                and os.fsdecode(os.fspath(path)) == os.fsdecode(os.fspath(destination))
            )

        def link_once(source, destination, *args, **kwargs):
            link_calls.append((source, destination))
            if len(link_calls) != 1:
                # Refuse to execute a second filesystem mutation in this fixture.
                raise AssertionError("Manifest publication must not retry os.link")
            result = real_link(source, destination, *args, **kwargs)
            committed.append((destination, kwargs.get("dst_dir_fd")))
            if fail_link_result:
                raise OSError(errno.EIO, "fixture_link_committed_then_eio")
            return result

        def observe(real_observer, path, *args, **kwargs):
            if (
                not fail_link_result
                and not observation_faults
                and is_final(path, kwargs.get("dir_fd"))
            ):
                observation_faults.append(True)
                raise OSError(errno.EIO, "fixture_committed_observation_eio")
            return real_observer(path, *args, **kwargs)

        def observe_stat(path, *args, **kwargs):
            return observe(real_stat, path, *args, **kwargs)

        def observe_lstat(path, *args, **kwargs):
            return observe(real_lstat, path, *args, **kwargs)

        def track_unlink(path, *args, **kwargs):
            if is_final(path, kwargs.get("dir_fd")):
                removed_final.append("unlink")
            return real_unlink(path, *args, **kwargs)

        def track_remove(path, *args, **kwargs):
            if is_final(path, kwargs.get("dir_fd")):
                removed_final.append("remove")
            return real_remove(path, *args, **kwargs)

        with contextlib.ExitStack() as patches:
            patches.enter_context(patch.object(manifest.os, "link", side_effect=link_once))
            patches.enter_context(patch.object(manifest.os, "stat", side_effect=observe_stat))
            patches.enter_context(patch.object(manifest.os, "lstat", side_effect=observe_lstat))
            patches.enter_context(patch.object(manifest.os, "unlink", side_effect=track_unlink))
            patches.enter_context(patch.object(manifest.os, "remove", side_effect=track_remove))
            code, stdout, stderr = self.fixture.invoke()

        self.assertEqual(len(link_calls), 1, "Attempt the exclusive link exactly once")
        self.assertEqual(len(committed), 1, "The real filesystem link must succeed")
        self.assertEqual(len(observation_faults), 0 if fail_link_result else 1)
        self.assertEqual(
            removed_final, [], "Never delete the final path after uncertain publication"
        )
        self.assertEqual((code, stdout), (2, ""))
        self.assertEqual(
            stderr,
            "delivery-manifest=REFUSED code=manifest_output_commit_uncertain\n",
        )
        self.assertEqual(
            self.fixture.output.read_bytes(),
            self.expected,
            "Preserve the complete source-bound manifest committed before the error",
        )
        self.assertEqual(
            list(self.fixture.output_dir.iterdir()),
            [self.fixture.output],
            "Only the final manifest remains after owned staging cleanup",
        )
        document = json.loads(self.fixture.output.read_bytes())
        self.assertEqual(
            document["motionwright"]["source_revision"], self.fixture.sha
        )
        self.assertFalse(document["delivery_boundary"]["automatic_publish"])

    def test_real_link_commits_then_eio_keeps_complete_manifest(self):
        self._exercise_post_link_failure(fail_link_result=True)

    def test_real_link_then_destination_observation_eio_keeps_manifest(self):
        self._exercise_post_link_failure(fail_link_result=False)


if __name__ == "__main__":
    unittest.main()
