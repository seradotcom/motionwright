"""Focused parser and exact upstream-byte checks; no Cargo or Rust execution."""

from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

import tauri_generated_permissions as permissions


# Original format! raw string from:
# tauri-utils-2.10.1.crate, SHA256
# 2ff55a614843b9a3f010f211df637c94df2607175b8d0f6e0f4a7c19e18621ee
# member src/acl/build.rs, SHA256
# d198900aea6f34e6f29382fdf741ed51bff7502715f2d0a7ec7b07a45a59d256
# autogenerate_command_permissions, empty header, schema_ref=false.
# This independent original fixture is not imported from the implementation.
UPSTREAM_TEMPLATE = b"""{license_header}# Automatically generated - DO NOT EDIT!
{schema_entry}
[[permission]]
identifier = "allow-{slugified_command}"
description = "Enables the {command} command without any pre-configured scope."
commands.allow = ["{command}"]

[[permission]]
identifier = "deny-{slugified_command}"
description = "Denies the {command} command without any pre-configured scope."
commands.deny = ["{command}"]
"""
UPSTREAM_TEMPLATE_SHA256 = "576c080e285d7a92b0884fa692164b0fe8d048a63f2ef00f427c0c80b63b1e1f"

REAL_COMMANDS = (
    "bootstrap",
    "list_projects",
    "open_project",
    "project_projection_head",
    "project_scene_page",
    "project_scene_nodes",
    "semantic_graph_authoring_context",
    "scene_boundary_authoring_context",
    "preview_branch_combination",
    "issue_project_creation_grant",
    "create_project",
    "recover_project_creation",
    "issue_effect_grant",
    "project_history",
    "project_history_recent",
    "asset_integrity_page",
    "model_request_preflight",
    "production_runtime_status",
    "creative_runtime_doctor",
    "choose_semwright_connection",
    "choose_brand_font_media_root",
    "clear_saved_semwright_connection",
    "workflow_overview",
    "workflow_action",
    "production_jobs",
    "mlt_cleanup_inventory",
    "read_original_native_job_status",
    "production_jobs_history",
    "native_blender_product_source",
    "native_blender_product_attempt",
    "motion_canvas_preflight",
    "render_motion_canvas",
    "render_motion_canvas_window",
    "preflight_multi_segment_mlt_readiness",
    "assemble_av_master",
    "assemble_multisegment_av_master",
    "export_native_av_master",
    "verify_local_media_integrity",
    "review_native_av_master",
    "read_native_av_audio_qc",
    "native_av_audio_qc_receipt_inventory",
    "recover_native_av_audio_qc",
    "preview_native_frame",
    "prepare_file_import",
    "recover_file_import",
    "import_asset_file",
    "import_voice_file",
    "prepare_native_capsule_source_file",
    "import_native_capsule_source_file",
    "recover_native_capsule_source_file",
    "waveform_page",
    "export_project_bundle",
    "export_caption_sidecar",
    "export_otio",
    "inspect_project_bundle",
    "import_project_bundle",
    "native_original_mix_audition",
    "native_narration_take_snapshot",
    "native_narration_replacement_impact",
    "native_paid_generation_journal",
    "native_paid_generation_preflight",
    "native_paid_generation_reserve_preflight",
    "apply_change",
)

_PREFIX = (
    b"fn main() {\n"
    b"let attributes = tauri_build::Attributes::new().app_manifest("
    b"tauri_build::AppManifest::new().commands(&["
)
_SUFFIX = (
    b"]));\n"
    b'tauri_build::try_build(attributes).expect("build failed");\n'
    b"}\n"
)


def source(array: bytes = b'"bootstrap",') -> bytes:
    return _PREFIX + array + _SUFFIX


def original_permission(command: str) -> bytes:
    """Apply only the original generator's four format-string bindings."""
    return (
        UPSTREAM_TEMPLATE.replace(b"{license_header}", b"")
        .replace(b"{schema_entry}", b"")
        .replace(b"{slugified_command}", command.replace("_", "-").encode("ascii"))
        .replace(b"{command}", command.encode("ascii"))
    )


def literals(commands) -> bytes:
    return ", ".join('"' + command + '"' for command in commands).encode("ascii")


