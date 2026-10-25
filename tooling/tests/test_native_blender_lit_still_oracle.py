"""Synthetic byte/PNG contract tests only; these do not execute Blender or FFmpeg."""
from __future__ import annotations

import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_blender_lit_still_oracle as oracle


def opaque_black() -> bytearray:
    return bytearray(b"\0\0\0\xff" * (1920 * 1080))


def rectangle(data: bytearray, x: int, y: int, w: int, h: int, rgb: tuple[int, int, int]):
    row = bytes((*rgb, 255)) * w
    for yy in range(y, y + h):
        start = (yy * 1920 + x) * 4
        data[start:start + len(row)] = row


def picture(gold_x=300, gold_y=200, *, blue=True, gold=True):
    data = opaque_black()
    if gold:
        rectangle(data, gold_x, gold_y, 400, 300, (200, 130, 40))
    if blue:
        rectangle(data, 1000, 400, 240, 240, (50, 100, 170))
    for y in range(250, 430):
        dy = y + 0.5 - 340
        half = math.sqrt(max(0, 90 * 90 - dy * dy))
        x0 = math.ceil(1390 - half - 0.5)
        x1 = math.floor(1390 + half - 0.5)
        if x1 >= x0:
            rectangle(data, x0, y, x1 - x0 + 1, 1, (180, 60, 100))
    return bytes(data)


def chunk(kind, payload=b""):
    return (len(payload).to_bytes(4, "big") + kind + payload
            + (zlib.crc32(kind + payload) & 0xffffffff).to_bytes(4, "big"))


def framed_png(*, width=1920, height=1080, color=6, before=b"", tail=b""):
    # This deliberately tests framing only. It is not a decoded PNG or native artifact.
    header = struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + before
            + chunk(b"IDAT", b"synthetic-not-codec-evidence") + chunk(b"IEND") + tail)


class PixelContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = picture()
        cls.edited = picture(325)
        cls.black = bytes(opaque_black())

    def images(self, **changes):
        return dict({"original": self.original, "edited": self.edited,
                     "reopened": self.edited, "light_off": self.black}, **changes)

    def test_complete_predeclared_pixels_and_exact_centroid(self):
        r = oracle.observe_rgba(self.images())
        self.assertTrue(r["all_pixel_oracles_observed"])
        self.assertEqual(r["gold_displacement_exact"],
                         {"x": {"num": 25, "den": 1}, "y": {"num": 0, "den": 1}})
        self.assertEqual(r["complete_pixels_per_image"], 2073600)
        self.assertEqual(r["rgba_bytes_per_image"], 8294400)
        self.assertEqual(r["decoded_sha256"]["original"], oracle.sha256(self.original))
        self.assertFalse(r["formal_acceptance"])
        self.assertFalse(r["human_acceptance"])

    def test_missing_or_extra_role_cannot_be_accepted(self):
        missing = self.images()
        del missing["reopened"]
        for images in (missing, {**self.images(), "replacement": self.edited}):
            with self.subTest(keys=sorted(images)):
                with self.assertRaises(oracle.ObservationError):
                    oracle.observe_rgba(images)

    def test_truncated_last_pixel_is_rejected(self):
        with self.assertRaises(oracle.ObservationError):
            oracle.observe_rgba(self.images(original=self.original[:-4]))

    def test_nonopaque_last_pixel_is_rejected_even_outside_rois(self):
        changed = bytearray(self.original)
        changed[-1] = 254
        with self.assertRaises(oracle.ObservationError):
            oracle.observe_rgba(self.images(original=bytes(changed)))

    def test_reopen_difference_in_last_pixel_fails_full_equality(self):
        changed = bytearray(self.edited)
        changed[-4] = 1
        r = oracle.observe_rgba(self.images(reopened=bytes(changed)))
        self.assertFalse(r["checks"]["edited_reopened_exact"])
        self.assertFalse(r["all_pixel_oracles_observed"])

    def test_light_control_last_pixel_cannot_escape_sampling(self):
        changed = bytearray(self.black)
        changed[-2] = 1
        r = oracle.observe_rgba(self.images(light_off=bytes(changed)))
        self.assertFalse(r["checks"]["light_off_black"])

    def test_no_edit_fails_both_strips_and_displacement(self):
        r = oracle.observe_rgba(self.images(edited=self.original, reopened=self.original))
        for name in ("gold_old_strip_removed", "gold_new_strip_gained", "gold_displacement_centroid"):
            self.assertFalse(r["checks"][name], name)

    def test_predeclared_centroid_tolerance_is_inclusive(self):
        for shift in (24, 26):
            with self.subTest(shift=shift):
                edited = picture(300 + shift)
                r = oracle.observe_rgba(self.images(edited=edited, reopened=edited))
                self.assertTrue(r["all_pixel_oracles_observed"])

    def test_two_pixel_horizontal_error_is_rejected(self):
        for shift in (23, 27):
            with self.subTest(shift=shift):
                edited = picture(300 + shift)
                r = oracle.observe_rgba(self.images(edited=edited, reopened=edited))
                self.assertFalse(r["checks"]["gold_displacement_centroid"])

    def test_vertical_error_is_not_hidden_by_correct_x(self):
        edited = picture(325, 202)
        r = oracle.observe_rgba(self.images(edited=edited, reopened=edited))
        self.assertFalse(r["checks"]["gold_displacement_centroid"])

    def test_empty_original_cannot_pass_black_and_reopen_controls(self):
        r = oracle.observe_rgba(self.images(original=self.black))
        self.assertFalse(r["checks"]["original_not_black"])
        self.assertFalse(r["checks"]["gold_displacement_centroid"])

    def test_blue_anchor_is_independently_required(self):
        r = oracle.observe_rgba(self.images(original=picture(blue=False)))
        self.assertFalse(r["checks"]["original_not_black"])

    def test_lost_unedited_object_cannot_hide_in_identical_reopened_pixels(self):
        for role, box in (("blue", (1000, 400, 240, 240)),
                          ("rose_disk", (1300, 250, 180, 180))):
            with self.subTest(role=role):
                changed = bytearray(self.edited)
                rectangle(changed, *box, (0, 0, 0))
                changed = bytes(changed)
                observed = oracle.observe_rgba(self.images(edited=changed, reopened=changed))
                self.assertFalse(observed["checks"]["unedited_interiors_visible"])
                self.assertFalse(observed["all_pixel_oracles_observed"])
                self.assertTrue(all(value for key, value in observed["checks"].items()
                                    if key != "unedited_interiors_visible"))
                for phase in ("edited", "reopened"):
                    self.assertEqual(observed["unedited_interiors"][phase][role]["foreground"], 0)

    def test_unedited_visibility_requires_each_phase_at_the_integer_boundary(self):
        for role in ("blue", "rose_disk"):
            x0, y0, x1, y1 = oracle.INTERIORS[role]
            total = (x1 - x0) * (y1 - y0)
            minimum = (95 * total + 99) // 100
            for phase in ("edited", "reopened"):
                for foreground, expected in ((minimum, True), (minimum - 1, False)):
                    with self.subTest(role=role, phase=phase, foreground=foreground):
                        changed = bytearray(self.edited)
                        remaining = total - foreground
                        for y in range(y0, y1):
                            take = min(x1 - x0, remaining)
                            if take == 0:
                                break
                            rectangle(changed, x0, y, take, 1, (0, 0, 0))
                            remaining -= take
                        self.assertEqual(remaining, 0)
                        observed = oracle.observe_rgba(self.images(**{phase: bytes(changed)}))
                        self.assertEqual(observed["checks"]["unedited_interiors_visible"], expected)
                        self.assertEqual(
                            observed["unedited_interiors"][phase][role]["foreground"], foreground)

    def test_interior_foreground_threshold_uses_integer_boundary(self):
        # Gold interior has117216pixels. Removing5860 stays>=95%;5861 falls below.
        for removed, expected in ((5860, True), (5861, False)):
            with self.subTest(removed=removed):
                data = bytearray(self.original)
                remaining = removed
                for y in range(202, 498):
                    take = min(396, remaining)
                    if not take:
                        break
                    rectangle(data, 302, y, take, 1, (0, 0, 0))
                    remaining -= take
                r = oracle.observe_rgba(self.images(original=bytes(data)))
                self.assertEqual(r["checks"]["original_not_black"], expected)


