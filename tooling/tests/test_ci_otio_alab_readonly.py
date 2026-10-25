"""Offline caller contracts. All media, terminals and admissions are fixtures.

No test starts a real ffprobe, accesses the network, creates an encryption key,
or admits an external source. Positive snapshot controls use the existing A4
intake/capture with explicit I/O doubles and the existing immutable collector.
"""
from __future__ import annotations

import copy
from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
sys.path.insert(0, str(ROOT / "tooling/otio-media"))
import ci_otio_alab_readonly as gate
import test_bridge as owner_controls


def context():
    return {"source_sha": "1" * 40, "semwright_sha": "2" * 40,
            "run_id": "1234567", "run_attempt": "1"}


def source():
    return {"checkout": "1" * 40, "tree": "3" * 40,
            "semwright_sha": "2" * 40, "files": {}}


def environment():
    return {"GITHUB_ACTIONS": "true", "CI": "true", "RUNNER_OS": "Linux",
            "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_JOB": gate.JOB, "MOTIONWRIGHT_KDENLIVE_GUI_SCOPE": gate.SCOPE,
            "GITHUB_SHA": "1" * 40, "GITHUB_RUN_ID": "1234567"}


def admission():
    # This intentionally has no usable recipient key and cannot authorize main().
    return {"schema": gate.ADMISSION_SCHEMA, "authority": "release-integrator",
            "admission_reference": "synthetic-source-control-with-no-external-authority",
            "scope": gate.SCOPE, "job_name": gate.JOB, "source_sha": "1" * 40,
            "source_tree": "3" * 40, "semwright_sha": "2" * 40, "run_attempt": 1,
            "recipient_spki_sha256": "4" * 64, "existing_recipient": True,
            "request_contract_sha256": gate.contract_sha256(), "metadata_only": True,
            "automatic_retry": False, "source_origin": "NOT_OBSERVED",
            "decoded_frames_verified": False, "candidate_admitted": False,
            "human_acceptance": False, "release_authorized": False}


class AdmissionTests(unittest.TestCase):
    def validate(self, value, **kwargs):
        raw = gate.custody.json_bytes(value)
        recipient = {"spki_der_base64": "", "sha256": "4" * 64}
        with patch.object(gate.custody, "public_key") as key:
            result = gate.validate_admission(raw, kwargs.get("digest", gate.custody.sha(raw)),
                                             recipient, source(), context())
            key.assert_called_once_with(recipient)
            return result

    def test_closed_admission_calls_existing_real_key_validator(self):
        self.assertEqual(self.validate(admission()), admission())

    def test_synthetic_recipient_cannot_be_used_as_actual_admission(self):
        raw = gate.custody.json_bytes(admission())
        with self.assertRaises(Exception):
            gate.validate_admission(raw, gate.custody.sha(raw),
                {"spki_der_base64": "", "sha256": "4" * 64}, source(), context())

    def test_absent_or_wrong_frozen_digest_is_rejected(self):
        for digest in ("", "0" * 64, None, True):
            with self.subTest(digest=digest), self.assertRaises(gate.Rejected):
                self.validate(admission(), digest=digest)

    def test_stale_source_tree_or_sdk_cannot_transfer_admission(self):
        for key in ("source_sha", "source_tree", "semwright_sha", "recipient_spki_sha256",
                    "request_contract_sha256"):
            value = admission(); value[key] = "0" * len(value[key])
            with self.subTest(field=key), self.assertRaises(gate.Rejected):
                self.validate(value)

    def test_exact_attempt_and_boolean_types_are_required(self):
        for key, variants in (("run_attempt", [True, 1.0, "1", 2]),
                              ("existing_recipient", [1, "true", False]),
                              ("metadata_only", [1, "true", False]),
                              ("automatic_retry", [0, "false", True])):
            for variant in variants:
                value = admission(); value[key] = variant
                with self.subTest(field=key, value=variant), self.assertRaises(gate.Rejected):
                    self.validate(value)

    def test_human_release_decode_and_origin_promotions_are_refused(self):
        for key, replacement in (("candidate_admitted", True), ("human_acceptance", True),
                                 ("release_authorized", True), ("decoded_frames_verified", True),
                                 ("source_origin", "inferred-from-timecode")):
            value = admission(); value[key] = replacement
            with self.subTest(field=key), self.assertRaises(gate.Rejected):
                self.validate(value)

    def test_other_scope_authority_job_and_missing_fields_are_refused(self):
        for key in ("scope", "authority", "job_name"):
            value = admission(); value[key] = "other"
            with self.subTest(field=key), self.assertRaises(gate.Rejected):
                self.validate(value)
        for value in ({**admission(), "extra": False}, {k: v for k, v in admission().items() if k != "scope"}):
            with self.assertRaises(gate.Rejected): self.validate(value)

    def test_duplicate_json_keys_cannot_override_admission(self):
        raw = gate.custody.json_bytes(admission()).rstrip()[:-1] + b',"scope":"source"}'
        with self.assertRaises(Exception):
            gate.validate_admission(raw, gate.custody.sha(raw), {"sha256": "4" * 64}, source(), context())

    def test_request_integer_attempt_and_seal_string_attempt_are_distinct(self):
        current = context()
        request = gate.request_for(admission(), current)
        self.assertIs(type(request["context"]["run_attempt"]), int)
        self.assertEqual(current["run_attempt"], "1")
        gate.owner.validate_request(request)
        gate.custody.validate_context(current)
        self.assertEqual(request["source_tree"], source()["tree"])


