#!/usr/bin/env python3
"""Verify a frozen SDK source pair and affected Linux source contracts.

The Foundation observer owns process, stream, binary-enumeration and assertion
custody. This module supplies the SDK selection and exact Cargo/source bindings.
Synthetic encoders and browser API mocks establish source behavior only.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys

try:
    from tooling import ci_foundation_causal as observer
    from tooling import ci_storage_apply_recovery as custody
except ModuleNotFoundError as error:
    if error.name != "tooling":
        raise
    import ci_foundation_causal as observer
    import ci_storage_apply_recovery as custody

WORKFLOW = ".github/workflows/sdk-native-source-verification.yml"
HELPER = "tooling/ci_sdk_native_source.py"
TEST = "tooling/tests/test_ci_sdk_native_source.py"
BASE = "41259eaeea54c6794e19378628de52e30e477834"
RED = "b4be734d84cb1546f30928a69b7d094d37f6db16"
GREEN = "def9c35e38432704cbb45d9ba2c3683a8dd373ba"
TREES = {RED: "7b6f87c2709e5b1a3fc317fdee54214ba5aec712",
         GREEN: "7a89faaf881321f230d78cb5fa004dc26d75216c"}
REPAIR_PATHS = tuple("crates/driver-mlt-video/src/" + x + ".rs"
                     for x in ("color", "jobs", "runtime"))
GATE_PINS = {
    "tooling/ci_foundation_causal.py": "1d31c58aa7d63c927ceb2518282a433e0f728fc125da8978fb635b18cff354c2",
    "tooling/ci_storage_apply_recovery.py": "9aea9a41d365a36fd1abae86ec5a0974236d62d9781a372eace0578ef2da1520",
}
GATE_FILES = (WORKFLOW, HELPER, TEST, *GATE_PINS,
              "tooling/ci_test_runner.py", "Cargo.toml", "Cargo.lock", "SOURCE_LOCK.json")
SHA = re.compile(r"[0-9a-f]{40}\Z")
require = custody.require

FROZEN = {'b4be734d84cb1546f30928a69b7d094d37f6db16': {'.gitattributes': '78eb00c800c448e3305c394dffb1a2bcffce39a9a10978e462769537941a3fab',
                                              'Cargo.lock': 'ed67a971ee483aa2fd8eca71e8ff25e565f03fd98b4a4e1a90b97bd7696a30d4',
                                              'crates/driver-mlt-video/Cargo.toml': 'f64edc760430b26575a3a255425c35bfe492ab6aa8179a573536f59b89312740',
                                              'crates/driver-mlt-video/fixtures/SHA256SUMS.json': '83e5c38c02851ece2c71f75a3d00a420bfa484fa00d6726337b9e29c8d6cfebe',
                                              'crates/driver-mlt-video/fixtures/expected/capabilities.json': '6b1fd9a1ca245a652649018aa7a94b4b209cf528b210e3176d5d9ed1610cabba',
                                              'crates/driver-mlt-video/fixtures/expected/catalog-wire.json': '194327513f1eff5eb97a87348b96923785049853ea95ccfbb6bd6864a21f5569',
                                              'crates/driver-mlt-video/fixtures/expected/digests.json': '2223ef0d874fea17e93a97b108d560103eeb319eac8b21cb9c79393c3292d814',
                                              'crates/driver-mlt-video/src/app.rs': 'fa149b9c68a4227bcb787b756ba4826ca5d124f7da329f4b1f24da61480ddac3',
                                              'crates/driver-mlt-video/src/bin/fake_ffprobe.rs': '98908c9710946fa2a85d62c2218adea06658c93d1da6ff3cae244351515923b8',
                                              'crates/driver-mlt-video/src/bin/runtime_runner.rs': '6f8604d2ffef174ae12a70619bacbdf62b526e8ff24b360ed46392c59d974810',
                                              'crates/driver-mlt-video/src/catalog.json': '6b1fd9a1ca245a652649018aa7a94b4b209cf528b210e3176d5d9ed1610cabba',
                                              'crates/driver-mlt-video/src/color.rs': '4ec72e659897e620746d2016e97876ca22df892bed5c3127e1f0fa9476c274e1',
                                              'crates/driver-mlt-video/src/fs.rs': '17e41e92da06398d00d1dd5c83907e0f0f4dfc880cb2bc7cb15ca6f78e80ccab',
                                              'crates/driver-mlt-video/src/jobs.rs': 'ed2130bc0c6cfbc2430177b76adada476a899d1505b3d946d282addb8e429150',
                                              'crates/driver-mlt-video/src/lib.rs': '4c90f00009b71fd501edab75d2bb043b1c18ee2b66e6ec14ea7ba50d134b6783',
                                              'crates/driver-mlt-video/src/main.rs': '60c36fdcccfa3eab1dbc0375c5e79e47410c68b6ad91ae55b9123c5405ec1b18',
                                              'crates/driver-mlt-video/src/runtime.rs': '98e5e9cbc560f83f515f2c6bf0e6027778a6196f9135889a9c8a7ea2009701bd',
                                              'crates/video-domain/src/lib.rs': '7df81ad40a112814c23a39d320c346189a1823309df1b921d50b945924fb0491',
                                              'crates/video-domain/src/native_observation.rs': '7b2c3f94511e8152980839982d8fc8e3bbc18b2d7527eda6e58d74d836f122dc',
                                              'scripts/generate-mlt-catalog-fixtures.py': '39eb15e7b1f395e63b2f03f065b03348a1c83e4b28b2e2f07092d502b84594e2',
                                              'crates/driver-motion-canvas/src/compiler.rs': 'f54104c1ea9384572d55f93fe280b4f5dec101fa71793e5e788ab89dcaaf74f3',
                                              'crates/driver-motion-canvas/src/renderer.rs': '77358406c409670cb419467807d53850f7dc89d7e426a6b51be15aa5ffa6c800',
                                              'crates/driver-motion-canvas/src/security.rs': '5d74b96f0bf703d663ce1531d503d4daf39842bbf0bb4723c7dd73136eabb53e',
                                              'integrations/motion-canvas/runtime/render.mjs': '2ca37c29344c26ac1d38b170b6a54040ec1ffadf838b71dbd84deea056e9698f'},
 'def9c35e38432704cbb45d9ba2c3683a8dd373ba': {'.gitattributes': '78eb00c800c448e3305c394dffb1a2bcffce39a9a10978e462769537941a3fab',
                                              'Cargo.lock': 'ed67a971ee483aa2fd8eca71e8ff25e565f03fd98b4a4e1a90b97bd7696a30d4',
                                              'crates/driver-mlt-video/Cargo.toml': 'f64edc760430b26575a3a255425c35bfe492ab6aa8179a573536f59b89312740',
                                              'crates/driver-mlt-video/fixtures/SHA256SUMS.json': '83e5c38c02851ece2c71f75a3d00a420bfa484fa00d6726337b9e29c8d6cfebe',
                                              'crates/driver-mlt-video/fixtures/expected/capabilities.json': '6b1fd9a1ca245a652649018aa7a94b4b209cf528b210e3176d5d9ed1610cabba',
                                              'crates/driver-mlt-video/fixtures/expected/catalog-wire.json': '194327513f1eff5eb97a87348b96923785049853ea95ccfbb6bd6864a21f5569',
                                              'crates/driver-mlt-video/fixtures/expected/digests.json': '2223ef0d874fea17e93a97b108d560103eeb319eac8b21cb9c79393c3292d814',
                                              'crates/driver-mlt-video/src/app.rs': 'fa149b9c68a4227bcb787b756ba4826ca5d124f7da329f4b1f24da61480ddac3',
                                              'crates/driver-mlt-video/src/bin/fake_ffprobe.rs': '98908c9710946fa2a85d62c2218adea06658c93d1da6ff3cae244351515923b8',
                                              'crates/driver-mlt-video/src/bin/runtime_runner.rs': '6f8604d2ffef174ae12a70619bacbdf62b526e8ff24b360ed46392c59d974810',
                                              'crates/driver-mlt-video/src/catalog.json': '6b1fd9a1ca245a652649018aa7a94b4b209cf528b210e3176d5d9ed1610cabba',
                                              'crates/driver-mlt-video/src/color.rs': '57c6f7cd31ded0a7a76b532dedcbfd7ecbc7736019367e89224921d6d413a1a6',
                                              'crates/driver-mlt-video/src/fs.rs': '17e41e92da06398d00d1dd5c83907e0f0f4dfc880cb2bc7cb15ca6f78e80ccab',
                                              'crates/driver-mlt-video/src/jobs.rs': '48221bd3d2d0ba23faf42b394e13fe015b68d56b100117fb0b45a2a68d9de0eb',
                                              'crates/driver-mlt-video/src/lib.rs': '4c90f00009b71fd501edab75d2bb043b1c18ee2b66e6ec14ea7ba50d134b6783',
                                              'crates/driver-mlt-video/src/main.rs': '60c36fdcccfa3eab1dbc0375c5e79e47410c68b6ad91ae55b9123c5405ec1b18',
                                              'crates/driver-mlt-video/src/runtime.rs': '7ac6eb265e0d8fa3923e7da004dca8c27f783d31f17c07b121f518f0a6c5272c',
                                              'crates/video-domain/src/lib.rs': '7df81ad40a112814c23a39d320c346189a1823309df1b921d50b945924fb0491',
                                              'crates/video-domain/src/native_observation.rs': '7b2c3f94511e8152980839982d8fc8e3bbc18b2d7527eda6e58d74d836f122dc',
                                              'scripts/generate-mlt-catalog-fixtures.py': '39eb15e7b1f395e63b2f03f065b03348a1c83e4b28b2e2f07092d502b84594e2',
                                              'crates/driver-motion-canvas/src/compiler.rs': 'f54104c1ea9384572d55f93fe280b4f5dec101fa71793e5e788ab89dcaaf74f3',
                                              'crates/driver-motion-canvas/src/renderer.rs': '77358406c409670cb419467807d53850f7dc89d7e426a6b51be15aa5ffa6c800',
                                              'crates/driver-motion-canvas/src/security.rs': '5d74b96f0bf703d663ce1531d503d4daf39842bbf0bb4723c7dd73136eabb53e',
                                              'integrations/motion-canvas/runtime/render.mjs': '2ca37c29344c26ac1d38b170b6a54040ec1ffadf838b71dbd84deea056e9698f'}}

CAUSAL_CASES = ('color::png_completion_tests::png_completion_rejects_missing_iend',
 'color::png_completion_tests::png_completion_rejects_corrupt_iend_crc',
 'color::png_completion_tests::png_completion_rejects_bytes_after_iend',
 'runtime::native_closure_tests::final_readback_preserves_unchanged_artifact_roster',
 'runtime::native_closure_tests::final_readback_preserves_changed_artifact_rejection',
 'runtime::native_closure_tests::final_readback_rejects_cancellation_arriving_at_hash_eof',
 'runtime::native_closure_tests::final_readback_rejects_deadline_expiring_at_hash_eof',
 'runtime::native_closure_tests::probe_fixture_completion_is_observable_with_its_own_budget',
 'runtime::native_closure_tests::output_probe_cannot_complete_past_the_shared_deadline',
 'jobs::native_staging_closure_tests::unchanged_staging_reaches_the_native_process',
 'jobs::native_staging_closure_tests::replaced_staged_media_is_rejected_with_identical_xml')

HOST_CASES = ('host_terminal_receipt_requires_the_original_request_and_closed_descriptor',
 'host_original_request_rejects_xml_media_and_request_substitution',
 'host_binder_accepts_the_exact_complete_report_and_original_sources',
 'host_binder_rejects_swapped_terminal_report_payload',
 'host_binder_rejects_self_consistent_report_from_other_source',
 'complete_report_cannot_replace_actual_png_decoding',
 'host_copy_cancellation_is_not_an_interrupted_retry_loop',
 'unknown_publication_precedes_cancellation_state',
 'host_custody_retains_exact_original_request_and_terminal',
 'host_custody_rejects_substituted_request_or_terminal_before_writes',
 'host_custody_never_replaces_existing_originals',
 'host_custody_failure_preserves_original_native_terminal',
 'host_custody_is_absent_from_other_profiles',
 'host_custody_keeps_partial_publication_and_requires_new_destination')

CANVAS_CASES = ('observed_canvas_color_accepts_real_context_alpha_capability',
 'observed_canvas_color_refuses_missing_config_substitutes_and_wrong_types')

NODE_CASES = {'failure-receipt.test.mjs': ('local error details are UTF-8 bounded and classified by finite values',
                              'receipt publication is exclusive, private and bound to the selected output '
                              'child',
                              'output aliases cannot redirect private diagnostics to another directory'),
 'font-readiness.test.mjs': ('<file>',),
 'frame-contract.test.mjs': ('integer frame ranges round trip through the native ceil clock',
                             'only one native singleton tail is dropped after the awaited requested frame',
                             'out of order, duplicate, noninteger and unrelated frames reach the strict '
                             'binding',
                             'an aborted or failed export cannot authorize dropping the following frame',
                             'renderer diagnostics handle nonenumerable fields, hostile getters and UTF-8 '
                             'budgets'),
 'runtime-contract.test.mjs': ('runtime dependencies use exact versions',
                               'render harness is bound to Driver Host, runtime-bundle Firefox, and local '
                               'origin',
                               'render harness reports bounded state when browser rendering stalls',
                               'renderer TypeErrors expose only allowlisted stack origins and normalized '
                               'hints',
                               'authoring font evidence is derived from pinned Fontsource resources and '
                               'browser readiness',
                               'native styled text preserves Motion Canvas public Txt layout contract',
                               'native observation never forces layout geometry before the native draw',
                               'terminal authored shots stay visible through the final half-open frame',
                               'native text probe normalizes DOMRectList before iteration',
                               'native text digests use the bounded Node harness binding, not browser '
                               'WebCrypto'),
 'svg-rectangle-radii.test.mjs': ('<file>',)}

INTEGRATION_CASES = {'conformance': ('driver_frame_big_endian_roundtrip',
                 'driver_frame_eof',
                 'driver_frame_truncated_header',
                 'driver_frame_zero_denied',
                 'driver_frame_oversize_denied',
                 'hello_identity_and_executable_pin',
                 'hello_unknown_fields_denied',
                 'capability_namespace_unique_and_bounded',
                 'static_descriptor_golden_is_checked',
                 'schemas_reject_unknown_arguments',
                 'native_caption_glyph_observer_is_read_only_and_closed_world',
                 'doctor_satisfies_actual_output_schema',
                 'wrong_descriptor_digest_rejected_before_execution',
                 'lifecycle_hello_catalog_health_shutdown',
                 'mutation_catalog_does_not_claim_transport_dry_run',
                 'schema_pages_bounded',
                 'av_mux_schema_is_closed_and_declares_only_certified_audio_profile'),
 'domain_contract': ('generated_mlt_exposes_complete_safe_semantic_contract',
                     'kdenlive_contract_reuses_native_support_source_of_truth',
                     'opaque_native_state_is_structured_as_read_only_projection_loss',
                     'native_warnings_are_not_hidden_inside_portable_project_only'),
 'process': ('process_success_exit_code',
             'process_nonzero_not_success',
             'process_wall_timeout',
             'process_cancel_before_spawn',
             'process_cancel_running',
             'process_stdout_flood_is_bounded',
             'process_stderr_flood_is_bounded',
             'process_argv_is_literal',
             'process_environment_is_explicit',
             'process_cwd_is_private',
             'process_address_space_budget_is_bounded',
             'process_relative_executable_denied',
             'process_failed_partial_not_success',
             'process_cancellation_kills_same_group_descendant',
             'service_discovery_excludes_untrusted_tokens',
             'profile_unavailable_without_codec_probe',
             'native_render_profiles_project_to_semantic_contract',
             'media_probe_rational_duration',
             'native_project_cannot_reach_render_runtime',
             'probe_duration_capacity_is_rational_not_nominal_fps',
             'probe_without_duration_is_not_a_source_handle_guarantee',
             'media_probe_observes_audio_rate_and_channels'),
 'runtime_runner': ('runtime_runner_discovers_bounded_catalog_from_explicit_dependency',
                    'runtime_runner_rejects_unmaterialized_relative_dependencies',
                    'runtime_runner_rejects_bundle_entrypoint_that_does_not_match_sealed_tool',
                    'runtime_runner_rejects_entrypoint_symlink_that_escapes_runtime_root',
                    'runtime_runner_probes_one_confined_scratch_file_with_sealed_ffprobe',
                    'runtime_runner_probe_rejects_scratch_traversal',
                    'runtime_runner_renders_one_curated_profile_inside_scratch',
                    'runtime_runner_rejects_unknown_render_profile',
                    'runtime_runner_rejects_unknown_operations')}

INTEGRATION_SOURCE_PINS = {'crates/driver-mlt-video/tests/conformance.rs': 'd804740747d3551ff9592ccd1d3f2a5f5070d7884cf6c7fa7a39ffd2dae72985',
 'crates/driver-mlt-video/tests/domain_contract.rs': '97027c9a732cd27cac9685c8f7c5efc9522220848c3045141f1bfbb72b878d50',
 'crates/driver-mlt-video/tests/process.rs': '3a72edccc4f3c88bd126fc31275f35ae18ddf2429604d51e1954ae1bac4d348b',
 'crates/driver-mlt-video/tests/runtime_runner.rs': '00c989cd71bf7bf70413cfb491749893e066f2c6d48c46f519f980255faefebd',
 'crates/driver-mlt-video/tests/common/mod.rs': '145113446d1f04578ef418ee05296803a3faf69c1a4ed7d5aa628c5f6f72a6af'}

MLT = "semwright-mlt-video-driver"
MC = "semwright-driver-motion-canvas"
VIDEO = "semwright-video-domain"
RUNTIME = "integrations/motion-canvas/runtime"
CATALOG_FILES = (
    "crates/driver-mlt-video/src/catalog.json",
    "crates/driver-mlt-video/fixtures/expected/capabilities.json",
    "crates/driver-mlt-video/fixtures/expected/catalog-wire.json",
    "crates/driver-mlt-video/fixtures/expected/digests.json",
    "crates/driver-mlt-video/fixtures/SHA256SUMS.json",
)
FIXTURES = ("hello-text", "semantic-complete")
MANDATORY_MODULES = (
    ("crates/driver-mlt-video/src/color.rs", "png_completion_tests",
     "54915b8eb1d30ea83ec55939e631dfd24e0c535abd9564ff085b4e4ea9b68089"),
    ("crates/driver-mlt-video/src/runtime.rs", "native_closure_tests",
     "6d2a8f4be8bc395eb7145c2b39488370f34f8a03abfe57ce28896c9ec050041b"),
    ("crates/driver-mlt-video/src/jobs.rs", "native_staging_closure_tests",
     "bdcd4dad442cc94d31c429e567f35a0cbee0de9941d85c1b7ee6f8cb06737a0f"),
)
FAILURE_SITES = {
    "color::png_completion_tests::png_completion_rejects_missing_iend":
        ("color", 1320, "assert!(", ["source rejected=", "decoded rejected=", "rejected=false"]),
    "color::png_completion_tests::png_completion_rejects_corrupt_iend_crc":
        ("color", 1320, "assert!(", ["source rejected=", "decoded rejected=", "rejected=false"]),
    "color::png_completion_tests::png_completion_rejects_bytes_after_iend":
        ("color", 1320, "assert!(", ["source rejected=", "decoded rejected=", "rejected=false"]),
    "runtime::native_closure_tests::final_readback_rejects_cancellation_arriving_at_hash_eof":
        ("runtime", 2535, 'assert_eq!(result.unwrap_err().code, "Cancelled");',
         ["called `Result::unwrap_err()` on an `Ok` value"]),
    "runtime::native_closure_tests::final_readback_rejects_deadline_expiring_at_hash_eof":
        ("runtime", 2557, 'assert_eq!(result.unwrap_err().code, "Timeout");',
         ["called `Result::unwrap_err()` on an `Ok` value"]),
    "runtime::native_closure_tests::output_probe_cannot_complete_past_the_shared_deadline":
        ("runtime", 2605, 'result.err().expect("shared deadline must expire").code,',
         ["shared deadline must expire"]),
    "jobs::native_staging_closure_tests::replaced_staged_media_is_rejected_with_identical_xml":
        ("jobs", 1346, 'assert_eq!(error.code, "StaleReference");',
         ["assertion `left == right` failed", 'left: "BackendFailed"', 'right: "StaleReference"']),
}


def spec(package, crate, name, kind, source, *, empty=False):
    return {"package": package, "crate": crate, "name": name, "kind": kind,
            "source": "crates/" + crate + "/" + source, "allow_empty": empty,
            "prefixes": None}


MLT_TESTS = (
    spec(MLT, "driver-mlt-video", "semwright_mlt_video", "lib", "src/lib.rs"),
    spec(MLT, "driver-mlt-video", MLT, "bin", "src/main.rs"),
    spec(MLT, "driver-mlt-video", "semwright-mlt-runtime-runner", "bin",
         "src/bin/runtime_runner.rs", empty=True),
    spec(MLT, "driver-mlt-video", "fake-melt", "bin", "src/bin/fake_melt.rs", empty=True),
    spec(MLT, "driver-mlt-video", "fake-ffprobe", "bin", "src/bin/fake_ffprobe.rs", empty=True),
)
MLT_INTEGRATIONS = tuple(
    {**spec(MLT, "driver-mlt-video", name, "test", "tests/" + name + ".rs"),
     "closed_roster": names}
    for name, names in INTEGRATION_CASES.items()
)


VIDEO_TESTS = (
    spec(VIDEO, "video-domain", "semwright_video_domain", "lib", "src/lib.rs"),
    *(spec(VIDEO, "video-domain", name, "test", "tests/" + name + ".rs")
      for name in ("backend_contract", "domain", "render")),
)
MC_TESTS = (spec(MC, "driver-motion-canvas", "semwright_driver_motion_canvas",
                 "lib", "src/lib.rs"),)
MATERIALIZE = spec(MC, "driver-motion-canvas", "materialize", "example",
                   "examples/materialize.rs")


def integration_compile_argv(cargo):
    selected = [part for item in MLT_INTEGRATIONS for part in ("--test", item["name"])]
    return [cargo, "test", "--locked", "-p", MLT, "--features", "test-tools",
            *selected, "--no-run", "--message-format=json"]


def execution_context(environment, runtime_platform=None):
    runtime_platform = sys.platform if runtime_platform is None else runtime_platform
    require(runtime_platform == "linux" and environment.get("RUNNER_OS") == "Linux"
            and environment.get("RUNNER_ARCH") == "X64", "requires Linux x64")
    require(environment.get("GITHUB_ACTIONS") == "true"
            and environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and environment.get("MW_SDK_SCOPE") == "sdk_native_source",
            "explicit source-only dispatch required")
    require(environment.get("GITHUB_REPOSITORY") == "seradotcom/motionwright"
            and environment.get("GITHUB_JOB") == "sdk-native-source",
            "wrong repository or job")
    require(all(SHA.fullmatch(environment.get(k, "")) for k in
                ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA")), "full workflow SHA required")
    require(environment["GITHUB_SHA"] == environment["GITHUB_WORKFLOW_SHA"]
            and environment["GITHUB_SHA"] not in (BASE, RED, GREEN),
            "gate/product source identity differs")
    require(environment.get("GITHUB_RUN_ATTEMPT") == "1"
            and re.fullmatch(r"[1-9][0-9]*", environment.get("GITHUB_RUN_ID", "")),
            "first observed attempt only; no automatic replay")
    require(environment.get("GITHUB_WORKFLOW_REF", "").startswith(
        "seradotcom/motionwright/" + WORKFLOW + "@"), "wrong workflow identity")
    forbidden = [k for k in environment if k.startswith("SEMWRIGHT_")
                 or k in ("NODE_OPTIONS", "NODE_PATH", "NPM_CONFIG_USERCONFIG",
                          "npm_config_userconfig", "TS_NODE_PROJECT")]
    require(not forbidden, "inherited runtime/test override: " + ",".join(sorted(forbidden)))
    keys = ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_REPOSITORY",
            "GITHUB_JOB", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "RUNNER_OS", "RUNNER_ARCH")
    return {k: environment[k] for k in keys}


def changed_paths(repo, before, after):
    return tuple(x.decode() for x in custody.git(
        repo, "diff", "--name-only", "-z", before, after).split(b"\0") if x)


def verify_pair(repo):
    require(custody.git(repo, "rev-parse", RED + "^").decode().strip() == BASE
            and custody.git(repo, "rev-parse", GREEN + "^").decode().strip() == RED,
            "frozen parent chain changed")
    for source in (RED, GREEN):
        require(custody.git(repo, "rev-parse", source + "^{tree}").decode().strip()
                == TREES[source], "frozen SDK tree changed")
        require(set(changed_paths(repo, BASE, source)) == set(FROZEN[GREEN])
                and len(FROZEN[GREEN]) == 24, "canonical 24-path delta changed")
        for path, expected in FROZEN[source].items():
            raw = custody.git(repo, "show", source + ":" + path)
            require(hashlib.sha256(raw).hexdigest() == expected, "frozen SDK source changed: " + path)
        for path, expected in INTEGRATION_SOURCE_PINS.items():
            raw = custody.git(repo, "show", source + ":" + path)
            require(hashlib.sha256(raw).hexdigest() == expected,
                    "selected integration source changed: " + path)
        for path, module, expected in MANDATORY_MODULES:
            raw = custody.git(repo, "show", source + ":" + path).replace(b"\r\n", b"\n")
            anchor = ("mod " + module + " {").encode()
            require(raw.count(anchor) == 1, "missing or duplicate causal module")
            require(hashlib.sha256(raw[raw.index(anchor):]).hexdigest() == expected,
                    "causal test module changed")
    require(changed_paths(repo, RED, GREEN) == REPAIR_PATHS, "RED/GREEN repair paths changed")
    require(all(FROZEN[RED][p] == FROZEN[GREEN][p] for p in set(FROZEN[GREEN]) - set(REPAIR_PATHS)),
            "common test harness or source changed")
    for stem, line, needle, _ in FAILURE_SITES.values():
        raw = custody.git(repo, "show", RED + ":crates/driver-mlt-video/src/" + stem + ".rs")
        require(needle in raw.decode().splitlines()[line - 1], "frozen assertion site changed")
    return {"base": BASE, "red": RED, "green": GREEN, "trees": TREES,
            "canonical_delta": sorted(FROZEN[GREEN]), "repair_paths": list(REPAIR_PATHS),
            "identical_causal_modules": list(MANDATORY_MODULES), "common_paths": 21,
            "green_integration_cases": INTEGRATION_CASES,
            "integration_source_pins": INTEGRATION_SOURCE_PINS}


def causal_pair():
    failures = {name: {"source": "crates/driver-mlt-video/src/" + row[0] + ".rs",
                       "line": row[1], "markers": row[3]}
                for name, row in FAILURE_SITES.items()}
    require(len(CAUSAL_CASES) == 11 and len(failures) == 7
            and set(failures) <= set(CAUSAL_CASES), "causal inventory changed")
    return {"id": "sdk-native-source", "failures": failures,
            "baseline": {"sha": RED, "tree": TREES[RED], "targets": {"mlt": list(CAUSAL_CASES)}},
            "repair": {"sha": GREEN, "tree": TREES[GREEN], "targets": {"mlt": list(CAUSAL_CASES)}}}


def input_paths(repo, source):
    paths = [x.decode() for x in custody.git(
        repo, "ls-tree", "-r", "--name-only", "-z", source).split(b"\0") if x]
    roots = ("crates/", ".cargo/", "integrations/composition/motion/",
             "integrations/motion-canvas/runtime/", "fixtures/", "schemas/", "recipes/")
    extra = {"Cargo.toml", "Cargo.lock", "rust-toolchain.toml",
             "scripts/generate-mlt-catalog-fixtures.py",
             "docs/motion-canvas/API_COVERAGE.json", "docs/motion-canvas/CORE_API_COVERAGE.json",
             "docs/motion-canvas/AUX_API_COVERAGE.json"}
    selected = sorted(p for p in paths if p in extra or p.startswith(roots)
                      or p.endswith(("/Cargo.toml", "/build.rs")))
    require(len(paths) == len(set(paths)) and set(FROZEN[GREEN]) - {".gitattributes"} <= set(selected)
            and extra <= set(selected) and set(INTEGRATION_SOURCE_PINS) <= set(selected),
            "compiled source inventory incomplete")
    return sorted(set(selected) | {".gitattributes"})


def cargo_rows(raw, code):
    require(type(code) is int and code == 0, "compiler failure is not causal RED")
    rows = [custody.strict_json(line.encode()) for line in raw.decode().splitlines() if line.strip()]
    require(rows and all(type(row) is dict for row in rows), "invalid Cargo JSON")
    endings = [r for r in rows if r.get("reason") == "build-finished"]
    require(len(endings) == 1 and endings[0].get("success") is True,
            "successful compilation absent")
    require(not any(r.get("reason") == "compiler-message"
                    and r.get("message", {}).get("level") == "error" for r in rows),
            "compiler error was reported")
    return rows


def select_binaries(raw, code, repo, target, specs, *, testing):
    rows = cargo_rows(raw, code)
    expected = {(s["name"], s["kind"]): s for s in specs}
    require(len(expected) == len(specs), "duplicate target contract")
    manifests = {str(repo / ("crates/" + s["crate"] + "/Cargo.toml")) for s in specs}
    found = {}
    for row in rows:
        if (row.get("reason") != "compiler-artifact" or not row.get("executable")
                or row.get("profile", {}).get("test") is not testing
                or str(Path(row.get("manifest_path", "")).absolute()) not in manifests):
            continue
        t = row.get("target", {})
        require(type(t.get("kind")) is list and len(t["kind"]) == 1, "ambiguous target kind")
        key = (t.get("name"), t["kind"][0])
        require(key in expected and key not in found, "unexpected or duplicate compiled target")
        selected = expected[key]
        require(Path(row["manifest_path"]).absolute() ==
                repo / ("crates/" + selected["crate"] + "/Cargo.toml")
                and Path(t["src_path"]).absolute() == repo / selected["source"],
                "compiled binary belongs to another source")
        require(selected["package"] != MLT or "test-tools" in row.get("features", []),
                "test-tools compilation is absent")
        path = Path(row["executable"]).absolute()
        identity = custody.fingerprint(path, target)
        require(identity["bytes"] > 0, "empty compiled binary")
        found[key] = {"absolute_path": str(path), **identity}
    require(set(found) == set(expected), "missing compiled target")
    return {s["name"]: found[(s["name"], s["kind"])] for s in specs}


def roster(raw, code, selected, required=()):
    require(code == 0, "enumeration failed")
    text = raw.decode().replace("\r\n", "\n")
    names = tuple(re.findall(r"(?m)^([A-Za-z_][A-Za-z_0-9:]*): test$", text))
    if not names:
        require(selected["allow_empty"] and re.fullmatch(r"\s*0 tests, 0 benchmarks\s*", text),
                "selected target has no enumerated tests")
        return {"all": (), "selected": (), "not_selected": (), "not_selected_credit": "NONE"}
    expected = selected.get("closed_roster", names)
    result = observer.selected_roster(raw, code, {**selected, "prefixes": None}, expected, "repair")
    for suffix in required:
        require(sum(n == suffix or n.endswith("::" + suffix) for n in names) == 1,
                "mandatory observed case missing or ambiguous: " + suffix)
    return result


def node_tap(raw, code, expected, file_path):
    require(code == 0, "Node source contract failed")
    text = raw.decode().replace("\r\n", "\n")
    require(not re.search(r"(?m)^\s*not ok\b|^Bail out!|^ok .*#\s*(?:SKIP|TODO)", text, re.I),
            "Node failure, skip or todo")
    rows = re.findall(r"(?m)^ok (\d+) - (.+)$", text)
    names = tuple(name for _, name in rows)
    if expected == ("<file>",):
        allowed = {str(file_path), "tests/" + file_path.name}
        require(len(names) == 1 and names[0] in allowed, "Node file-level case changed")
    else:
        require(names == tuple(expected), "Node source roster changed")
    count = len(expected)
    require([int(n) for n, _ in rows] == list(range(1, count + 1))
            and re.findall(r"(?m)^1\.\.(\d+)$", text) == [str(count)], "Node plan or numbering differs")
    for field, value in (("tests", count), ("pass", count), ("fail", 0),
                         ("cancelled", 0), ("skipped", 0), ("todo", 0)):
        require(re.findall(r"(?m)^# " + field + r" (\d+)$", text) == [str(value)],
                "Node summary differs: " + field)
    return {"status": "PASS", "names": names, "cases": count, "skipped": 0}


def copied_sources(root, source_snapshot, *, prefixes=None):
    observed = {}
    for name, expected in source_snapshot["files"].items():
        if prefixes is not None and not name.startswith(prefixes):
            continue
        identity = custody.fingerprint(root / name, root, 16 * 1024 * 1024)
        require((identity["bytes"], identity["sha256"]) == (expected["bytes"], expected["sha256"]),
                "working copy differs from original source: " + name)
        observed[name] = identity
    return observed


def copy_source_tree(source, snapshot, destination):
    require(not destination.exists(), "fresh source working copy required")
    destination.mkdir()
    for name, expected in snapshot["files"].items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with (source / name).open("rb") as src, path.open("xb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        actual = custody.fingerprint(path, destination, 16 * 1024 * 1024)
        require((actual["bytes"], actual["sha256"]) == (expected["bytes"], expected["sha256"]),
                "source copy identity changed")
    return copied_sources(destination, snapshot)


def dependency_inventory(root):
    require(root.is_dir() and root.resolve(strict=True) == root, "dependency root is not canonical")
    paths = sorted(root.rglob("*"))
    require(len(paths) <= 40000, "dependency namespace exceeds bound")
    result, total = {}, 0
    for path in paths:
        name = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            target = path.resolve(strict=True)
            require(target.is_relative_to(root) and target.is_file(), "dependency link escaped closure")
            result[name] = {"link": os.readlink(path), "target": target.relative_to(root).as_posix()}
        elif stat.S_ISREG(info.st_mode):
            identity = custody.fingerprint(path, root)
            total += identity["bytes"]
            require(total <= 768 * 1024 * 1024, "dependency bytes exceed bound")
            result[name] = identity
        else:
            require(stat.S_ISDIR(info.st_mode), "nonregular dependency entry")
    require([p.relative_to(root).as_posix() for p in sorted(root.rglob("*"))]
            == [p.relative_to(root).as_posix() for p in paths], "dependency inventory changed")
    return result


def verify_binaries(collection, target):
    for binary in collection.values():
        require({"absolute_path": binary["absolute_path"],
                 **custody.fingerprint(Path(binary["absolute_path"]), target)} == binary,
                "compiled original binary changed")


def verify_commands(evidence, observations):
    for obs in observations:
        folder = evidence / obs["folder"]
        require(custody.fingerprint(folder / Path(obs["receipt"]["path"]).name, evidence)
                == obs["receipt"], "original command receipt changed")
        for channel in ("stdout", "stderr"):
            observer.original_stream(folder, obs["command"], channel)


def run(gate, sdk, output):
    gate, sdk, output = gate.absolute(), sdk.absolute(), output.absolute()
    require(not output.exists() and not output.is_relative_to(gate)
            and not output.is_relative_to(sdk), "fresh external run root required")
    output.mkdir(parents=True)
    evidence = output / "evidence"
    evidence.mkdir()
    report = {"schema": "motionwright.sdk-native-source/1", "at_utc": custody.now(),
              "status": "INCOMPLETE", "roles": {}, "new_native_operations": 0,
              "physical_render": "NOT_RUN", "pixel_acceptance": False, "human_acceptance": False}
    observations = []
    def command(argv, cwd, env, logs, label, timeout, ctx):
        return observer.command(argv, cwd, env, logs, label, timeout, ctx, (evidence, observations))
    def require_command(argv, cwd, env, logs, label, timeout, ctx):
        row = command(argv, cwd, env, logs, label, timeout, ctx)
        require(row["exit_code"] == 0, "source command failed: " + label)
        return row
    def compile_target(argv, work, env, logs, label, ctx, target, specs, testing):
        row = command(argv, work, env, logs, label, 1800, ctx)
        binaries = select_binaries(observer.original_stream(logs, row, "stdout"),
                                   row["exit_code"], work, target, specs, testing=testing)
        for binary in binaries.values():
            observer.retain_file(Path(binary["absolute_path"]), target, evidence, "binaries")
        return binaries
    def enumerate_binary(binary, selected, work, env, logs, label, ctx, required=()):
        row = command([binary["absolute_path"], "--list", "--format", "terse"],
                      work, env, logs, label, 45, ctx)
        return roster(observer.original_stream(logs, row, "stdout"), row["exit_code"], selected, required)
    def cases(binary, names, listed, pair, role, work, target, env, logs, label, ctx, results,
              dependencies=None):
        for number, name in enumerate(names):
            verify_binaries({"selected": binary}, target)
            if dependencies:
                verify_binaries(dependencies, target)
            row = command(observer.case_argv(binary, pair, role, name), work, env, logs,
                          label + "-" + str(number), 180, ctx)
            after = {"absolute_path": binary["absolute_path"],
                     **custody.fingerprint(Path(binary["absolute_path"]), target)}
            raw = (observer.original_stream(logs, row, "stdout") + b"\n"
                   + observer.original_stream(logs, row, "stderr"))
            results.append(observer.admit_case(pair, role, name, raw, row, binary, after, listed, ctx))
            if dependencies:
                verify_binaries(dependencies, target)
    try:
        context = execution_context(os.environ)
        report["context"] = context
        require(Path(observer.__file__).resolve() == gate / "tooling/ci_foundation_causal.py"
                and Path(custody.__file__).resolve() == gate / "tooling/ci_storage_apply_recovery.py",
                "observer module origin differs")
        before_gate = custody.source_snapshot(gate, context["GITHUB_SHA"], GATE_FILES)
        require(all(before_gate["files"][p]["sha256"] == h for p, h in GATE_PINS.items()),
                "reviewed observer authority changed")
        report["gate_before"] = before_gate
        report["retained_gate_sources"] = observer.retain_source(gate, before_gate, evidence)
        report["pair_identity"] = verify_pair(sdk)
        pair = causal_pair()
        report["closed_pair"] = pair
        tool_target, tool_tmp = output / "tool-target", output / "tool-tmp"
        tool_target.mkdir(); tool_tmp.mkdir()
        tool_env = observer.build_environment(os.environ, tool_target, tool_tmp)
        tool_env["RUSTUP_TOOLCHAIN"] = "1.98.1"
        tools = observer.tools_identity(gate, evidence, tool_env, context, (evidence, observations))
        report["tools"] = tools
        cargo = tools["cargo"]["absolute_path"]
        for role, source in (("baseline", RED), ("repair", GREEN)):
            work, target, tmp = (output / (role + "-" + suffix) for suffix in ("checkout", "target", "tmp"))
            logs = evidence / (role + "-originals")
            for path in (target, tmp, logs):
                path.mkdir()
            env = observer.build_environment(os.environ, target, tmp)
            env.update(RUSTUP_TOOLCHAIN="1.98.1", CARGO_BUILD_JOBS="2",
                       PYTHONDONTWRITEBYTECODE="1")
            ctx = {**context, "role": role, "product_source_sha": source}
            require_command(["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf",
                             "worktree", "add", "--detach", str(work), source],
                            sdk, env, logs, "checkout", 90, ctx)
            paths = input_paths(sdk, source)
            before = custody.source_snapshot(work, source, paths, TREES[source])
            result = {"source_before": before, "results": [], "binaries": {}, "rosters": {},
                      "retained_sources": observer.retain_source(work, before, evidence),
                      "clippy": "NOT_SELECTED", "supplemental": {}}
            report["roles"][role] = result
            built = compile_target([cargo, "build", "--locked", "-p", MLT, "--features", "test-tools",
                                    "--bins", "--message-format=json"],
                                   work, env, logs, "test-tools-build", ctx, target, MLT_TESTS[1:], False)
            result["binaries"]["test-tools"] = built
            specs = MLT_TESTS if role == "repair" else MLT_TESTS[:1]
            argv = [cargo, "test", "--locked", "-p", MLT, "--features", "test-tools", "--lib"]
            if role == "repair":
                argv.append("--bins")
            binaries = compile_target([*argv, "--no-run", "--message-format=json"],
                                      work, env, logs, "mlt-test-compile", ctx, target, specs, True)
            result["binaries"]["mlt-tests"] = binaries
            verify_binaries(built, target)
            lib = binaries["semwright_mlt_video"]
            full = enumerate_binary(lib, MLT_TESTS[0], work, env, logs, "mlt-list", ctx, CAUSAL_CASES)
            selected_spec = {**MLT_TESTS[0], "prefixes": (
                "color::png_completion_tests::", "runtime::native_closure_tests::",
                "jobs::native_staging_closure_tests::")}
            list_row = observations[-1]["command"]
            selected = observer.selected_roster(observer.original_stream(logs, list_row, "stdout"),
                                                list_row["exit_code"], selected_spec, CAUSAL_CASES, role)
            result["rosters"]["causal"] = selected
            cases(lib, CAUSAL_CASES, selected, pair, role, work, target, env, logs,
                  "causal", ctx, result["results"])
            verify_binaries(built, target)
            if role == "repair":
                def green_suite(label, selected_specs, selected_binaries, skip=(), dependencies=None):
                    suite = {"binaries": selected_binaries, "rosters": {}, "results": [],
                             "already_observed_cases": list(skip),
                             "fixture_binaries": dependencies or {}}
                    result["supplemental"][label] = suite
                    for selected_target in selected_specs:
                        name = selected_target["name"]
                        binary = selected_binaries[name]
                        required = HOST_CASES if name == MLT else (
                            CANVAS_CASES if name == "semwright_driver_motion_canvas" else ())
                        listed = full if name == "semwright_mlt_video" else enumerate_binary(
                            binary, selected_target, work, env, logs, label + "-" + name + "-list", ctx, required)
                        observed_names = [n for n in listed["all"] if n not in skip]
                        suite["rosters"][name] = listed
                        contract = {"id": "sdk-source-" + label, "failures": {},
                                    "repair": {"targets": {name: observed_names}}}
                        cases(binary, observed_names, listed, contract, "repair", work, target, env,
                              logs, label + "-" + name, ctx, suite["results"], dependencies)
                    require(len(suite["results"]) + len(skip) ==
                            sum(len(v["all"]) for v in suite["rosters"].values()),
                            "supplemental source selection incomplete")
                    suite["status"] = "PASS_COMPLETE_COMPILED_ROSTER"
                green_suite("mlt", MLT_TESTS, binaries, CAUSAL_CASES)
                integrations = compile_target(integration_compile_argv(cargo), work, env, logs,
                                              "mlt-integration-compile", ctx, target,
                                              MLT_INTEGRATIONS, True)
                verify_binaries(built, target)
                green_suite("mlt-integrations", MLT_INTEGRATIONS, integrations, dependencies=built)
                domain = compile_target([cargo, "test", "--locked", "-p", VIDEO, "--lib", "--tests",
                                         "--no-run", "--message-format=json"],
                                        work, env, logs, "video-domain-compile", ctx, target, VIDEO_TESTS, True)
                green_suite("video-domain", VIDEO_TESTS, domain)
                canvas = compile_target([cargo, "test", "--locked", "-p", MC, "--lib", "--no-run",
                                         "--message-format=json"],
                                        work, env, logs, "motion-canvas-compile", ctx, target, MC_TESTS, True)
                green_suite("motion-canvas", MC_TESTS, canvas)
                require_command([cargo, "clippy", "--locked", "-p", MLT, "-p", MC, "-p", VIDEO,
                                 "--all-targets", "--features", MLT + "/test-tools", "--", "-D", "warnings"],
                                work, env, logs, "strict-clippy", 1800, ctx)
                result["clippy"] = "PASS"
                result["runtime"] = run_runtime(work, before, output, evidence, logs, env, ctx,
                                                target, cargo, command, require_command, compile_target)
            result["source_after"] = custody.source_snapshot(work, source, paths, TREES[source])
            result["admission"] = observer.role_complete(pair, role, result)
            for collection in result["binaries"].values():
                verify_binaries(collection, target)
            for suite in result["supplemental"].values():
                verify_binaries(suite["binaries"], target)
        report["gate_after"] = custody.source_snapshot(gate, context["GITHUB_SHA"], GATE_FILES)
        require(report["gate_before"] == report["gate_after"], "gate source changed")
        for tool in tools.values():
            path = Path(tool["absolute_path"])
            require(custody.fingerprint(path, path.parent) ==
                    {k: tool[k] for k in ("path", "bytes", "sha256", "identity")}, "toolchain identity changed")
        verify_commands(evidence, observations)
        report.update(status="PASS_SOURCE_G1", baseline_expected_failures=7, baseline_controls_passed=4,
                      causal_green_passed=11, ignored_selected_cases=0, omitted_selected_cases=0)
    except (observer.UnknownExecution, custody.UnknownObservation) as error:
        report.update(status="UNKNOWN_NO_RETRY", error=str(error), uncertain_command=error.command)
    except Exception as error:
        report.update(status="FAIL_OR_INCOMPLETE", error=type(error).__name__ + ": " + str(error))
    report["commands"] = observations
    report["ended_at_utc"] = custody.now()
    custody.write_new(evidence / "receipt.json", report)
    return report


def run_runtime(work, source, output, evidence, logs, env, ctx, target, cargo,
                command, require_command, compile_target):
    root = output / "runtime-source"
    copied = copy_source_tree(work, source, root)
    runtime = root / RUNTIME
    require(not (runtime / "node_modules").exists(), "runtime dependencies already present")
    node = Path(shutil.which("node") or "").resolve(strict=True)
    npm = Path(shutil.which("npm") or "").resolve(strict=True)
    tool_ids = {name: {"absolute_path": str(p), **custody.fingerprint(p, p.parent)}
                for name, p in (("node", node), ("npm_cli", npm))}
    node_env = dict(env, PATH=str(node.parent) + os.pathsep + env["PATH"],
                    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD="1", npm_config_cache=str(output / "npm-cache"))
    version = require_command([str(node), "--version"], runtime, node_env, logs, "node-version", 30, ctx)
    require(observer.original_stream(logs, version, "stdout").decode().strip() == "v22.22.0",
            "Node version differs")
    require_command([str(node), str(npm), "--version"], runtime, node_env, logs, "npm-version", 30, ctx)
    package = custody.strict_json((runtime / "package.json").read_bytes())
    require(package["engines"] == {"node": "22.22.0"}
            and package["scripts"]["test"] == "node --test tests/*.test.mjs && node tools/semantic-coverage.mjs"
            and package["scripts"]["typecheck"] == "tsc --noEmit", "runtime script contract changed")
    require_command([str(node), str(npm), "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                    runtime, node_env, logs, "npm-ci-locked", 600, ctx)
    dependency_before = dependency_inventory(runtime / "node_modules")
    custody.write_new(evidence / "node-dependencies-before.json", dependency_before)
    cases_seen = {}
    for index, (name, expected) in enumerate(NODE_CASES.items()):
        path = runtime / "tests" / name
        row = command([str(node), "--test", "--test-reporter=tap", "tests/" + name],
                      runtime, node_env, logs, "node-contract-" + str(index), 180, ctx)
        cases_seen[name] = node_tap(observer.original_stream(logs, row, "stdout"), row["exit_code"], expected, path)
    coverage = require_command([str(node), "tools/semantic-coverage.mjs"],
                               runtime, node_env, logs, "semantic-coverage", 120, ctx)
    coverage_rows = [custody.strict_json(x.encode()) for x in
                     observer.original_stream(logs, coverage, "stdout").decode().splitlines() if x.strip()]
    require(len(coverage_rows) == 3 and all(x.get("status") == "complete" for x in coverage_rows),
            "three runtime semantic coverage projections required")
    materialize = compile_target([cargo, "build", "--locked", "-p", MC, "--example", "materialize",
                                  "--message-format=json"], work, env, logs, "materialize-compile", ctx,
                                 target, (MATERIALIZE,), False)["materialize"]
    generated = {}
    tsc = runtime / "node_modules/typescript/lib/tsc.js"
    tsc_before = custody.fingerprint(tsc, runtime / "node_modules")
    for name in FIXTURES:
        destination = output / ("typecheck-" + name)
        require(not destination.exists(), "typecheck destination must be new")
        fixture = root / ("fixtures/motion-canvas/" + name + "/semwright-motion.json")
        require_command([materialize["absolute_path"], str(fixture), str(destination)],
                        root, env, logs, "materialize-" + name, 120, ctx)
        paths = sorted(p for p in destination.rglob("*") if p.is_file())
        require(paths and (destination / "tsconfig.json").is_file(), "generated typecheck source incomplete")
        identities = {p.relative_to(destination).as_posix(): custody.fingerprint(p, destination) for p in paths}
        for p in paths:
            observer.retain_file(p, destination, evidence, "sources")
        os.symlink(runtime / "node_modules", destination / "node_modules", target_is_directory=True)
        require_command([str(node), str(tsc), "--noEmit"], destination, node_env,
                        logs, "typecheck-" + name, 180, ctx)
        require(all(custody.fingerprint(destination / p, destination) == observed
                    for p, observed in identities.items()), "generated source changed during typecheck")
        generated[name] = {"files": identities, "status": "TYPECHECK_PASS", "native_render": "NOT_RUN"}
    require({"absolute_path": materialize["absolute_path"],
             **custody.fingerprint(Path(materialize["absolute_path"]), target)} == materialize,
            "materializer binary changed")
    require(custody.fingerprint(tsc, runtime / "node_modules") == tsc_before, "TypeScript compiler changed")
    require_command([sys.executable, "-B", str(root / "scripts/generate-mlt-catalog-fixtures.py")],
                    root, env, logs, "catalog-regeneration", 120, ctx)
    copied_sources(root, source)
    catalog = json.loads((root / CATALOG_FILES[0]).read_bytes())
    names = [x["descriptor"]["name"] for x in catalog]
    require(len(names) == 72 and names == sorted(set(names)), "catalog capability roster differs")
    sums = custody.strict_json((root / CATALOG_FILES[-1]).read_bytes())
    require(len(sums) == 71, "fixture hash inventory differs")
    fixture_root = root / "crates/driver-mlt-video/fixtures"
    fixture_ids = {}
    for relative, expected in sums.items():
        require(type(relative) is str and relative and "\\" not in relative
                and not Path(relative).is_absolute() and ".." not in Path(relative).parts,
                "unsafe fixture path")
        actual = custody.fingerprint(fixture_root / relative, fixture_root, 16 * 1024 * 1024)
        require(actual["sha256"] == expected, "fixture digest differs")
        fixture_ids[relative] = actual
    dependency_after = dependency_inventory(runtime / "node_modules")
    custody.write_new(evidence / "node-dependencies-after.json", dependency_after)
    require(dependency_before == dependency_after, "runtime dependency bytes changed")
    for identity in tool_ids.values():
        path = Path(identity["absolute_path"])
        require(custody.fingerprint(path, path.parent) ==
                {k: identity[k] for k in ("path", "bytes", "sha256", "identity")}, "Node/npm identity changed")
    return {"status": "PASS_SOURCE_RUNTIME", "copied_sources_before": copied,
            "copied_sources_after": copied_sources(root, source), "node_tools": tool_ids,
            "node_cases": cases_seen, "semantic_coverage": coverage_rows,
            "materializer": materialize, "typescript": tsc_before, "typechecks": generated,
            "fixture_inventory": fixture_ids, "catalog_capabilities": names,
            "runtime_dependency_files": len(dependency_before), "native_render": "NOT_RUN"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run(args.gate, args.sdk, args.output)
    print(json.dumps({"status": report["status"], "receipt": str(args.output / "evidence/receipt.json")}))
    return 0 if report["status"] == "PASS_SOURCE_G1" else 2


if __name__ == "__main__":
    raise SystemExit(main())