class PngFramingContracts(unittest.TestCase):
    def test_full_crc_framing_does_not_claim_codec_decode(self):
        r = oracle.inspect_png(framed_png())
        self.assertTrue(r["full_crc_verified"])
        self.assertEqual(r["chunks"], ["IHDR", "IDAT", "IEND"])
        self.assertNotIn("all_pixels_decoded", r)

    def test_corrupt_crc_is_rejected(self):
        data = bytearray(framed_png())
        data[-1] ^= 1
        with self.assertRaises(oracle.ObservationError):
            oracle.inspect_png(bytes(data))

    def test_wrong_dimensions_and_format_are_rejected(self):
        for data in (framed_png(width=1919), framed_png(height=1079), framed_png(color=2)):
            with self.assertRaises(oracle.ObservationError):
                oracle.inspect_png(data)

    def test_trailing_bytes_or_second_png_are_rejected(self):
        for tail in (b"x", framed_png()):
            with self.assertRaises(oracle.ObservationError):
                oracle.inspect_png(framed_png(tail=tail))

    def test_apng_extension_cannot_be_silently_first_frame_only(self):
        with self.assertRaises(oracle.ObservationError):
            oracle.inspect_png(framed_png(before=chunk(b"acTL", struct.pack(">II", 2, 0))))

    def test_duplicate_ihdr_is_rejected(self):
        duplicate = chunk(b"IHDR", struct.pack(">IIBBBBB", 1920, 1080, 8, 6, 0, 0, 0))
        with self.assertRaises(oracle.ObservationError):
            oracle.inspect_png(framed_png(before=duplicate))

    def test_missing_iend_or_oversized_chunk_is_rejected(self):
        data = framed_png()
        for bad in (data[:-12], data[:33] + b"\xff\xff\xff\xffIDAT" + data[41:]):
            with self.assertRaises(oracle.ObservationError):
                oracle.inspect_png(bad)

    def test_noncontiguous_idat_is_rejected(self):
        data = framed_png()
        bad = data[:-12] + chunk(b"tEXt", b"fixture") + chunk(b"IDAT", b"more") + data[-12:]
        with self.assertRaises(oracle.ObservationError):
            oracle.inspect_png(bad)

    def test_unknown_critical_chunk_is_rejected(self):
        with self.assertRaises(oracle.ObservationError):
            oracle.inspect_png(framed_png(before=chunk(b"ZZZZ", b"fixture")))


class CustodyContracts(unittest.TestCase):
    def test_intent_writer_preserves_original_bytes_on_second_attempt(self):
        with tempfile.TemporaryDirectory() as raw:
            p = Path(raw) / "once.json"
            oracle._write_new(p, {"attempt": "original"})
            before = p.read_bytes()
            with self.assertRaises(FileExistsError):
                oracle._write_new(p, {"attempt": "replacement"})
            self.assertEqual(p.read_bytes(), before)
            self.assertEqual(json.loads(before), {"attempt": "original"})

    def test_input_fd_opens_only_a_regular_canonical_bounded_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            p = root / "source"
            p.write_bytes(b"fixture")
            with oracle._regular(p, 7) as f:
                self.assertEqual(f.read(), b"fixture")
            for bad, maximum in ((p, 6), (root, 100)):
                with self.assertRaises(oracle.ObservationError):
                    oracle._regular(bad, maximum)


# These controller fixtures exercise evidence validation only. They never invoke
# the canonical Host and cannot be submitted as physical phase observations.
import copy
import native_blender_lit_still_e2e as producer


def receipt_fixture():
    source = {"id": "fixture-project", "generation": "fixture-generation", "revision": 7}
    source_bytes = json.dumps(source).encode()
    plan = {"source": {"contribution": {
        "project_id": source["id"], "generation": source["generation"], "revision": source["revision"]}}}
    request = {"request_id": "fixture", "phase": "create"}
    command = "driver.blender.scene.inspect"
    request_id = "fixture:lit-create:000001"
    response = {"provenance_checked": True, "execution": {
        "backend": "fixture-only", "dry_run": False, "provenance": {
            "provider": "driver:blender", "source": "driver", "execution_provider": "driver:blender",
            "untrusted_metadata": False, "descriptor_sha256": "a" * 64, "provider_generation": 4}},
        "data": {"synthetic_unit_fixture": True}}
    common = {"project_id": source["id"], "generation": source["generation"],
              "revision": source["revision"], "command": command,
              "request_id": request_id, "request_sha256": "b" * 64}
    completed = {**common, "id": "completed-one", "stage": "completed",
                 "payload": {"broker_request_id": "unit-broker-one", "result": copy.deepcopy(response)}}
    dispatch = {**common, "id": "dispatch-one", "stage": "dispatching", "payload": {}}
    journal = {"schema": "motionwright.blender.lit-still/1", "phase": "lit-create", "plan": plan,
               "calls": [{"sequence": 1, "request_id": request_id, "command": command, "args": {},
                          "mutation": False, "native_error": None,
                          "native_result": {"replayed": False, "receipt": copy.deepcopy(completed),
                                            "result": copy.deepcopy(response)}}]}
    persisted = {"request": request, "journal": journal, "custody": {
        "sqlite_reopened": True, "project_unchanged": True, "project": source,
        "production": {"complete": True, "watermark": {"count": 2, "latest_id": completed["id"]},
                       "receipts": [completed, dispatch]}}}
    return persisted, request, plan, source_bytes


