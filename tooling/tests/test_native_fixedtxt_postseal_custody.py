"""Offline custody contracts only: synthetic originals, no Native or formal credit."""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import tempfile
import sys
import tarfile
import unittest

TOOLING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLING))
import native_fixedtxt_postseal_custody as post
import native_owner_resources as resources
from native_fixedtxt_session import ROSTER, SESSION_SCHEMA
from ci_private_evidence import encrypt_snapshot, decrypt_snapshot
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

CONTEXT = {"source_sha": "a" * 40, "semwright_sha": "b" * 40,
           "run_id": "12345", "run_attempt": "1"}
BOOT = "e4920778-1744-4d8b-99ac-ddaa55771133"
HELPER_RAW = (TOOLING / "native_owner_resources.py").read_bytes()
HELPER_SHA = hashlib.sha256(HELPER_RAW).hexdigest()


def kernel(origin, *, controller_present):
    rows = {}
    for name, identity in origin["identities"].items():
        members = [601] if name == "controller" and controller_present else []
        events = {"low": 0, "high": 0, "max": 0, "oom": 0, "oom_kill": 0}
        # Retained page accounting can be nonzero after all PIDs have exited.
        rows[name] = {"device": identity[0], "inode": identity[1],
            "peak_bytes": 32 * post.MIB, "current_bytes": 65536, "pids": len(members),
            "members": members, "populated": bool(members), "events": events,
            "originals": {"memory.max": str(resources.MAXIMUM), "memory.high": str(resources.HIGH),
                "memory.swap.max": "0", "memory.oom.group": "1", "pids.max": str(resources.TASKS),
                "memory.peak": str(32 * post.MIB), "memory.current": "65536",
                "pids.current": str(len(members)), "memory.events": "\n".join(k + " " + str(v) for k, v in events.items()),
                "cgroup.events": "populated " + str(int(bool(members)))}}
    return {"parent": rows.pop("parent"), "leaves": rows,
            "physical_available_bytes": 2 * resources.RESERVE}


