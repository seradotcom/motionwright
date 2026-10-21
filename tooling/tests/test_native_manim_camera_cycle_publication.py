"""Source-only tests of the canonical inline camera-cycle publisher.

The publisher is loaded from its actual workflow heredoc. This module does not
copy its validation logic or invoke a native producer. Lower-level verifier
results are explicit unit fixtures; their own contracts are tested separately.
The fixture media and seals are synthetic bytes, not audiovisual evidence.
"""

from __future__ import annotations

import ast
import copy
from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import textwrap
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "native-manim-e2e.yml"

sys.path.insert(0, str(ROOT / "tooling"))
import native_manim_camera_evidence as evidence
import native_manim_camera_cycle as cycle


def load_canonical_publisher():
    """Compile only imports and the two real publisher function definitions."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    step_label = (
        "- name: Stage only verified Manim MP4, not original Broker receipts"
    )
    steps = [
        index for index, line in enumerate(lines)
        if line.strip() == step_label
    ]
    if len(steps) != 1:
        raise AssertionError("Expected exactly one canonical publication step")
    step_start = steps[0]
    step_indent = len(lines[step_start]) - len(lines[step_start].lstrip())
    step_end = len(lines)
    for index in range(step_start + 1, len(lines)):
        line = lines[index]
        indent = len(line) - len(line.lstrip())
        if indent == step_indent and line.lstrip().startswith("- "):
            step_end = index
            break

    condition = (
        'if [ "$MANIM_NATIVE_SCENARIO" = "camera120-source-cycle" ]; then'
    )
    conditions = [
        index for index in range(step_start + 1, step_end)
        if lines[index].strip() == condition
    ]
    if len(conditions) != 1:
        raise AssertionError("Expected exactly one source-cycle publication arm")

    command = (
        '"$RUNNER_TEMP/manim-custody-venv/bin/python" -B - <<\'PY\''
    )
    opener = conditions[0] + 1
    while opener < step_end and not lines[opener].strip():
        opener += 1
    if opener >= step_end or lines[opener].strip() != command:
        raise AssertionError("Canonical source-cycle Python heredoc changed")

    closer = next(
        (
            index for index in range(opener + 1, step_end)
            if lines[index].strip() == "PY"
        ),
        None,
    )
    if closer is None:
        raise AssertionError("Canonical source-cycle heredoc is incomplete")

    source = textwrap.dedent("\n".join(lines[opener + 1:closer])) + "\n"
    parsed = ast.parse(source, filename=str(WORKFLOW))
    selected = []
    definitions = []
    guards = []

    expected_guard = ast.parse(
        "if __name__ == '__main__':\n    main()\n"
    ).body[0]

    for node in parsed.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            selected.append(node)
        elif isinstance(node, ast.FunctionDef):
            if node.name not in {"verified_cycle_summary", "main"}:
                raise AssertionError(
                    f"Unexpected function in canonical publisher: {node.name}"
                )
            definitions.append(node.name)
            selected.append(node)
        elif isinstance(node, ast.If):
            if ast.dump(node, include_attributes=False) != ast.dump(
                expected_guard, include_attributes=False
            ):
                raise AssertionError("Unexpected executable publisher guard")
            guards.append(node)
        else:
            raise AssertionError(
                "Unexpected top-level executable publisher statement"
            )

    if sorted(definitions) != ["main", "verified_cycle_summary"]:
        raise AssertionError("Canonical publisher definitions are not unique")
    if len(guards) != 1:
        raise AssertionError("Expected one unexecuted canonical main guard")

    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    ast.increment_lineno(module, opener + 1)
    namespace = {
        "__name__": "motionwright_camera_cycle_publisher_under_test",
    }
    exec(compile(module, str(WORKFLOW), "exec"), namespace)
    return namespace


@contextmanager
def working_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class CameraCyclePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve(strict=True)
        self.namespace = load_canonical_publisher()
        self.source = "1" * 40
        self.pin = "2" * 40
        self.case_number = 0

        self.publication = self.start_patch(
            evidence, "verified_publication", self.verified_publication
        )
        self.edit_contract = self.start_patch(
            cycle, "_checked_edit_contract", self.checked_edit_contract
        )
        self.native_link = self.start_patch(
            cycle, "_verify_native_link", self.verify_native_link
        )
        self.new_fixture()

    def start_patch(self, module, name, callback):
        patch = mock.patch.object(module, name, side_effect=callback)
        result = patch.start()
        self.addCleanup(patch.stop)
        return result

    @staticmethod
    def write_bytes(path, value):
        path.write_bytes(value)
        path.chmod(0o600)

    @classmethod
    def write_json(cls, path, value):
        packed = (
            json.dumps(
                value, sort_keys=True, indent=2, ensure_ascii=False,
                allow_nan=False,
            ) + "\n"
        ).encode("utf-8")
        cls.write_bytes(path, packed)

    def new_fixture(self):
        self.case_number += 1
        self.case = self.root / f"case-{self.case_number}"
        self.runner_temp = self.case / "runner"
        self.checkout = self.case / "checkout"
        self.original = (
            self.runner_temp / "manim-camera-private"
            / ("original-" + self.source)
        )
        self.edited = (
            self.runner_temp / "manim-camera-private"
            / ("edited-" + self.source)
        )
        sealed = self.runner_temp / "manim-camera-sealed"
        for path in (self.checkout, self.original, self.edited, sealed):
            path.mkdir(parents=True, mode=0o700)
        self.sealed_original = sealed / "originals.mwci"
        self.sealed_edited = sealed / "edited.mwci"
        self.write_bytes(self.sealed_original, b"synthetic original seal")
        self.write_bytes(self.sealed_edited, b"synthetic edited seal")
        self.write_json(
            self.checkout / "SOURCE_LOCK.json",
            {"dependencies": {"semwright": {"revision": self.pin}}},
        )

        self.original_media = b"synthetic original MP4 unit-fixture bytes"
        self.edited_media = b"synthetic edited MP4 unit-fixture bytes"
        self.original_plan = evidence.sha(b"original compiled plan fixture")
        self.edited_plan = evidence.sha(b"edited compiled plan fixture")
        self.program_sha = evidence.sha(b"edited program fixture")
        self.bundle_sha = evidence.sha(b"current export bundle fixture")
        self.bad_sha = evidence.sha(b"unrelated fixture binding")
        recipient = evidence.sha(b"synthetic recipient identity")

        self.first_projection = {
            "sealed_originals_recipient_sha256": recipient,
            "sealed_originals_sha256": evidence.sha(
                evidence.read(self.sealed_original)
            ),
            "compiled_plan_sha256": self.original_plan,
            "decoded_frame_count": 120,
        }
        self.second_projection = {
            "sealed_originals_recipient_sha256": recipient,
            "sealed_originals_sha256": evidence.sha(
                evidence.read(self.sealed_edited)
            ),
            "compiled_plan_sha256": self.edited_plan,
            "decoded_frame_count": 120,
        }

        self.first_binding = {
            "project_id": "12345678-1234-4234-8234-123456789abc",
            "scene_id": "23456789-2345-4345-8345-23456789abcd",
            "deliverable_id": "3456789a-3456-4456-8456-3456789abcde",
            "generation": "456789ab-4567-4567-8567-456789abcdef",
            "revision": 41,
        }
        self.edited_binding = {
            **self.first_binding,
            "revision": self.first_binding["revision"] + 1,
        }
        self.first_native = {
            "request_id": "private-original-native-request",
            "private_marker": "private-original-native-evidence",
        }
        self.second_native = {
            "request_id": "private-edited-native-request",
            "private_marker": "private-edited-native-evidence",
        }
        self.first_result = {
            "project": copy.deepcopy(self.first_binding),
            "program_sha256": evidence.sha(b"original program fixture"),
        }
        self.second_result = {
            "project": copy.deepcopy(self.edited_binding),
            "program_sha256": self.program_sha,
        }
        self.carrier = {
            "sha256": self.bundle_sha,
            "closure": {
                "plan_sha256": self.edited_plan,
                "source_sha256": self.program_sha,
            },
        }
        inspection = {
            "current_binding": copy.deepcopy(self.edited_binding),
            "current_closure": copy.deepcopy(self.carrier["closure"]),
        }
        history = {
            "original_source_asset_id": "56789abc-5678-4678-8678-56789abcdef0",
            "edited_source_asset_id": "6789abcd-6789-4789-8789-6789abcdef01",
            "original_source_sha256": evidence.sha(b"original source fixture"),
            "edited_source_sha256": evidence.sha(b"edited source fixture"),
            "source_project_sha256": evidence.sha(b"source project fixture"),
            "current_export_bundle_sha256": self.bundle_sha,
        }
        self.edit = {
            **history,
            "current_source_inspection": copy.deepcopy(inspection),
        }
        self.readback = {
            "schema": "motionwright.manim.camera.source-cycle/1",
            "phase": "source-cycle-readback",
            "canonical_stamp": {
                "resource": "project:" + self.edited_binding["project_id"],
                "generation": self.edited_binding["generation"],
                "revision": self.edited_binding["revision"],
            },
            **{
                key: self.edited_binding[key]
                for key in ("project_id", "scene_id", "deliverable_id")
            },
            "production_receipt_window_limit": 256,
            "fresh_sqlite_readback": True,
            "original_native_receipts_unchanged": True,
            "production_receipt_window_unchanged": True,
            "saved_export_matches_native_carrier": True,
            "source_changed_by_example": False,
            "native_execution_performed": False,
            "native_compiler_registered": False,
            "provider_dispatch_performed": False,
            "new_render_job_created": False,
            "imported_source_authority_used": False,
            "fresh_registered_source_reopen_verified": False,
            "native_video_frame_content_verified": False,
            "human_acceptance": False,
            **history,
            "edited_native": {
                "private_marker": "private-edited-native-link",
            },
            "current_source_inspection": copy.deepcopy(inspection),
        }
        self.write_json(
            self.original / evidence.EVIDENCE_NAME, self.first_native
        )
        self.negative = {
            "schema": "motionwright.manim.camera.original-media-negative/1",
            "status": "EXPECTED_GEOMETRIC_REJECTION",
            "original_media_sha256": evidence.sha(self.original_media),
            "edited_plan_sha256": self.edited_plan,
            "original_native_evidence_sha256": evidence.sha(
                evidence.read(self.original / evidence.EVIDENCE_NAME)
            ),
            "decoded_frame_count": 120,
            "metadata_and_original_digest_verified_before_geometry": True,
            "synthetic_media_used": False,
            "provider_dispatch_performed": False,
            "automatic_retry": False,
            "human_acceptance": False,
        }
        self.observation = {
            "schema": "motionwright.manim.camera.source-cycle-observation/1",
            "source_cycle_observation": "PASS_REQUIRES_SEALED_CUSTODY",
            "motionwright_sha": self.source,
            "semwright_sha": self.pin,
            "native_effect_invocations": 2,
            "saved_camera_source_cycle_verified": True,
            "same_sqlite_store_reopened_in_fresh_process": True,
            "current_export_equals_second_native_carrier": True,
            "original_native_receipts_unchanged": True,
            "fresh_requests_and_distinct_once_scopes": True,
            "source_export_grants_native_authority": False,
            "motion_canvas_equivalence_verified": False,
            "voice_or_caption_mixing_verified": False,
            "human_acceptance": False,
            "automatic_retry": False,
            "original_request_id": self.first_native["request_id"],
            "edited_request_id": self.second_native["request_id"],
            "original_plan_sha256": self.original_plan,
            "edited_plan_sha256": self.edited_plan,
            "private_marker": "private-observation-not-for-publication",
        }
        self.retained = {
            "original_native_evidence_sha256":
                self.original / evidence.EVIDENCE_NAME,
            "edited_native_evidence_sha256":
                self.edited / evidence.EVIDENCE_NAME,
            "edit_receipt_sha256":
                self.edited / "source-cycle-edit.json",
            "fresh_source_readback_sha256":
                self.edited / "source-cycle-readback.json",
            "original_media_negative_sha256":
                self.edited / "original-media-edited-geometry-negative.json",
        }
        self.persist_current_receipts()
        self.reset_verifier_calls()

    def persist_current_receipts(self):
        """Rehash outer custody so deep negative cases reach their own checks."""
        self.write_json(
            self.original / evidence.EVIDENCE_NAME, self.first_native
        )
        self.write_json(
            self.edited / evidence.EVIDENCE_NAME, self.second_native
        )
        self.write_json(self.original / "result.json", self.first_result)
        self.write_json(self.edited / "result.json", self.second_result)
        self.write_json(self.retained["edit_receipt_sha256"], self.edit)
        self.readback["edit_receipt_sha256"] = evidence.sha(
            evidence.read(self.retained["edit_receipt_sha256"])
        )
        self.write_json(
            self.retained["fresh_source_readback_sha256"], self.readback
        )
        self.write_json(
            self.retained["original_media_negative_sha256"], self.negative
        )
        for key, path in self.retained.items():
            self.observation[key] = evidence.sha(evidence.read(path))
        self.write_json(
            self.edited / "source-cycle-observation.json", self.observation
        )

    def reset_verifier_calls(self):
        for verifier in (
            self.publication, self.edit_contract, self.native_link
        ):
            verifier.reset_mock()

    def verified_publication(self, root, source, pin, seal, *, fixture):
        self.assertEqual(source, self.source)
        self.assertEqual(pin, self.pin)
        if fixture == "original":
            self.assertEqual(root, self.original)
            self.assertEqual(seal, self.sealed_original)
            return self.original_media, copy.deepcopy(self.first_projection)
        if fixture == "edited1040":
            self.assertEqual(root, self.edited)
            self.assertEqual(seal, self.sealed_edited)
            return self.edited_media, copy.deepcopy(self.second_projection)
        self.fail(f"Unexpected canonical verifier fixture: {fixture}")

    def checked_edit_contract(self, edit, original):
        self.assertEqual(edit, self.edit)
        self.assertEqual(
            original,
            {
                "prepared": {
                    "binding": self.first_result["project"],
                },
                "native": self.first_native,
                "native_path": self.original / evidence.EVIDENCE_NAME,
            },
        )
        return copy.deepcopy(self.edited_binding)

    def verify_native_link(self, link, native, path):
        self.assertEqual(link, self.readback["edited_native"])
        self.assertEqual(native, self.second_native)
        self.assertEqual(path, self.edited / evidence.EVIDENCE_NAME)
        return copy.deepcopy(self.carrier)

    def arguments(self):
        return (
            self.original, self.edited,
            self.sealed_original, self.sealed_edited,
            self.source, self.pin,
        )

    def summary(self, arguments=None):
        return self.namespace["verified_cycle_summary"](
            *(self.arguments() if arguments is None else arguments)
        )

    def assert_rejected(self, code, arguments=None):
        with self.assertRaisesRegex(
            evidence.CameraEvidenceError, re.escape(code)
        ):
            self.summary(arguments)

    def expected_public_summary(self):
        return {
            "schema": "motionwright.manim.camera.source-cycle-public/1",
            "status": "VERIFIED_SCOPED_CAMERA_CYCLE",
            "motionwright_sha": self.source,
            "semwright_sha": self.pin,
            "profile": "camera120-source-cycle",
            "native_effect_invocations": 2,
            "publisher_native_effect_invocations": 0,
            "original": {
                "media_sha256": evidence.sha(self.original_media),
                "plan_sha256": self.original_plan,
                "sealed_originals_sha256":
                    self.first_projection["sealed_originals_sha256"],
                "decoded_frame_count": 120,
            },
            "edited": {
                "media_sha256": evidence.sha(self.edited_media),
                "plan_sha256": self.edited_plan,
                "sealed_originals_sha256":
                    self.second_projection["sealed_originals_sha256"],
                "decoded_frame_count": 120,
            },
            "saved_camera_source_cycle_observed": True,
            "original_media_rejected_by_edited_geometry": True,
            "source_export_grants_native_authority": False,
            "motion_canvas_equivalence_verified": False,
            "voice_or_caption_mixing_verified": False,
            "full_manim_fidelity_verified": False,
            "human_acceptance": False,
            "automatic_retry": False,
            "release_approved": False,
        }

    def assert_exact_verifier_calls(self):
        self.assertEqual(
            self.publication.call_args_list,
            [
                mock.call(
                    self.original, self.source, self.pin,
                    self.sealed_original, fixture="original",
                ),
                mock.call(
                    self.edited, self.source, self.pin,
                    self.sealed_edited, fixture="edited1040",
                ),
            ],
        )
        self.edit_contract.assert_called_once()
        self.native_link.assert_called_once()

    def test_complete_cycle_publishes_only_allowlisted_summary(self):
        expected = self.expected_public_summary()
        self.assertEqual(self.summary(), expected)
        self.assert_exact_verifier_calls()

        packed = json.dumps(expected, sort_keys=True)
        for private_value in (
            self.first_binding["project_id"],
            self.first_binding["scene_id"],
            self.first_binding["deliverable_id"],
            self.first_native["request_id"],
            self.second_native["request_id"],
            "private-original-native-evidence",
            "private-edited-native-evidence",
            "private-observation-not-for-publication",
            evidence.EVIDENCE_NAME,
        ):
            self.assertNotIn(private_value, packed)

        self.reset_verifier_calls()
        with working_directory(self.checkout), \
             mock.patch.object(sys, "path", list(sys.path)), \
             mock.patch.dict(
                 os.environ,
                 {
                     "NATIVE_CI_JOB_STATUS": "success",
                     "GITHUB_SHA": self.source,
                     "RUNNER_TEMP": str(self.runner_temp),
                 },
                 clear=True,
             ):
            self.namespace["main"]()

        output = self.checkout / "verification" / "native-manim-public"
        self.assertEqual(
            {path.name for path in output.iterdir()},
            {"public-evidence.json"},
        )
        self.assertEqual(
            json.loads((output / "public-evidence.json").read_bytes()),
            expected,
        )
        self.assert_exact_verifier_calls()

    def test_rejected_original_custody_stops_before_the_edited_verifier(self):
        failure = evidence.CameraEvidenceError(
            "original_custody_fixture_rejected"
        )
        self.publication.side_effect = failure
        with self.assertRaises(evidence.CameraEvidenceError) as caught:
            self.summary()
        self.assertIs(caught.exception, failure)
        self.publication.assert_called_once_with(
            self.original, self.source, self.pin,
            self.sealed_original, fixture="original",
        )
        self.edit_contract.assert_not_called()
        self.native_link.assert_not_called()

    def test_rejected_edited_custody_cannot_publish_the_valid_original_alone(self):
        failure = evidence.CameraEvidenceError(
            "edited_custody_fixture_rejected"
        )

        def reject_second(root, source, pin, seal, *, fixture):
            if fixture == "edited1040":
                self.assertEqual(root, self.edited)
                self.assertEqual(source, self.source)
                self.assertEqual(pin, self.pin)
                self.assertEqual(seal, self.sealed_edited)
                raise failure
            return self.verified_publication(
                root, source, pin, seal, fixture=fixture
            )

        self.publication.side_effect = reject_second
        with self.assertRaises(evidence.CameraEvidenceError) as caught:
            self.summary()
        self.assertIs(caught.exception, failure)
        self.assertEqual(
            self.publication.call_args_list,
            [
                mock.call(
                    self.original, self.source, self.pin,
                    self.sealed_original, fixture="original",
                ),
                mock.call(
                    self.edited, self.source, self.pin,
                    self.sealed_edited, fixture="edited1040",
                ),
            ],
        )
        self.edit_contract.assert_not_called()
        self.native_link.assert_not_called()

    def test_reused_or_unrelated_cycle_endpoints_are_rejected(self):
        cases = (
            ("paths", "cycle_public_distinct_paths"),
            ("recipient", "cycle_public_two_distinct_seals"),
            ("seal", "cycle_public_two_distinct_seals"),
            ("request", "cycle_public_fresh_requests"),
            ("plan", "cycle_public_distinct_source_and_media"),
            ("media", "cycle_public_distinct_source_and_media"),
        )
        for case, reason in cases:
            with self.subTest(case=case):
                self.new_fixture()
                arguments = list(self.arguments())
                if case == "paths":
                    arguments[1] = self.original
                elif case == "recipient":
                    self.second_projection[
                        "sealed_originals_recipient_sha256"
                    ] = self.bad_sha
                elif case == "seal":
                    self.write_bytes(
                        self.sealed_edited,
                        evidence.read(self.sealed_original),
                    )
                    self.second_projection["sealed_originals_sha256"] = (
                        self.first_projection["sealed_originals_sha256"]
                    )
                elif case == "request":
                    self.second_native["request_id"] = (
                        self.first_native["request_id"]
                    )
                    self.observation["edited_request_id"] = (
                        self.first_native["request_id"]
                    )
                elif case == "plan":
                    self.second_projection["compiled_plan_sha256"] = (
                        self.original_plan
                    )
                    self.observation["edited_plan_sha256"] = (
                        self.original_plan
                    )
                elif case == "media":
                    self.edited_media = self.original_media
                self.persist_current_receipts()
                self.assert_rejected(reason, arguments)

    def test_observation_authority_and_edited_binding_must_match(self):
        cases = (
            ("source_format", "cycle_public_source_identity"),
            ("observation_source", "cycle_public_observation_scope"),
            ("observation_pin", "cycle_public_observation_scope"),
            ("float_invocations", "cycle_public_observation_scope"),
            ("retry_authority", "cycle_public_observation_authority"),
            ("edited_result", "cycle_public_edited_binding"),
            ("readback_revision", "cycle_public_readback_binding"),
            ("readback_window", "cycle_public_readback_binding"),
            ("readback_execution", "cycle_public_readback_authority"),
        )
        for case, reason in cases:
            with self.subTest(case=case):
                self.new_fixture()
                arguments = list(self.arguments())
                if case == "source_format":
                    arguments[4] = "A" * 40
                elif case == "observation_source":
                    self.observation["motionwright_sha"] = "a" * 40
                elif case == "observation_pin":
                    self.observation["semwright_sha"] = "b" * 40
                elif case == "float_invocations":
                    # Equality with 2 alone must not admit a JSON float.
                    self.observation["native_effect_invocations"] = 2.0
                elif case == "retry_authority":
                    self.observation["automatic_retry"] = True
                elif case == "edited_result":
                    self.second_result["project"]["revision"] += 1
                elif case == "readback_revision":
                    self.readback["canonical_stamp"]["revision"] += 1
                elif case == "readback_window":
                    self.readback["production_receipt_window_limit"] = 255
                elif case == "readback_execution":
                    self.readback["native_execution_performed"] = True
                self.persist_current_receipts()
                self.assert_rejected(reason, arguments)

    def test_source_history_and_carrier_closure_must_match(self):
        cases = (
            ("history", "cycle_public_source_history"),
            ("bundle", "cycle_public_export_carrier_closure"),
            ("plan", "cycle_public_export_carrier_closure"),
            ("program", "cycle_public_export_carrier_closure"),
        )
        for case, reason in cases:
            with self.subTest(case=case):
                self.new_fixture()
                if case == "history":
                    self.readback["original_source_sha256"] = self.bad_sha
                elif case == "bundle":
                    self.carrier["sha256"] = self.bad_sha
                else:
                    key = "plan_sha256" if case == "plan" else "source_sha256"
                    self.carrier["closure"][key] = self.bad_sha
                    # Keep the observation and edit mutually coherent. The
                    # publisher must still bind them to the verified plan
                    # and actual second result, not just to each other.
                    self.edit["current_source_inspection"][
                        "current_closure"
                    ] = copy.deepcopy(self.carrier["closure"])
                    self.readback["current_source_inspection"] = (
                        copy.deepcopy(self.edit["current_source_inspection"])
                    )
                self.persist_current_receipts()
                self.assert_rejected(reason)

    def test_original_media_negative_requires_exact_custody_and_binding(self):
        for case in ("missing", "unhashed_edit", "media", "plan", "synthetic"):
            with self.subTest(case=case):
                self.new_fixture()
                negative_path = self.retained["original_media_negative_sha256"]
                if case == "missing":
                    negative_path.unlink()
                    with self.assertRaises(FileNotFoundError):
                        self.summary()
                    self.assertFalse(negative_path.exists())
                    continue
                if case == "unhashed_edit":
                    changed = {
                        **self.negative,
                        "private_tamper_marker": "unrecorded changed bytes",
                    }
                    self.write_json(negative_path, changed)
                    self.assert_rejected(
                        "cycle_public_retained_receipt_digest"
                    )
                    continue
                if case == "media":
                    self.negative["original_media_sha256"] = self.bad_sha
                elif case == "plan":
                    self.negative["edited_plan_sha256"] = self.bad_sha
                elif case == "synthetic":
                    self.negative["synthetic_media_used"] = True
                self.persist_current_receipts()
                self.assert_rejected(
                    "cycle_public_original_media_negative"
                )

    def test_failed_job_or_publication_error_never_discloses_private_data(self):
        expected = {
            "schema": "motionwright.manim.camera.source-cycle-public/1",
            "status": "NOT_PUBLISHED",
            "reason": "cycle_or_prior_job_verification_incomplete",
            "publisher_native_effect_invocations": 0,
            "human_acceptance": False,
            "automatic_retry": False,
            "release_approved": False,
        }
        secret = "private-original-path-and-native-receipt-must-not-leak"
        for prior_failure in (True, False):
            with self.subTest(prior_failure=prior_failure):
                self.new_fixture()
                verifier = mock.Mock(
                    side_effect=evidence.CameraEvidenceError(secret)
                )
                with ExitStack() as stack:
                    stack.enter_context(working_directory(self.checkout))
                    stack.enter_context(
                        mock.patch.object(sys, "path", list(sys.path))
                    )
                    stack.enter_context(
                        mock.patch.dict(
                            self.namespace,
                            {"verified_cycle_summary": verifier},
                        )
                    )
                    stack.enter_context(
                        mock.patch.dict(
                            os.environ,
                            {
                                "NATIVE_CI_JOB_STATUS": (
                                    "failure" if prior_failure else "success"
                                ),
                                "GITHUB_SHA": self.source,
                                "RUNNER_TEMP": str(self.runner_temp),
                            },
                            clear=True,
                        )
                    )
                    reads = None
                    if prior_failure:
                        reads = stack.enter_context(
                            mock.patch.object(
                                Path,
                                "read_bytes",
                                side_effect=AssertionError(
                                    "Failed job must not read publication inputs"
                                ),
                            )
                        )
                    with self.assertRaises(SystemExit) as caught:
                        self.namespace["main"]()
                    if reads is not None:
                        reads.assert_not_called()

                if prior_failure:
                    verifier.assert_not_called()
                else:
                    verifier.assert_called_once_with(*self.arguments())
                self.publication.assert_not_called()
                self.edit_contract.assert_not_called()
                self.native_link.assert_not_called()
                self.assertNotIn(secret, str(caught.exception))
                self.assertEqual(
                    str(caught.exception),
                    "Camera cycle publication withheld; inspect encrypted "
                    "originals. No replay.",
                )

                output = (
                    self.checkout / "verification" / "native-manim-public"
                )
                self.assertEqual(
                    {path.name for path in output.iterdir()},
                    {"public-evidence.json"},
                )
                packed = (output / "public-evidence.json").read_bytes()
                self.assertEqual(json.loads(packed), expected)
                self.assertNotIn(secret.encode(), packed)


if __name__ == "__main__":
    unittest.main()