class DispatchTests(unittest.TestCase):
    def test_exact_fresh_manual_linux_nonroot_context_is_admitted(self):
        gate.validate_dispatch(environment(), "linux", 1001, "1" * 40)

    def test_root_windows_boolean_uid_and_other_event_are_rejected(self):
        for platform, uid in (("win32", 1001), ("darwin", 1001), ("linux", 0), ("linux", True)):
            with self.subTest(platform=platform, uid=uid), self.assertRaises(gate.Rejected):
                gate.validate_dispatch(environment(), platform, uid, "1" * 40)
        for key, value in (("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_RUN_ATTEMPT", "2"),
                           ("GITHUB_JOB", "another-job"), ("GITHUB_SHA", "9" * 40),
                           ("MOTIONWRIGHT_KDENLIVE_GUI_SCOPE", "source"), ("GITHUB_RUN_ID", "0")):
            changed = environment(); changed[key] = value
            with self.subTest(field=key), self.assertRaises(gate.Rejected):
                gate.validate_dispatch(changed, "linux", 1001, "1" * 40)

    def test_single_outer_command_carries_exact_context_through_sanitized_environment(self):
        argv = gate.command_argv(Path("/tmp/request"), Path("/tmp/originals"), context())
        self.assertEqual(argv[:6], ["/usr/bin/env", "GITHUB_SHA=" + "1" * 40,
            "GITHUB_RUN_ID=1234567", "GITHUB_RUN_ATTEMPT=1", "GITHUB_JOB=" + gate.JOB,
            "PYTHONDONTWRITEBYTECODE=1"])
        self.assertEqual(argv.count(gate.SCOPE), 1)
        self.assertEqual(argv[-2:], ["--ffprobe", str(gate.FFPROBE)])
        self.assertNotIn(gate.owner.ARCHIVE["url"], argv)
        self.assertFalse(any(value in argv for value in ("ffmpeg", "-count_frames", "--retry")))

    def test_existing_run_tool_is_the_only_outer_runner(self):
        import mlt_official_runtime_intake
        self.assertIs(gate.run_tool, mlt_official_runtime_intake.run_tool)

    def test_original_path_aliases_are_rejected_even_with_matching_snapshot_bytes(self):
        for name in ("original:stream", "CON.txt", "dir/NUL", "dir/file.", "dir/file ", "com7"):
            with self.subTest(path=name), self.assertRaises(gate.Rejected):
                gate.descriptor_bytes({"alab/" + name: b"x"},
                    {"path": name, "bytes": 1, "sha256": gate.custody.sha(b"x")})
        self.assertEqual(gate.descriptor_bytes({"alab/safe/file.bin": b"x"},
            {"path": "safe/file.bin", "bytes": 1, "sha256": gate.custody.sha(b"x")}), b"x")

    def test_owner_roster_contains_all_94_controls_including_linux_symlinks(self):
        def ids(suite):
            if isinstance(suite, unittest.TestSuite):
                for child in suite: yield from ids(child)
            else: yield suite.id()
        roster = list(ids(unittest.defaultTestLoader.loadTestsFromModule(owner_controls)))
        self.assertEqual(len(roster), 94)
        self.assertEqual(len(set(roster)), 94)
        self.assertTrue(any("symlink" in name for name in roster))

    def test_workflow_reuses_existing_job_and_excludes_gui_effects_for_alab(self):
        text = (ROOT / ".github/workflows/v05-otio-real-media.yml").read_text()
        self.assertIn("options: [source, accessibility-only, kdenlive-gui, external-alab-readonly]", text)
        self.assertEqual(text.count("  admitted-native-kdenlive-gui:"), 1)
        self.assertNotIn("  external-alab-readonly:", text)
        for name in ("Install disposable GUI and accessibility observation dependencies",
                     "Run exact offline GUI contracts without native effects or skips",
                     "Obtain the exact official Kdenlive distribution once",
                     "Run one selected native observation or import-edit-owner route"):
            step = text.split("      - name: " + name + "\n", 1)[1].split("      - ", 1)[0]
            self.assertIn("if: inputs.verification_scope != 'external-alab-readonly'", step)
        self.assertIn("ALAB_ADMISSION_SHA256: ${{ inputs.external_alab_admission_sha256 }}", text)
        self.assertIn('--admission-sha256 "$ALAB_ADMISSION_SHA256"', text)
        upload = text.split("      - name: Retain only sealed originals", 1)[1].split("      - name:", 1)[0]
        self.assertIn("/projection.json", upload)
        self.assertIn("/original-evidence.mwseal", upload)
        self.assertNotIn("alab-readonly-original", upload)


class SyntheticSnapshotTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="alab-caller-source-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.caller = self.root / "caller"; self.caller.mkdir()
        self.owner_root = self.root / "alab"
        self.tool = self.root / "synthetic-ffprobe-never-executed"
        self.tool.write_bytes(b"SYNTHETIC IDENTITY ONLY\n")
        members, archive = owner_controls._alab_small_roster()
        for target, name, value in ((gate.owner, "MEMBERS", members), (gate, "FFPROBE", self.tool)):
            patched = patch.object(target, name, value); patched.start(); self.addCleanup(patched.stop)
        self.members = members
        self.request = gate.request_for(admission(), context())
        self.request_raw = gate.custody.json_bytes(self.request)
        request_path = self.caller / "request.original.json"
        request_path.write_bytes(self.request_raw)
        self.metadata = {"streams": [
            {"index": 0, "codec_type": "video", "nb_frames": "117", "start_pts": 0},
            {"index": 1, "codec_type": "data", "codec_tag_string": "tmcd",
             "tags": {"timecode": "00:00:41:18"}}],
            "format": {"start_time": "0.000000", "tags": {"timecode": "00:00:41:18"}}}
        self.commands = []
        def runner(argv, root, label, deadline, **kwargs):
            self.commands.append(list(argv))
            stdout = (b"ffprobe version synthetic-source-control\n" if "-version" in argv
                      else gate.custody.json_bytes(self.metadata))
            class CompletedSourceDouble:
                pid = 73123
                returncode = 0
                def __init__(self):
                    self.stdout, self.stderr = io.BytesIO(stdout), io.BytesIO()
                def poll(self): return 0
                def wait(self, timeout=None): return 0
                def kill(self): raise AssertionError("completed source double must not be killed")
            with patch.object(gate.owner.subprocess, "Popen", return_value=CompletedSourceDouble()):
                return gate.owner.run_command(argv, root, label, deadline, **kwargs)
        self.result = gate.owner.external_alab_readonly(
            request_path, self.owner_root, self.tool,
            transport=owner_controls._ALabWire(archive), command_runner=runner, environ=environment())
        self.assertEqual(self.result["status"], gate.OBSERVED)
        self.assertEqual(len(self.commands), 3)
        self.result_raw = (self.owner_root / "result.json").read_bytes()
        _, identity = gate.custody.regular(self.tool)
        self.expected = {name: b"synthetic source fixture; no authority\n"
                         for name in gate.REQUIRED_CALLER_ORIGINALS}
        self.expected.update({"request.original.json": self.request_raw,
            "ffprobe-identity-before.json": gate.custody.json_bytes(identity),
            "ffprobe-identity-after.json": gate.custody.json_bytes(identity),
            "stdout.original.bin": self.result_raw, "stderr.original.bin": b"",
            "outer-terminal.original.json": gate.custody.json_bytes({
                "execution_status": "TERMINAL_OBSERVED", "exit_code": 0, "automatic_retry": False})})
        for name, raw in self.expected.items():
            if name == "request.original.json": continue
            (self.caller / name).write_bytes(raw)
        self.packed = gate.custody.gather_snapshot({"caller": self.caller, "alab": self.owner_root})
        self.files = gate.snapshot_files(self.packed)

    def pack(self, files):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for name, raw in sorted(files.items()):
                member = tarfile.TarInfo(name); member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))
        return stream.getvalue()

    def verify(self, *, result=None, files=None, expected=None, result_raw=None):
        files = dict(self.files if files is None else files)
        expected = dict(self.expected if expected is None else expected)
        raw = self.result_raw if result_raw is None else result_raw
        if result is not None:
            raw = gate.custody.json_bytes(result)
            files["alab/result.json"] = raw
            files["caller/stdout.original.bin"] = raw
            expected["stdout.original.bin"] = raw
        return gate.verify_snapshot(self.pack(files), self.request_raw, raw,
                                    self.owner_root, expected)

    def rewrite_original(self, result, files, name, raw):
        """Mutate only explicit negative-test memory, rebinding its fixture refs."""
        files["alab/" + name] = raw
        def visit(value):
            if isinstance(value, dict):
                if set(value) == {"path", "bytes", "sha256"} and value["path"] == name:
                    value.update(bytes=len(raw), sha256=gate.custody.sha(raw))
                else:
                    for child in value.values(): visit(child)
            elif isinstance(value, list):
                for child in value: visit(child)
        visit(result)

    def test_real_owner_capture_and_existing_snapshot_accept_consistent_synthetic_originals(self):
        self.assertTrue(self.verify())
        self.assertEqual(self.result["source_origin"], "NOT_OBSERVED")
        self.assertIs(self.result["decoded_frames_verified"], False)
        self.assertEqual(len(self.result["intake"]["members"]), 5)

    def test_changed_sha_tree_sdk_or_run_cannot_inherit_a_prior_result(self):
        for key in ("source_sha", "semwright_sha", "run_id", "run_attempt"):
            changed = copy.deepcopy(self.result)
            changed["context"][key] = 2 if key == "run_attempt" else "9" * len(str(changed["context"][key]))
            with self.subTest(field=key), self.assertRaises(gate.Rejected): self.verify(result=changed)
        changed = copy.deepcopy(self.result); changed["source_tree"] = "9" * 40
        with self.assertRaises(gate.Rejected): self.verify(result=changed)

    def test_missing_duplicate_or_reordered_media_never_complete_the_two_mov_roster(self):
        for media in (self.result["media"][:1], self.result["media"][::-1], [self.result["media"][0]] * 2):
            changed = copy.deepcopy(self.result); changed["media"] = copy.deepcopy(media)
            with self.assertRaises(gate.Rejected): self.verify(result=changed)

    def test_success_cannot_omit_one_fixed_notice_member(self):
        changed = copy.deepcopy(self.result); changed["intake"]["members"].pop()
        with self.assertRaises(gate.Rejected): self.verify(result=changed)

    def test_modified_original_bytes_are_rejected_without_recapture(self):
        files = dict(self.files); files["alab/" + self.members[0]["retained_path"]] += b"changed"
        with self.assertRaises(gate.Rejected): self.verify(files=files)

    def test_result_json_changed_after_observation_is_rejected(self):
        files = dict(self.files); files["alab/result.json"] += b"\n"
        with self.assertRaises(gate.Rejected): self.verify(files=files)

    def test_omitted_or_duplicated_original_descriptor_is_rejected(self):
        for mode in ("missing", "duplicate"):
            changed = copy.deepcopy(self.result)
            if mode == "missing": changed["originals"].pop()
            else: changed["originals"].append(copy.deepcopy(changed["originals"][0]))
            with self.subTest(mode=mode), self.assertRaises(gate.Rejected): self.verify(result=changed)

    def test_numeric_aliases_in_counts_flags_and_attempt_are_rejected(self):
        for key, replacement in (("requests", float(self.result["intake"]["requests"])),
                                 ("requested_bytes", True)):
            changed = copy.deepcopy(self.result); changed["intake"][key] = replacement
            with self.assertRaises(gate.Rejected): self.verify(result=changed)
        for replacement in (True, 1.0):
            changed = copy.deepcopy(self.result); changed["context"]["run_attempt"] = replacement
            with self.assertRaises(gate.Rejected): self.verify(result=changed)

    def test_origin_frame_decode_adoption_and_human_promotions_are_rejected(self):
        for key in gate.FALSE_CLAIMS:
            changed = copy.deepcopy(self.result); changed[key] = True
            with self.subTest(field=key), self.assertRaises(gate.Rejected): self.verify(result=changed)
        changed = copy.deepcopy(self.result); changed["source_origin"] = "0"
        with self.assertRaises(gate.Rejected): self.verify(result=changed)

    def test_unreported_streams_or_filled_missing_metadata_fields_are_rejected(self):
        for mutation in (lambda row: row["observations"]["streams"].pop(),
                         lambda row: row["observations"]["format"].update(nb_frames="234")):
            changed = copy.deepcopy(self.result); mutation(changed["media"][0])
            with self.assertRaises(gate.Rejected): self.verify(result=changed)

    def test_metadata_nb_frames_never_establishes_decoded_frames(self):
        self.assertTrue(self.verify())
        self.assertEqual(self.result["media"][0]["observations"]["streams"][0]["nb_frames"], "117")
        self.assertIs(self.result["media"][0]["decoded_frames_verified"], False)
        self.assertNotIn("nb_frames", self.result["media"][0]["observations"]["format"])

    def test_unknown_boolean_float_or_nonzero_process_terminal_cannot_pass(self):
        for code in (None, True, 0.0, 7):
            changed, files = copy.deepcopy(self.result), dict(self.files)
            process = changed["media"][0]["process"]
            process["terminal"]["exit_code"] = code
            raw = gate.custody.json_bytes(process["terminal"])
            self.rewrite_original(changed, files, process["originals"]["terminal"]["path"], raw)
            with self.subTest(code=code), self.assertRaises(gate.Rejected):
                self.verify(result=changed, files=files)

    def test_wrong_cwd_or_probe_argv_is_rejected_even_when_original_hashes_match(self):
        changed, files = copy.deepcopy(self.result), dict(self.files)
        ref = changed["media"][0]["process"]["originals"]["argv"]
        argv = gate.owner.strict_object(files["alab/" + ref["path"]], "fixture argv")
        argv["argv"].insert(1, "-count_frames")
        self.rewrite_original(changed, files, ref["path"], gate.custody.json_bytes(argv))
        with self.assertRaises(gate.Rejected): self.verify(result=changed, files=files)

    def test_changed_tool_identity_cannot_use_matching_self_reported_before_after(self):
        changed = copy.deepcopy(self.result)
        for key in ("identity_before", "identity_after"):
            changed["tools"][0][key]["inode"] += 1
        with self.assertRaises(gate.Rejected): self.verify(result=changed)

    def test_wrong_r2_admission_proof_literal_is_rejected(self):
        changed = copy.deepcopy(self.result)
        changed["intake"]["source_tree_and_recipient_admission"] = "EXTERNAL_A6_PRE_POST_PROOF"
        with self.assertRaises(gate.Rejected): self.verify(result=changed)

    def test_range_count_extra_fields_and_old_result_shape_are_rejected(self):
        changed = copy.deepcopy(self.result); changed["intake"]["requests"] += 1
        with self.assertRaises(gate.Rejected): self.verify(result=changed)
        changed = copy.deepcopy(self.result); changed["archive"] = copy.deepcopy(gate.owner.ARCHIVE)
        with self.assertRaises(gate.Rejected): self.verify(result=changed)

    def test_http_original_before_classification_remains_unchanged(self):
        self.assertTrue(self.verify())
        row = self.result["intake"]["ranges"][0]
        original = gate.owner.strict_object(self.files["alab/" + row["terminal"]["path"]], "HTTP terminal")
        self.assertIs(original["complete"], False)
        self.assertEqual(original["validation_status"], "NOT_CLASSIFIED")
        self.assertIs(row["complete"], True)
        self.assertNotIn("terminal", original)

    def test_retained_http_headers_must_match_interpreted_response(self):
        changed, files = copy.deepcopy(self.result), dict(self.files)
        row = changed["intake"]["ranges"][0]
        raw = files["alab/" + row["headers"]["path"]].replace(b"HTTP/1.1 206", b"HTTP/1.1 200")
        self.rewrite_original(changed, files, row["headers"]["path"], raw)
        with self.assertRaises(gate.Rejected): self.verify(result=changed, files=files)

    def test_caller_original_roster_and_frozen_bytes_are_required(self):
        expected = dict(self.expected); expected.pop("admission.original.json")
        with self.assertRaises(gate.Rejected): self.verify(expected=expected)
        files = dict(self.files); files["caller/admission.original.json"] += b"changed"
        with self.assertRaises(gate.Rejected): self.verify(files=files)

    def test_duplicate_or_linked_snapshot_members_are_rejected(self):
        for kind in ("duplicate", "symlink"):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as archive:
                row = tarfile.TarInfo("alab/original"); row.size = 1
                if kind == "symlink": row.type = tarfile.SYMTYPE; row.linkname = "/outside"; row.size = 0
                archive.addfile(row, io.BytesIO(b"x"))
                if kind == "duplicate": archive.addfile(row, io.BytesIO(b"x"))
            with self.assertRaises(gate.Rejected): gate.snapshot_files(stream.getvalue())


