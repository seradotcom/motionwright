"""Schema regression; synthetic values, real Rust seed/request JSON shape."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_blender_lit_still_e2e as producer
import native_evidence_projection as selection

def rust_seed_shape():
    project = "123e4567-e89b-12d3-a456-426614174000"
    generation = "11111111-2222-4333-8444-555555555555"
    resource = "project:" + project
    return {
        "schema": "motionwright.blender.lit-still-seed/1",
        "fixture_kind": "first_party_synthetic",
        "fixture_contract_sha256": producer.pixels.FIXTURE_CONTRACT_SHA256,
        "project_id": project, "resource": resource,
        "generation": generation, "revision": 1,
        "expected": {"resource": resource, "generation": generation, "revision": 1},
        "scene_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        "role_node_ids": {
            "edited_gold_card": "00000000-0000-4000-8000-000000000001",
            "blue_anchor": "00000000-0000-4000-8000-000000000002",
            "rose_disk": "00000000-0000-4000-8000-000000000003"},
        "project_json": {"path": "source-project.json", "bytes": 128, "sha256": "a" * 64},
        "plan_path": "source-plan.json", "sqlite_reopened": True,
        "native_execution": "NOT_RUN", "human_acceptance": False}

class RustSeedSelectionContractTests(unittest.TestCase):
    def setUp(self):
        self.policies, _, self.entries = producer.selection_contract()
        self.seed_rule = self.policies["lit-seed-v1"]
    def test_real_rust_seed_shape_survives_exact_original(self):
        seed = rust_seed_shape()
        projected = selection.project(seed, self.seed_rule, exact=True)
        self.assertEqual(projected, seed)
        self.assertEqual(projected["expected"]["resource"], seed["resource"])
    def test_seed_capture_remains_json_original(self):
        matches = [entry for entry in self.entries if entry[0] == "seed/seed.json"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0][4:], ("json-original", "lit-seed-v1"))
    def test_seed_requires_expected_resource(self):
        seed = rust_seed_shape()
        del seed["expected"]["resource"]
        with self.assertRaises(selection.SelectionError) as caught:
            selection.project(seed, self.seed_rule, exact=True)
        self.assertEqual(caught.exception.code, "projection-required-field")
    def test_seed_rejects_malformed_expected_resource(self):
        project = rust_seed_shape()["project_id"]
        for resource in ("scene:" + project, "project:not-a-uuid", "project:" + project[:-1]):
            with self.subTest(resource=resource):
                seed = rust_seed_shape()
                seed["expected"]["resource"] = resource
                with self.assertRaises(selection.SelectionError) as caught:
                    selection.project(seed, self.seed_rule, exact=True)
                self.assertEqual(caught.exception.code, "projection-string-pattern")
    def test_seed_exact_rejects_unregistered_nested_and_root_fields(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                seed = rust_seed_shape()
                target = seed["expected"] if nested else seed
                target["unregistered"] = "unreviewed"
                with self.assertRaises(selection.SelectionError) as caught:
                    selection.project(seed, self.seed_rule, exact=True)
                self.assertEqual(caught.exception.code, "original-json-extra-field")
    def test_phase_projection_preserves_real_revision_stamp_resource(self):
        seed = rust_seed_shape()
        request = {
            "project_id": seed["project_id"], "expected": copy.deepcopy(seed["expected"]),
            "scene_id": seed["scene_id"], "request_id": "blender-lit-12345",
            "phase": "create", "source": None, "edit_node_id": None}
        rule = self.policies["lit-phase-v1"].fields["request"].rule
        projected = selection.project(request, rule, exact=False)
        self.assertEqual(projected, request)
        self.assertEqual(projected["expected"]["resource"], seed["resource"])
if __name__ == "__main__":
    unittest.main()
