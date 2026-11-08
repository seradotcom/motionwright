"""Exercise retained-byte transport only; fixtures grant no Native or G2 credit."""
from __future__ import annotations

import base64
import copy
from contextlib import ExitStack
import io
import itertools
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import fixedtxt_source_kit as kit


class RetainedSourceTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = os.environ.get("MOTIONWRIGHT_TEST_FIXEDTXT_SOURCE_ZIP")
        cls.archive = None
        if fixture:
            cls.archive = kit.retained_regular(Path(fixture), kit.RETAINED_ZIP_BYTES)
        elif os.environ.get("GITHUB_JOB") == "fixedtxt-retained-source":
            _, cls.archive, _, _ = kit.read_retained_event()
        if cls.archive is not None:
            cls.encoded = base64.b85encode(cls.archive).decode("ascii")
            _, cls.originals = kit.decode_retained_source(cls.encoded)
        else:
            cls.encoded = "x" * kit.RETAINED_BASE85_CHARS
        cls.workflow = (ROOT / kit.WORKFLOW).read_text(encoding="utf-8")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mw-fixedtxt-retained-unit-",
                                                dir=os.environ.get("MOTIONWRIGHT_TEST_ROOT"))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.event_path = self.root / "event.json"
        branch = kit.RETAINED_BRANCHES[1]
        self.environment = {
            "GITHUB_ACTIONS": "true", "CI": "true", "GITHUB_REPOSITORY": "seradotcom/motionwright",
            "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_JOB": "fixedtxt-retained-source", "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64",
            "GITHUB_REF": "refs/heads/" + branch, "GITHUB_REF_NAME": branch,
            "GITHUB_WORKFLOW_REF": "seradotcom/motionwright/" + kit.WORKFLOW + "@refs/heads/" + branch,
            "GITHUB_SHA": "1" * 40, "GITHUB_WORKFLOW_SHA": "1" * 40, "GITHUB_RUN_ID": "900001",
            "GITHUB_EVENT_PATH": str(self.event_path),
        }
        self.event = {
            "ref": branch, "repository": {"full_name": "seradotcom/motionwright"},
            "inputs": {"verification_scope": "source", "expected_source_sha": "1" * 40,
                       "g2_admission": "", "retained_reference_zip_base64": "",
                       "fixedtxt_mode": "prepare", "fixedtxt_request": kit.RETAINED_MARKER,
                       "fixedtxt_delivery": "", kit.RETAINED_INPUT: self.encoded},
        }
        self.owner = self.root / "unit-output"
        self.payload, self.retained = self.owner / "payload", self.owner / "custody"
        self.write_event()

    def require_original(self):
        if self.archive is None:
            self.skipTest("Retained ZIP required for genuine-byte tests; no Native inference")

    def write_event(self):
        self.event_path.write_bytes(kit.retained_canonical(self.event))

    def unit_source_guards(self):
        """Isolate unchanged Git/source guards; real byte and file checks run."""
        stack = ExitStack()
        stack.enter_context(patch.dict(os.environ, self.environment))
        stack.enter_context(patch.object(kit.sys, "platform", "linux"))
        selected = stack.enter_context(patch.object(kit, "selected_pins", return_value="unit-source-evidence.json"))
        stack.enter_context(patch.object(kit.custody, "source_snapshot",
                                        return_value={"sha": "1" * 40, "files": {}}))
        stack.enter_context(patch.object(kit, "source_matches"))
        stack.enter_context(patch.object(kit, "retained_layout",
                                        return_value=(self.owner, self.payload, self.retained)))
        stack.enter_context(patch.object(kit, "prepare", side_effect=AssertionError("factory must never run")))
        return stack, selected

    def repack(self, mutate=None, extra=None):
        self.require_original()
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in self.originals.items():
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                if mutate:
                    info, raw = mutate(info, raw)
                archive.writestr(info, raw)
            if extra:
                archive.writestr(*extra)
        return output.getvalue()

    def test_exact_archive_and_all_five_original_roles_are_admitted(self):
        self.require_original()
        current, archive, members, observed = kit.read_retained_event(self.environment)
        self.assertEqual(kit.sha(archive), kit.RETAINED_ZIP_SHA)
        self.assertEqual(len(archive), 49077)
        self.assertEqual(set(members), set(kit.RETAINED_MEMBERS))
        self.assertEqual(sum(map(len, members.values())), 85331)
        self.assertEqual(current["semwright_sha"], kit.SDK)
        self.assertLessEqual(observed["dispatch_payload_bytes"], 65535)
        for name, raw in members.items():
            self.assertEqual((len(raw), kit.sha(raw)), kit.RETAINED_MEMBERS[name])

    def test_exact_canonical_and_A4_branches_are_the_only_positive_contexts(self):
        for branch in kit.RETAINED_BRANCHES:
            with self.subTest(branch=branch):
                environment, event = dict(self.environment), copy.deepcopy(self.event)
                environment.update(GITHUB_REF="refs/heads/" + branch, GITHUB_REF_NAME=branch,
                    GITHUB_WORKFLOW_REF="seradotcom/motionwright/" + kit.WORKFLOW + "@refs/heads/" + branch)
                event["ref"] = branch
                self.assertEqual(kit.retained_context(event, environment)["source_sha"], "1" * 40)

    def test_other_refs_forks_events_attempts_and_source_identities_are_rejected(self):
        variants = [
            ("GITHUB_REF", "refs/heads/main"), ("GITHUB_REF", "refs/tags/retained-source-kit"),
            ("GITHUB_REF", "refs/heads/" + kit.RETAINED_BRANCHES[0] + "-other"),
            ("GITHUB_REF", "refs/heads/" + kit.RETAINED_BRANCHES[1] + "-other"),
            ("GITHUB_REF", "refs/heads/fix/native-gate-closeout-20261030-a4"),
            ("GITHUB_REPOSITORY", "fork/motionwright"), ("GITHUB_EVENT_NAME", "push"),
            ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_RUN_ATTEMPT", "2"),
            ("GITHUB_RUN_ATTEMPT", ""), ("GITHUB_JOB", "source-contracts"),
            ("GITHUB_SHA", "2" * 40), ("GITHUB_WORKFLOW_SHA", "2" * 40),
            ("GITHUB_WORKFLOW_REF", "seradotcom/motionwright/" + kit.WORKFLOW + "@refs/heads/main"),
            ("RUNNER_OS", "Windows"), ("RUNNER_ARCH", "ARM64"), ("GITHUB_RUN_ID", "0"),
        ]
        for key, value in variants:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                kit.retained_context(self.event, {**self.environment, key: value})
        for key, value in (("ref", "main"), ("repository", {"full_name": "fork/motionwright"})):
            with self.subTest(event_key=key), self.assertRaises(ValueError):
                kit.retained_context({**self.event, key: value}, self.environment)

    def test_closed_source_selection_rejects_other_authority_and_payloads(self):
        variants = [("verification_scope", "normal"), ("verification_scope", "fixedtxt-v4-one-fixture"),
                    ("verification_scope", "film180-g2"), ("fixedtxt_mode", "deliver"),
                    ("fixedtxt_request", "managed-project-source-kit"), ("expected_source_sha", "2" * 40),
                    ("g2_admission", "{}"), ("fixedtxt_delivery", "{}"),
                    ("retained_reference_zip_base64", "eA=="), ("fixedtxt_mode", True)]
        for key, value in variants:
            event = copy.deepcopy(self.event)
            event["inputs"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                kit.retained_context(event, self.environment)
        for change in ("extra", "missing", "oversized"):
            event = copy.deepcopy(self.event)
            if change == "extra":
                event["inputs"]["unexpected"] = ""
            elif change == "missing":
                del event["inputs"]["expected_source_sha"]
            else:
                event["inputs"][kit.RETAINED_INPUT] = "x" * 65535
            with self.subTest(change=change), self.assertRaises(ValueError):
                kit.retained_context(event, self.environment)

    def test_optional_empty_event_shapes_preserve_original_identity(self):
        optional = ("g2_admission", "fixedtxt_delivery", "retained_reference_zip_base64")
        for forms in itertools.product(("absent", "null", "empty"), repeat=len(optional)):
            with self.subTest(forms=forms):
                event = copy.deepcopy(self.event)
                for key, form in zip(optional, forms):
                    if form == "absent":
                        del event["inputs"][key]
                    else:
                        event["inputs"][key] = None if form == "null" else ""
                original = copy.deepcopy(event)
                current = kit.retained_context(event, self.environment)
                self.assertEqual(current["source_sha"], "1" * 40)
                self.assertTrue(event == original, "context must preserve the original event")
                if self.archive is not None:
                    raw = kit.retained_canonical(event)
                    self.event_path.write_bytes(raw)
                    result, archive, members, observed = kit.read_retained_event(self.environment)
                    self.assertEqual(result, current)
                    self.assertEqual(kit.sha(archive), kit.RETAINED_ZIP_SHA)
                    self.assertEqual(set(members), set(kit.RETAINED_MEMBERS))
                    self.assertEqual(observed["event_sha256"], kit.sha(raw))
                    self.assertEqual(observed["event_bytes"], len(raw))
                    self.assertEqual(observed["dispatch_payload_bytes"], len(kit.retained_canonical({
                        "ref": self.environment["GITHUB_REF_NAME"], "inputs": original["inputs"]})))
                    self.assertEqual(kit.sha(self.event_path.read_bytes()), kit.sha(raw))

    def test_required_inputs_cannot_be_missing_null_or_coerced(self):
        required = ("verification_scope", "expected_source_sha", "fixedtxt_mode",
                    "fixedtxt_request", kit.RETAINED_INPUT)
        for key in required:
            event = copy.deepcopy(self.event)
            del event["inputs"][key]
            with self.subTest(key=key, form="missing"), self.assertRaisesRegex(
                    ValueError, "^retained source input roster: missing-required$"):
                kit.retained_context(event, self.environment)
            for number, value in enumerate((None, False, True, 0, 1, [], {})):
                event = copy.deepcopy(self.event)
                event["inputs"][key] = value
                with self.subTest(key=key, case=number), self.assertRaisesRegex(
                        ValueError, "^retained source input roster: required-type$"):
                    kit.retained_context(event, self.environment)

    def test_optional_authority_and_unknown_keys_are_never_coerced(self):
        optional = ("g2_admission", "fixedtxt_delivery", "retained_reference_zip_base64")
        for key in optional:
            for number, value in enumerate((False, True, 0, 1, [], {}, "null", "false", " ", "{}")):
                event = copy.deepcopy(self.event)
                event["inputs"][key] = value
                with self.subTest(key=key, case=number), self.assertRaisesRegex(
                        ValueError, "^retained source input roster: optional-authority-nonempty$"):
                    kit.retained_context(event, self.environment)
        for number, value in enumerate((None, "", False, 0, [], {})):
            event = copy.deepcopy(self.event)
            event["inputs"]["unexpected"] = value
            with self.subTest(unknown_case=number), self.assertRaisesRegex(
                    ValueError, "^retained source input roster: unknown-key$"):
                kit.retained_context(event, self.environment)
        for number, value in enumerate((None, "", False, 0, [], ["inputs"])):
            event = copy.deepcopy(self.event)
            event["inputs"] = value
            with self.subTest(nonobject_case=number), self.assertRaisesRegex(
                    ValueError, "^retained source input roster: not-object$"):
                kit.retained_context(event, self.environment)

    def test_encoding_and_original_archive_identity_cannot_be_substituted(self):
        self.require_original()
        bad_values = ["", self.encoded[:-1], self.encoded + "\n",
                      self.encoded[:-1] + "\\", "\N{SNOWMAN}" + self.encoded[1:],
                      base64.b64encode(self.archive).decode("ascii")]
        changed = bytearray(self.archive)
        changed[100] ^= 1
        bad_values += [base64.b85encode(changed).decode("ascii"),
                       base64.b85encode(self.repack()).decode("ascii")]
        for number, encoded in enumerate(bad_values):
            with self.subTest(case=number), self.assertRaises(ValueError):
                kit.decode_retained_source(encoded)

    def test_closed_zip_rejects_extra_duplicate_path_link_type_size_and_hash(self):
        self.require_original()
        cases = [self.repack(extra=("extra.txt", b"x")), self.repack(extra=("../escape.txt", b"x"))]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            cases.append(self.repack(extra=("caption_source.txt", b"x")))
        for alteration in ("symlink", "directory", "size", "same-size-hash", "name"):
            def change(info, raw, kind=alteration):
                if info.filename == "caption_source.txt":
                    if kind == "symlink":
                        info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    elif kind == "directory":
                        info.external_attr = (stat.S_IFDIR | 0o700) << 16
                    elif kind == "size":
                        raw += b"x"
                    elif kind == "same-size-hash":
                        raw = b"x" * len(raw)
                    else:
                        info.filename = "other.txt"
                return info, raw
            cases.append(self.repack(mutate=change))
        for number, archive in enumerate(cases):
            with self.subTest(case=number), self.assertRaises(ValueError):
                kit.retained_members(archive)

    def test_event_read_is_regular_bounded_and_rejects_links_or_fifo(self):
        path = self.root / "small.bin"
        path.write_bytes(b"abcd")
        self.assertEqual(kit.retained_regular(path, 4), b"abcd")
        with self.assertRaises(ValueError):
            kit.retained_regular(path, 3)
        path.write_bytes(b"")
        with self.assertRaises(ValueError):
            kit.retained_regular(path, 4)
        path.write_bytes(b"abcd")
        linked = self.root / "hardlink.bin"
        os.link(path, linked)
        with self.assertRaises(ValueError):
            kit.retained_regular(linked, 4)
        linked.unlink()
        if os.name == "posix":
            linked.symlink_to(path)
            with self.assertRaises(ValueError):
                kit.retained_regular(linked, 4)
            linked.unlink()
            os.mkfifo(linked)
            with self.assertRaises(ValueError):
                kit.retained_regular(linked, 4)

    def test_event_change_during_descriptor_read_is_rejected(self):
        path = self.root / "changing.bin"
        path.write_bytes(b"abcd")
        read, changed = os.read, False
        def mutate(descriptor, count):
            nonlocal changed
            raw = read(descriptor, count)
            if not changed:
                changed = True
                with path.open("ab") as stream:
                    stream.write(b"e")
            return raw
        with patch.object(kit.os, "read", side_effect=mutate), self.assertRaises(ValueError):
            kit.retained_regular(path, 4)

    def test_duplicate_event_fields_and_environment_payload_fallback_are_rejected(self):
        raw = kit.retained_canonical(self.event)
        self.event_path.write_bytes(raw.replace(b'"inputs":', b'"inputs":{},"inputs":', 1))
        with self.assertRaises(ValueError):
            kit.read_retained_event(self.environment)
        self.write_event()
        environment = dict(self.environment)
        del environment["GITHUB_EVENT_PATH"]
        environment[kit.RETAINED_INPUT] = self.encoded
        with self.assertRaises(ValueError):
            kit.read_retained_event(environment)

    def test_invalid_context_or_archive_cannot_write_or_reach_source_setup(self):
        self.require_original()
        for change in ("context", "archive"):
            self.write_event()
            if change == "context":
                self.environment["GITHUB_EVENT_NAME"] = "push"
            else:
                self.environment["GITHUB_EVENT_NAME"] = "workflow_dispatch"
                self.event["inputs"][kit.RETAINED_INPUT] = self.encoded[:-1]
                self.write_event()
            stack, selected = self.unit_source_guards()
            with stack, self.assertRaises(ValueError):
                kit.retain_original_bundle()
            selected.assert_not_called()
            self.assertFalse(self.owner.exists())

    def test_five_file_materialization_preserves_separate_origin_and_no_credit(self):
        self.require_original()
        stack, _ = self.unit_source_guards()
        with stack:
            kit.retain_original_bundle()
            with self.assertRaises(ValueError):
                kit.retain_original_bundle()
        self.assertEqual({p.name for p in self.payload.iterdir()}, set(kit.RETAINED_MEMBERS))
        self.assertEqual(kit.sha((self.retained / "source-inputs.original.zip").read_bytes()), kit.RETAINED_ZIP_SHA)
        receipt = json.loads((self.retained / "SOURCE_TRANSPORT.original.json").read_bytes())
        self.assertNotEqual(receipt["transport_context"]["run_id"],
                            str(receipt["original_source_origin"]["factory_origin"]["run_id"]))
        self.assertEqual(receipt["original_source_origin"]["factory_roles"],
                         ["template_film.original.json", "managed_project.original.json"])
        self.assertEqual(receipt["original_source_origin"]["independent_roles"]["red_native.ts"]["source_sdk"],
                         "e64a2d28d1d866c539a2c40824cbb57f86a13c10")
        for name in ("factory_invocations", "SDK_builds", "Native_calls"):
            self.assertEqual(receipt[name], 0)
        for name in ("R28_issued", "F_selected", "g2_source_bundle_complete", "formal_acceptance",
                     "release_authorized", "original_API_revalidated"):
            self.assertIs(receipt[name], False)
        self.assertEqual(receipt["formal_G2"], "NOT_GRANTED")
        self.assertEqual(receipt["G3"], "NOT_GRANTED")
        self.assertEqual(receipt["physical_font_color_baselines"], "NOT_RUN")

    def test_upload_binding_does_not_relabel_origin_or_claim_api_readback(self):
        self.require_original()
        stack, _ = self.unit_source_guards()
        with stack:
            kit.retain_original_bundle()
            with self.assertRaises(ValueError):
                kit.record_retained_upload()
            with patch.dict(os.environ, {"MW_FIXEDTXT_RETAINED_ARTIFACT_ID": "900002",
                                         "MW_FIXEDTXT_RETAINED_ARTIFACT_DIGEST": "a" * 64}):
                kit.record_retained_upload()
        binding = json.loads((self.retained / "UPLOAD_BINDING.original.json").read_bytes())
        self.assertEqual(binding["artifact"]["run_id"], 900001)
        self.assertEqual(binding["artifact"]["artifact_id"], 900002)
        self.assertEqual(binding["artifact"]["zip_sha256"], "a" * 64)
        self.assertEqual(binding["original_source_origin"]["factory_origin"]["artifact_id"], 13064212124)
        self.assertEqual(binding["input_archive_sha256"], kit.RETAINED_ZIP_SHA)
        self.assertIs(binding["remote_API_and_downloaded_ZIP_verified_here"], False)
        self.assertIs(binding["formal_acceptance"], False)
        self.assertIs(binding["R28_issued"], False)

    def test_changed_payload_cannot_receive_an_upload_binding(self):
        self.require_original()
        stack, _ = self.unit_source_guards()
        with stack:
            kit.retain_original_bundle()
            (self.payload / "caption_source.txt").write_bytes(b"x" * 35)
            with patch.dict(os.environ, {"MW_FIXEDTXT_RETAINED_ARTIFACT_ID": "900002",
                                         "MW_FIXEDTXT_RETAINED_ARTIFACT_DIGEST": "a" * 64}):
                with self.assertRaises(ValueError):
                    kit.record_retained_upload()
        self.assertFalse((self.retained / "UPLOAD_BINDING.original.json").exists())

    def test_workflow_has_nine_inputs_no_payload_export_and_no_native_route(self):
        inputs = self.workflow.split("    inputs:\n", 1)[1].split("  pull_request:\n", 1)[0]
        self.assertEqual(len(re.findall(r"^      [a-z0-9_]+:", inputs, re.MULTILINE)), 9)
        self.assertIn("      fixedtxt_session_cases:\n", inputs)
        expressions = re.findall(r"\$\{\{(.*?)\}\}", self.workflow)
        payload_refs = [value for value in expressions if kit.RETAINED_INPUT in value]
        self.assertEqual(len(payload_refs), 6)
        self.assertTrue(all(" == ''" in value or " != ''" in value for value in payload_refs))
        job = self.workflow.split("  fixedtxt-retained-source:\n", 1)[1].split("  source-contracts:\n", 1)[0]
        self.assertIn("inputs.fixedtxt_request == 'retained-source-kit' || inputs.retained_fixedtxt_source_zip_base85 != ''", job)
        for prohibited in ("--prepare", "cargo ", "apt-get", "repository: seradotcom/semwright"):
            self.assertNotIn(prohibited, job)
        self.assertIn("--retain-original-bundle", job)
        self.assertIn("--record-retained-upload", job)
        guard = "test '${{ inputs.retained_fixedtxt_source_zip_base85 == '' && inputs.fixedtxt_request != 'retained-source-kit' }}' = 'true'"
        for name in ("source-contracts", "native-mlt-sequence", "fixedtxt-original-owner",
                     "fixedtxt-session-owner", "fixedtxt-r28-original-delivery"):
            block = self.workflow.split("  " + name + ":\n", 1)[1]
            block = re.split(r"\n  [a-z][a-z0-9-]+:\n", block, maxsplit=1)[0]
            self.assertIn(guard, block, name)
        self.assertIn("inputs.verification_scope == 'source' && !(inputs.fixedtxt_mode == 'prepare' && inputs.fixedtxt_request == 'retained-source-kit')", self.workflow)


if __name__ == "__main__":
    unittest.main()
