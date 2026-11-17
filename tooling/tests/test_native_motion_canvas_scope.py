"""Pure retained-profile contracts; no Native, runtime, media, or keys."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict, fields, is_dataclass
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_motion_canvas_scope import RETAINED_SCOPES, RetainedScope, retained_scope


class RetainedScopeTests(unittest.TestCase):
    def test_historical_editable_profile_preserves_exact_contract(self):
        profile = retained_scope("editable-source-cycle")
        self.assertIs(type(profile), RetainedScope)
        self.assertTrue(is_dataclass(profile))
        self.assertEqual(asdict(profile), {
            "selection": "editable-source-cycle",
            "stages": ("original", "reopened", "edited", "edited_reopened"),
            "jobs": 5,
            "hosts": 4,
            "preflight_prefix": "mw-mc-cycle-preflight-",
            "owner_prefix": "mw-mc-cycle-",
            "evidence_directory": "native-editable-private",
            "public_directory": "native-editable-public",
            "sdk_directory": "verification/mc-cycle-sdk",
        })
        self.assertEqual(profile.full_frames, 300)
        self.assertIs(type(profile.full_frames), int)

    def test_out_cubic_profile_is_one_phase_two_jobs_one_host(self):
        profile = retained_scope("out-cubic-xy")
        self.assertEqual(asdict(profile), {
            "selection": "out-cubic-xy",
            "stages": ("original",),
            "jobs": 2,
            "hosts": 1,
            "preflight_prefix": "mw-mc-xy-preflight-",
            "owner_prefix": "mw-mc-xy-",
            "evidence_directory": "native-out-cubic-xy-private",
            "public_directory": "native-out-cubic-xy-public",
            "sdk_directory": "verification/mc-xy-sdk",
        })
        self.assertEqual(profile.full_frames, 120)
        self.assertIs(type(profile.full_frames), int)
        self.assertEqual({field.name for field in fields(RetainedScope)}, {
            "selection", "stages", "jobs", "hosts", "preflight_prefix",
            "owner_prefix", "evidence_directory", "public_directory", "sdk_directory",
        })

    def test_selection_rejects_non_exact_string_types(self):
        class StringSubclass(str):
            pass

        class StringLike:
            def __str__(self):
                return "out-cubic-xy"

        invalid = (
            None, True, False, 0, 2, 2.0, b"out-cubic-xy",
            bytearray(b"out-cubic-xy"), ["out-cubic-xy"], ("out-cubic-xy",),
            {"selection": "out-cubic-xy"}, {"out-cubic-xy"},
            StringSubclass("out-cubic-xy"), StringLike(),
        )
        for value in invalid:
            with self.subTest(kind=type(value).__name__):
                with self.assertRaisesRegex(TypeError, "exact str"):
                    retained_scope(value)

    def test_selection_rejects_unknown_aliases_and_normalization(self):
        for value in (
            "", "standard", "n83-preview", "original", "OUT-CUBIC-XY",
            " out-cubic-xy", "out-cubic-xy ", "out-cubic-xy\n",
            "out_cubic_xy", "editable-source-cycle/original",
            "../out-cubic-xy", "out-cubic-xy\0",
            '{"selection":"out-cubic-xy","jobs":5}',
        ):
            with self.subTest(selection=value):
                with self.assertRaisesRegex(ValueError, "unsupported retained"):
                    retained_scope(value)

    def test_profiles_and_stage_rosters_are_immutable(self):
        self.assertIs(type(RETAINED_SCOPES), frozenset)
        self.assertEqual(RETAINED_SCOPES, {"editable-source-cycle", "out-cubic-xy"})
        with self.assertRaises(AttributeError):
            RETAINED_SCOPES.add("standard")
        for selection in RETAINED_SCOPES:
            profile = retained_scope(selection)
            before = asdict(profile)
            self.assertFalse(hasattr(profile, "__dict__"))
            self.assertIs(type(profile.stages), tuple)
            self.assertTrue(all(type(stage) is str for stage in profile.stages))
            for field in fields(profile):
                with self.subTest(selection=selection, field=field.name):
                    with self.assertRaises(FrozenInstanceError):
                        setattr(profile, field.name, "external")
            with self.assertRaises(TypeError):
                profile.stages[0] = "external"
            self.assertEqual(asdict(retained_scope(selection)), before)

    def test_profiles_have_separate_namespaces_and_no_external_overrides(self):
        original = retained_scope("editable-source-cycle")
        cubic = retained_scope("out-cubic-xy")
        self.assertIsNot(original, cubic)
        for field in (
            "preflight_prefix", "owner_prefix", "evidence_directory",
            "public_directory", "sdk_directory",
        ):
            with self.subTest(field=field):
                self.assertNotEqual(getattr(original, field), getattr(cubic, field))
        for override in (
            {"jobs": 5}, {"hosts": 4}, {"full_frames": 300},
            {"stages": original.stages}, {"owner_prefix": original.owner_prefix},
            {"sdk_directory": original.sdk_directory},
        ):
            with self.subTest(override=override):
                with self.assertRaises(TypeError):
                    retained_scope("out-cubic-xy", **override)
        with self.assertRaises(TypeError):
            retained_scope("out-cubic-xy", original)
        self.assertEqual((cubic.jobs, cubic.hosts, cubic.full_frames), (2, 1, 120))
        self.assertEqual((original.jobs, original.hosts, original.full_frames), (5, 4, 300))


if __name__ == "__main__":
    unittest.main()
