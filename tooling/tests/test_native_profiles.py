"""Causal controls for the new Hero lane; no renderer or Native is executed.

Request/API fixtures exercise admission logic only. Custody controls use real
files, ZIP/TAR bytes and RSA/AES-GCM. They confer no physical cgroup or G2 credit.
"""
from __future__ import annotations

import ast
import base64
import copy
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import time
import unittest
from unittest import mock
import zipfile

TOOLING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLING))
import native_profiles_workflow as route
import native_profiles_custody as custody
import native_owner_resources as resources
from ci_private_evidence import decrypt_snapshot
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


class ProfileControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        der = cls.key.public_key().public_bytes(serialization.Encoding.DER,
                                              serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.recipient = {"spki_der_base64": base64.b64encode(der).decode(), "sha256": route.digest(der)}

    def fixtures(self):
        current = {"source_sha": "a" * 40, "semwright_sha": "b" * 40, "run_id": "12345678901", "run_attempt": "1"}
        request = {"schema": "motionwright.native.hero-profiles-request/1", **{key: current[key]
            for key in ("source_sha", "semwright_sha")}, "source_tree": "c" * 40, "semwright_tree": "d" * 40,
            "roster": list(route.ROSTER), "limits": route.LIMITS, "operations_per_profile": route.OPERATIONS,
            "owner_resources": resources.OWNER_RESOURCE_POLICY, "recipient": self.recipient,
            "runtime_bootstrap_sha256": "e" * 64}
        raw = route.canonical(request)
        clock = route.canonical({"schema": "logic-fixture-not-a-physical-clock"})
        index = {"schema": "motionwright.native.hero-preparation-index/1", "context": current,
            "request": route.descriptor(raw), "clock": route.descriptor(clock), "limits": route.LIMITS,
            "shared_identity": route.descriptor(b"identity fixture"), "shared_readonly": route.descriptor(b"mount fixture"),
            "runtime_bootstrap": route.descriptor(b"runtime fixture"), "runtime_observation": route.descriptor(b"observation fixture"),
            "source": {"context": current}, "resource_prefix": route.descriptor(b"prefix fixture"),
            "runner": {"job_id": 55, "runner_id": 66, "workflow_id": 77, "runner_name": "fixture-runner"},
            "cases": [{"profile": name, "namespace": "/home/runner/work/_temp/hp12345678901-1/" + name,
                "width": route.SIZES[name][0], "height": route.SIZES[name][1], "frames": 180,
                "frame_rate": [30, 1], "operations": route.OPERATIONS} for name in route.ROSTER],
            "preparation_complete": True, "native_calls": 0, "source_gate_or_native_grant": False,
            "fixedtxt_authority_inherited": False, "created_monotonic": 123.0}
        index_raw = route.canonical(index)
        prepared = {"artifact_id": 88, "artifact_sha256": "f" * 64}
        permission = {"schema": "motionwright.native.hero-profile-owner-admissions/1", "context": current,
            "preparation_index": route.descriptor(index_raw), "preparation_artifact": prepared,
            "owner_actor_id": route.OWNER_ACTOR, "grants": [{"profile": row["profile"], "admission_id": "fixture-" + row["profile"],
                "namespace": row["namespace"], "runner": index["runner"], "source_index_sha256": route.digest(index_raw),
                "request_sha256": route.digest(raw), "shared_identity_sha256": index["shared_identity"]["sha256"],
                "operations": route.OPERATIONS, "limits": route.LIMITS, "width": row["width"], "height": row["height"],
                "frames": 180, "owner_authorized": True, "release_authorized": False, "G2_claimed": False}
                for row in index["cases"]]}
        return current, request, raw, clock, index, index_raw, prepared, permission

    def test_request_requires_exact_three_profiles_and_versioned_resource_policy(self):
        current, request, *_ = self.fixtures()
        route.checked_request(route.canonical(request), current)
        for mutate in (
            lambda value: value.pop("owner_resources"),
            lambda value: value["owner_resources"].update(swap_max_bytes=False),
            lambda value: value["owner_resources"].update(pids_max=1024),
            lambda value: value["limits"].update(native_seconds_each=540.0),
            lambda value: value["operations_per_profile"].update(mlt_av_mux=2),
            lambda value: value.update(roster=["landscape", "portrait", "square", "4k"]),
        ):
            value = copy.deepcopy(request)
            mutate(value)
            with self.subTest(value=value.get("roster")), self.assertRaises(route.ProfileRejected):
                route.checked_request(route.canonical(value), current)

    def test_index_rejects_foreign_owner_and_numeric_aliases(self):
        current, _, raw, clock, index, index_raw, *_ = self.fixtures()
        route.checked_index(index_raw, current=current, request_raw=raw, clock_raw=clock)
        for mutate in (
            lambda value: value["cases"][1].update(namespace="/home/runner/work/_temp/hp999-1/portrait"),
            lambda value: value["cases"][1].update(namespace="/home/runner/work/_temp/hp12345678901-1/../portrait"),
            lambda value: value["cases"][1].update(frames=180.0),
            lambda value: value["cases"][1]["operations"].update(automatic_retry=False),
            lambda value: value.update(native_calls=False),
        ):
            changed = copy.deepcopy(index)
            mutate(changed)
            with self.assertRaises(route.ProfileRejected):
                route.checked_index(route.canonical(changed), current=current, request_raw=raw, clock_raw=clock)

    def test_three_permissions_cannot_reuse_id_or_authorize_extra_effect(self):
        current, _, raw, clock, _, index_raw, prepared, permission = self.fixtures()
        kwargs = {"current": current, "request_raw": raw, "clock_raw": clock, "prepared_artifact": prepared}
        route.checked_permissions(route.canonical(permission), index_raw, **kwargs)
        for mutate in (
            lambda value: value["grants"][1].update(admission_id=value["grants"][0]["admission_id"]),
            lambda value: value["grants"].pop(),
            lambda value: value["grants"][2]["operations"].update(motion_canvas_render_start=2),
            lambda value: value["grants"][2].update(G2_claimed=True),
            lambda value: value["grants"][2].update(source_index_sha256="0" * 64),
            lambda value: value["preparation_artifact"].update(artifact_id=89),
        ):
            changed = copy.deepcopy(permission)
            mutate(changed)
            with self.assertRaises(route.ProfileRejected):
                route.checked_permissions(route.canonical(changed), index_raw, **kwargs)

    @staticmethod
    def make_zip(raw, *, name="owner-permissions.original.json", mode=stat.S_IFREG | 0o600, extra=False):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
            entry = zipfile.ZipInfo(name)
            entry.external_attr = mode << 16
            archive.writestr(entry, raw)
            if extra:
                archive.writestr("unselected.json", b"{}");
        return stream.getvalue()

    def test_permission_zip_requires_actual_identity_and_single_regular_member(self):
        raw = b'{"fixture":"no-Native"}\n'
        for values in ({}, {"name": "../owner-permissions.original.json"},
                       {"mode": stat.S_IFLNK | 0o600}, {"extra": True}):
            archive = self.make_zip(raw, **values)
            item = {"size_in_bytes": len(archive), "digest": "sha256:" + route.digest(archive)}
            if values:
                with self.assertRaises(route.ProfileRejected):
                    route.permission_zip(archive, item)
            else:
                self.assertEqual(route.permission_zip(archive, item), raw)
                with self.assertRaises(route.ProfileRejected):
                    route.permission_zip(archive + b"x", item)

    def test_loaded_permission_joins_retained_zip_and_delivery_before_any_effect(self):
        current, _, request_raw, clock, index, index_raw, prepared, permission = self.fixtures()
        raw = route.canonical(permission)
        archive = self.make_zip(raw)
        delivery = {"id": 12345678902, "run_attempt": 1, "head_sha": current["source_sha"], "event": "workflow_dispatch",
            "path": route.WORKFLOW, "repository": {"full_name": route.REPO}, "head_repository": {"full_name": route.REPO},
            "actor": {"id": route.OWNER_ACTOR}, "triggering_actor": {"id": route.OWNER_ACTOR},
            "status": "completed", "conclusion": "success", "workflow_id": 77}
        item = {"id": 99, "name": route.delivery_name(current["run_id"], route.digest(index_raw)),
            "size_in_bytes": len(archive), "digest": "sha256:" + route.digest(archive), "expired": False,
            "workflow_run": {"id": delivery["id"], "head_sha": current["source_sha"]}}
        consumed = {"context": current, "preparation_index": route.descriptor(index_raw), "prepared_artifact": prepared,
            "permissions": route.descriptor(raw), "delivery_run": delivery["id"], "artifact": route.artifact_identity(item),
            "native_calls": 0, "automatic_retry": False, "owner_actor_id": route.OWNER_ACTOR, "verified_monotonic": 456.0}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "permissions"
            folder.mkdir(mode=0o700)
            for name, value in (("owner-permissions.original.json", raw), ("permissions.original.zip", archive),
                ("delivery-run.original.json", delivery), ("delivery-artifact.original.json", item),
                ("consumed.original.json", consumed)):
                route.write(folder / name, value)
            for profile in route.ROSTER:
                actual = route.loaded_permission(root, current, request_raw, clock, index_raw, profile)
                self.assertEqual(actual["profile"], profile)
            # Deliberately alter only this disposable fixture. The original
            # workflow never overwrites an admission or retries consumption.
            (folder / "permissions.original.zip").write_bytes(archive[:-1] + b"x")
            with self.assertRaises(route.ProfileRejected):
                route.loaded_permission(root, current, request_raw, clock, index_raw, "portrait")

    def test_original_clock_and_upload_reserve_can_only_shorten_execution(self):
        import native_fixedtxt_g2_supervisor as supervisor
        now = time.monotonic()
        value = supervisor.AttemptClock({"attempt_seconds": 660, "custody_seconds": 120},
            started=now, upload_seconds=route.LIMITS["upload_seconds_each"], ceiling=now + 660)
        self.assertEqual(value.work, now + 540)
        self.assertEqual(value.custody_end, now + 600)
        # Each 60-second upload is deferred until all local owners are closed.
        # Its latest completion belongs to the original session, never to
        # landscape's already elapsed local processing window.
        session_end = now + 3 * 660 + route.LIMITS["final_seconds"]
        latest = [session_end - route.LIMITS["final_seconds"] - (2 - ordinal) * 60 for ordinal in range(3)]
        self.assertEqual(latest, [now + 1860, now + 1920, now + 1980])
        self.assertGreater(latest[0], value.total)
        self.assertEqual(session_end - latest[-1], 180)
        value.reserve("work")
        with self.assertRaises(supervisor.SupervisionRejected):
            value.reserve("work")
        original = {"started_monotonic": now - 1201, "deadline_monotonic": now + 2699}
        clock = route.PreparationClock(original)
        with self.assertRaises(route.ProfileRejected):
            clock.reserve("prepare-13")
        self.assertLess(clock.reserve("prepare-11"), original["deadline_monotonic"])
        with self.assertRaises(route.ProfileRejected):
            clock.reserve("prepare-11")

    def test_precheckout_yaml_literals_equal_the_runtime_contract(self):
        path = TOOLING.parent / ".github/workflows/native-profiles-e2e.yml"
        yaml = path.read_text()
        block = yaml.split("          import hashlib, json, os, pathlib, re, stat, time\n", 1)[1].split("          PY\n", 1)[0]
        tree = ast.parse("\n".join(line[10:] if line.startswith("          ") else line for line in block.splitlines()))
        literals = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"POLICY", "LIMITS", "OPERATIONS"}}
        self.assertEqual(route.canonical(literals["POLICY"]), route.canonical(resources.OWNER_RESOURCE_POLICY))
        self.assertEqual(route.canonical(literals["LIMITS"]), route.canonical(route.LIMITS))
        self.assertEqual(route.canonical(literals["OPERATIONS"]), route.canonical(route.OPERATIONS))
        self.assertEqual(route.LIMITS["native_seconds_each"] + route.LIMITS["custody_seconds_each"], 660)

    @unittest.skipUnless(os.name == "posix", "actual Linux anchored file descriptors")
    def test_real_tar_and_authenticated_envelope_preserve_exact_originals(self):
        current, request, *_ = self.fixtures()
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "originals"
            source.mkdir(mode=0o700)
            (source / "project.json").write_bytes(b'{"original":"no-render-fixture"}\n')
            (source / "media.bin").write_bytes(bytes(range(256)) * 1024)
            result = custody.seal({"case": source}, {"case": lambda parts: True}, base / "sealed",
                current=current, request=request, scope="control-only-no-Native", payload_max=1024 * 1024,
                claims={"native_calls": 0})
            self.assertTrue(result["selected_originals_complete"])
            plaintext, bound = decrypt_snapshot((base / "sealed/sealed-originals.bin").read_bytes(), self.key)
            self.assertEqual(bound, current)
            self.assertEqual(plaintext, (base / "sealed/selected-originals.tar").read_bytes())
            with tarfile.open(fileobj=io.BytesIO(plaintext), mode="r:") as archive:
                self.assertEqual(archive.extractfile("case/media.bin").read(), (source / "media.bin").read_bytes())
            with self.assertRaises(FileExistsError):
                custody.seal({"case": source}, {"case": lambda parts: True}, base / "sealed",
                    current=current, request=request, scope="control-only-no-Native", payload_max=1024 * 1024, claims={})

    @unittest.skipUnless(os.name == "posix", "actual links and FIFO")
    def test_links_fifo_and_missing_physical_hash_remain_negative(self):
        current, *_ = self.fixtures()
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "originals"
            source.mkdir(mode=0o700)
            (source / "original").write_bytes(b"physical fixture")
            os.link(source / "original", source / "hardlink")
            (source / "symlink").symlink_to(source / "original")
            os.mkfifo(source / "pipe", 0o600)
            begun = time.monotonic()
            _, manifest = custody.capture_tar(base / "negative.tar", {"case": source}, {"case": lambda parts: True},
                current=current, scope="negative-control", payload_max=1024, files_max=32,
                claims={"physical_proof": {"required_originals": {"original": route.descriptor(b"physical fixture")}}})
            self.assertLess(time.monotonic() - begun, 2)
            self.assertFalse(manifest["selected_originals_complete"])
            self.assertFalse(manifest["required_physical_originals_matched"])
            states = {row["path"]: row["status"] for row in manifest["members"]}
            self.assertEqual(states["pipe"], "METADATA_ONLY_NONREGULAR")
            self.assertEqual(states["symlink"], "METADATA_ONLY_NONREGULAR")
            self.assertEqual(states["hardlink"], "CAPTURE_FAILED")
            self.assertTrue((source / "original").is_file())

    @unittest.skipUnless(os.name == "posix", "actual quota and expired original deadline")
    def test_physical_quota_and_deadline_never_publish_a_complete_snapshot(self):
        current, request, *_ = self.fixtures()
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "originals"
            source.mkdir(mode=0o700)
            (source / "too-large.bin").write_bytes(b"x" * 2048)
            _, manifest = custody.capture_tar(base / "bounded.tar", {"case": source}, {"case": lambda parts: True},
                current=current, scope="quota-negative", payload_max=1024, files_max=16, claims={})
            self.assertFalse(manifest["selected_originals_complete"])
            self.assertEqual(manifest["selected_payload_bytes"], 0)
            with self.assertRaises(route.ProfileRejected):
                custody.seal({"case": source}, {"case": lambda parts: True}, base / "expired",
                    current=current, request=request, scope="deadline-negative", payload_max=4096,
                    claims={}, deadline=time.monotonic() - 1)
            self.assertFalse((base / "expired").exists())


if __name__ == "__main__":
    unittest.main()