def ack_fixture():
    value = {"schema": "motionwright.blender.lit-still-phase-evidence/1",
        "fixture_contract_sha256": oracle.FIXTURE_CONTRACT_SHA256,
        "phase_contract_verified": False, "custody_errors": [],
        "decoded_pixel_verification": False, "human_acceptance": False,
        "native_terminal": {"status": "ok", "value": {"synthetic_unit_fixture": True}},
        "coordinator_append_custody": {
            "schema": "motionwright.production.private-receipt-custody/1", "durable": False,
            "mutex_poisoned": False, "reserved_slots": 0, "reserved_serialized_bytes": 0,
            "slot_limit": 8, "serialized_byte_limit": 32 * 1024 * 1024,
            "pending_without_terminal": 0, "entries": []}}
    body = json.dumps(value).encode()
    ack = {"schema": "motionwright.blender.lit-still-phase-ack/1", "phase": "create",
           "phase_contract_verified": True, "evidence": producer.binding(body),
           "decoded_pixel_verification": False, "formal_acceptance": False}
    return value, body, ack


class ProducerReceiptContracts(unittest.TestCase):
    def test_real_wire_stage_names_keep_original_dispatch_completion_pair(self):
        fixture = receipt_fixture()
        calls, summary = producer.receipt_calls(*fixture)
        self.assertEqual(len(calls), 1)
        self.assertEqual(summary["complete_receipt_count"], 2)
        self.assertEqual(summary["provider_generation"], 4)

    def test_original_unknown_older_than_256_rows_is_not_replaced_by_completion(self):
        p, request, plan, body = receipt_fixture()
        rows = p["custody"]["production"]["receipts"]
        rows.extend({"id": f"unrelated-{i}", "request_id": f"other-{i}",
                     "stage": "completed"} for i in range(300))
        unknown = copy.deepcopy(rows[0])
        unknown.update(id="original-unknown", stage="outcome_unknown")
        rows.append(unknown)
        p["custody"]["production"]["watermark"]["count"] = len(rows)
        with self.assertRaisesRegex(producer.ProducerError, "unresolved-or-failed-history"):
            producer.receipt_calls(p, request, plan, body)

    def test_unrelated_unknown_does_not_change_this_phase_authority(self):
        p, request, plan, body = receipt_fixture()
        p["custody"]["production"]["receipts"].append({
            "id": "unrelated-unknown", "request_id": "other:phase", "stage": "outcome_unknown"})
        p["custody"]["production"]["watermark"]["count"] = 3
        self.assertEqual(producer.receipt_calls(p, request, plan, body)[1]["original_call_count"], 1)

    def test_pending_unjournaled_dispatch_is_not_a_complete_phase(self):
        p, request, plan, body = receipt_fixture()
        extra = copy.deepcopy(p["custody"]["production"]["receipts"][1])
        extra.update(id="extra-pending", request_id="fixture:lit-create:000002")
        p["custody"]["production"]["receipts"].append(extra)
        p["custody"]["production"]["watermark"]["count"] = 3
        with self.assertRaisesRegex(producer.ProducerError, "unjournaled-operation"):
            producer.receipt_calls(p, request, plan, body)

    def test_failed_known_original_is_not_hidden_by_later_completion(self):
        p, request, plan, body = receipt_fixture()
        p["custody"]["production"]["receipts"][1]["stage"] = "failed_known"
        with self.assertRaisesRegex(producer.ProducerError, "unresolved-or-failed-history"):
            producer.receipt_calls(p, request, plan, body)

    def test_missing_original_dispatch_is_rejected(self):
        p, request, plan, body = receipt_fixture()
        p["custody"]["production"]["receipts"].pop()
        p["custody"]["production"]["watermark"]["count"] = 1
        with self.assertRaisesRegex(producer.ProducerError, "dispatch-completion-pair"):
            producer.receipt_calls(p, request, plan, body)

    def test_dispatch_request_hash_substitution_is_rejected(self):
        p, request, plan, body = receipt_fixture()
        p["custody"]["production"]["receipts"][1]["request_sha256"] = "c" * 64
        with self.assertRaisesRegex(producer.ProducerError, "dispatch-completion-pair"):
            producer.receipt_calls(p, request, plan, body)

    def test_native_response_not_equal_to_durable_receipt_is_rejected(self):
        p, request, plan, body = receipt_fixture()
        p["journal"]["calls"][0]["native_result"]["result"]["data"]["replacement"] = True
        with self.assertRaisesRegex(producer.ProducerError, "response-custody-mismatch"):
            producer.receipt_calls(p, request, plan, body)

    def test_replayed_reply_is_rejected(self):
        p, request, plan, body = receipt_fixture()
        p["journal"]["calls"][0]["native_result"]["replayed"] = True
        with self.assertRaisesRegex(producer.ProducerError, "replayed-native-call"):
            producer.receipt_calls(p, request, plan, body)

    def test_duplicate_durable_id_is_rejected(self):
        p, request, plan, body = receipt_fixture()
        p["custody"]["production"]["receipts"][1]["id"] = "completed-one"
        with self.assertRaisesRegex(producer.ProducerError, "duplicate-or-missing-receipt"):
            producer.receipt_calls(p, request, plan, body)

    def test_changed_project_after_real_reopen_is_rejected(self):
        p, request, plan, body = receipt_fixture()
        p["custody"]["project"]["revision"] = 8
        with self.assertRaisesRegex(producer.ProducerError, "sqlite-source-changed"):
            producer.receipt_calls(p, request, plan, body)

    def test_incomplete_watermark_is_rejected(self):
        p, request, plan, body = receipt_fixture()
        p["custody"]["production"]["watermark"]["count"] = 3
        with self.assertRaisesRegex(producer.ProducerError, "snapshot-incomplete"):
            producer.receipt_calls(p, request, plan, body)

    def test_wrong_mutation_authority_is_rejected(self):
        p, request, plan, body = receipt_fixture()
        p["journal"]["calls"][0]["mutation"] = True
        with self.assertRaisesRegex(producer.ProducerError, "command-authority-or-order"):
            producer.receipt_calls(p, request, plan, body)