def fixture(cipher, recipient, *, start=1096.094195524, end=None,
            job="fixedtxt-session-owner", argv=None):
    end = start + 3900 if end is None else end
    template = post.canonical({"schema": "motionwright.native.fixedtxt-workflow-request/2",
        "source_sha": CONTEXT["source_sha"], "semwright_sha": CONTEXT["semwright_sha"],
        "owner_resources": resources.OWNER_RESOURCE_POLICY, "limits": {"attempt_seconds": 3900}})
    clock = {"folder": "/home/runner/work/_temp/mw-fixedtxt-g2-control-12345-1",
        "clock": {"bytes": 101, "sha256": "c" * 64}, "request": post.descriptor(template),
        "started_monotonic": start, "deadline_monotonic": end, "boot_id": BOOT}
    origin = {"schema": "motionwright.native.owner-resource-origin/1", "scope": "fixedtxt",
        "context": CONTEXT, "job": job, "uid": 1001, "gid": 1001,
        "source_root": "/source", "base_temp": "/home/runner/work/_temp",
        "cgroup": "/sys/fs/cgroup/motionwright-fixedtxt-12345-1", "clock": clock,
        "source_closure": {"tooling/native_owner_resources.py": {"bytes": len(HELPER_RAW), "sha256": HELPER_SHA}},
        "owner_resources": resources.OWNER_RESOURCE_POLICY, "leaves": list(resources.LEAVES),
        "memory_physical_limit_bytes": resources.LIMIT, "memory_max_bytes": resources.MAXIMUM,
        "memory_high_bytes": resources.HIGH, "physical_reserve_bytes": resources.RESERVE,
        "pids_max": resources.TASKS, "Native_authority_granted": False, "automatic_retry": False,
        "identities": {name: [4, n + 100] for n, name in enumerate(("parent", *resources.LEAVES))}}
    origin_raw = post.canonical(origin)
    origin_ref = post.descriptor(origin_raw)
    argv = argv or ["/home/runner/work/_temp/mw-fixedtxt-custody-python-12345-1/bin/python",
                    "-B", "-E", "-s", "/source/tooling/native_fixedtxt_session.py", "--seal-session"]
    common = {"context": CONTEXT, "origin": origin_ref}
    intent = {**common, "schema": "motionwright.native.owner-resource-command/1", "scope": "fixedtxt",
        "action": "seal-session", "case_index": None, "argv": argv, "controller_pid": 601,
        "deadline": {"initial": clock, "selected": clock, "effective_deadline_monotonic": end},
        "before": kernel(origin, controller_present=True), "stream_bytes_each": 65536,
        "Native_outcome": "NOT_INFERRED", "automatic_retry": False}
    controller = {**common, "pid": 601, "uid": [1001] * 3, "gid": [1001] * 3, "groups": [],
        "membership": ["0::/motionwright-fixedtxt-12345-1/controller"],
        "privileges_dropped_before_exec": True}
    spawned = {**common, "pid": 602, "automatic_retry": False}
    coordinator = {**common, "pid": 602, "parent_pid": 601, "uid": [1001] * 3, "gid": [1001] * 3,
        "argv": argv, "membership": ["0::/motionwright-fixedtxt-12345-1/coordinator"],
        "Native_authority_granted": False, "automatic_retry": False}
    streams = {"stdout": b"private-seal-stdout\n", "stderr": b""}
    terminal = {**common, "schema": "motionwright.native.owner-resource-terminal/1", "scope": "fixedtxt",
        "action": "seal-session", "case_index": None, "controller_pid": 601, "child_pid": 602,
        "exit_code": 0, "reason": None, "first_error_type": None, "secondary_error_types": [],
        "killed_owned_leaves": [], "surviving_owned_leaves": [], "original_complete": True,
        "after": kernel(origin, controller_present=True),
        "minimum_physical_available_bytes": 2 * resources.RESERVE,
        "streams": {name: {"eof": True, "observed_bytes": len(raw), "retained_bytes": len(raw),
                          "sha256": post.descriptor(raw)["sha256"]} for name, raw in streams.items()},
        "Native_outcome": "NOT_INFERRED", "automatic_retry": False}
    returned = {**common, "schema": "motionwright.native.owner-resource-returned/1",
        "action": "seal-session", "case_index": None, "observed": kernel(origin, controller_present=False),
        "all_owned_groups_empty": True, "Native_outcome": "NOT_INFERRED", "automatic_retry": False}
    originals = {"owner_resources/origin.original.json": origin_raw}
    for name, value in zip(resources.CLOSED_FILES, (intent, controller, spawned, coordinator, terminal, returned)):
        originals[post.PREFIX + name] = post.canonical(value)
    for name, raw in streams.items():
        originals[post.PREFIX + name + ".original"] = raw
    manifest = {"schema": "motionwright.native.fixedtxt-resource-custody-manifest/1", "context": CONTEXT,
        "case_index": None, "request": post.descriptor(template), "origin": origin_ref,
        "active_action": "seal-session", "active_guard_completion_claimed": False, "previous_custody": [],
        "originals": [{"path": "commands/seal-session/" + name,
                      **post.descriptor(originals[post.PREFIX + name])} for name in resources.PREFIX_FILES],
        "failures": [], "Native_outcome": "NOT_INFERRED", "automatic_retry": False}
    originals.update({
        "session/session-clock.original.json": post.canonical({"schema": SESSION_SCHEMA, "context": CONTEXT,
            "boot_id": BOOT, "started_monotonic": start, "deadline_monotonic": end,
            "template": post.descriptor(template), "roster": list(ROSTER)}),
        "session/session-template.original.json": template,
        # The LIVE entry separately invokes the existing runtime dispatch validator.
        # This pure contract fixture does not manufacture that authority or run it.
        "session/runtime-bootstrap.original.json": post.canonical({"fixture_only": True, "recipient": recipient}),
        "session/resource-session-manifest.original.json": post.canonical(manifest),
        "session/session-public.json": post.canonical({"schema": "motionwright.native.fixedtxt-session-public/1",
            "context": CONTEXT, "sealed_originals": post.descriptor(cipher),
            "recipient_sha256": recipient["sha256"], "original_files": 100, "native_acceptance": False,
            "G2_accepted": False, "F_accepted": False, "automatic_retry": False}),
    })
    kwargs = {"current": copy.deepcopy(CONTEXT), "recipient_sha256": recipient["sha256"],
        "helper_sha256": HELPER_SHA, "observed_monotonic": start + 3899, "boot_id": BOOT}
    return originals, kwargs


class PostsealCustodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ephemeral, in-memory test key only. No recovery key or Native data is read.
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        der = cls.key.public_key().public_bytes(serialization.Encoding.DER,
                                              serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.recipient = {"spki_der_base64": base64.b64encode(der).decode(),
                         "sha256": hashlib.sha256(der).hexdigest()}
        cls.cipher = encrypt_snapshot(b"synthetic-original-session-capsule", cls.recipient, CONTEXT)

    def setUp(self):
        self.originals, self.kwargs = fixture(self.cipher, self.recipient)

    def validate(self, originals=None, cipher=None, **changes):
        args = {**self.kwargs, **changes}
        return post.validate_originals(self.originals if originals is None else originals,
                                      self.cipher if cipher is None else cipher, **args)

    def mutate(self, path, change):
        values = copy.deepcopy(self.originals)
        item = json.loads(values[path])
        change(item)
        values[path] = post.canonical(item)
        return values

    def test_closed_guard_round_trip_keeps_literal_originals_and_false_native_credit(self):
        result = self.validate()
        self.assertTrue(result["guard"]["guard_completion_observed"])
        self.assertFalse(result["all_session_cases_verified_by_observer"])
        self.assertFalse(result["observer_completion_claimed"])
        self.assertFalse(result["session_ciphertext_AEAD_verified_by_observer"])
        self.assertEqual(result["Native_outcome"], "NOT_INFERRED")
        plain = post.pack_snapshot(self.originals, result, {"fixture_only": True, "observer_completion_claimed": False})
        envelope = encrypt_snapshot(plain, self.recipient, CONTEXT)
        self.assertLessEqual(len(envelope), post.MAX_POSTSEAL)
        self.assertNotIn(b"private-seal-stdout", envelope)
        recovered, context = decrypt_snapshot(envelope, self.key)
        self.assertEqual(recovered, plain)
        self.assertEqual(context, CONTEXT)
        with tarfile.open(fileobj=io.BytesIO(recovered), mode="r:") as archive:
            self.assertEqual(set(archive.getnames()), {*post.LIMITS, "snapshot-manifest.json"})
            for name, raw in self.originals.items():
                self.assertEqual(archive.extractfile(name).read(), raw)
            manifest = json.load(archive.extractfile("snapshot-manifest.json"))
            self.assertEqual(manifest["verification"]["session_ciphertext"], post.descriptor(self.cipher))
            self.assertTrue(all(manifest[name] is False for name in ("native_acceptance", "G2_accepted", "F_accepted", "automatic_retry")))

    def test_source_run_attempt_recipient_and_helper_crosses_reject(self):
        for key, value in (("source_sha", "d" * 40), ("semwright_sha", "e" * 40),
                           ("run_id", "99999"), ("run_attempt", "2")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate(current={**CONTEXT, key: value})
        for key in ("recipient_sha256", "helper_sha256"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate(**{key: "0" * 64})

    def test_only_session_owner_and_exact_seal_command_are_accepted(self):
        for selection in ({"job": "fixedtxt-original-owner"}, {"argv": ["/bin/true"]}):
            values, args = fixture(self.cipher, self.recipient, **selection)
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                post.validate_originals(values, self.cipher, **args)

    def test_missing_incomplete_failed_or_live_guard_rejects_closure(self):
        for name in ("terminal.original.json", "returned.original.json"):
            values = copy.deepcopy(self.originals)
            del values[post.PREFIX + name]
            with self.subTest(missing=name), self.assertRaises(ValueError):
                self.validate(values)
        for key, value in (("original_complete", False), ("exit_code", 1),
                           ("first_error_type", "OriginalFailure"), ("surviving_owned_leaves", ["work"])):
            values = self.mutate(post.PREFIX + "terminal.original.json", lambda row: row.__setitem__(key, value))
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate(values)
        values = self.mutate(post.PREFIX + "returned.original.json",
                             lambda row: row.__setitem__("all_owned_groups_empty", False))
        with self.assertRaises(ValueError):
            self.validate(values)

    def test_stream_change_truncation_overflow_or_missing_eof_rejects(self):
        for raw in (b"private-seal-stdout", b"changed-seal-stdout\n", b"x" * (post.MAX_JSON + 1)):
            values = copy.deepcopy(self.originals)
            values[post.PREFIX + "stdout.original"] = raw
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                self.validate(values)
        values = self.mutate(post.PREFIX + "terminal.original.json",
                            lambda row: row["streams"]["stdout"].__setitem__("eof", False))
        with self.assertRaises(ValueError):
            self.validate(values)

    def test_cipher_or_original_public_modification_rejects(self):
        changed = self.cipher[:-1] + bytes([self.cipher[-1] ^ 1])
        with self.assertRaises(ValueError):
            self.validate(cipher=changed)
        values = self.mutate("session/session-public.json",
                            lambda row: row.__setitem__("G2_accepted", True))
        with self.assertRaises(ValueError):
            self.validate(values)
        other = encrypt_snapshot(b"other-original", self.recipient, {**CONTEXT, "run_id": "99999"})
        values = self.mutate("session/session-public.json",
                            lambda row: row.__setitem__("sealed_originals", post.descriptor(other)))
        with self.assertRaises(ValueError):
            self.validate(values, cipher=other)
        from ci_private_evidence import MAGIC
        offset = len(MAGIC)
        size = int.from_bytes(self.cipher[offset:offset + 4], "big")
        body = self.cipher[offset + 4 + size:]
        for key, value in (("nonce", "!"), ("wrapped_key", base64.b64encode(b"short").decode()),
                           ("plaintext_sha256", "z" * 64)):
            header = json.loads(self.cipher[offset + 4:offset + 4 + size])
            header[key] = value
            aad = post.canonical(header)
            malformed = MAGIC + len(aad).to_bytes(4, "big") + aad + body
            values = self.mutate("session/session-public.json",
                                lambda row: row.__setitem__("sealed_originals", post.descriptor(malformed)))
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate(values, cipher=malformed)

    def test_original_manifest_prefix_origin_or_context_crosses_reject(self):
        mutations = (
            lambda row: row.__setitem__("origin", {"bytes": 1, "sha256": "0" * 64}),
            lambda row: row["originals"][0].__setitem__("sha256", "0" * 64),
            lambda row: row["originals"].append(row["originals"][0]),
            lambda row: row.__setitem__("context", {**CONTEXT, "run_id": "99999"}),
            lambda row: row.__setitem__("active_guard_completion_claimed", True),
        )
        for index, change in enumerate(mutations):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.validate(self.mutate("session/resource-session-manifest.original.json", change))

    def test_original_float_clock_uses_exact_addition_without_tolerance_or_extension(self):
        start = 1096.094195524
        self.assertNotEqual((start + 3900) - start, 3900)
        self.validate()
        for end in (math.nextafter(start + 3900, math.inf), math.nextafter(start + 3900, -math.inf)):
            values, args = fixture(self.cipher, self.recipient, start=start, end=end)
            with self.subTest(end=end), self.assertRaises(ValueError):
                post.validate_originals(values, self.cipher, **args)
        for now in (start - 1, start + 3900, float("nan"), float("inf"), True):
            with self.subTest(now=now), self.assertRaises(ValueError):
                self.validate(observed_monotonic=now)
        with self.assertRaises(ValueError):
            self.validate(boot_id="another-boot")

    def test_roster_extra_member_empty_metadata_and_bound_fail_closed(self):
        for key, value in (("unselected/private.txt", b"not-authorized"),
                           ("session/session-clock.original.json", b""),
                           ("session/resource-session-manifest.original.json", b"x" * (post.MAX_MANIFEST + 1))):
            values = copy.deepcopy(self.originals)
            values[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate(values)

    def publication_boundary_parts(self):
        # Literal synthetic byte strings for budget arithmetic only; no Native
        # custody or cryptographic validity is inferred from these fixtures.
        old_public = b"synthetic-session-public\n"
        public = b"synthetic-postseal-public\n"
        envelope = b"n" * (post.MIB - len(public))
        old_cipher = b"o" * (post.MAX_SESSION_CIPHER - len(old_public) - len(envelope) - len(public))
        return old_cipher, old_public, envelope, public

    def test_publication_budget_accepts_exact_existing_session_ceiling(self):
        old_cipher, old_public, envelope, public = self.publication_boundary_parts()
        self.assertEqual(sum(map(len, (old_cipher, old_public, envelope, public))), post.MAX_SESSION_CIPHER)
        for original in (old_cipher[:-1], old_cipher):
            with self.subTest(original_bytes=len(original)):
                post.validate_publication_budget(post.descriptor(original), post.descriptor(old_public),
                                                 envelope, public)

    def test_publication_budget_rejects_one_extra_byte_in_each_of_four_files(self):
        parts = self.publication_boundary_parts()
        for index in range(len(parts)):
            changed = list(parts)
            changed[index] += b"x"
            old_cipher, old_public, envelope, public = changed
            with self.subTest(file_index=index), self.assertRaisesRegex(
                    post.PostsealRejected, "^postseal_session_custody_aggregate_bound$"):
                post.validate_publication_budget(post.descriptor(old_cipher), post.descriptor(old_public),
                                                 envelope, public)

    def test_publication_budget_preserves_own_two_mib_ceiling(self):
        verified = self.validate()
        public = b"synthetic-postseal-public\n"
        envelope = b"n" * (post.MAX_POSTSEAL - len(public))
        post.validate_publication_budget(verified["session_ciphertext"], verified["session_public"],
                                         envelope, public)
        with self.assertRaisesRegex(post.PostsealRejected, "^postseal_output_bound$"):
            post.validate_publication_budget(verified["session_ciphertext"], verified["session_public"],
                                             envelope + b"x", public)

    def test_publication_budget_rejects_invalid_original_descriptors(self):
        verified = self.validate()
        originals = [verified["session_ciphertext"], verified["session_public"]]
        for index, maximum in enumerate((post.MAX_SESSION_CIPHER, post.MAX_JSON)):
            for value in (True, 1.0, 0, -1, maximum + 1):
                changed = copy.deepcopy(originals)
                changed[index]["bytes"] = value
                with self.subTest(index=index, bytes=value), self.assertRaisesRegex(
                        post.PostsealRejected, "^postseal_original_budget_descriptor$"):
                    post.validate_publication_budget(*changed, b"synthetic-envelope", b"synthetic-public")
            for change in (lambda row: row.pop("sha256"),
                           lambda row: row.__setitem__("sha256", "not-a-digest"),
                           lambda row: row.__setitem__("unexpected", 1)):
                changed = copy.deepcopy(originals)
                change(changed[index])
                with self.subTest(index=index, descriptor=changed[index]), self.assertRaisesRegex(
                        post.PostsealRejected, "^postseal_original_budget_descriptor$"):
                    post.validate_publication_budget(*changed, b"synthetic-envelope", b"synthetic-public")

    def test_publication_budget_rejection_creates_no_directory_or_files(self):
        old_cipher, old_public, envelope, public = self.publication_boundary_parts()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "postseal-output"
            self.assertFalse(directory.exists())
            # The budget must reject before any platform-specific output handles
            # or ownership are used. No physical evidence is mocked or claimed.
            with self.assertRaisesRegex(post.PostsealRejected, "^postseal_session_custody_aggregate_bound$"):
                post.publish_closed_envelope(directory, envelope + b"x", public, uid=0, gid=0,
                    session_ciphertext=post.descriptor(old_cipher), session_public=post.descriptor(old_public))
            self.assertFalse(directory.exists())
            self.assertEqual(list(root.iterdir()), [])

    def test_current_checkout_source_roster_matches_real_git_blobs(self):
        from native_evidence_projection import RootHandles, SelectionError
        if os.name != "posix":
            with self.assertRaises(SelectionError):
                RootHandles({"source": "/unsupported-on-this-platform"})
            return
        import subprocess
        import time
        repo = TOOLING.parent
        source = subprocess.run(["/usr/bin/git", "--no-replace-objects", "-C", str(repo),
                                 "rev-parse", "HEAD"], check=True, capture_output=True,
                                timeout=10).stdout.decode().strip()
        held = RootHandles({"source": str(repo)})
        try:
            bindings, marks = post.source_bindings(held, {"source_sha": source}, time.monotonic() + 30)
            self.assertEqual(bindings[post.SELF],
                             post.descriptor((TOOLING / "native_fixedtxt_postseal_custody.py").read_bytes()))
            self.assertIn("tooling/native_evidence_projection.py", bindings)
            self.assertIn("tooling/native_owner_resources.py", bindings)
            self.assertEqual(set(marks), {("source", name) for name in bindings})
            post.unchanged(held, marks)
        finally:
            held.close()

    def test_private_input_links_mutation_and_root_rebinding_cannot_be_published(self):
        from native_evidence_projection import RootHandles, SelectionError
        if os.name != "posix":
            with self.assertRaises(SelectionError):
                RootHandles({"resources": "/unsupported-on-this-platform"})
            return
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "resources"
            root.mkdir(mode=0o700)
            member = root / "terminal.original.json"
            member.write_bytes(b"original")
            member.chmod(0o600)
            held = RootHandles({"resources": str(root)})
            try:
                _, mark = held.read("resources", (member.name,), 64)
                member.write_bytes(b"changed!")
                with self.assertRaises(ValueError):
                    post.unchanged(held, {("resources", member.name): mark})
                link = root / "linked.original.json"
                link.symlink_to(member)
                with self.assertRaises(SelectionError):
                    held.read("resources", (link.name,), 64)
                link.unlink()
            finally:
                held.close()
            held = RootHandles({"resources": str(root)})
            try:
                _, mark = held.read("resources", (member.name,), 64)
                root.rename(Path(temporary) / "retained-old-root")
                root.mkdir(mode=0o700)
                with self.assertRaises(ValueError):
                    post.unchanged(held, {("resources", member.name): mark})
            finally:
                held.close()
            output = Path(temporary) / "sealed-output"
            verified = self.validate()
            post.publish_closed_envelope(output, self.cipher, b"public-test-fixture\n",
                                         uid=os.geteuid(), gid=os.getegid(),
                                         session_ciphertext=verified["session_ciphertext"],
                                         session_public=verified["session_public"])
            self.assertEqual((output / "sealed-postseal-originals.bin").read_bytes(), self.cipher)
            self.assertEqual((output / "postseal-public.json").read_bytes(), b"public-test-fixture\n")
            with self.assertRaises(FileExistsError):
                post.publish_closed_envelope(output, self.cipher, b"public-test-fixture\n",
                                             uid=os.geteuid(), gid=os.getegid(),
                                             session_ciphertext=verified["session_ciphertext"],
                                             session_public=verified["session_public"])

    def test_observer_manifest_budget_and_previous_native_failure_stay_separate(self):
        result = self.validate()
        # The pre-existing session may have failed a case; a successful sealer
        # guard still cannot authorize or assert that Native case's success.
        self.assertFalse(result["all_session_cases_verified_by_observer"])
        with self.assertRaises(ValueError):
            post.pack_snapshot(self.originals, result, {"oversized": "x" * post.MAX_MANIFEST})
        class BrokenClose:
            def close(self):
                raise OSError("synthetic descriptor close failure")
        first = post.PostsealRejected("postseal_original_changed_after_capture")
        try:
            try:
                raise first
            finally:
                post.close_preserving_primary(BrokenClose())
        except BaseException as observed:
            self.assertIs(observed, first)
            self.assertIn("OSError", observed.__notes__[0])
        with self.assertRaises(OSError):
            post.close_preserving_primary(BrokenClose())


if __name__ == "__main__":
    unittest.main()
