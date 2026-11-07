"""Focused source predicates only; synthetic fixtures never grant Native/G2 credit."""
from __future__ import annotations
import base64
import copy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import ci_private_evidence as engine
import native_manim_resume_transfer as transfer
import native_manim_resume_focals as focals
import native_manim_resume_resources as resources
import native_manim_editable_outer as outer
import native_manim_resume_runtime as runtime
import native_manim_resume_lineage as lineage

CURRENT = {"source_sha": "a" * 40, "semwright_sha": transfer.SDK, "run_id": "12345", "run_attempt": "1"}
INSTANT = datetime(2026, 11, 7, 12, 1, tzinfo=timezone.utc)


class TransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        der = cls.key.public_key().public_bytes(serialization.Encoding.DER,
                                                serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.recipient = {"spki_der_base64": base64.b64encode(der).decode(), "sha256": engine.sha(der)}

    def setUp(self):
        self.files = {name: ("SYNTHETIC_SOURCE_UNIT:" + name).encode() for name in transfer.INPUTS}
        originals = {name: (len(raw), engine.sha(raw)) for name, raw in self.files.items()}
        patcher = mock.patch.dict(transfer.INPUTS, originals, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.public = transfer.recipient_record(self.recipient, CURRENT, "42",
            "2026-11-07T12:00:00+00:00", "2026-11-07T12:10:00+00:00", "b" * 64)

    def release(self, assets=None):
        return {"id": 11, "draft": True, "prerelease": True, "published_at": None,
                "tag_name": transfer.tag(CURRENT), "name": transfer.tag(CURRENT),
                "target_commitish": CURRENT["source_sha"], "body": transfer.release_body(self.public, CURRENT),
                "author": {"id": 42}, "created_at": "2026-11-01T00:00:00Z",
                "assets": [] if assets is None else assets}

    def asset(self, state="uploaded"):
        return {"id": 22, "name": transfer.ASSET_NAME, "state": state, "size": 20123,
                "digest": "sha256:" + "d" * 64, "content_type": "application/octet-stream",
                "uploader": {"id": 42}, "created_at": "2026-11-07T12:00:30Z",
                "updated_at": "2026-11-07T12:00:31Z"}

    def test_real_aead_roundtrip_keeps_exact_five_files_and_sixth_manifest(self):
        raw = transfer.make_plaintext(self.files, CURRENT, self.recipient["sha256"])
        envelope = engine.encrypt_snapshot(raw, self.recipient, CURRENT)
        restored, context = engine.decrypt_snapshot(envelope, self.key)
        self.assertEqual(context, CURRENT)
        files, manifest = transfer.inspect_plaintext(restored, CURRENT, self.recipient["sha256"])
        self.assertEqual(files, self.files)
        self.assertEqual(manifest, engine.json_bytes(transfer.manifest(CURRENT, self.recipient["sha256"])))
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            self.assertEqual(len(archive.getmembers()), 6)

    def test_changed_aead_context_or_ciphertext_never_becomes_source_admission(self):
        raw = transfer.make_plaintext(self.files, CURRENT, self.recipient["sha256"])
        envelope = engine.encrypt_snapshot(raw, self.recipient, CURRENT)
        for mutation in (envelope[:-1] + bytes([envelope[-1] ^ 1]),
                         envelope.replace(b'"run_id":"12345"', b'"run_id":"12346"', 1)):
            with self.subTest(kind=engine.sha(mutation)), self.assertRaises(InvalidTag):
                engine.decrypt_snapshot(mutation, self.key)

    def test_original_source_change_extra_file_or_missing_file_is_rejected(self):
        variants = [{**self.files, "private-key.pem": b"forbidden"},
                    {name: raw for name, raw in self.files.items() if name != next(iter(self.files))},
                    {**self.files, next(iter(self.files)): b"changed"}]
        for variant in variants:
            with self.subTest(keys=list(variant)), self.assertRaises(ValueError):
                transfer.make_plaintext(variant, CURRENT, self.recipient["sha256"])

    def test_noncanonical_trailing_tar_bytes_and_foreign_target_are_rejected(self):
        raw = transfer.make_plaintext(self.files, CURRENT, self.recipient["sha256"])
        with self.assertRaisesRegex(ValueError, "resume_archive_noncanonical"):
            transfer.inspect_plaintext(raw + b"unselected-original", CURRENT, self.recipient["sha256"])
        with self.assertRaisesRegex(ValueError, "resume_manifest_exact_binding"):
            transfer.inspect_plaintext(raw, {**CURRENT, "run_id": "99999"}, self.recipient["sha256"])

    def test_tar_links_and_duplicate_names_never_extract(self):
        raw = transfer.make_plaintext(self.files, CURRENT, self.recipient["sha256"])
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            rows = [(row, archive.extractfile(row).read()) for row in archive.getmembers()]
        for kind in ("link", "duplicate"):
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for i, (source, data) in enumerate(rows):
                    row = copy.copy(source)
                    if i == 0 and kind == "link":
                        row.type, row.linkname, row.size = tarfile.SYMTYPE, "../../private-key", 0
                        data = b""
                    if i == 0 and kind == "duplicate":
                        row.name = rows[1][0].name
                    archive.addfile(row, io.BytesIO(data))
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                transfer.inspect_plaintext(output.getvalue(), CURRENT, self.recipient["sha256"])

    def test_recipient_binds_gate_actor_context_and_finite_lifetime(self):
        self.assertEqual(transfer.validate_recipient(self.public, CURRENT, "42", "b" * 64, clock=INSTANT),
                         self.recipient)
        for current, actor, gate, clock in (({**CURRENT, "run_id": "777"}, "42", "b" * 64, INSTANT),
                (CURRENT, "43", "b" * 64, INSTANT), (CURRENT, "42", "c" * 64, INSTANT),
                (CURRENT, "42", "b" * 64, datetime(2026, 11, 7, 12, 11, tzinfo=timezone.utc))):
            with self.subTest(actor=actor, gate=gate), self.assertRaises(ValueError):
                transfer.validate_recipient(self.public, current, actor, gate, clock=clock)
        altered = {**self.public, "historical_private_key_requested": 0}
        with self.assertRaisesRegex(ValueError, "resume_recipient_exact_context"):
            transfer.validate_recipient(altered, CURRENT, "42", "b" * 64, clock=INSTANT)

    def test_draft_commit_date_may_predate_recipient_but_body_must_bind_new_run(self):
        release = self.release([self.asset()])
        self.assertEqual(transfer.validate_release(release, self.public, CURRENT)["asset_id"], 22)
        wrong = {**release, "body": transfer.release_body(self.public, {**CURRENT, "run_id": "6"})}
        with self.assertRaisesRegex(ValueError, "resume_draft_release_binding"):
            transfer.validate_release(wrong, self.public, CURRENT)

    def test_uploaded_asset_is_the_only_ready_state_and_id_is_explicit(self):
        for state in ("new", "open", "starter"):
            with self.subTest(state=state):
                self.assertIsNone(transfer.uploaded_asset(self.release([self.asset(state)]), self.public, CURRENT))
        observed = transfer.uploaded_asset(self.release([self.asset()]), self.public, CURRENT)
        self.assertEqual((observed["release_id"], observed["asset_id"], observed["sha256"]), (11, 22, "d" * 64))
        with self.assertRaises(ValueError):
            transfer.uploaded_asset(self.release([self.asset(), self.asset()]), self.public, CURRENT)

    def test_published_release_foreign_actor_digest_and_stale_asset_are_denied(self):
        original = self.release([self.asset()])
        variants = [{**original, "draft": False}, {**original, "published_at": "2026-11-07T12:01:00Z"},
                    {**original, "author": {"id": 43}}, {**original, "target_commitish": "c" * 40}]
        for key, value in (("digest", None), ("digest", "sha256:garbage"), ("size", True),
                           ("created_at", "2026-11-07T11:59:00Z"), ("updated_at", "2026-11-07T12:11:00Z")):
            variants.append({**original, "assets": [{**self.asset(), key: value}]})
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises((ValueError, TypeError)):
                transfer.validate_release(variant, self.public, CURRENT)

    def test_bounded_draft_list_is_unique_and_rejects_duplicate_json_keys(self):
        raw = json.dumps([self.release()]).encode()
        self.assertEqual(transfer.find_draft(raw, self.public, CURRENT)["id"], 11)
        self.assertIsNone(transfer.find_draft(b"[]", self.public, CURRENT))
        for raw in (json.dumps([self.release()] * 2).encode(), json.dumps([{}] * 101).encode(),
                    b'[{"tag_name":"x","tag_name":"y"}]'):
            with self.subTest(raw=raw[:30]), self.assertRaises(ValueError):
                transfer.find_draft(raw, self.public, CURRENT)

    def test_get_by_tag_unbounded_queries_and_foreign_hosts_are_never_requested(self):
        with mock.patch("urllib.request.build_opener", side_effect=AssertionError("network forbidden")):
            for endpoint in ("/repos/seradotcom/motionwright/releases/tags/name",
                             "/repos/seradotcom/motionwright/releases?per_page=1000",
                             "https://example.invalid/private", "/repos/foreign/project/releases/1"):
                with self.subTest(endpoint=endpoint), self.assertRaisesRegex(ValueError, "resume_api_closed"):
                    transfer.github_get(endpoint, token="synthetic-not-a-credential", limit=1024)


class GateAndResourcesTests(unittest.TestCase):
    def test_runtime_delta_and_foreign_context_stop_before_private_inputs(self):
        projection = copy.deepcopy(runtime.EXPECTED)
        projection.pop("pyav_native_files_sha256")
        projection.pop("pyav_native_files_count")
        projection["pyav_native_files"] = [{"path": "synthetic-unit.so", "bytes": 3, "sha256": "1" * 64}]
        expected = copy.deepcopy(runtime.EXPECTED)
        expected.update(pyav_native_files_count=1,
                        pyav_native_files_sha256=engine.sha(runtime.canonical(projection["pyav_native_files"])))
        measured = {**projection, "schema": "motionwright.manim.runtime-inventory/2",
                    "context": CURRENT, "native_render_performed": False}
        with mock.patch.object(runtime, "EXPECTED", expected), mock.patch.object(runtime, "ORIGINAL_PROJECTION_SHA",
                engine.sha(runtime.canonical(projection))):
            self.assertEqual(runtime.compare(measured, CURRENT)["status"], "SAME_ORIGINAL_RUNTIME")
            changed = copy.deepcopy(measured)
            changed["inventory_sha256"] = "2" * 64
            result = runtime.compare(changed, CURRENT)
            self.assertEqual(result["status"], "RUNTIME_CHANGED_BEFORE_PRIVATE_INPUTS")
            self.assertEqual(result["differences"], ["inventory_sha256"])
            self.assertIs(result["inferred_package_versions"], False)
            with self.assertRaisesRegex(ValueError, "measured_new_context"):
                runtime.compare(measured, {**CURRENT, "run_id": "999"})

    def test_lineage_intake_requires_completed_success_and_exact_attempt(self):
        value = {"id": 12345, "head_sha": CURRENT["source_sha"], "run_attempt": 1,
                 "head_branch": transfer.REF.removeprefix("refs/heads/"), "event": "workflow_dispatch",
                 "status": "completed", "conclusion": "success", "path": ".github/workflows/native-manim-e2e.yml",
                 "repository": {"full_name": transfer.REPOSITORY}}
        lineage.completed_run(value, CURRENT)
        for key, changed in (("conclusion", "failure"), ("status", "in_progress"), ("head_sha", "f" * 40),
                             ("run_attempt", 2), ("id", True)):
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, "completed_original_run_required"):
                lineage.completed_run({**value, key: changed}, CURRENT)

    def test_lineage_selected_member_requires_authentic_bytes_and_manifest(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        recipient = {"spki_der_base64": base64.b64encode(der).decode(), "sha256": engine.sha(der)}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            source.mkdir(mode=0o700)
            (source / "unit.json").write_bytes(b'{"fixture":"SOURCE_ONLY_SYNTHETIC"}\n')
            plain = engine.gather_snapshot({"evidence": source})
            raw = engine.encrypt_snapshot(plain, recipient, CURRENT)
            capsule = root / "unit.mwseal"
            capsule.write_bytes(raw)
            with mock.patch.object(lineage, "RECIPIENT_SHA", recipient["sha256"]):
                snapshot = lineage.Snapshot(capsule, lineage.identity(raw), CURRENT, key, complete=True)
                self.assertEqual(snapshot.raw("evidence/unit.json"), (source / "unit.json").read_bytes())
                snapshot.close()
                with self.assertRaisesRegex(ValueError, "authenticated_context"):
                    lineage.Snapshot(capsule, lineage.identity(raw), {**CURRENT, "run_id": "9"}, key, complete=True)
                changed = raw[:-1] + bytes([raw[-1] ^ 1])
                capsule.write_bytes(changed)
                with self.assertRaises(InvalidTag):
                    lineage.Snapshot(capsule, lineage.identity(changed), CURRENT, key, complete=True)

    def test_eight_actual_named_lines_and_zero_failures_are_required(self):
        output = ("\n".join("test " + name + " ... ok" for name in focals.SELECTED)
                  + "\ntest result: ok. 8 passed; 0 failed; 0 ignored; 0 measured; 29 filtered out; finished in 0.2s\n").encode()
        observed = focals.parse_original_tests(output)
        self.assertEqual(observed["passed"], 8)
        self.assertEqual(observed["selected_ids"], list(focals.SELECTED))
        for changed in (output.replace(b"8 passed", b"0 passed"), output.replace(b"0 ignored", b"1 ignored"),
                        output.replace(focals.SELECTED[0].encode(), b"synthetic_mismatched_test"),
                        output + output, output.replace(b"... ok", b"... FAILED", 1)):
            with self.subTest(sha=engine.sha(changed)), self.assertRaisesRegex(ValueError, "eight_actual"):
                focals.parse_original_tests(changed)

    def test_unknown_or_changed_cgroup_limits_are_never_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = {"memory.current": "0", "memory.peak": "1000", "memory.max": str(resources.LIMIT),
                      "memory.events": "oom 0\noom_kill 0\n", "memory.swap.max": "0", "pids.max": "256"}
            for name, value in values.items():
                (root / name).write_text(value)
            self.assertEqual(resources.observe(root)["limit_bytes"], resources.LIMIT)
            for name, value in (("memory.max", "max"), ("memory.max", str(resources.LIMIT * 2)),
                                ("memory.swap.max", "1"), ("pids.max", "max")):
                (root / name).write_text(value)
                try:
                    with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                        resources.observe(root)
                finally:
                    (root / name).write_text(values[name])

    def test_resume_context_requires_explicit_branch_sha_attempt_and_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "SOURCE_LOCK.json").write_text(json.dumps({"dependencies": {"semwright": {"revision": transfer.SDK}}}))
            env = {"GITHUB_WORKSPACE": str(root), "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
                   "GITHUB_JOB": transfer.JOB, "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": CURRENT["source_sha"],
                   "GITHUB_RUN_ID": CURRENT["run_id"], "GITHUB_REPOSITORY": transfer.REPOSITORY,
                   "GITHUB_REF": transfer.REF, "MANIM_NATIVE_SCENARIO": "editable-source-resume",
                   "MW_MANIM_DEVELOPMENT_NATIVE": "true", "MW_MANIM_RESUME_EXPECTED_SHA": CURRENT["source_sha"]}
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "platform", "linux"):
                self.assertEqual(outer.current_context(), CURRENT)
                for key, value in (("GITHUB_REF", "refs/heads/main"), ("MW_MANIM_RESUME_EXPECTED_SHA", "c" * 40),
                                   ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_JOB", "unselected-job"),
                                   ("MW_MANIM_DEVELOPMENT_NATIVE", "false")):
                    with self.subTest(key=key), mock.patch.dict(os.environ, {key: value}), self.assertRaises(ValueError):
                        outer.current_context()


if __name__ == "__main__":
    unittest.main()