class ProducerAckContracts(unittest.TestCase):
    def test_synced_ack_is_bound_to_exact_pre_ack_original(self):
        value, body, ack = ack_fixture()
        self.assertEqual(producer.check_ack(ack, body, "create"), value)
        self.assertFalse(value["phase_contract_verified"])

    def test_semantically_identical_reencoded_evidence_is_not_the_original(self):
        value, body, ack = ack_fixture()
        with self.assertRaisesRegex(producer.ProducerError, "ack-not-bound"):
            producer.check_ack(ack, json.dumps(value, indent=2).encode(), "create")

    def test_unknown_terminal_cannot_be_replaced_by_a_later_publication_ack(self):
        value, _, ack = ack_fixture()
        value["native_terminal"] = {"status": "error", "error": {
            "code": "Backend", "outcome_known": False}}
        body = json.dumps(value).encode()
        ack["evidence"] = producer.binding(body)
        with self.assertRaisesRegex(producer.ProducerError, "terminal-not-success"):
            producer.check_ack(ack, body, "create")

    def test_pending_private_terminal_does_not_substitute_for_durable_receipts(self):
        value, _, ack = ack_fixture()
        value["coordinator_append_custody"]["pending_without_terminal"] = 1
        body = json.dumps(value).encode()
        ack["evidence"] = producer.binding(body)
        with self.assertRaisesRegex(ValueError, "custody_unconfirmed"):
            producer.check_ack(ack, body, "create")

    def test_an_ack_cannot_grant_formal_acceptance(self):
        _, body, ack = ack_fixture()
        ack["formal_acceptance"] = True
        with self.assertRaisesRegex(producer.ProducerError, "ack-not-bound"):
            producer.check_ack(ack, body, "create")

    def test_phase_identity_cannot_be_substituted(self):
        _, body, ack = ack_fixture()
        with self.assertRaisesRegex(producer.ProducerError, "ack-not-bound"):
            producer.check_ack(ack, body, "edit")

    def test_duplicate_json_member_is_rejected_even_when_hash_matches(self):
        _, body, ack = ack_fixture()
        body = b'{"schema":"foreign",' + body[1:]
        ack["evidence"] = producer.binding(body)
        with self.assertRaisesRegex(ValueError, "duplicate_member"):
            producer.check_ack(ack, body, "create")


def context_fixture():
    observations = [{"complete": True, "first": {
        "pid": 100 + i, "start_ticks": 1000 + i,
        "daemon_pid": 200 + i, "daemon_start_ticks": 900 + i,
        "workspace_device": 1, "workspace_inode": 10 + i, "blender_sha256": "a" * 64}}
        for i in range(4)]
    return observations, [f"session-{i}" for i in range(4)]