class TauriGeneratedPermissionsTests(unittest.TestCase):
    def rejected(self, raw, code: str | None = None):
        expression = "^tauri_permissions_" + (code + "$" if code else "")
        with self.assertRaisesRegex(permissions.TauriGeneratedPermissionsError, expression):
            permissions.derive_command_permissions(raw)

    def test_real_build_rs_63_match_original_upstream_bytes(self):
        self.assertEqual(hashlib.sha256(UPSTREAM_TEMPLATE).hexdigest(),
                         UPSTREAM_TEMPLATE_SHA256)
        path = Path(__file__).resolve().parents[1] / "apps/desktop/src-tauri/build.rs"
        result = permissions.derive_command_permissions(path.read_bytes())
        self.assertEqual(len(result), 63)
        expected_paths = [permissions.GENERATED_ROOT + "/" + command + ".toml"
                          for command in REAL_COMMANDS]
        self.assertEqual(list(result), expected_paths)
        for command, path in zip(REAL_COMMANDS, expected_paths):
            with self.subTest(command=command):
                self.assertEqual(result[path], original_permission(command))

    def test_comments_spacing_and_crlf_preserve_roster(self):
        original = source(b'"bootstrap", "read_mix",')
        comment = b'/* "decoy" /* nested */ AppManifest::new().commands(&["fake"]) */'
        decorated = (
            b'// tauri_build::AppManifest::new().commands(&["fake"])\n'
            + original.replace(b"::", comment + b" :: " + comment)
            .replace(b"&[", b"& " + comment + b" [")
            .replace(b'"bootstrap"', b'"bootstrap" ' + comment)
        ).replace(b"\n", b"\r\n")
        self.assertEqual(permissions.derive_command_permissions(decorated),
                         permissions.derive_command_permissions(original))

    def test_optional_trailing_comma_and_single_command(self):
        for array in (b'"play_clip"', b'"play_clip",'):
            with self.subTest(array=array):
                self.assertEqual(permissions.derive_command_permissions(source(array)), {
                    permissions.GENERATED_ROOT + "/play_clip.toml":
                        original_permission("play_clip"),
                })

    def test_call_like_expect_string_is_opaque(self):
        raw = source().replace(
            b'"build failed"',
            b'"tauri_build::AppManifest::new().commands(&[\\"decoy\\"])"')
        self.assertEqual(permissions.derive_command_permissions(raw),
                         permissions.derive_command_permissions(source()))

    def test_comment_only_call_decoys_are_rejected(self):
        decoy = b'tauri_build::AppManifest::new().commands(&["fake"])'
        for raw in (b"// " + decoy, b"/* " + decoy + b" */",
                    b"fn main() { /* " + decoy + b" */ }"):
            with self.subTest(raw=raw):
                self.rejected(raw)

    def test_string_and_raw_string_call_decoys_are_rejected(self):
        decoys = (
            b'fn main() { let unused = "tauri_build::AppManifest::new().commands(&[\\"fake\\"])"; }',
            b'fn main() { let unused = r#"tauri_build::AppManifest::new().commands(&["fake"])"#; }',
            b'fn main() { let unused = b"AppManifest::new().commands(&[\\"fake\\"])"; }',
        )
        for raw in decoys:
            with self.subTest(raw=raw):
                self.rejected(raw)

    def test_dynamic_or_nonliteral_command_arguments_are_rejected(self):
        cases = (
            source().replace(b'&["bootstrap",]', b"COMMANDS"),
            source().replace(b'&["bootstrap",]', b"&commands()"),
            source(b'include_str!("commands.txt"),'),
            source(b'concat!("boot", "strap"),'),
            source(b'"bootstrap".to_owned(),'),
            source(b'command_name,'),
            source(b'*b"bootstrap",'),
        )
        for raw in cases:
            with self.subTest(raw=raw):
                self.rejected(raw)

    def test_additional_or_unexecuted_source_forms_are_rejected(self):
        raw = source()
        cases = (
            raw + raw,
            raw.replace(b"fn main()", b"fn unused()"),
            raw.replace(b"fn main() {", b"fn main() { if false {").replace(
                b"}\n", b"} }\n"),
            raw.replace(b"fn main() {", b"fn main() { let another = manifest();"),
            b"#[cfg(any())]\n" + raw,
            raw.replace(b"tauri_build::AppManifest", b"other::AppManifest"),
            raw.replace(b"AppManifest::new()", b"AppManifest::default()"),
            raw.replace(b"try_build(attributes)", b"try_build(other_attributes)"),
            raw.replace(b".expect(", b".unwrap_or_else("),
        )
        for candidate in cases:
            with self.subTest(raw=candidate):
                self.rejected(candidate)

    def test_duplicate_commands_are_rejected(self):
        self.rejected(source(b'"bootstrap", "bootstrap",'), "command_duplicate")

    def test_windows_casefold_collisions_are_rejected(self):
        self.rejected(source(b'"bootstrap", "Bootstrap",'), "command_path_collision")

    def test_unsafe_or_injecting_command_names_are_rejected(self):
        for name in ("../escape", "sub/file", ".", "..", "with-dash", "a:b",
                     "word ", "_leading", "line\tbreak", 'a; remove()', "café",
                     "safe\u202eevil"):
            with self.subTest(name=name):
                self.rejected(source(('"' + name + '",').encode("utf-8")),
                              "command_identifier_invalid")
        for raw in (source(b'"quote\\\"injection",'),
                    source(b'"slash\\\\injection",'),
                    source(b'"line\nbreak",'),
                    source(b'"line\rbreak",'),
                    source(b'"nul\x00break",')):
            with self.subTest(raw=raw):
                self.rejected(raw)

    def test_windows_reserved_file_names_are_rejected(self):
        for name in ("CON", "con", "Prn", "AUX", "nul", "COM1", "COM9", "LPT1", "LPT9"):
            with self.subTest(name=name):
                self.rejected(source(literals([name])), "command_windows_reserved")

    def test_escaped_raw_or_byte_command_literals_are_rejected(self):
        for array in (b'"boot\\nstrap",', b'"boot\\x73trap",',
                      b'"boot\\u{73}trap",', b'r"bootstrap",',
                      b'r#"bootstrap"#,', b'b"bootstrap",'):
            with self.subTest(array=array):
                self.rejected(source(array))

    def test_empty_or_malformed_literal_arrays_are_rejected(self):
        for array in (b"", b",", b'"bootstrap",,', b'"bootstrap" "second"',
                      b'["bootstrap"]', b'"bootstrap";', b'""'):
            with self.subTest(array=array):
                self.rejected(source(array))
        self.rejected(source().replace(b"]));", b"));"))

    def test_unterminated_comments_and_strings_are_rejected(self):
        for raw in (source() + b"/*", b"/* /* */" + source(),
                    source(b'"bootstrap'), source() + b'"'):
            with self.subTest(raw=raw):
                self.rejected(raw)

    def test_nonbyte_invalid_encoding_and_controls_are_rejected(self):
        for raw in (None, "source", bytearray(source()), memoryview(source())):
            with self.subTest(type=type(raw).__name__):
                self.rejected(raw, "source_bytes_required")
        self.rejected(b"\xff" + source(), "source_encoding_invalid")
        for control in (b"\x00", b"\x01", b"\x0b", b"\x0c", b"\x1f", b"\x7f"):
            with self.subTest(control=control):
                self.rejected(b"// " + control + b"\n" + source(),
                              "source_control_rejected")

    def test_source_byte_budget_is_inclusive(self):
        raw = source()
        padded = raw + b" " * (permissions.MAX_BUILD_RS_BYTES - len(raw))
        self.assertEqual(permissions.derive_command_permissions(padded),
                         permissions.derive_command_permissions(raw))
        self.rejected(padded + b" ", "source_byte_budget")
        self.rejected(b"", "source_byte_budget")

    def test_command_count_budget_is_inclusive(self):
        commands = ["command_" + str(index) for index in range(permissions.MAX_COMMANDS)]
        self.assertEqual(len(permissions.derive_command_permissions(source(literals(commands)))),
                         permissions.MAX_COMMANDS)
        self.rejected(source(literals(commands + ["one_more"])), "command_count_budget")

    def test_command_byte_budget_is_inclusive(self):
        command = "x" * permissions.MAX_COMMAND_BYTES
        result = permissions.derive_command_permissions(source(literals([command])))
        self.assertEqual(next(iter(result.values())), original_permission(command))
        self.rejected(source(literals([command + "x"])), "command_byte_budget")

    def test_nested_comment_depth_is_bounded(self):
        def nested(depth):
            return b"/*" * depth + b" comment " + b"*/" * depth + source()
        self.assertEqual(permissions.derive_command_permissions(nested(permissions.MAX_COMMENT_DEPTH)),
                         permissions.derive_command_permissions(source()))
        self.rejected(nested(permissions.MAX_COMMENT_DEPTH + 1), "comment_depth_budget")

    def test_token_and_total_output_budgets_are_bounded(self):
        self.rejected(b"x " * (permissions.MAX_TOKENS + 1) + source(), "token_budget")
        commands = [
            ("command_" + str(index) + "_").ljust(permissions.MAX_COMMAND_BYTES, "x")
            for index in range(permissions.MAX_COMMANDS)
        ]
        self.rejected(source(literals(commands)), "total_permission_byte_budget")


if __name__ == "__main__":
    unittest.main()
