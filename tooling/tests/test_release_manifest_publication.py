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

    def _exercise_post_commit_checkout_change(self, *, replace_after_cleanup_read):
        # Both cases change a real tracked LICENSE and call the real Git checkout
        # verifier. The legacy race hook is deliberately dormant once post-commit
        # read/delete cleanup is removed; it never claims a GREEN foreign swap.
        real_checkout = manifest._checkout
        real_writer = manifest.write_new_manifest
        real_link = manifest.os.link
        real_read_bytes = fixture_module.Path.read_bytes
        real_unlink = manifest.os.unlink
        real_remove = manifest.os.remove
        license_path = self.fixture.root / "LICENSE"
        changed_license = real_read_bytes(license_path) + b"fixture post-commit source change\n"
        foreign_stage = self.fixture.output_dir / "foreign-replacement.json"
        foreign_bytes = b'{"independent_fixture_evidence":"preserve this replacement"}\n'
        checkout_calls = []
        checkout_failures = []
        link_calls = []
        committed = []
        before_source_failure = []
        cleanup_reads = []
        replacements = []
        final_removals = []
        trace = []

        def is_final(path, directory_fd):
            if isinstance(path, int):
                return False
            value = os.fsdecode(os.fspath(path))
            if directory_fd is None and value == str(self.fixture.output):
                return True
            return bool(committed and directory_fd == committed[0][1]
                        and value == os.fsdecode(os.fspath(committed[0][0])))

        def link_once(source, target, *args, **kwargs):
            link_calls.append((source, target))
            if len(link_calls) != 1:
                raise AssertionError("Manifest publication must not retry os.link")
            result = real_link(source, target, *args, **kwargs)
            committed.append((target, kwargs.get("dst_dir_fd")))
            trace.append("real manifest link committed")
            return result

        def replace_with_foreign(stage):
            if replacements:
                raise AssertionError("The fixture must replace the final path only once")
            foreign_stage.write_bytes(foreign_bytes)
            os.replace(foreign_stage, self.fixture.output)
            replacements.append((stage, real_read_bytes(self.fixture.output)))
            trace.append("real foreign os.replace: " + stage)

        def fail_last_checkout(root, sha):
            checkout_calls.append((root, sha))
            if len(checkout_calls) == 3:
                before_source_failure.append(real_read_bytes(self.fixture.output))
                if not replace_after_cleanup_read:
                    # This replacement always happens, independently of whether
                    # the product still has any post-failure cleanup reads.
                    replace_with_foreign("before last checkout")
                license_path.write_bytes(changed_license)
            try:
                return real_checkout(root, sha)
            except manifest.ManifestError as exc:
                checkout_failures.append((len(checkout_calls), exc.code))
                trace.append("real checkout refused: " + exc.code)
                raise

        def read_then_replace(path):
            contents = real_read_bytes(path)
            if path == self.fixture.output and checkout_failures:
                cleanup_reads.append(contents)
                trace.append("product read final bytes after checkout refusal")
                if replace_after_cleanup_read:
                    # The real reader is closed. On the old implementation this
                    # returns the original bytes AFTER a real foreign replacement,
                    # opening the read-equality/unlink pathname window.
                    self.assertEqual(contents, self.expected)
                    replace_with_foreign("after cleanup read before unlink")
            return contents

        def track_removal(operation, real_operation, path, *args, **kwargs):
            if is_final(path, kwargs.get("dir_fd")):
                # Observe the actual bytes about to be removed, without invoking
                # the injected Path.read_bytes hook a second time.
                final_removals.append((operation, real_read_bytes(self.fixture.output)))
                trace.append("product attempted final " + operation)
            return real_operation(path, *args, **kwargs)

        def track_unlink(path, *args, **kwargs):
            return track_removal("unlink", real_unlink, path, *args, **kwargs)

        def track_remove(path, *args, **kwargs):
            return track_removal("remove", real_remove, path, *args, **kwargs)

        with contextlib.ExitStack() as patches:
            writer = patches.enter_context(
                patch.object(manifest, "write_new_manifest", wraps=real_writer)
            )
            patches.enter_context(
                patch.object(manifest, "_checkout", side_effect=fail_last_checkout)
            )
            patches.enter_context(patch.object(manifest.os, "link", side_effect=link_once))
            patches.enter_context(
                patch.object(fixture_module.Path, "read_bytes", autospec=True,
                             side_effect=read_then_replace)
            )
            patches.enter_context(patch.object(manifest.os, "unlink", side_effect=track_unlink))
            patches.enter_context(patch.object(manifest.os, "remove", side_effect=track_remove))
            code, stdout, stderr = self.fixture.invoke()

        self.assertEqual(len(checkout_calls), 3, trace)
        self.assertEqual(checkout_failures, [(3, "source_metadata_dirty")], trace)
        self.assertEqual(before_source_failure, [self.expected], trace)
        self.assertEqual(real_read_bytes(license_path), changed_license)
        self.assertEqual(writer.call_count, 1, "Do not retry a published manifest")
        self.assertEqual((len(link_calls), len(committed)), (1, 1), trace)
        if replace_after_cleanup_read and cleanup_reads:
            # RED must demonstrate the actual substitution and the original
            # read, rather than just a mocked failure or an unarmed hook.
            self.assertEqual(cleanup_reads, [self.expected], trace)
            self.assertEqual(
                replacements,
                [("after cleanup read before unlink", foreign_bytes)],
                trace,
            )
        self.assertEqual(
            final_removals, [],
            "Never remove the final pathname after publication; trace=" + repr(trace),
        )
        self.assertEqual(
            cleanup_reads, [],
            "Do not inspect final bytes for post-commit deletion; trace=" + repr(trace),
        )
        self.assertEqual((code, stdout), (2, ""), trace)
        self.assertEqual(
            stderr, "delivery-manifest=REFUSED code=manifest_output_commit_uncertain\n",
            trace,
        )
        if replace_after_cleanup_read:
            self.assertEqual(replacements, [], "Removed cleanup must leave the race hook dormant")
            expected_final = self.expected
        else:
            self.assertEqual(replacements, [("before last checkout", foreign_bytes)], trace)
            expected_final = foreign_bytes
        self.assertEqual(real_read_bytes(self.fixture.output), expected_final, trace)
        self.assertFalse(foreign_stage.exists(), trace)
        self.assertEqual(list(self.fixture.output_dir.iterdir()), [self.fixture.output], trace)

    def test_real_foreign_replace_before_failed_checkout_is_preserved_as_uncertain(self):
        self._exercise_post_commit_checkout_change(replace_after_cleanup_read=False)

    def test_post_read_preunlink_swap_window_is_removed_after_checkout_failure(self):
        self._exercise_post_commit_checkout_change(replace_after_cleanup_read=True)



if __name__ == "__main__":
    unittest.main()
