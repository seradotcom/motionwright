#!/usr/bin/env python3
"""Verify four frozen Foundation baseline/repair pairs on one selected Linux runner.

Named assertion failures plus passing controls establish a causal baseline.
Compiler/setup failures, skipped cases and UNKNOWN never establish that result.
These synthetic contracts do not establish installed or human acceptance.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys

try:
    from tooling import ci_storage_apply_recovery as custody
except ModuleNotFoundError as error:
    if error.name != "tooling":
        raise
    import ci_storage_apply_recovery as custody

TARGETS = {'jobs': {'package': 'motionwright-service',
          'kind': 'test',
          'name': 'production_jobs_cancellation_recovery',
          'source': 'crates/service/tests/production_jobs_cancellation_recovery.rs',
          'prefixes': None},
 'svg-references': {'package': 'motionwright-service',
                    'kind': 'test',
                    'name': 'file_import_svg_references',
                    'source': 'crates/service/tests/file_import_svg_references.rs',
                    'prefixes': None},
 'svg-budget': {'package': 'motionwright-service',
                'kind': 'test',
                'name': 'file_import_svg_budget',
                'source': 'crates/service/tests/file_import_svg_budget.rs',
                'prefixes': None},
 'import-storage': {'package': 'motionwright-storage',
                    'kind': 'lib',
                    'name': 'motionwright_storage',
                    'source': 'crates/storage/src/lib.rs',
                    'prefixes': ('file_import::source_open_tests::',)},
 'import-service': {'package': 'motionwright-service',
                    'kind': 'test',
                    'name': 'file_import_idempotency',
                    'source': 'crates/service/tests/file_import_idempotency.rs',
                    'prefixes': None},
 'receipt': {'package': 'motionwright-storage',
             'kind': 'test',
             'name': 'apply_receipt_integrity',
             'source': 'crates/storage/tests/apply_receipt_integrity.rs',
             'prefixes': None}}

PAIRS = [{'id': 'jobs',
  'failures': {'cancel_request_survives_failed_status_read_and_reopen': {'source': 'crates/service/tests/production_jobs_cancellation_recovery.rs',
                                                                         'line': 126,
                                                                         'markers': ['assertion `left == '
                                                                                     'right` failed',
                                                                                     'left: Running',
                                                                                     'right: '
                                                                                     'CancelRequested']},
               'cancel_request_survives_unknown_status_and_queued_observation': {'source': 'crates/service/tests/production_jobs_cancellation_recovery.rs',
                                                                                 'line': 126,
                                                                                 'markers': ['assertion '
                                                                                             '`left == '
                                                                                             'right` failed',
                                                                                             'left: Queued',
                                                                                             'right: '
                                                                                             'CancelRequested']}},
  'baseline': {'sha': 'f7f58b8310a68e456e3f324c3004f4c3dec0e7da',
               'tree': '4fdad91e137e5f8a104e7983c2d726c2d702f531',
               'targets': {'jobs': ['cancel_request_survives_failed_status_read_and_reopen',
                                    'cancel_request_survives_unknown_status_and_queued_observation',
                                    'terminal_observations_win_after_pending_cancel_request_and_unknown',
                                    'running_observation_recovers_unknown_without_inventing_cancellation']},
               'fixture_sha256': {'crates/service/tests/production_jobs_cancellation_recovery.rs': 'e040e61152bf60a66ce6c6210b1fc9679256f006d38fd9860488784095ac8dfa'}},
  'repair': {'sha': '9286632ab99e45d4156b87d13097366092b4aded',
             'tree': 'f69f5f2755aba17e5dc533f96532ca4495c066b6',
             'targets': {'jobs': ['cancel_request_survives_failed_status_read_and_reopen',
                                  'cancel_request_survives_unknown_status_and_queued_observation',
                                  'terminal_observations_win_after_pending_cancel_request_and_unknown',
                                  'running_observation_recovers_unknown_without_inventing_cancellation']},
             'fixture_sha256': {'crates/service/tests/production_jobs_cancellation_recovery.rs': 'e040e61152bf60a66ce6c6210b1fc9679256f006d38fd9860488784095ac8dfa'}}},
 {'id': 'svg',
  'failures': {'svg_style_attribute_decodes_xml_entities_before_reference_admission': {'source': 'crates/service/tests/file_import_svg_references.rs',
                                                                                       'line': 65,
                                                                                       'markers': ['an '
                                                                                                   'encoded '
                                                                                                   'CSS '
                                                                                                   'reference '
                                                                                                   'was '
                                                                                                   'admitted '
                                                                                                   'as an '
                                                                                                   'immutable '
                                                                                                   'SVG '
                                                                                                   'asset']},
               'svg_presentation_attribute_decodes_xml_entities_before_reference_admission': {'source': 'crates/service/tests/file_import_svg_references.rs',
                                                                                              'line': 65,
                                                                                              'markers': ['an '
                                                                                                          'encoded '
                                                                                                          'CSS '
                                                                                                          'reference '
                                                                                                          'was '
                                                                                                          'admitted '
                                                                                                          'as '
                                                                                                          'an '
                                                                                                          'immutable '
                                                                                                          'SVG '
                                                                                                          'asset']},
               'svg_style_attribute_decodes_css_identifier_escapes_before_admission': {'source': 'crates/service/tests/file_import_svg_references.rs',
                                                                                       'line': 65,
                                                                                       'markers': ['an '
                                                                                                   'encoded '
                                                                                                   'CSS '
                                                                                                   'reference '
                                                                                                   'was '
                                                                                                   'admitted '
                                                                                                   'as an '
                                                                                                   'immutable '
                                                                                                   'SVG '
                                                                                                   'asset']},
               'svg_style_element_decodes_css_at_rule_escapes_before_admission': {'source': 'crates/service/tests/file_import_svg_references.rs',
                                                                                  'line': 65,
                                                                                  'markers': ['an encoded '
                                                                                              'CSS reference '
                                                                                              'was admitted '
                                                                                              'as an '
                                                                                              'immutable SVG '
                                                                                              'asset']},
               'svg_namespaced_style_element_decodes_xml_entities_before_admission': {'source': 'crates/service/tests/file_import_svg_references.rs',
                                                                                      'line': 65,
                                                                                      'markers': ['an '
                                                                                                  'encoded '
                                                                                                  'CSS '
                                                                                                  'reference '
                                                                                                  'was '
                                                                                                  'admitted '
                                                                                                  'as an '
                                                                                                  'immutable '
                                                                                                  'SVG '
                                                                                                  'asset']},
               'svg_style_text_fragments_are_joined_before_css_reference_admission': {'source': 'crates/service/tests/file_import_svg_references.rs',
                                                                                      'line': 65,
                                                                                      'markers': ['an '
                                                                                                  'encoded '
                                                                                                  'CSS '
                                                                                                  'reference '
                                                                                                  'was '
                                                                                                  'admitted '
                                                                                                  'as an '
                                                                                                  'immutable '
                                                                                                  'SVG '
                                                                                                  'asset']},
               'svg_nested_css_blocks_cannot_hide_decoded_references': {'source': 'crates/service/tests/file_import_svg_references.rs',
                                                                        'line': 65,
                                                                        'markers': ['an encoded CSS '
                                                                                    'reference was admitted '
                                                                                    'as an immutable SVG '
                                                                                    'asset']},
               'svg_css_image_constructors_cannot_load_quoted_resources': {'source': 'crates/service/tests/file_import_svg_references.rs',
                                                                           'line': 65,
                                                                           'markers': ['an encoded CSS '
                                                                                       'reference was '
                                                                                       'admitted as an '
                                                                                       'immutable SVG '
                                                                                       'asset']},
               'svg_animation_cannot_replace_an_admitted_local_reference': {'source': 'crates/service/tests/file_import_svg_references.rs',
                                                                            'line': 65,
                                                                            'markers': ['an encoded CSS '
                                                                                        'reference was '
                                                                                        'admitted as an '
                                                                                        'immutable SVG '
                                                                                        'asset']}},
  'baseline': {'sha': 'ebbb348e2c300807927f008c24fa9986185b736b',
               'tree': 'bfd24b1bc3f0246594fad3d9e1ab645054bb7500',
               'targets': {'svg-references': ['svg_style_attribute_decodes_xml_entities_before_reference_admission',
                                              'svg_presentation_attribute_decodes_xml_entities_before_reference_admission',
                                              'svg_style_attribute_decodes_css_identifier_escapes_before_admission',
                                              'svg_style_element_decodes_css_at_rule_escapes_before_admission',
                                              'svg_namespaced_style_element_decodes_xml_entities_before_admission',
                                              'ordinary_svg_entities_and_static_styles_preserve_original_bytes',
                                              'ordinary_svg_css_identifier_escapes_preserve_original_bytes',
                                              'svg_style_text_fragments_are_joined_before_css_reference_admission',
                                              'svg_nested_css_blocks_cannot_hide_decoded_references',
                                              'svg_css_image_constructors_cannot_load_quoted_resources',
                                              'svg_animation_cannot_replace_an_admitted_local_reference',
                                              'ordinary_svg_nested_css_functions_and_local_fragments_keep_original_bytes',
                                              'ordinary_xml_like_asset_remains_compatible_with_conservative_sniffing']},
               'fixture_sha256': {'crates/service/tests/file_import_svg_references.rs': '3cac623ba4cadb3900b74c5416c56ac30140cb07170ae08d3e59368575bca0fd',
                                  'crates/service/tests/file_import_svg_budget.rs': '1b58e2becff6866e9586f6da334439a4f40506bcba2c9f80c3025c3443db569b'}},
  'repair': {'sha': 'd85e5e126216ee0b6108cfe12e726c940bc95bdf',
             'tree': '45cda6546e47f725dd28fcebcaf2c9cd1da9db05',
             'targets': {'svg-references': ['svg_style_attribute_decodes_xml_entities_before_reference_admission',
                                            'svg_presentation_attribute_decodes_xml_entities_before_reference_admission',
                                            'svg_style_attribute_decodes_css_identifier_escapes_before_admission',
                                            'svg_style_element_decodes_css_at_rule_escapes_before_admission',
                                            'svg_namespaced_style_element_decodes_xml_entities_before_admission',
                                            'ordinary_svg_entities_and_static_styles_preserve_original_bytes',
                                            'ordinary_svg_css_identifier_escapes_preserve_original_bytes',
                                            'svg_style_text_fragments_are_joined_before_css_reference_admission',
                                            'svg_nested_css_blocks_cannot_hide_decoded_references',
                                            'svg_css_image_constructors_cannot_load_quoted_resources',
                                            'svg_animation_cannot_replace_an_admitted_local_reference',
                                            'ordinary_svg_nested_css_functions_and_local_fragments_keep_original_bytes',
                                            'ordinary_xml_like_asset_remains_compatible_with_conservative_sniffing'],
                         'svg-budget': ['declared_svg_mime_budget_rejects_before_staging',
                                        'declared_svg_extension_budget_rejects_before_staging',
                                        'small_valid_svg_still_captures_and_imports_the_exact_original',
                                        'svg_budget_does_not_become_an_opaque_asset_size_limit']},
             'fixture_sha256': {'crates/service/tests/file_import_svg_references.rs': '3cac623ba4cadb3900b74c5416c56ac30140cb07170ae08d3e59368575bca0fd',
                                'crates/service/tests/file_import_svg_budget.rs': '1b58e2becff6866e9586f6da334439a4f40506bcba2c9f80c3025c3443db569b'}}},
 {'id': 'retained-import',
  'failures': {'file_import::source_open_tests::replaced_symlink_source_is_rejected_before_staging': {'source': 'crates/storage/src/file_import.rs',
                                                                                                      'line': 835,
                                                                                                      'markers': ['replaced '
                                                                                                                  'leaf '
                                                                                                                  'must '
                                                                                                                  'never '
                                                                                                                  'capture '
                                                                                                                  'its '
                                                                                                                  'target']},
               'file_import::source_open_tests::oversized_opened_source_is_rejected_before_staging': {'source': 'crates/storage/src/file_import.rs',
                                                                                                      'line': 857,
                                                                                                      'markers': ['assertion '
                                                                                                                  'failed: '
                                                                                                                  '!store.blob_root.join(".staging").exists()']},
               'file_import::source_open_tests::replaced_directory_source_is_rejected_before_staging': {'source': 'crates/storage/src/file_import.rs',
                                                                                                        'line': 874,
                                                                                                        'markers': ['assertion '
                                                                                                                    'failed: '
                                                                                                                    '!store.blob_root.join(".staging").exists()']}},
  'baseline': {'sha': 'edd78365c485a620104f5343f1764791bad9aa3a',
               'tree': '94a0ada1f2fa894af8d4f3deb42ed35960225361',
               'targets': {'import-storage': ['file_import::source_open_tests::regular_source_capture_preserves_exact_bytes_and_sql',
                                              'file_import::source_open_tests::replaced_symlink_source_is_rejected_before_staging',
                                              'file_import::source_open_tests::oversized_opened_source_is_rejected_before_staging',
                                              'file_import::source_open_tests::replaced_directory_source_is_rejected_before_staging']},
               'fixture_sha256': {'crates/storage/src/file_import.rs': '4def788d3a254b6d64a3602febada9886c3bf19e5589e5a97a2bb6710f61a96c'}},
  'repair': {'sha': 'babcafffefd878efbf5c316691b922f8c1fae487',
             'tree': '3d89d05b1f4fca42bb15824e5915698642e1894f',
             'targets': {'import-storage': ['file_import::source_open_tests::regular_source_capture_preserves_exact_bytes_and_sql',
                                            'file_import::source_open_tests::replaced_symlink_source_is_rejected_before_staging',
                                            'file_import::source_open_tests::oversized_opened_source_is_rejected_before_staging',
                                            'file_import::source_open_tests::replaced_directory_source_is_rejected_before_staging',
                                            'file_import::source_open_tests::replay_source_hash_preserves_bytes_without_staging_or_database',
                                            'file_import::source_open_tests::replay_source_hash_rejects_replaced_symlink_without_creating_outputs',
                                            'file_import::source_open_tests::replaced_fifo_source_is_rejected_without_waiting_for_a_writer',
                                            'file_import::tests::original_import_remains_readable_without_sources_after_edit_and_reopen',
                                            'file_import::tests::absent_receipt_requires_current_revision_only_when_requested',
                                            'file_import::tests::corrupt_receipt_hash_event_or_result_is_never_treated_as_absent',
                                            'file_import::tests::changed_purpose_or_metadata_rejects_request_reuse_without_effects',
                                            'file_import::staging_tests::staged_import_rejects_changed_capture_without_publishing_or_leaking_stage',
                                            'file_import::staging_tests::dropping_duplicate_stages_and_rejecting_stale_import_preserve_blob'],
                         'import-service': ['asset_import_replays_identical_bytes_and_metadata_after_reopen',
                                            'voice_import_replays_identical_bytes_and_metadata_after_reopen',
                                            'invalid_import_authority_takes_precedence_over_missing_source_io',
                                            'changed_file_bytes_cannot_reuse_a_committed_asset_import_key',
                                            'committed_import_keeps_real_cas_content_when_the_original_file_disappears',
                                            'historical_asset_and_voice_recovery_preserves_head_without_source_or_cas',
                                            'normal_asset_and_voice_replay_does_not_rehydrate_missing_cas',
                                            'concurrent_connections_import_identical_bytes_with_one_event_and_one_outcome',
                                            'rejected_active_svg_and_undecodable_voice_leave_no_published_blob_or_staging']},
             'fixture_sha256': {'crates/storage/src/file_import.rs': '4fd50ade3c9747ea5924a13fa1c97b5551c6932aa2f1142ce5bd2ead502952a6',
                                'crates/service/tests/file_import_idempotency.rs': '253e5d359f823d2f375dd8acf21c0e7a4f027524302ea98befa8756e2615fc29'}}},
 {'id': 'bounded-receipt',
  'failures': {'known_request_rejects_a_valid_result_from_another_project': {'source': 'crates/storage/tests/apply_receipt_integrity.rs',
                                                                             'line': 91,
                                                                             'markers': ['accepted corrupt '
                                                                                         'known result:']},
               'known_request_rejects_a_different_embedded_request_id': {'source': 'crates/storage/tests/apply_receipt_integrity.rs',
                                                                         'line': 91,
                                                                         'markers': ['accepted corrupt known '
                                                                                     'result:']},
               'known_request_rejects_a_different_result_generation': {'source': 'crates/storage/tests/apply_receipt_integrity.rs',
                                                                       'line': 91,
                                                                       'markers': ['accepted corrupt known '
                                                                                   'result:']},
               'known_request_rejects_a_persisted_replay_flag': {'source': 'crates/storage/tests/apply_receipt_integrity.rs',
                                                                 'line': 91,
                                                                 'markers': ['accepted corrupt known '
                                                                             'result:']},
               'known_request_rejects_an_inconsistent_revision_transition': {'source': 'crates/storage/tests/apply_receipt_integrity.rs',
                                                                             'line': 91,
                                                                             'markers': ['accepted corrupt '
                                                                                         'known result:']},
               'known_request_rejects_a_valid_result_ahead_of_current_authority': {'source': 'crates/storage/tests/apply_receipt_integrity.rs',
                                                                                   'line': 91,
                                                                                   'markers': ['accepted '
                                                                                               'corrupt '
                                                                                               'known '
                                                                                               'result:']},
               'known_request_rejects_a_result_that_breaks_domain_invariants': {'source': 'crates/storage/tests/apply_receipt_integrity.rs',
                                                                                'line': 91,
                                                                                'markers': ['accepted '
                                                                                            'corrupt known '
                                                                                            'result:']},
               'known_request_rejects_a_result_bound_to_another_valid_event': {'source': 'crates/storage/tests/apply_receipt_integrity.rs',
                                                                               'line': 91,
                                                                               'markers': ['accepted corrupt '
                                                                                           'known result:']},
               'known_request_rejects_a_missing_commit_event': {'source': 'crates/storage/tests/apply_receipt_integrity.rs',
                                                                'line': 246,
                                                                'markers': ['accepted corrupt event '
                                                                            'binding:']},
               'known_request_rejects_a_changed_commit_event_payload': {'source': 'crates/storage/tests/apply_receipt_integrity.rs',
                                                                        'line': 246,
                                                                        'markers': ['accepted corrupt event '
                                                                                    'binding:']},
               'known_request_rejects_a_changed_commit_event_kind': {'source': 'crates/storage/tests/apply_receipt_integrity.rs',
                                                                     'line': 246,
                                                                     'markers': ['accepted corrupt event '
                                                                                 'binding:']}},
  'baseline': {'sha': '5c2ed0a86e658550e88287124ea3d3f57439f0b1',
               'tree': 'f3961fe1daee02e35b0caeb1c202dd4328764fba',
               'targets': {'receipt': ['known_request_rejects_a_valid_result_from_another_project',
                                       'known_request_rejects_a_different_embedded_request_id',
                                       'known_request_rejects_a_different_result_generation',
                                       'known_request_rejects_a_persisted_replay_flag',
                                       'known_request_rejects_an_inconsistent_revision_transition',
                                       'known_request_rejects_a_valid_result_ahead_of_current_authority',
                                       'known_request_rejects_a_result_that_breaks_domain_invariants',
                                       'known_request_rejects_a_result_bound_to_another_valid_event',
                                       'known_request_rejects_a_missing_commit_event',
                                       'known_request_rejects_a_changed_commit_event_payload',
                                       'known_request_rejects_a_changed_commit_event_kind',
                                       'known_request_replays_its_original_snapshot_after_later_edit_and_reopen']},
               'fixture_sha256': {'crates/storage/tests/apply_receipt_integrity.rs': 'cc995895ccfae9c8f584e15555e38aeccdca684f3afcc5b64d72b69619428883'}},
  'repair': {'sha': 'b2ca444b123f3c159500dc8c45d7a474a351c797',
             'tree': 'c5bd58ec345b8eb400ade61282819d2df293468b',
             'targets': {'receipt': ['known_request_rejects_a_valid_result_from_another_project',
                                     'known_request_rejects_a_different_embedded_request_id',
                                     'known_request_rejects_a_different_result_generation',
                                     'known_request_rejects_a_persisted_replay_flag',
                                     'known_request_rejects_an_inconsistent_revision_transition',
                                     'known_request_rejects_a_valid_result_ahead_of_current_authority',
                                     'known_request_rejects_a_result_that_breaks_domain_invariants',
                                     'known_request_rejects_a_result_bound_to_another_valid_event',
                                     'known_request_rejects_a_missing_commit_event',
                                     'known_request_rejects_a_changed_commit_event_payload',
                                     'known_request_rejects_a_changed_commit_event_kind',
                                     'known_request_replays_its_original_snapshot_after_later_edit_and_reopen']},
             'fixture_sha256': {'crates/storage/tests/apply_receipt_integrity.rs': 'cc995895ccfae9c8f584e15555e38aeccdca684f3afcc5b64d72b69619428883'}}}]


WORKFLOW = ".github/workflows/security-delivery.yml"
GATE_FILES = (
    WORKFLOW, "tooling/ci_foundation_causal.py",
    "tooling/tests/test_ci_foundation_causal.py",
    "tooling/ci_storage_apply_recovery.py", "tooling/ci_test_runner.py",
)
ROOT_INPUTS = ("Cargo.toml", "Cargo.lock", "SOURCE_LOCK.json", "rust-toolchain.toml")
CORE_INPUTS = (
    "crates/domain/Cargo.toml", "crates/domain/src/lib.rs",
    "crates/storage/Cargo.toml", "crates/storage/src/lib.rs",
    "crates/storage/src/file_import.rs", "crates/service/Cargo.toml",
    "crates/service/src/lib.rs", "crates/service/src/jobs.rs",
    "crates/service/src/file_import.rs",
)
TEST_OPTIONS = ("--test-threads=1", "--format", "pretty")
ROLES = ("baseline", "repair")
CASE_TIMEOUT = 120
BUILD_TIMEOUT = 1500
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class UnknownExecution(custody.VerificationError):
    """A timed-out process is not permission to replay or continue."""


def require(ok, message):
    custody.require(ok, message)


def validate_catalogue(pairs=None):
    pairs = PAIRS if pairs is None else pairs
    require([p["id"] for p in pairs] ==
            ["jobs", "svg", "retained-import", "bounded-receipt"],
            "the four reviewed pairs are required exactly once")
    for pair, expected_counts, expected_red in zip(
            pairs, ((4, 4), (13, 17), (4, 22), (12, 12)), (2, 9, 3, 11)):
        baseline_names = []
        for role, count in zip(ROLES, expected_counts):
            source = pair[role]
            require(SHA.fullmatch(source["sha"]) and SHA.fullmatch(source["tree"]),
                    "full frozen source and tree required")
            names = []
            for target, cases in source["targets"].items():
                require(target in TARGETS and cases, "unknown or empty target")
                require(all(re.fullmatch(r"[A-Za-z_][A-Za-z_0-9:]*", n) for n in cases),
                        "invalid exact Rust case")
                names.extend(cases)
            require(len(names) == len(set(names)) == count, "case inventory differs")
            require(all(DIGEST.fullmatch(v) for v in source["fixture_sha256"].values()),
                    "fixture hash required")
            if role == "baseline":
                baseline_names = names
                require(not any("fifo" in name.lower() for name in names),
                        "FIFO is forbidden on a plain-open baseline")
            else:
                require(set(baseline_names) <= set(names), "a causal baseline case was removed")
        failures = pair["failures"]
        require(len(failures) == expected_red and set(failures) < set(baseline_names),
                "expected named failures and passing controls are required")
        for failure in failures.values():
            require(failure["line"] > 0 and failure["markers"]
                    and all(isinstance(x, str) and x for x in failure["markers"]),
                    "a causal assertion site and message are required")
    return pairs


def execution_context(environment, runtime_platform=None):
    runtime_platform = sys.platform if runtime_platform is None else runtime_platform
    require(runtime_platform == "linux" and environment.get("RUNNER_OS") == "Linux"
            and environment.get("RUNNER_ARCH") == "X64", "requires the selected Linux x64 runner")
    require(environment.get("GITHUB_ACTIONS") == "true"
            and environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and environment.get("FOUNDATION_CAUSAL_SELECTION") == "foundation-causal",
            "causal execution requires the explicit selected dispatch")
    for name in ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA"):
        require(SHA.fullmatch(environment.get(name, "")), "full gate workflow SHA required")
    require(environment["GITHUB_SHA"] == environment["GITHUB_WORKFLOW_SHA"],
            "workflow and checkout sources differ")
    require(environment.get("GITHUB_JOB") == "foundation-causal",
            "unexpected logical job")
    for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        require(re.fullmatch(r"[1-9][0-9]*", environment.get(name, "")),
                "run and attempt required")
    repository = environment.get("GITHUB_REPOSITORY", "")
    require(repository and environment.get("GITHUB_WORKFLOW_REF", "").startswith(
        repository + "/" + WORKFLOW + "@"), "unexpected workflow identity")
    names = ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF",
             "GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
             "GITHUB_JOB", "RUNNER_OS", "RUNNER_ARCH")
    return {name: environment[name] for name in names}


def build_environment(environment, target, temporary):
    env = custody.isolated_build_environment(environment)
    forbidden = [name for name in env if name in
                 {"RUSTC", "CARGO_BUILD_RUSTC", "CARGO_BUILD_TARGET"}
                 or name.startswith("RUST_TEST_")]
    require(not forbidden, "inherited compiler/test overrides: " + ",".join(sorted(forbidden)))
    env.update(CARGO_TARGET_DIR=str(target), CARGO_TERM_COLOR="never", RUST_BACKTRACE="0",
               TMP=str(temporary), TEMP=str(temporary), TMPDIR=str(temporary))
    return env


def input_paths(repo, sha, source):
    raw = custody.git(repo, "ls-tree", "-r", "--name-only", "-z", sha)
    tracked = [p.decode("utf-8") for p in raw.split(b"\0") if p]
    require(len(tracked) == len(set(tracked)), "duplicate tracked paths")
    selected = sorted(p for p in tracked if
        p in ROOT_INPUTS or p.startswith((".cargo/", "crates/domain/",
            "crates/storage/", "crates/service/", "vendor/"))
        or p.endswith(("/Cargo.toml", "/build.rs")))
    required = {*ROOT_INPUTS, *CORE_INPUTS, *source["fixture_sha256"]}
    require(required <= set(selected), "missing required compiled input")
    for target in source["targets"]:
        require(TARGETS[target]["source"] in selected, "missing selected target source")
    require(all(not Path(p).is_absolute() and ".." not in Path(p).parts for p in selected),
            "unsafe tracked source path")
    return tuple(selected)


def product_snapshot(repo, source):
    result = custody.source_snapshot(repo, source["sha"],
        input_paths(repo, source["sha"], source), source["tree"])
    for name, expected in source["fixture_sha256"].items():
        require(result["files"][name]["sha256"] == expected, "frozen fixture changed: " + name)
    return result


def retain_file(path, source_root, output, folder, expected=None):
    original = custody.fingerprint(path, source_root)
    if expected is not None:
        require((original["bytes"], original["sha256"]) ==
                (expected["bytes"], expected["sha256"]), "source copy preimage differs")
    destination = output / folder / original["sha256"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        with path.open("rb") as src, destination.open("xb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
    retained = custody.fingerprint(destination, output)
    require((retained["bytes"], retained["sha256"]) ==
            (original["bytes"], original["sha256"])
            and custody.fingerprint(path, source_root) == original,
            "retained original changed")
    return retained


def retain_source(repo, snapshot, output):
    return {name: retain_file(repo / name, repo, output, "sources", observed)
            for name, observed in snapshot["files"].items()}


def command(argv, cwd, environment, output, label, timeout, context, observations=None):
    row = custody.execute(argv, cwd, environment, output, label, timeout, context)
    if observations is not None:
        root, items = observations
        items.append({"folder": output.relative_to(root).as_posix(), "command": row,
                      "receipt": custody.fingerprint(output / (label + ".command.json"), root)})
    if row["observation"] == "UNKNOWN_TIMEOUT_NO_RETRY":
        raise UnknownExecution("timed out; stop all remaining cases without replay: " + label)
    require(row["observation"] == "DIRECT_PROCESS_EXIT_OBSERVED",
            "command did not start or exit observably: " + label)
    require(row["log_custody"] == "VERIFIED_SNAPSHOT", "original command log custody failed")
    return row


def compile_argv(cargo, spec):
    selection = ["--lib"] if spec["kind"] == "lib" else ["--test", spec["name"]]
    return [cargo, "test", "--locked", "-p", spec["package"],
            *selection, "--no-run", "--message-format=json"]


def clippy_argv(cargo, spec):
    selection = ["--lib"] if spec["kind"] == "lib" else ["--test", spec["name"]]
    return [cargo, "clippy", "--locked", "-p", spec["package"],
            *selection, "--", "-D", "warnings"]


def select_binary(raw, code, repo, target, spec):
    require(code == 0, "compilation failure is not a causal RED result")
    messages = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    require(all(isinstance(item, dict) for item in messages), "invalid Cargo JSON messages")
    endings = [item for item in messages if item.get("reason") == "build-finished"]
    require(len(endings) == 1 and endings[0].get("success") is True,
            "missing successful compilation completion")
    artifacts = [item for item in messages if item.get("reason") == "compiler-artifact"
        and item.get("executable") and item.get("target", {}).get("name") == spec["name"]
        and item["target"].get("kind") == [spec["kind"]]
        and item.get("profile", {}).get("test") is True]
    require(len(artifacts) == 1, "compiled selected binary is absent or ambiguous")
    item = artifacts[0]
    crate = "service" if spec["package"] == "motionwright-service" else "storage"
    require(Path(item["manifest_path"]).absolute() == repo / ("crates/" + crate + "/Cargo.toml")
            and Path(item["target"]["src_path"]).absolute() == repo / spec["source"],
            "compiled binary belongs to another source")
    path = Path(item["executable"]).absolute()
    observed = custody.fingerprint(path, target)
    require(observed["bytes"] > 0, "empty compiled binary")
    return {"absolute_path": str(path), **observed}


def selected_roster(raw, code, spec, expected, role):
    require(code == 0, "enumeration failed")
    text = raw.decode("utf-8").replace("\r\n", "\n")
    names = tuple(re.findall(r"(?m)^([A-Za-z_][A-Za-z_0-9:]*): test$", text))
    require(names and len(names) == len(set(names)), "missing or duplicate enumerated tests")
    remainder = re.sub(r"(?m)^[A-Za-z_][A-Za-z_0-9:]*: test\n?", "", text)
    remainder = re.sub(r"(?m)^\d+ tests?, 0 benchmarks?\s*$", "", remainder)
    require(not remainder.strip(), "unrecognized test enumeration output")
    prefixes = spec["prefixes"]
    if spec["name"] == "motionwright_storage" and role == "repair":
        prefixes = ("file_import::source_open_tests::",
                    "file_import::tests::", "file_import::staging_tests::")
    selected = names if prefixes is None else tuple(n for n in names if n.startswith(prefixes))
    require(len(expected) == len(set(expected))
            and set(selected) == set(expected) and len(selected) == len(expected),
            "selected test roster changed, missing or extra")
    return {"all": names, "selected": tuple(expected),
            "not_selected": tuple(n for n in names if n not in set(expected)),
            "not_selected_credit": "NONE"}


def case_argv(binary, pair, role, name):
    require(role in ROLES and name in {
        n for names in pair[role]["targets"].values() for n in names}, "unselected exact case")
    require(role != "baseline" or "fifo" not in name.lower(),
            "FIFO must never execute against the plain-open baseline")
    return [binary["absolute_path"], "--exact", name, *TEST_OPTIONS]


def admit_case(pair, role, name, raw, row, binary, after, roster, context):
    require(row["observation"] == "DIRECT_PROCESS_EXIT_OBSERVED"
            and row["log_custody"] == "VERIFIED_SNAPSHOT", "case process or log custody incomplete")
    require(row["argv"] == case_argv(binary, pair, role, name)
            and row["context"] == context and binary == after,
            "command, context or executed binary changed")
    require(name in roster["selected"] and name in roster["all"], "case was not enumerated")
    expected_red = pair["failures"].get(name) if role == "baseline" else None
    failed = expected_red is not None
    require(row["exit_code"] == (101 if failed else 0),
            "unexpected exit; setup, signals and launch errors are not causal RED")
    text = custody.ANSI.sub("", raw.decode("utf-8").replace("\r\n", "\n"))
    results = re.findall(r"(?m)^test (.+) \.\.\. (ok|FAILED|ignored)\s*$", text)
    summaries = re.findall(r"(?m)^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; "
        r"(\d+) ignored; (\d+) measured; (\d+) filtered out;", text)
    expected_state = "FAILED" if failed else "ok"
    counts = (0, 1, 0, 0, len(roster["all"]) - 1) if failed else (
              1, 0, 0, 0, len(roster["all"]) - 1)
    require(results == [(name, expected_state)] and len(summaries) == 1
            and summaries[0][0] == expected_state
            and tuple(map(int, summaries[0][1:])) == counts,
            "missing, repeated, filtered, skipped or unsuccessful exact case")
    panics = re.findall(r"(?m)^thread '([^']+)'(?: \(\d+\))? panicked at ([^\n]+):\s*$", text)
    if failed:
        require(len(panics) == 1 and panics[0][0] == name,
                "expected one named assertion failure, not an unrelated panic")
        site = panics[0][1].replace("\\", "/")
        expected_site = re.escape(expected_red["source"]) + ":" + str(expected_red["line"]) + r":\d+"
        require(re.fullmatch(r"(?:.*/)?" + expected_site, site)
                and all(marker in text for marker in expected_red["markers"]),
                "baseline failed outside the frozen causal assertion site or message")
    else:
        require(not panics, "passing case emitted a panic")
    return {"name": name, "status": "EXPECTED_CAUSAL_RED" if failed else "PASS",
            "pair": pair["id"], "role": role, "context": context,
            "binary_sha256": binary["sha256"], "command": row,
            "assertion_site": expected_red}


def role_complete(pair, role, result):
    expected = [name for cases in pair[role]["targets"].values() for name in cases]
    observed = result.get("results", [])
    require(len(observed) == len(expected)
            and {row["name"] for row in observed} == set(expected),
            "incomplete, duplicate or foreign role results")
    for row in observed:
        state = "EXPECTED_CAUSAL_RED" if role == "baseline" and row["name"] in pair["failures"] else "PASS"
        require(row["status"] == state, "causal outcome differs")
    require(result.get("source_before") == result.get("source_after")
            and result.get("source_before"), "source custody changed or is incomplete")
    require(result.get("clippy") == ("PASS" if role == "repair" else "NOT_SELECTED"),
            "selected strict Clippy did not complete")
    return {"status": "PASS_EXACT_CAUSAL_ROLE", "cases": len(expected),
            "expected_assertion_failures": len(pair["failures"]) if role == "baseline" else 0,
            "passing_cases": len(expected) - (len(pair["failures"]) if role == "baseline" else 0)}


def tools_identity(gate, output, environment, context, observations=None):
    tools = {}
    for name in ("rustc", "cargo", "clippy-driver"):
        found = command(["rustup", "which", "--toolchain", "1.98.1", name],
                        gate, environment, output, name + "-path", 45, context, observations)
        require(found["exit_code"] == 0, "selected toolchain executable unavailable")
        path = Path(custody.read_original(output, found, "stdout").decode().strip())
        require(path.is_absolute(), "tool executable path is not absolute")
        path = path.resolve(strict=True)
        identity = custody.fingerprint(path, path.parent)
        version = command([str(path), "-Vv" if name == "rustc" else "-V"],
                          gate, environment, output, name + "-version", 45, context, observations)
        require(version["exit_code"] == 0, "tool version query failed")
        raw = custody.read_original(output, version, "stdout").decode()
        if name == "rustc":
            require("release: 1.98.1\n" in raw and "host: x86_64-unknown-linux-gnu\n" in raw,
                    "unexpected compiler release or host")
        else:
            require(raw.startswith("cargo " if name == "cargo" else "clippy "),
                    "unexpected Cargo or Clippy version output")
        tools[name] = {"absolute_path": str(path), **identity, "version": raw,
                       "path_command": found, "version_command": version}
    python = Path(sys.executable).resolve(strict=True)
    tools["python"] = {"absolute_path": str(python), **custody.fingerprint(python, python.parent),
                       "version": sys.version}
    return tools


def run_role(pair, role, gate, workspace, output, target, temporary, environment, context, toolchain,
             observations=None):
    source = pair[role]
    repo = workspace / "foundation-sources" / pair["id"] / role
    repo.parent.mkdir(parents=True, exist_ok=True)
    folder = output / pair["id"] / role
    folder.mkdir(parents=True, exist_ok=False)
    frame = {**context, "pair": pair["id"], "role": role,
             "product_source": source["sha"], "product_tree": source["tree"]}
    result = {"pair": pair["id"], "role": role, "source": source["sha"], "tree": source["tree"],
              "status": "FAIL", "results": [], "phase": "checkout",
              "clippy": "NOT_RUN" if role == "repair" else "NOT_SELECTED",
              "formal_acceptance": False}
    custody.write_new(folder / "started.json", {**result, "started_utc": custody.now()})
    try:
        require(not repo.exists(), "prior product checkout exists; no reuse")
        checkout = command(["git", "--no-replace-objects", "-c", "core.autocrlf=false",
            "-c", "core.fsmonitor=false", "-C", str(gate),
            "worktree", "add", "--detach", str(repo), source["sha"]],
            gate, environment, folder, "checkout", 120, frame, observations)
        require(checkout["exit_code"] == 0, "exact frozen product checkout failed")
        before = product_snapshot(repo, source)
        result["source_before"] = before
        custody.write_new(folder / "source-before.json", before)
        result["retained_source"] = retain_source(repo, before, output)
        for target_name, names in source["targets"].items():
            spec = TARGETS[target_name]
            result["phase"] = "compile:" + target_name
            build = command(compile_argv(toolchain["cargo"]["absolute_path"], spec),
                            repo, environment, folder, target_name + "-build", BUILD_TIMEOUT, frame, observations)
            binary = select_binary(custody.read_original(folder, build, "stdout"),
                                   build["exit_code"], repo, target, spec)
            retained = retain_file(Path(binary["absolute_path"]), target, output, "binaries")
            result.setdefault("binaries", {})[target_name] = {**binary, "retained": retained,
                                                           "build": build}
            listing = command([binary["absolute_path"], "--list", "--format", "terse"],
                              repo, environment, folder, target_name + "-list", 45, frame, observations)
            roster = selected_roster(custody.read_original(folder, listing, "stdout"),
                                     listing["exit_code"], spec, names, role)
            result.setdefault("rosters", {})[target_name] = roster
            result["phase"] = "cases:" + target_name
            for index, name in enumerate(names, 1):
                case_temp = temporary / pair["id"] / role / target_name / str(index)
                case_temp.mkdir(parents=True, exist_ok=False)
                case_env = dict(environment, TMP=str(case_temp), TEMP=str(case_temp),
                                TMPDIR=str(case_temp))
                require(binary == {"absolute_path": binary["absolute_path"],
                    **custody.fingerprint(Path(binary["absolute_path"]), target)},
                    "compiled binary changed before exact case")
                label = target_name + "-case-" + str(index).zfill(2)
                executed = command(case_argv(binary, pair, role, name),
                    repo, case_env, folder, label, CASE_TIMEOUT, frame, observations)
                after = {"absolute_path": binary["absolute_path"],
                         **custody.fingerprint(Path(binary["absolute_path"]), target)}
                try:
                    row = admit_case(pair, role, name,
                        custody.read_original(folder, executed, "stdout") + b"\n"
                        + custody.read_original(folder, executed, "stderr"),
                        executed, binary, after, roster, frame)
                except (ValueError, OSError, UnicodeError) as error:
                    row = {"name": name, "status": "FAIL", "error": str(error),
                           "command": executed, "binary_before": binary, "binary_after": after}
                result["results"].append(row)
                custody.write_new(folder / (label + ".receipt.json"), row)
            if role == "repair":
                strict = command(clippy_argv(toolchain["cargo"]["absolute_path"], spec),
                    repo, environment, folder, target_name + "-clippy", BUILD_TIMEOUT, frame, observations)
                require(strict["exit_code"] == 0, "selected strict Clippy failed")
        result["clippy"] = "PASS" if role == "repair" else "NOT_SELECTED"
        result["phase"] = "postcheck"
        result["source_after"] = product_snapshot(repo, source)
        custody.write_new(folder / "source-after.json", result["source_after"])
        result.update(role_complete(pair, role, result))
        result["phase"] = "complete"
    except UnknownExecution as error:
        result.update(status="UNKNOWN", error=str(error),
                      terminal_custody="UNSEALED_DESCENDANTS_NOT_OBSERVED")
    except Exception as error:
        result.update(error=type(error).__name__ + ": " + str(error))
    result["finished_utc"] = custody.now()
    result["operations_retried"] = False
    result["not_run"] = [name for names in source["targets"].values() for name in names
                        if name not in {row["name"] for row in result["results"]}]
    custody.write_new(folder / "receipt.json", result)
    return result


def verify_originals(output, observations, receipt):
    """Recheck in-memory observations before declaring an archive complete."""
    verified = {}
    for item in observations:
        original = item["receipt"]
        path = output / original["path"]
        require(custody.fingerprint(path, output) == original,
                "command receipt changed after observation")
        row = item["command"]
        for stream in ("stdout", "stderr"):
            expected = row[stream]
            relative = str(Path(item["folder"]) / expected["path"])
            require(custody.fingerprint(output / relative, output / item["folder"]) == expected,
                    "original command output changed after observation")
            verified[relative] = expected
        verified[original["path"]] = original
    retained = list(receipt.get("retained_gate", {}).values())
    for pair in receipt.get("pairs", []):
        for role in pair["roles"]:
            retained.extend(role.get("retained_source", {}).values())
            retained.extend(item["retained"] for item in role.get("binaries", {}).values())
    for original in retained:
        require(custody.fingerprint(output / original["path"], output) == original,
                "retained source or executable changed after observation")
        verified[original["path"]] = original
    require(observations and retained, "no retained observations or source originals")
    return {"status": "VERIFIED_BEFORE_TERMINAL_RECEIPT", "commands": len(observations),
            "files": verified}


def run_pairs(callback):
    results = []
    for pair in PAIRS:
        result = {"id": pair["id"], "status": "FAIL", "roles": []}
        for role in ROLES:
            observed = callback(pair, role)
            result["roles"].append(observed)
            if observed["status"] == "UNKNOWN":
                result["status"] = "UNKNOWN"
                break
        if len(result["roles"]) == 2 and all(
                row["status"] == "PASS_EXACT_CAUSAL_ROLE" for row in result["roles"]):
            result["status"] = "PASS_EXACT_CAUSAL_PAIR"
        results.append(result)
        if result["status"] == "UNKNOWN":
            break
    return results


def run(workspace, output):
    context = execution_context(dict(os.environ))
    validate_catalogue()
    gate = Path(__file__).resolve().parents[1]
    workspace, output = workspace.resolve(strict=True), output.resolve()
    require(workspace == Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
            and gate.is_relative_to(workspace) and gate != workspace
            and output.is_relative_to(workspace) and output != workspace
            and not output.is_relative_to(gate) and not gate.is_relative_to(output),
            "gate, product worktrees and output require separate workspace paths")
    output.mkdir(parents=True, exist_ok=False)
    receipt = {"schema": "motionwright.foundation-causal-linux/1", "status": "FAIL",
               "context": context, "pairs": [], "formal_acceptance": False,
               "installed_application": "NOT_RUN", "operations_retried": False,
               "scope": "Only the eight frozen product sources and selected synthetic Rust contracts",
               "limitations": [
                   "Schema4 is a distinct unexecuted pair.",
                   "Same-payload receipt substitution remains outside the limited receipt repair.",
                   "No provider cancellation, installed application or human acceptance is established.",
                   "Seven original decoded SVG cases are a subset of thirteen, not additional executions.",
               ]}
    custody.write_new(output / "started.json", {**receipt, "started_utc": custody.now()})
    try:
        target, temporary = workspace / "foundation-target", workspace / "foundation-tmp"
        require(not target.exists() and not temporary.exists()
                and not (workspace / "foundation-sources").exists(), "prior run state exists; no replay")
        target.mkdir(); temporary.mkdir()
        environment = build_environment(os.environ, target, temporary)
        gate_before = custody.source_snapshot(gate, context["GITHUB_SHA"], GATE_FILES)
        receipt["gate_source_before"] = gate_before
        custody.write_new(output / "gate-source-before.json", gate_before)
        receipt["retained_gate"] = retain_source(gate, gate_before, output)
        tool_folder = output / "tools"
        tool_folder.mkdir()
        observations = (output, [])
        toolchain = tools_identity(gate, tool_folder, environment, context, observations)
        receipt["tools"] = toolchain
        environment["RUSTC"] = toolchain["rustc"]["absolute_path"]
        environment["RUSTUP_TOOLCHAIN"] = "1.98.1"
        receipt["build_environment"] = {k: environment.get(k, "") for k in (
            "CARGO_TARGET_DIR", "RUSTC", "RUSTUP_TOOLCHAIN", "CARGO_INCREMENTAL",
            "CARGO_PROFILE_TEST_DEBUG", "CARGO_PROFILE_DEV_DEBUG", "CARGO_TERM_COLOR",
            "RUST_BACKTRACE")}
        receipt["pairs"] = run_pairs(lambda pair, role: run_role(
            pair, role, gate, workspace, output, target, temporary,
            environment, context, toolchain, observations))
        if any(p["status"] == "UNKNOWN" for p in receipt["pairs"]):
            receipt.update(status="UNKNOWN", terminal_custody="UNSEALED_DESCENDANTS_NOT_OBSERVED")
        if receipt["status"] != "UNKNOWN":
            gate_after = custody.source_snapshot(gate, context["GITHUB_SHA"], GATE_FILES)
            receipt["gate_source_after"] = gate_after
            custody.write_new(output / "gate-source-after.json", gate_after)
            require(gate_before == gate_after, "gate source changed during execution")
            for tool in toolchain.values():
                path = Path(tool["absolute_path"])
                require({k: tool[k] for k in ("path", "bytes", "sha256", "identity")} ==
                        custody.fingerprint(path, path.parent), "tool executable changed")
            receipt["originals_final"] = verify_originals(output, observations[1], receipt)
            if len(receipt["pairs"]) == 4 and all(
                    p["status"] == "PASS_EXACT_CAUSAL_PAIR" for p in receipt["pairs"]):
                receipt["status"] = "PASS_FOUR_EXACT_CAUSAL_PAIRS"
    except UnknownExecution as error:
        receipt.update(status="UNKNOWN", error=str(error),
                       terminal_custody="UNSEALED_DESCENDANTS_NOT_OBSERVED")
    except Exception as error:
        receipt.update(error=type(error).__name__ + ": " + str(error))
    receipt["finished_utc"] = custody.now()
    receipt["not_run_pairs"] = [p["id"] for p in PAIRS
                               if p["id"] not in {row["id"] for row in receipt["pairs"]}]
    receipt["expected_invocations"] = 88
    receipt["actual_case_results"] = sum(len(r["results"]) for p in receipt["pairs"] for r in p["roles"])
    custody.write_new(output / "receipt.json", receipt)
    print(json.dumps({k: receipt.get(k) for k in
                     ("status", "actual_case_results", "not_run_pairs", "error")}))
    return 0 if receipt["status"] == "PASS_FOUR_EXACT_CAUSAL_PAIRS" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        return run(args.workspace, args.output)
    except (ValueError, OSError, UnicodeError) as error:
        print("Foundation causal verification refused: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