class ProducerContextContracts(unittest.TestCase):
    def test_four_actual_identity_dimensions_must_be_distinct(self):
        self.assertTrue(producer.distinct_contexts(*context_fixture())["four_distinct_native_contexts"])

    def test_reused_workspace_is_rejected(self):
        observations, sessions = context_fixture()
        observations[3]["first"]["workspace_inode"] = observations[0]["first"]["workspace_inode"]
        with self.assertRaisesRegex(producer.ProducerError, "context-reused"):
            producer.distinct_contexts(observations, sessions)

    def test_foreign_runtime_binary_is_rejected(self):
        observations, sessions = context_fixture()
        observations[3]["first"]["blender_sha256"] = "b" * 64
        with self.assertRaisesRegex(producer.ProducerError, "runtime-changed"):
            producer.distinct_contexts(observations, sessions)

    def test_reused_native_session_is_rejected(self):
        observations, sessions = context_fixture()
        sessions[3] = sessions[0]
        with self.assertRaisesRegex(producer.ProducerError, "contexts-not-complete"):
            producer.distinct_contexts(observations, sessions)



class SelectedEvidenceContractTests(unittest.TestCase):
    """Source-only policy and failure-retention checks; no capture or native run."""

    U = "00000000-0000-4000-8000-000000000001"
    G = "00000000-0000-4000-8000-000000000002"

    def policies(self):
        import native_evidence_projection as selection
        policies, roles, entries = producer.selection_contract()
        return selection, policies, roles, entries

    def record(self):
        return {"schema": producer.SCHEMA,
                "fixture_contract_sha256": oracle.FIXTURE_CONTRACT_SHA256,
                "context": {"source_sha": "a" * 40, "semwright_sha": "b" * 40,
                            "run_id": "123456", "run_attempt": "1"},
                "state": "INCOMPLETE", "phases": [], "formal_acceptance": False,
                "human_acceptance": False, "public_upload_performed": False,
                "automatic_retry": False, "elapsed_seconds": 0.2,
                "compiler_execution_requires_original_GHA_evidence": True}

    def seed(self):
        return {"schema": "motionwright.blender.lit-still-seed/1",
                "fixture_kind": "first_party_synthetic",
                "fixture_contract_sha256": oracle.FIXTURE_CONTRACT_SHA256,
                "project_id": self.U, "resource": "project:" + self.U,
                "generation": self.G, "revision": 4,
                "expected": {"resource": "project:" + self.U, "generation": self.G, "revision": 4}, "scene_id": self.U,
                "role_node_ids": {k: self.U for k in ("edited_gold_card", "blue_anchor", "rose_disk")},
                "project_json": {"path": "source-project.json", "bytes": 100, "sha256": "c" * 64},
                "plan_path": "source-plan.json", "sqlite_reopened": True,
                "native_execution": "NOT_RUN", "human_acceptance": False}

    def phase(self):
        return {"schema": "motionwright.blender.lit-still-phase-evidence/1",
                "fixture_contract_sha256": oracle.FIXTURE_CONTRACT_SHA256,
                "request": {"project_id": self.U,
                    "expected": {"resource": "project:" + self.U, "generation": self.G, "revision": 4}, "scene_id": self.U,
                    "request_id": "lit-fixture", "phase": "create", "source": None, "edit_node_id": None},
                "phase_contract_verified": False, "decoded_pixel_verification": False,
                "human_acceptance": False, "custody_errors": [],
                "native_terminal": {"status": "error", "error": {
                    "code": "BackendFailed", "outcome_known": False,
                    "message": "PRIVATE_PROVIDER_MESSAGE", "session_ticket": "PRIVATE_TICKET"}},
                "journal": {"calls": [{"args": {"secret": "PRIVATE_ARGUMENT"}}]}}

    def manifest(self):
        _, _, _, entries = self.policies()
        return {"members": [{"root": "case", "relative_path": path, "phase": phase,
                             "layer": layer, "role": role, "kind": kind, "policy": policy,
                             "required": True}
                            for path, phase, layer, role, kind, policy in entries],
                "absence_guards": [{"root": "case",
                    "relative_path": "motionwright.sqlite3-" + suffix,
                    "phase": "single", "layer": "persistence", "role": "absent-sqlite-" + suffix,
                    "opened": False, "hashed": False, "absent_before": True, "absent_after": True}
                    for suffix in ("wal", "shm", "journal")],
                "required_layers_complete": True, "source_bytes_bound": True,
                "selected_namespace_stable": True}

    def test_selected_fixed_roster_contains_all_native_media_and_closed_store(self):
        _, _, roles, entries = self.policies()
        self.assertEqual(len(entries), 33)
        paths = [row[0] for row in entries]
        for extension, count in ((".png", 4), (".rgba", 4), (".blend", 2), (".sqlite3", 1)):
            self.assertEqual(sum(path.endswith(extension) for path in paths), count)
        self.assertEqual(len(set(paths)), 33)
        self.assertEqual(sum(value == (1, 1) for value in roles.values()), 33)
        self.assertEqual(sum(value == (0, 0) for value in roles.values()), 3)
        self.assertFalse(any("session" in path or path.endswith((".stdout", ".stderr")) for path in paths))

    def test_pixel_policy_requires_current_schema_and_unedited_visibility(self):
        selection, policies, _, _ = self.policies()
        original, edited = picture(), picture(325)
        observed = oracle.observe_rgba({
            "original": original, "edited": edited, "reopened": edited,
            "light_off": bytes(opaque_black()),
        })
        policy = policies["lit-pixels-v2"]
        projected = selection.project(observed, policy)
        self.assertEqual(projected["schema"], oracle.PIXEL_OBSERVATION_SCHEMA)
        self.assertTrue(projected["checks"]["unedited_interiors_visible"])
        self.assertEqual(selection.project(projected, policy, exact=True), projected)
        missing = copy.deepcopy(projected)
        del missing["checks"]["unedited_interiors_visible"]
        old_schema = copy.deepcopy(projected)
        old_schema["schema"] = "motionwright.blender.lit-still-pixel-observation/1"
        unexpected = copy.deepcopy(projected)
        unexpected["checks"]["unmeasured_color_fidelity"] = True
        for changed in (missing, old_schema, unexpected):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                selection.project(changed, policy, exact=True)

    def test_json_original_seed_rejects_extra_fields_and_forged_acceptance(self):
        selection, policies, _, _ = self.policies()
        seeded = self.seed()
        self.assertEqual(selection.project(seeded, policies["lit-seed-v1"], exact=True), seeded)
        for key, value in (("session_ticket", "secret"), ("human_acceptance", True),
                           ("sqlite_reopened", False), ("fixture_kind", "imported-user-project")):
            changed = copy.deepcopy(seeded)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                selection.project(changed, policies["lit-seed-v1"], exact=True)

    def test_unknown_terminal_survives_projection_without_private_message_or_arguments(self):
        selection, policies, _, _ = self.policies()
        projected = selection.project(self.phase(), policies["lit-phase-v1"])
        self.assertEqual(projected["native_terminal"], {"status": "error", "error": {
            "code": "BackendFailed", "outcome_known": False}})
        self.assertNotIn("journal", projected)
        self.assertNotIn("PRIVATE_", json.dumps(projected))
        self.assertFalse(projected["phase_contract_verified"])

    def test_receipt_projection_keeps_original_unknown_stage_and_drops_payload(self):
        selection, policies, _, _ = self.policies()
        value = self.phase()
        value["custody"] = {"sqlite_reopened": True, "project_unchanged": True, "production": {
            "complete": True, "watermark": {"latest_id": self.U, "count": 1},
            "receipts": [{"id": self.U, "project_id": self.U, "generation": self.G,
                "revision": 4, "request_id": "lit:original", "request_sha256": "d" * 64,
                "command": "driver.blender.render", "stage": "outcome_unknown",
                "payload": {"secret": "PRIVATE_TERMINAL"}, "session": "PRIVATE_SESSION"}]}}
        projected = selection.project(value, policies["lit-phase-v1"])
        row = projected["custody"]["production"]["receipts"][0]
        self.assertEqual(row["stage"], "outcome_unknown")
        self.assertNotIn("payload", row)
        self.assertNotIn("session", row)

    def test_producer_projection_retains_incomplete_and_timeout_without_private_paths(self):
        selection, policies, _, _ = self.policies()
        value = self.record()
        value.update(original_error={"type": "ProducerError", "message": "PRIVATE_PATH"},
                     decoder={"sha256": "e" * 64, "path": "/PRIVATE_PATH"})
        value["phases"] = [{"phase": "create", "state": "ATTEMPTED", "observer": None,
            "verification": None, "command": {"argv": ["PRIVATE_TICKET"],
            "terminal": {"status": "PROCESS_TIMEOUT", "outcome_known": False}}}]
        projected = selection.project(value, policies["lit-producer-v1"])
        self.assertEqual(projected["state"], "INCOMPLETE")
        self.assertEqual(projected["phases"][0]["command"]["terminal"]["status"], "PROCESS_TIMEOUT")
        self.assertNotIn("PRIVATE_", json.dumps(projected))
        self.assertFalse(projected["formal_acceptance"])

    def test_spec_uses_producer_measured_artifact_identity_even_after_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = "create/context/workspace/lit-original.png"
            actual = root / path
            actual.parent.mkdir(parents=True)
            actual.write_bytes(b"replacement is not the original image")
            record = self.record()
            record["selected_original_files"] = [{"path": path, "bytes": 12, "sha256": "a" * 64}]
            spec = producer.build_selection_spec(root, root, record, [])
            member = next(row for row in spec["members"] if row["relative_path"] == path)
            self.assertEqual((member["bytes"], member["expected_sha256"]), (12, "a" * 64))
            self.assertTrue(member["required"])

    def test_spec_does_not_rehash_unobserved_media_into_expected_originals(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "create/context/workspace/lit-original.png"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"unobserved media")
            spec = producer.build_selection_spec(root, root, self.record(), [])
            row = next(row for row in spec["members"] if row["relative_path"].endswith("lit-original.png"))
            self.assertIsNone(row["bytes"])
            self.assertIsNone(row["expected_sha256"])
            self.assertEqual(len(spec["absence_guards"]), 3)
            self.assertEqual(spec["origin"]["upload_authorization"], "NOT_GRANTED_BY_COLLECTOR")

    def test_spec_rgba_digest_comes_only_from_completed_original_decode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = self.record()
            record["decodes"] = {"original": {"state": "COMPLETE",
                "raw_sha256": "f" * 64, "raw_bytes": oracle.RGBA_BYTES}}
            spec = producer.build_selection_spec(root, root, record, [])
            row = next(row for row in spec["members"] if row["relative_path"] == "decoded/original.rgba")
            self.assertEqual((row["bytes"], row["expected_sha256"]), (oracle.RGBA_BYTES, "f" * 64))
            record["decodes"]["original"]["state"] = "INCOMPLETE"
            spec = producer.build_selection_spec(root, root, record, [])
            row = next(row for row in spec["members"] if row["relative_path"] == "decoded/original.rgba")
            self.assertIsNone(row["expected_sha256"])

    def test_family_manifest_refuses_missing_role_or_path_substitution(self):
        from unittest.mock import patch
        with patch("native_evidence_projection.validate_selected_manifest", return_value=None):
            self.assertTrue(producer.validate_lit_manifest(self.manifest(), {})["complete"])
            for change in ("remove", "path", "root", "required", "policy"):
                value = self.manifest()
                if change == "remove":
                    value["members"].pop()
                elif change == "required":
                    value["members"][0]["required"] = False
                else:
                    key = {"path": "relative_path", "root": "root", "policy": "policy"}[change]
                    value["members"][0][key] = "substitution"
                with self.subTest(change=change), self.assertRaises(producer.ProducerError):
                    producer.validate_lit_manifest(value, {})

    def test_family_manifest_refuses_omitted_or_substituted_sqlite_absence_guard(self):
        from unittest.mock import patch
        with patch("native_evidence_projection.validate_selected_manifest", return_value=None):
            for change in ("remove", "path", "opened", "hashed"):
                value = self.manifest()
                if change == "remove":
                    value["absence_guards"].pop()
                elif change == "path":
                    value["absence_guards"][0]["relative_path"] = "unrelated-wal"
                else:
                    value["absence_guards"][0][change] = True
                with self.subTest(change=change), self.assertRaises(producer.ProducerError):
                    producer.validate_lit_manifest(value, {})
            value = self.manifest()
            value["absence_guards"][0]["absent_after"] = False
            self.assertFalse(producer.validate_lit_manifest(value, {})["complete"])

    def test_failed_selection_is_retained_before_completeness_is_interpreted(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = {"schema": "synthetic-incomplete-unit-test", "source_bytes_bound": False}
            with patch("native_evidence_projection.capture_selected_originals",
                       return_value=(b"synthetic retained collector bytes", manifest)), \
                 patch.object(producer, "validate_lit_manifest",
                              side_effect=ValueError("selected-layer-incomplete")):
                result = producer.capture_lit_evidence(root, root, self.record(), [])
            self.assertEqual((root / "selected-evidence.tar").read_bytes(),
                             b"synthetic retained collector bytes")
            self.assertEqual(json.loads((root / "selected-evidence-manifest.json").read_bytes()), manifest)
            self.assertFalse(result["required_layers_complete"])
            self.assertFalse(result["encrypted_or_uploaded"])
            self.assertEqual(result["selection_validation_error"], {"type": "ValueError"})

    def test_projection_rejects_claims_of_formal_or_human_acceptance(self):
        selection, policies, _, _ = self.policies()
        for field in ("formal_acceptance", "human_acceptance", "public_upload_performed", "automatic_retry"):
            value = self.record()
            value[field] = True
            with self.subTest(field=field), self.assertRaises(ValueError):
                selection.project(value, policies["lit-producer-v1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