class OutcomeTests(unittest.TestCase):
    def test_outer_timeout_or_ambiguous_exit_is_never_retried(self):
        for effect in (subprocess.TimeoutExpired(["synthetic"], 120), OSError("fixture"), True, None):
            records = Mock(root=Path("/fixture"))
            values = {"side_effect": effect} if isinstance(effect, Exception) else {"return_value": effect}
            with self.subTest(effect=effect), patch.object(gate, "run_tool", **values) as run:
                terminal = gate.execute_once(["synthetic-never-executed"], records)
                self.assertEqual(terminal["execution_status"], gate.UNKNOWN)
                self.assertIsNone(terminal["exit_code"])
                run.assert_called_once()
                records.document.assert_called_once()

    def test_known_nonzero_outer_exit_is_preserved_exactly(self):
        records = Mock(root=Path("/fixture"))
        with patch.object(gate, "run_tool", return_value=7) as run:
            terminal = gate.execute_once(["synthetic"], records)
        self.assertEqual(terminal["execution_status"], "TERMINAL_OBSERVED")
        self.assertEqual(terminal["exit_code"], 7)
        run.assert_called_once()

    def test_unknown_dominates_later_source_recording_and_custody_failures(self):
        for replacement in ("SOURCE_OR_ADMISSION_CHANGED", "LOCAL_RECORDING_FAILED", "CUSTODY_FAILED"):
            self.assertEqual(gate.preserve_unknown(gate.UNKNOWN, replacement), gate.UNKNOWN)
        with patch.object(gate.custody, "gather_snapshot", side_effect=OSError("fixture")) as gather, \
             patch.object(gate.custody, "encrypt_snapshot") as seal, \
             patch.object(gate.custody, "new_json"):
            projection = gate.seal_and_publish(Path("/caller"), Path("/alab"), Path("/public"),
                {}, context(), "3" * 40, gate.UNKNOWN, b"", b"", {}, "4" * 64)
        self.assertEqual(projection["status"], gate.UNKNOWN)
        self.assertEqual(projection["custody_status"], "FAILED")
        self.assertIsNone(projection["ciphertext_sha256"])
        gather.assert_called_once(); seal.assert_not_called()

    def test_binding_failure_seals_same_original_snapshot_once_without_recapture(self):
        packed = b"synthetic immutable capture fixture; not real evidence"
        with patch.object(gate.custody, "gather_snapshot", return_value=(packed, True)) as gather, \
             patch.object(gate, "verify_snapshot", side_effect=gate.Rejected("fixture binding")), \
             patch.object(gate.custody, "encrypt_snapshot", return_value=b"synthetic-seal-test-double") as seal, \
             patch.object(gate.custody, "new_bytes"), patch.object(gate.custody, "new_json"):
            projection = gate.seal_and_publish(Path("/caller"), Path("/alab"), Path("/public"),
                {}, context(), "3" * 40, gate.OBSERVED, b"", b"", {}, "4" * 64)
        self.assertEqual(projection["status"], "SNAPSHOT_BINDING_FAILED")
        self.assertIs(projection["snapshot_binding_verified"], False)
        gather.assert_called_once()
        seal.assert_called_once_with(packed, {}, context())

    def test_partial_failure_is_preserved_without_demanding_success_rosters(self):
        packed = b"synthetic partial original fixture"
        with patch.object(gate.custody, "gather_snapshot", return_value=(packed, False)) as gather, \
             patch.object(gate, "verify_snapshot") as verify, \
             patch.object(gate.custody, "encrypt_snapshot", return_value=b"synthetic-seal-test-double") as seal, \
             patch.object(gate.custody, "new_bytes"), patch.object(gate.custody, "new_json"):
            projection = gate.seal_and_publish(Path("/caller"), Path("/alab"), Path("/public"),
                {}, context(), "3" * 40, gate.UNKNOWN, b"", b"", {}, "4" * 64)
        self.assertEqual(projection["status"], gate.UNKNOWN)
        self.assertEqual(projection["custody_status"], "INCOMPLETE")
        self.assertEqual(projection["source_origin"], "NOT_OBSERVED")
        gather.assert_called_once(); verify.assert_not_called()
        seal.assert_called_once_with(packed, {}, context())


class CallerMainTests(unittest.TestCase):
    """Source-only composition; I/O doubles cannot grant a real admission."""
    def run_main(self, variant):
        temporary = tempfile.TemporaryDirectory(prefix="alab-caller-main-source-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        tool, env_tool = root / "synthetic-ffprobe", root / "synthetic-env"
        tool.write_bytes(b"SYNTHETIC NEVER EXECUTED ffprobe identity")
        env_tool.write_bytes(b"SYNTHETIC NEVER EXECUTED env identity")
        recipient_path, admission_path = root / "recipient.json", root / "admission.json"
        recipient_path.write_bytes(gate.custody.json_bytes({"spki_der_base64": "", "sha256": "4" * 64}))
        members, archive = owner_controls._alab_small_roster()
        real_regular = gate.custody.regular
        real_document = gate.gui.Recorder.document
        def regular(path, limit=gate.custody.MAX_FILE):
            if Path(path) == Path("/usr/bin/env"):
                return real_regular(env_tool, limit)
            return real_regular(path, limit)
        def document(records, name, value):
            if variant == "unknown-terminal-custody" and name == "outer-terminal.original.json":
                raise OSError("synthetic late terminal custody failure")
            return real_document(records, name, value)
        def outer(argv, stdout, stderr, _root):
            if variant in ("timeout", "unknown-terminal-custody"):
                stdout.write_bytes(b"synthetic retained partial output")
                stderr.write_bytes(b"synthetic retained partial error")
                raise subprocess.TimeoutExpired(argv, 120)
            request = Path(argv[argv.index("--request") + 1])
            target = Path(argv[argv.index("--evidence-root") + 1])
            def runner(command, destination, label, deadline, **kwargs):
                output = (b"ffprobe version synthetic fixture\n" if "-version" in command
                          else b'{"streams":[{"codec_type":"video","nb_frames":"117"},{"codec_type":"data"}],"format":{}}\n')
                class Child:
                    pid = 73124
                    returncode = 0
                    def __init__(self): self.stdout, self.stderr = io.BytesIO(output), io.BytesIO()
                    def poll(self): return 0
                    def wait(self, timeout=None): return 0
                    def kill(self): raise AssertionError("synthetic completed child")
                with patch.object(gate.owner.subprocess, "Popen", return_value=Child()):
                    return gate.owner.run_command(command, destination, label, deadline, **kwargs)
            result = gate.owner.external_alab_readonly(request, target, tool,
                transport=owner_controls._ALabWire(archive), command_runner=runner, environ=environment())
            stdout.write_bytes(gate.custody.json_bytes(result)); stderr.write_bytes(b"")
            return 0
        before, after = source(), source()
        if variant == "source-drift": after["tree"] = "9" * 40
        with ExitStack() as stack:
            stack.enter_context(patch.object(gate.owner, "MEMBERS", members))
            stack.enter_context(patch.object(gate, "FFPROBE", tool))
            value = admission(); raw = gate.custody.json_bytes(value)
            admission_path.write_bytes(raw)
            stack.enter_context(patch.dict(os.environ, {**environment(), "RUNNER_TEMP": str(root)}))
            stack.enter_context(patch.object(gate.sys, "platform", "linux"))
            stack.enter_context(patch.object(gate.os, "getuid", return_value=1001, create=True))
            stack.enter_context(patch.object(gate, "source_snapshot", side_effect=[before, after]))
            admitted = stack.enter_context(patch.object(gate, "validate_admission", return_value=value))
            stack.enter_context(patch.object(gate.custody, "regular", side_effect=regular))
            stack.enter_context(patch.object(gate.gui.Recorder, "document", new=document))
            command = stack.enter_context(patch.object(gate, "run_tool", side_effect=outer))
            seal = stack.enter_context(patch.object(gate.custody, "encrypt_snapshot",
                                                    return_value=b"synthetic-seal-source-double"))
            stack.enter_context(redirect_stdout(io.StringIO()))
            code = gate.main(["--source-sha", "1" * 40,
                "--admission-sha256", gate.custody.sha(raw), "--recipient", str(recipient_path),
                "--admission", str(admission_path), "--evidence-root", str(root / "evidence"),
                "--public-dir", str(root / "public")])
            admitted.assert_called_once(); command.assert_called_once(); seal.assert_called_once()
        projection = gate.custody.strict((root / "public/projection.json").read_bytes())
        return code, projection, root

    def test_main_composes_recorder_owner_and_immutable_custody_once(self):
        code, projection, _ = self.run_main("consistent")
        self.assertEqual(code, 0)
        self.assertEqual(projection["status"], gate.OBSERVED)
        self.assertIs(projection["snapshot_binding_verified"], True)
        self.assertIs(projection["decoded_frames_verified"], False)
        self.assertEqual(projection["source_origin"], "NOT_OBSERVED")

    def test_main_source_drift_refuses_observed_metadata_without_repeating_it(self):
        code, projection, root = self.run_main("source-drift")
        self.assertEqual(code, 1)
        self.assertEqual(projection["status"], "SOURCE_OR_ADMISSION_CHANGED")
        self.assertIs(projection["snapshot_binding_verified"], False)
        self.assertTrue((root / "evidence/alab/result.json").is_file())

    def test_main_outer_timeout_keeps_unknown_through_incomplete_collection(self):
        code, projection, root = self.run_main("timeout")
        self.assertEqual(code, 1)
        self.assertEqual(projection["status"], gate.UNKNOWN)
        self.assertEqual(projection["custody_status"], "INCOMPLETE")
        self.assertTrue((root / "evidence/caller/stdout.original.bin").is_file())

    def test_main_late_terminal_recording_failure_cannot_erase_unknown(self):
        code, projection, root = self.run_main("unknown-terminal-custody")
        self.assertEqual(code, 1)
        self.assertEqual(projection["status"], gate.UNKNOWN)
        self.assertEqual(projection["custody_status"], "INCOMPLETE")
        self.assertTrue((root / "evidence/caller/caller-error.original.json").is_file())


if __name__ == "__main__":
    unittest.main()
