#!/usr/bin/env python3
"""Verify a closed selection of frozen Foundation pairs on one Linux runner.

Named assertion failures plus passing controls establish a causal baseline.
Compiler/setup failures, skipped cases and UNKNOWN never establish that result.
These synthetic contracts do not establish installed or human acceptance.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
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
                    'prefixes': ('file_import::source_open_tests::',),
                    'repair_prefixes': ('file_import::source_open_tests::',
                                        'file_import::tests::',
                                        'file_import::staging_tests::')},
 'import-service': {'package': 'motionwright-service',
                    'kind': 'test',
                    'name': 'file_import_idempotency',
                    'source': 'crates/service/tests/file_import_idempotency.rs',
                    'prefixes': None},
 'receipt': {'package': 'motionwright-storage',
             'kind': 'test',
             'name': 'apply_receipt_integrity',
             'source': 'crates/storage/tests/apply_receipt_integrity.rs',
             'prefixes': None},
 'budget-service': {'package': 'motionwright-service',
                    'kind': 'test',
                    'name': 'file_import_limits',
                    'source': 'crates/service/tests/file_import_limits.rs',
                    'prefixes': None},
 'budget-storage': {'package': 'motionwright-storage',
                    'kind': 'lib',
                    'name': 'motionwright_storage',
                    'source': 'crates/storage/src/lib.rs',
                    'prefixes': ('file_import::source_budget_tests::',)},
 'budget-service-unit': {'package': 'motionwright-service',
                         'kind': 'lib',
                         'name': 'motionwright_service',
                         'source': 'crates/service/src/lib.rs',
                         'prefixes': ('import_limits::tests::',
                                      'audio::measurement_budget_tests::')}}

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


IMPORT_BUDGET_PAIR = {
    "id": "import-resource-budget",
    "failures": {
        "source_byte_boundary_applies_to_asset_voice_and_both_host_constructors": {
            "source": "crates/service/tests/file_import_limits.rs",
            "line": 375,
            "markers": [
                "expected source byte budget",
                "got Ok("
            ]
        },
        "known_source_hash_respects_reduced_bytes_but_source_free_recovery_survives": {
            "source": "crates/service/tests/file_import_limits.rs",
            "line": 411,
            "markers": [
                "expected source byte budget",
                "got Ok("
            ]
        },
        "audio_duration_accepts_one_second_and_rejects_one_additional_frame": {
            "source": "crates/service/tests/file_import_limits.rs",
            "line": 549,
            "markers": [
                "expected audio measurement limit exceeded: audio_seconds",
                "got Ok("
            ]
        },
        "stereo_sample_budget_counts_both_channels_without_duration_preemption": {
            "source": "crates/service/tests/file_import_limits.rs",
            "line": 576,
            "markers": [
                "expected audio measurement limit exceeded: decoded_samples",
                "got Ok("
            ]
        },
        "packet_byte_budget_rejects_valid_pcm_before_publication": {
            "source": "crates/service/tests/file_import_limits.rs",
            "line": 357,
            "markers": [
                "expected audio measurement limit exceeded: packet_bytes",
                "got Ok("
            ]
        },
        "declared_packet_sample_budget_rejects_valid_pcm_before_publication": {
            "source": "crates/service/tests/file_import_limits.rs",
            "line": 357,
            "markers": [
                "expected audio measurement limit exceeded: packet_samples",
                "got Ok("
            ]
        },
        "demux_packet_budget_accepts_one_packet_and_rejects_a_three_packet_wav": {
            "source": "crates/service/tests/file_import_limits.rs",
            "line": 357,
            "markers": [
                "expected audio measurement limit exceeded: demux_packets",
                "got Ok("
            ]
        },
        "audio_read_budget_rejects_valid_wav_during_probe_without_publication": {
            "source": "crates/service/tests/file_import_limits.rs",
            "line": 357,
            "markers": [
                "expected audio measurement limit exceeded: audio_read_bytes",
                "got Ok("
            ]
        },
        "demux_budget_counts_real_packets_from_unselected_tracks": {
            "source": "crates/service/tests/file_import_limits.rs",
            "line": 797,
            "markers": [
                "expected audio measurement limit exceeded: demux_packets",
                "got Ok("
            ]
        },
        "actual_decode_sample_budget_applies_without_declared_frame_count": {
            "source": "crates/service/tests/file_import_limits.rs",
            "line": 814,
            "markers": [
                "expected audio measurement limit exceeded: decoded_samples",
                "got Ok("
            ]
        },
        "actual_decode_packet_budget_applies_without_declared_packet_frames": {
            "source": "crates/service/tests/file_import_limits.rs",
            "line": 833,
            "markers": [
                "expected audio measurement limit exceeded: packet_samples",
                "got Ok("
            ]
        },
        "actual_duration_budget_rejects_one_extra_frame_without_declared_duration": {
            "source": "crates/service/tests/file_import_limits.rs",
            "line": 852,
            "markers": [
                "expected audio measurement limit exceeded: audio_seconds",
                "got Ok("
            ]
        },
        "declared_svg_budget_also_respects_a_lower_host_byte_limit": {
            "source": "crates/service/tests/file_import_limits.rs",
            "line": 885,
            "markers": [
                "expected source byte budget",
                "got Ok("
            ]
        },
        "file_import::source_budget_tests::bounded_replay_hash_accepts_exact_budget_and_rejects_initial_excess": {
            "source": "crates/storage/src/file_import.rs",
            "line": 1152,
            "markers": [
                "expected initial source-size rejection",
                "got Ok("
            ]
        },
        "file_import::source_budget_tests::bounded_replay_hash_rejects_growth_after_first_read": {
            "source": "crates/storage/src/file_import.rs",
            "line": 1180,
            "markers": [
                "expected stream-growth rejection",
                "got Ok("
            ]
        }
    },
    "baseline": {
        "sha": "d3cbb9e875ebf39a9bcd1c02ca2786d852378e40",
        "tree": "1e093ebcaa998ddb30e43dab93390fc583cb13ef",
        "targets": {
            "budget-service": [
                "source_byte_boundary_applies_to_asset_voice_and_both_host_constructors",
                "known_source_hash_respects_reduced_bytes_but_source_free_recovery_survives",
                "known_voice_replay_does_not_remeasure_or_rehydrate_under_tiny_audio_budgets",
                "source_free_recovery_ignores_all_import_limits_after_reopen",
                "audio_duration_accepts_one_second_and_rejects_one_additional_frame",
                "stereo_sample_budget_counts_both_channels_without_duration_preemption",
                "packet_byte_budget_rejects_valid_pcm_before_publication",
                "declared_packet_sample_budget_rejects_valid_pcm_before_publication",
                "demux_packet_budget_accepts_one_packet_and_rejects_a_three_packet_wav",
                "audio_read_budget_rejects_valid_wav_during_probe_without_publication",
                "real_multitrack_control_has_unknown_duration_and_exact_selected_pcm",
                "demux_budget_counts_real_packets_from_unselected_tracks",
                "actual_decode_sample_budget_applies_without_declared_frame_count",
                "actual_decode_packet_budget_applies_without_declared_packet_frames",
                "actual_duration_budget_rejects_one_extra_frame_without_declared_duration",
                "declared_svg_budget_also_respects_a_lower_host_byte_limit"
            ],
            "budget-storage": [
                "file_import::source_budget_tests::bounded_replay_hash_accepts_exact_budget_and_rejects_initial_excess",
                "file_import::source_budget_tests::bounded_replay_hash_rejects_growth_after_first_read",
                "file_import::source_budget_tests::bounded_replay_hash_includes_growth_to_exact_budget",
                "file_import::source_budget_tests::staging_rejects_growth_without_touching_sql_cas_or_another_capture",
                "file_import::source_budget_tests::staging_captures_growth_to_exact_budget_then_removes_its_private_file"
            ]
        },
        "fixture_sha256": {
            "crates/service/tests/file_import_limits.rs": "6e09c0069069c84f3e2ca36514852e078aea484f80a62aa5f6cded6d7045a45f",
            "crates/service/src/import_limits.rs": "21a58b974f1c440350581a2c7ca5d54b4907401fd9a33a8a4cc48389e2310d1e",
            "crates/storage/src/file_import.rs": "ca57ad6e19cebafbd8e54981ab68a8be8744cf63efbaa15fd3b017e35febf69c",
            "crates/service/src/audio.rs": "c189e5daf526427cf7371659676c22357891476485f280e5c74ea3624de820e9"
        }
    },
    "repair": {
        "sha": "ec0ed076efe7e9fabbf1385ca83bd747e3b2fade",
        "tree": "922799c1b36813cda2d5bbd3658472f698ef1c25",
        "targets": {
            "budget-service": [
                "source_byte_boundary_applies_to_asset_voice_and_both_host_constructors",
                "known_source_hash_respects_reduced_bytes_but_source_free_recovery_survives",
                "known_voice_replay_does_not_remeasure_or_rehydrate_under_tiny_audio_budgets",
                "source_free_recovery_ignores_all_import_limits_after_reopen",
                "audio_duration_accepts_one_second_and_rejects_one_additional_frame",
                "stereo_sample_budget_counts_both_channels_without_duration_preemption",
                "packet_byte_budget_rejects_valid_pcm_before_publication",
                "declared_packet_sample_budget_rejects_valid_pcm_before_publication",
                "demux_packet_budget_accepts_one_packet_and_rejects_a_three_packet_wav",
                "audio_read_budget_rejects_valid_wav_during_probe_without_publication",
                "real_multitrack_control_has_unknown_duration_and_exact_selected_pcm",
                "demux_budget_counts_real_packets_from_unselected_tracks",
                "actual_decode_sample_budget_applies_without_declared_frame_count",
                "actual_decode_packet_budget_applies_without_declared_packet_frames",
                "actual_duration_budget_rejects_one_extra_frame_without_declared_duration",
                "declared_svg_budget_also_respects_a_lower_host_byte_limit"
            ],
            "budget-storage": [
                "file_import::source_budget_tests::bounded_replay_hash_accepts_exact_budget_and_rejects_initial_excess",
                "file_import::source_budget_tests::bounded_replay_hash_rejects_growth_after_first_read",
                "file_import::source_budget_tests::bounded_replay_hash_includes_growth_to_exact_budget",
                "file_import::source_budget_tests::staging_rejects_growth_without_touching_sql_cas_or_another_capture",
                "file_import::source_budget_tests::staging_captures_growth_to_exact_budget_then_removes_its_private_file"
            ],
            "budget-service-unit": [
                "import_limits::tests::every_limit_rejects_zero_and_an_increase",
                "import_limits::tests::reduced_limits_cannot_be_raised_again",
                "import_limits::tests::reducing_a_copy_preserves_the_original_and_other_limits",
                "audio::measurement_budget_tests::bounded_file_accepts_exact_budget_and_true_eof",
                "audio::measurement_budget_tests::bounded_file_seek_charges_rereads_and_keeps_exhaustion",
                "audio::measurement_budget_tests::bounded_file_consumes_only_one_excess_probe_byte"
            ]
        },
        "fixture_sha256": {
            "crates/service/tests/file_import_limits.rs": "6e09c0069069c84f3e2ca36514852e078aea484f80a62aa5f6cded6d7045a45f",
            "crates/service/src/import_limits.rs": "21a58b974f1c440350581a2c7ca5d54b4907401fd9a33a8a4cc48389e2310d1e",
            "crates/storage/src/file_import.rs": "7f080115695b37d48f7bdf3f09addc20ac8325c859541c259a90a5ba4ef82ffb",
            "crates/service/src/audio.rs": "0e02e56cc9546ae5570a424aca653c966e7ec17a7679dee5426418f9263f047c"
        }
    }
}



# The current composition is a separate, closed source selection.
CURRENT_TARGETS = {
    'current-storage-unit': {
        'package': 'motionwright-storage',
        'kind': 'lib',
        'name': 'motionwright_storage',
        'source': 'crates/storage/src/lib.rs',
        'prefixes': ('verified_blob_capture_budget_tests::',),
        'repair_prefixes': (
            'file_import::source_open_tests::',
            'file_import::tests::',
            'file_import::staging_tests::',
            'file_import::source_budget_tests::',
            'apply_receipt_binding::tests::',
            'apply_crash_tests::',
            'verified_blob_capture_budget_tests::',
        ),
    },
    'current-service-unit': {
        'package': 'motionwright-service',
        'kind': 'lib',
        'name': 'motionwright_service',
        'source': 'crates/service/src/lib.rs',
        'prefixes': ('audio_import_tests::original_mix_decodes_captured_cas_bytes_despite_a_b_a_replacement', 'audio_import_tests::original_mix_rejects_store_generation_replacement_at_the_same_revision', 'audio::original_mix_source_tests::imported_original_wav_and_its_pcm_keep_distinct_checked_sha256'),
        'repair_prefixes': (
            'import_limits::tests::',
            'audio::measurement_budget_tests::',
            'audio::original_mix_source_tests::',
            'audio_import_tests::original_mix_decodes_captured_cas_bytes_despite_a_b_a_replacement',
            'audio_import_tests::original_mix_rejects_store_generation_replacement_at_the_same_revision',
        ),
    },
    'binding4': {
        'package': 'motionwright-storage',
        'kind': 'test',
        'name': 'apply_receipt_binding',
        'source': 'crates/storage/tests/apply_receipt_binding.rs',
        'prefixes': None,
    },
    'binding4-migration': {
        'package': 'motionwright-storage',
        'kind': 'test',
        'name': 'apply_binding_migration',
        'source': 'crates/storage/tests/apply_binding_migration.rs',
        'prefixes': None,
    },
    'project-media-independence': {
        'package': 'motionwright-service',
        'kind': 'test',
        'name': 'project_projection_media_independence',
        'source': 'crates/service/tests/project_projection_media_independence.rs',
        'prefixes': None,
    },
    'original-mix-source': {
        'package': 'motionwright-service',
        'kind': 'test',
        'name': 'original_mix_source_budget',
        'source': 'crates/service/tests/original_mix_source_budget.rs',
        'prefixes': None,
    },
}

CURRENT_COMPOSITION_PAIR = {
    'id': 'foundation-current',
    'failures': {
        'verified_blob_capture_budget_tests::verified_blob_capture_rejects_growth_after_metadata_at_limit_plus_one': {
            'source': 'crates/storage/src/lib.rs',
            'line': 2901,
            'markers': ['verified blob capture accepted eight bytes after admitting a four-byte source with limit four'],
        },
        'audio_import_tests::original_mix_decodes_captured_cas_bytes_despite_a_b_a_replacement': {
            'source': 'crates/service/src/lib.rs',
            'line': 1746,
            'markers': ['captured source binding mismatch: original=A(+8192), decoded=B(-8192), final CAS=A'],
        },
        'audio_import_tests::original_mix_rejects_store_generation_replacement_at_the_same_revision': {
            'source': 'crates/service/src/lib.rs',
            'line': 1868,
            'markers': ['original mix accepted a replacement project generation at the same revision and digest'],
        },
    },
    'baseline': {
        'sha': '2344341580d3f86aa4c1f7922a4e7b5217d35322',
        'tree': 'bc7a61fb9170d0ee40b7c5dba9353e2439866b0e',
        'targets': {
            'current-storage-unit': ['verified_blob_capture_budget_tests::verified_blob_capture_rejects_growth_after_metadata_at_limit_plus_one', 'verified_blob_capture_budget_tests::verified_blob_capture_keeps_empty_exact_and_oversize_controls'],
            'current-service-unit': ['audio_import_tests::original_mix_decodes_captured_cas_bytes_despite_a_b_a_replacement', 'audio_import_tests::original_mix_rejects_store_generation_replacement_at_the_same_revision', 'audio::original_mix_source_tests::imported_original_wav_and_its_pcm_keep_distinct_checked_sha256'],
        },
        'fixture_sha256': {
            'crates/storage/src/lib.rs': '247fd57f4a62bbc36f1502453775da058e28031e0840a6f55be455ddbfde38c8',
            'crates/storage/src/file_import.rs': '7f080115695b37d48f7bdf3f09addc20ac8325c859541c259a90a5ba4ef82ffb',
            'crates/service/src/lib.rs': 'c4c29fdcad5e29e76e7abe4a1d81aa3778b84108ed5064f74edd5d4c85ea3856',
            'crates/service/src/audio.rs': '8bdc883a61bb0a2c313c9bed13f8f41879a707e442f859add967cf22d9d9b69a',
        },
    },
    'repair': {
        'sha': '307c7e762cb91e6383922dc2d6cdeeef8d3d4ed7',
        'tree': '62468b6b1d654abd33e60d284b35d38528dea1cd',
        'targets': {
            'jobs': [
                'cancel_request_survives_failed_status_read_and_reopen',
                'cancel_request_survives_unknown_status_and_queued_observation',
                'terminal_observations_win_after_pending_cancel_request_and_unknown',
                'running_observation_recovers_unknown_without_inventing_cancellation',
            ],
            'svg-references': [
                'svg_style_attribute_decodes_xml_entities_before_reference_admission',
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
                'ordinary_xml_like_asset_remains_compatible_with_conservative_sniffing',
            ],
            'svg-budget': [
                'declared_svg_mime_budget_rejects_before_staging',
                'declared_svg_extension_budget_rejects_before_staging',
                'small_valid_svg_still_captures_and_imports_the_exact_original',
                'svg_budget_does_not_become_an_opaque_asset_size_limit',
            ],
            'current-storage-unit': [
                'file_import::source_open_tests::regular_source_capture_preserves_exact_bytes_and_sql',
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
                'file_import::staging_tests::dropping_duplicate_stages_and_rejecting_stale_import_preserve_blob',
                'file_import::source_budget_tests::bounded_replay_hash_accepts_exact_budget_and_rejects_initial_excess',
                'file_import::source_budget_tests::bounded_replay_hash_rejects_growth_after_first_read',
                'file_import::source_budget_tests::bounded_replay_hash_includes_growth_to_exact_budget',
                'file_import::source_budget_tests::staging_rejects_growth_without_touching_sql_cas_or_another_capture',
                'file_import::source_budget_tests::staging_captures_growth_to_exact_budget_then_removes_its_private_file',
                'apply_receipt_binding::tests::every_legacy_version_rejects_reserved_hidden_and_case_variant_columns',
                'apply_receipt_binding::tests::observation_keeps_one_snapshot_during_real_peer_commit',
                'apply_receipt_binding::tests::observation_succeeds_on_query_only_connection_without_sql_changes',
                'apply_receipt_binding::tests::resealed_noncanonical_decimal_is_still_invalid',
                'apply_receipt_binding::tests::schema_constraint_tokens_cannot_be_supplied_by_literals_or_comments',
                'apply_receipt_binding::tests::schema_inspection_bounds_ddl_tokens_and_nesting',
                'apply_receipt_binding::tests::sqlite_statement_failure_is_not_content_failure_or_absence',
                'apply_crash_tests::real_apply_exit_after_project_update_rolls_back_document_and_eviction_outbox',
                'apply_crash_tests::real_apply_exit_after_event_insert_rolls_back_the_full_mutation',
                'apply_crash_tests::real_apply_exit_before_commit_rolls_back_receipt_and_journal',
                'apply_crash_tests::real_apply_exit_after_commit_recovers_outbox_and_replays_original_result',
                'apply_crash_tests::binding_migration_ddl_rolls_back_after_abrupt_exit',
                'verified_blob_capture_budget_tests::verified_blob_capture_rejects_growth_after_metadata_at_limit_plus_one',
                'verified_blob_capture_budget_tests::verified_blob_capture_keeps_empty_exact_and_oversize_controls',
            ],
            'import-service': [
                'asset_import_replays_identical_bytes_and_metadata_after_reopen',
                'voice_import_replays_identical_bytes_and_metadata_after_reopen',
                'invalid_import_authority_takes_precedence_over_missing_source_io',
                'changed_file_bytes_cannot_reuse_a_committed_asset_import_key',
                'committed_import_keeps_real_cas_content_when_the_original_file_disappears',
                'historical_asset_and_voice_recovery_preserves_head_without_source_or_cas',
                'normal_asset_and_voice_replay_does_not_rehydrate_missing_cas',
                'concurrent_connections_import_identical_bytes_with_one_event_and_one_outcome',
                'rejected_active_svg_and_undecodable_voice_leave_no_published_blob_or_staging',
            ],
            'receipt': [
                'known_request_rejects_a_valid_result_from_another_project',
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
                'known_request_replays_its_original_snapshot_after_later_edit_and_reopen',
            ],
            'budget-service': [
                'source_byte_boundary_applies_to_asset_voice_and_both_host_constructors',
                'known_source_hash_respects_reduced_bytes_but_source_free_recovery_survives',
                'known_voice_replay_does_not_remeasure_or_rehydrate_under_tiny_audio_budgets',
                'source_free_recovery_ignores_all_import_limits_after_reopen',
                'audio_duration_accepts_one_second_and_rejects_one_additional_frame',
                'stereo_sample_budget_counts_both_channels_without_duration_preemption',
                'packet_byte_budget_rejects_valid_pcm_before_publication',
                'declared_packet_sample_budget_rejects_valid_pcm_before_publication',
                'demux_packet_budget_accepts_one_packet_and_rejects_a_three_packet_wav',
                'audio_read_budget_rejects_valid_wav_during_probe_without_publication',
                'real_multitrack_control_has_unknown_duration_and_exact_selected_pcm',
                'demux_budget_counts_real_packets_from_unselected_tracks',
                'actual_decode_sample_budget_applies_without_declared_frame_count',
                'actual_decode_packet_budget_applies_without_declared_packet_frames',
                'actual_duration_budget_rejects_one_extra_frame_without_declared_duration',
                'declared_svg_budget_also_respects_a_lower_host_byte_limit',
            ],
            'current-service-unit': [
                'import_limits::tests::every_limit_rejects_zero_and_an_increase',
                'import_limits::tests::reduced_limits_cannot_be_raised_again',
                'import_limits::tests::reducing_a_copy_preserves_the_original_and_other_limits',
                'audio::measurement_budget_tests::bounded_file_accepts_exact_budget_and_true_eof',
                'audio::measurement_budget_tests::bounded_file_seek_charges_rereads_and_keeps_exhaustion',
                'audio::measurement_budget_tests::bounded_file_consumes_only_one_excess_probe_byte',
                'audio_import_tests::original_mix_decodes_captured_cas_bytes_despite_a_b_a_replacement',
                'audio_import_tests::original_mix_rejects_store_generation_replacement_at_the_same_revision',
                'audio::original_mix_source_tests::imported_original_wav_and_its_pcm_keep_distinct_checked_sha256',
                'audio::original_mix_source_tests::stale_sha_or_unsupported_rate_and_channels_never_pass_pcm_decoder',
                'audio::original_mix_source_tests::symlink_to_owner_audio_is_not_a_source_grant',
            ],
            'binding4': [
                'complete_observation_wire_fits_budget_for_maximum_request_keys',
                'exact_revisions_survive_above_javascript_integer_range_and_reject_sqlite_overflow',
                'historical_branch_switch_is_not_reconstructed_from_the_current_head',
                'imported_v1_observation_and_recovery_need_no_source_or_cas_after_reopen',
                'imported_v1_result_and_event_kind_share_the_canonical_verifier',
                'invalid_sqlite_receipt_types_and_utf8_are_content_failures',
                'legacy_representation_remains_unverified_without_backfill_or_reapplication',
                'lost_ack_after_later_replay_observes_the_original_commit_after_reopen',
                'malformed_or_oversized_binding_never_downgrades_or_repairs',
                'observer_distinguishes_mismatch_absence_and_authority_without_writes',
                'typed_creation_rejects_generic_apply_binding_markers_without_repair',
                'unsupported_contract_is_explicit_and_never_becomes_absence_or_legacy',
                'versioned_binding_rejects_same_change_from_a_later_execution',
            ],
            'binding4-migration': [
                'future_binding_schema_is_never_adopted_or_downgraded',
                'incomplete_or_misdeclared_schema4_columns_fail_before_wal',
                'noncanonical_marker_constraints_are_rejected_without_probing_writes',
                'real_quoted_constraint_and_harmless_extensions_remain_admissible',
                'reserved_legacy_columns_are_rejected_before_wal_or_schema_changes',
                'schema3_import_recovery_remains_source_free_and_unverified_after_migration',
                'schema3_upgrade_preserves_both_legacy_families_and_extensions',
            ],
            'project-media-independence': ['all_large_project_pages_reopen_without_selected_imported_blobs'],
            'original-mix-source': [
                'audition_compatibility_keeps_fifteen_seconds_and_rejects_one_more_frame',
                'explicit_full_cut_decodes_sixteen_seconds_with_the_nonzero_tail_intact',
                'full_cut_accepts_exactly_one_hundred_eighty_seconds_and_rejects_one_more_frame',
                'audition_enforces_its_eight_mebibyte_source_cap_independently_of_duration',
                'full_cut_enforces_its_sixty_four_mebibyte_source_cap_independently_of_duration',
                'complete_stamp_rejects_foreign_resource_generation_and_stale_revision_without_writes',
                'changed_cas_bytes_do_not_keep_the_original_digest_authority',
                'redirected_cas_leaf_is_rejected_without_changing_its_target',
            ],
        },
        'fixture_sha256': {
            'crates/service/src/audio.rs': '9450675ac6bd50c2a0a37e6cbc8243f89607349e9a14d2b822495ebf24cffcef',
            'crates/service/src/import_limits.rs': '21a58b974f1c440350581a2c7ca5d54b4907401fd9a33a8a4cc48389e2310d1e',
            'crates/service/src/lib.rs': '4350753e44bc20c7a996f4e10e675a52fa3bb54a66107b20da90dc5ddf98b3c3',
            'crates/service/tests/file_import_idempotency.rs': '253e5d359f823d2f375dd8acf21c0e7a4f027524302ea98befa8756e2615fc29',
            'crates/service/tests/file_import_limits.rs': '61d6221c8f9149d2d33e2671fc2bfa1d65eeb2975f16fe3e7f37884a5ba27b40',
            'crates/service/tests/file_import_svg_budget.rs': '1b58e2becff6866e9586f6da334439a4f40506bcba2c9f80c3025c3443db569b',
            'crates/service/tests/file_import_svg_references.rs': '3cac623ba4cadb3900b74c5416c56ac30140cb07170ae08d3e59368575bca0fd',
            'crates/service/tests/original_mix_source_budget.rs': '682435fdd9795d46a69c70cc5706415b8af34aaab836c3cc02e4b58bea78836f',
            'crates/service/tests/production_jobs_cancellation_recovery.rs': 'e040e61152bf60a66ce6c6210b1fc9679256f006d38fd9860488784095ac8dfa',
            'crates/service/tests/project_projection_media_independence.rs': 'c0707ff1ca7ce59e5eb6d54a7b57466905c176f40ff9790d4c5e17de012d27b9',
            'crates/storage/src/apply_crash_tests.rs': '9febb826879b1180ddd8afc84256a7fd6d7b76c3c5769c726ad612c2adc3e974',
            'crates/storage/src/apply_receipt_binding.rs': '9922802f0deaa32c9e2b2dee7d3d0d7fbcfdfa58b2047b506f0432fd9db4d4af',
            'crates/storage/src/file_import.rs': '037564e6894a59075f295739ae93967825c6a96429b3f113b53ed87b6d4c9039',
            'crates/storage/src/lib.rs': 'db8976efd0cccbb2a3c36f0a4b21dd78738e76166297cd21cfd33f7f21bcd8a4',
            'crates/storage/tests/apply_binding_migration.rs': '25500c33cbb4ad19079ad8e5bfa396bcf21afb7fd9595818493c082d11313ace',
            'crates/storage/tests/apply_receipt_binding.rs': 'b74d2e616e880da1b9be515d3dcb5d92332af557f291b88bc9b34245b6c45274',
            'crates/storage/tests/apply_receipt_integrity.rs': 'cc995895ccfae9c8f584e15555e38aeccdca684f3afcc5b64d72b69619428883',
        },
    },
}

CURRENT_INVENTORY_DIGESTS = {
    'baseline': '72854a4100177a57ce59e841bf2148c171ccc62d14aa81d25b707cbcf2a505cd',
    'repair': 'cfbc29b4bf64ef9c63a818e44e8e208f0dbb3c011cafcb5bef60e2d3766f3d87',
}

CURRENT_TARGET_COUNTS = {
    'baseline': {
        'current-storage-unit': 2,
        'current-service-unit': 3,
    },
    'repair': {
        'jobs': 4,
        'svg-references': 13,
        'svg-budget': 4,
        'current-storage-unit': 32,
        'import-service': 9,
        'receipt': 12,
        'budget-service': 16,
        'current-service-unit': 11,
        'binding4': 13,
        'binding4-migration': 7,
        'project-media-independence': 1,
        'original-mix-source': 8,
    },
}

CURRENT_REQUIRED_FIXTURES = {
    'baseline': (
        'crates/storage/src/lib.rs',
        'crates/storage/src/file_import.rs',
        'crates/service/src/lib.rs',
        'crates/service/src/audio.rs',
    ),
    'repair': (
        'crates/service/src/audio.rs',
        'crates/service/src/import_limits.rs',
        'crates/service/src/lib.rs',
        'crates/service/tests/file_import_idempotency.rs',
        'crates/service/tests/file_import_limits.rs',
        'crates/service/tests/file_import_svg_budget.rs',
        'crates/service/tests/file_import_svg_references.rs',
        'crates/service/tests/original_mix_source_budget.rs',
        'crates/service/tests/production_jobs_cancellation_recovery.rs',
        'crates/service/tests/project_projection_media_independence.rs',
        'crates/storage/src/apply_crash_tests.rs',
        'crates/storage/src/apply_receipt_binding.rs',
        'crates/storage/src/file_import.rs',
        'crates/storage/src/lib.rs',
        'crates/storage/tests/apply_binding_migration.rs',
        'crates/storage/tests/apply_receipt_binding.rs',
        'crates/storage/tests/apply_receipt_integrity.rs',
    ),
}

CURRENT_FAILURE_MARKERS = {
    'verified_blob_capture_budget_tests::verified_blob_capture_rejects_growth_after_metadata_at_limit_plus_one': ('crates/storage/src/lib.rs', 'verified blob capture accepted eight bytes after admitting a four-byte source with limit four'),
    'audio_import_tests::original_mix_decodes_captured_cas_bytes_despite_a_b_a_replacement': ('crates/service/src/lib.rs', 'captured source binding mismatch: original=A(+8192), decoded=B(-8192), final CAS=A'),
    'audio_import_tests::original_mix_rejects_store_generation_replacement_at_the_same_revision': ('crates/service/src/lib.rs', 'original mix accepted a replacement project generation at the same revision and digest'),
}

CURRENT_DEPENDENCY_HASHES = {
    'tooling/ci_storage_apply_recovery.py': '463cf14f9e3d907c8782bc7505574b214f646028baddb38efccba67d62984653',
    'tooling/tests/test_ci_storage_apply_recovery.py': '69e05027099b53306b1645a3d6b2ce1706fc14c4e5ce64289cd8d04a7168f374',
    '.github/workflows/storage-apply-process-recovery.yml': '9fe449b67bbee74d945600cec02bf9243e37880b81f1290879bc46baa119c8fc',
    'docs/reliability/storage-apply-process-recovery.md': 'c1e4ec2eac4f25eb26698d0ef6501a197da3e86f03aaf769b02c145b9b09fa0c',
}

CURRENT_PHASE_CASES = (
    'apply_crash_tests::real_apply_exit_after_project_update_rolls_back_document_and_eviction_outbox',
    'apply_crash_tests::real_apply_exit_after_event_insert_rolls_back_the_full_mutation',
    'apply_crash_tests::real_apply_exit_before_commit_rolls_back_receipt_and_journal',
    'apply_crash_tests::real_apply_exit_after_commit_recovers_outbox_and_replays_original_result',
)

CURRENT_DDL_CASE = 'apply_crash_tests::binding_migration_ddl_rolls_back_after_abrupt_exit'

TARGETS.update(CURRENT_TARGETS)

# The exact instrumented source and tree are frozen for this diagnostic.
# This one-source diagnostic grants no causal or Foundation135 credit.
PROJECTION_DIAGNOSTIC_SOURCE = {
    "sha": "fb5dd1840c7f6b97600430311a33a051744f826b",
    "tree": "d7ac466fb88359e71cb795a1519cc189c938f60a",
    "fixture_sha256": {
        "crates/service/tests/project_projection_media_independence.rs":
            "c1d9b2ca22a7770390bff776b6bf6aca8f2c154341fe039fb51882569a496b50",
    },
    "targets": {
        "project-media-independence": [
            "all_large_project_pages_reopen_without_selected_imported_blobs",
        ],
    },
}
PROJECTION_DIAGNOSTIC = {
    "id": "foundation-projection-diagnostic",
    "failures": {},
    "repair": PROJECTION_DIAGNOSTIC_SOURCE,
}

# A separately frozen composition keeps historical source identities intact.
COMPOSED_SOURCE_PINS = {'sha': 'be7d0377425f9eec201852450b5a7c53e4a1f403',
 'tree': '5f9e92f6640a39c7adddb249501399cc97e958f8',
 'fixture_sha256': {'crates/service/src/audio.rs': '9450675ac6bd50c2a0a37e6cbc8243f89607349e9a14d2b822495ebf24cffcef',
                    'crates/service/src/import_limits.rs': '21a58b974f1c440350581a2c7ca5d54b4907401fd9a33a8a4cc48389e2310d1e',
                    'crates/service/src/lib.rs': '4350753e44bc20c7a996f4e10e675a52fa3bb54a66107b20da90dc5ddf98b3c3',
                    'crates/service/tests/file_import_idempotency.rs': '253e5d359f823d2f375dd8acf21c0e7a4f027524302ea98befa8756e2615fc29',
                    'crates/service/tests/file_import_limits.rs': '61d6221c8f9149d2d33e2671fc2bfa1d65eeb2975f16fe3e7f37884a5ba27b40',
                    'crates/service/tests/file_import_svg_budget.rs': '1b58e2becff6866e9586f6da334439a4f40506bcba2c9f80c3025c3443db569b',
                    'crates/service/tests/file_import_svg_references.rs': '3cac623ba4cadb3900b74c5416c56ac30140cb07170ae08d3e59368575bca0fd',
                    'crates/service/tests/original_mix_source_budget.rs': '682435fdd9795d46a69c70cc5706415b8af34aaab836c3cc02e4b58bea78836f',
                    'crates/service/tests/production_jobs_cancellation_recovery.rs': 'e040e61152bf60a66ce6c6210b1fc9679256f006d38fd9860488784095ac8dfa',
                    'crates/service/tests/project_projection_media_independence.rs': 'c1d9b2ca22a7770390bff776b6bf6aca8f2c154341fe039fb51882569a496b50',
                    'crates/storage/src/apply_crash_tests.rs': '9febb826879b1180ddd8afc84256a7fd6d7b76c3c5769c726ad612c2adc3e974',
                    'crates/storage/src/apply_receipt_binding.rs': '9922802f0deaa32c9e2b2dee7d3d0d7fbcfdfa58b2047b506f0432fd9db4d4af',
                    'crates/storage/src/file_import.rs': '037564e6894a59075f295739ae93967825c6a96429b3f113b53ed87b6d4c9039',
                    'crates/storage/src/lib.rs': 'db8976efd0cccbb2a3c36f0a4b21dd78738e76166297cd21cfd33f7f21bcd8a4',
                    'crates/storage/tests/apply_binding_migration.rs': '25500c33cbb4ad19079ad8e5bfa396bcf21afb7fd9595818493c082d11313ace',
                    'crates/storage/tests/apply_receipt_binding.rs': 'b74d2e616e880da1b9be515d3dcb5d92332af557f291b88bc9b34245b6c45274',
                    'crates/storage/tests/apply_receipt_integrity.rs': 'cc995895ccfae9c8f584e15555e38aeccdca684f3afcc5b64d72b69619428883'}}
COMPOSED_TARGET_ORDER = ('jobs',
 'svg-references',
 'svg-budget',
 'current-storage-unit',
 'import-service',
 'receipt',
 'budget-service',
 'current-service-unit',
 'binding4',
 'binding4-migration',
 'original-mix-source',
 'project-media-independence')
COMPOSED_COMPOSITION_PAIR = copy.deepcopy(CURRENT_COMPOSITION_PAIR)
COMPOSED_COMPOSITION_PAIR["id"] = "foundation-composed-current"
COMPOSED_COMPOSITION_PAIR["repair"].update(copy.deepcopy(COMPOSED_SOURCE_PINS))
COMPOSED_COMPOSITION_PAIR["repair"]["targets"] = {
    name: COMPOSED_COMPOSITION_PAIR["repair"]["targets"][name]
    for name in COMPOSED_TARGET_ORDER
}
CURRENT_PHASE_SELECTIONS = ("foundation-current", "foundation-composed-current")

# Gate dependencies are independent of the frozen product source.
COMPOSED_DEPENDENCY_HASHES = {'tooling/ci_storage_apply_recovery.py': 'f89f31122b6f02d0032037706f3feb175bf0b18c2157ac808f040a7eb626c767',
 'tooling/tests/test_ci_storage_apply_recovery.py': 'c9a73ece260c9f92fc3bfd7eedaceb2c626a70bfe197780fd637dfb775ffbc61',
 '.github/workflows/storage-apply-process-recovery.yml': 'fab7d4e12b1d56e732b03d90ce840ee9f436968b986c335a350a86ab3734e5ce',
 'docs/reliability/storage-apply-process-recovery.md': 'c1e4ec2eac4f25eb26698d0ef6501a197da3e86f03aaf769b02c145b9b09fa0c'}

SELECTIONS = (
    "foundation-causal", "foundation-import-budget", "foundation-current",
    "foundation-projection-diagnostic", "foundation-composed-current",
)
PASS_STATUSES = {
    "foundation-causal": "PASS_FOUR_EXACT_CAUSAL_PAIRS",
    "foundation-import-budget": "PASS_IMPORT_BUDGET_CAUSAL_PAIR",
    "foundation-current": "PASS_CURRENT_COMPOSITION_AND_ORIGINAL_MIX_CAUSAL_PAIR",
    "foundation-projection-diagnostic": "PASS_EXACT_PROJECTION_DIAGNOSTIC",
    "foundation-composed-current": "PASS_FROZEN_COMPOSED_CURRENT_AND_ORIGINAL_MIX_CAUSAL_PAIR",
}

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
    """An uncertain process or receipt is not permission to replay or continue."""

    def __init__(self, message, command=None):
        super().__init__(message)
        self.command = command


def require(ok, message):
    custody.require(ok, message)


def validate_pairs(pairs, identities, counts, red_counts):
    require([p["id"] for p in pairs] == identities,
            "the reviewed selection requires each frozen pair exactly once")
    for pair, expected_counts, expected_red in zip(pairs, counts, red_counts):
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

def validate_catalogue(pairs=None):
    pairs = PAIRS if pairs is None else pairs
    return validate_pairs(pairs, ["jobs", "svg", "retained-import", "bounded-receipt"],
                          ((4, 4), (13, 17), (4, 22), (12, 12)), (2, 9, 3, 11))


def validate_import_budget(pair=None):
    pair = IMPORT_BUDGET_PAIR if pair is None else pair
    validate_pairs([pair], ["import-resource-budget"], ((21, 27),), (15,))
    expected_targets = (("budget-service", "budget-storage"),
                        ("budget-service", "budget-storage", "budget-service-unit"))
    for role, targets, counts in zip(ROLES, expected_targets, ((16, 5), (16, 5, 6))):
        actual = pair[role]["targets"]
        require(tuple(actual) == targets
                and tuple(map(len, actual.values())) == counts,
                "import budget target selection changed")
    require(all(pair["baseline"]["targets"][target] == pair["repair"]["targets"][target]
                for target in expected_targets[0]),
            "import budget causal cases differ across roles")
    require(all("got Ok(" in failure["markers"] for failure in pair["failures"].values()),
            "import budget RED must identify an admitted over-budget value")
    return [pair]


def current_inventory_digest(targets):
    rows = sorted((TARGETS[key]["package"], TARGETS[key]["kind"],
                   TARGETS[key]["name"], name)
                  for key, names in targets.items() for name in names)
    require(len(rows) == len(set(rows)), "duplicate current source contract")
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def validate_current_composition(pair=None):
    pair = CURRENT_COMPOSITION_PAIR if pair is None else pair
    for role in ROLES:
        source = pair[role]
        require(all(isinstance(source.get(key), str) and SHA.fullmatch(source[key])
                    for key in ("sha", "tree")),
                "current composition source/tree pins are pending or invalid")
        require(set(source["fixture_sha256"]) == set(CURRENT_REQUIRED_FIXTURES[role])
                and all(isinstance(value, str) and DIGEST.fullmatch(value)
                        for value in source["fixture_sha256"].values()),
                "current composition fixture hashes are pending or invalid")
        require({key: len(names) for key, names in source["targets"].items()}
                == CURRENT_TARGET_COUNTS[role], "current target selection changed")
        require(current_inventory_digest(source["targets"]) == CURRENT_INVENTORY_DIGESTS[role],
                "current contract roster changed or moved to another target")
    require(pair["baseline"]["sha"] != pair["repair"]["sha"],
            "causal variants require different frozen sources")
    require(set(pair["failures"]) == set(CURRENT_FAILURE_MARKERS),
            "current causal failure inventory changed")
    for name, (source, marker) in CURRENT_FAILURE_MARKERS.items():
        failure = pair["failures"][name]
        require(type(failure.get("line")) is int and failure["line"] > 0
                and failure.get("source") == source and failure.get("markers") == [marker],
                "current causal source site is pending or differs")
    return validate_pairs([pair], ["foundation-current"], ((5, 130),), (3,))



def validate_composed_current(pair=None):
    pair = COMPOSED_COMPOSITION_PAIR if pair is None else pair
    require(isinstance(pair, dict)
            and set(pair) == {"id", "baseline", "repair", "failures"}
            and pair["id"] == "foundation-composed-current",
            "composed selection identity differs")
    require(pair["baseline"] == CURRENT_COMPOSITION_PAIR["baseline"]
            and pair["failures"] == CURRENT_COMPOSITION_PAIR["failures"],
            "composed baseline source or causal assertions differ")
    source = pair["repair"]
    require(isinstance(source, dict)
            and set(source) == {"sha", "tree", "fixture_sha256", "targets"}
            and all(source.get(name) == expected
                    for name, expected in COMPOSED_SOURCE_PINS.items()),
            "composed source/tree/fixture pins are pending or differ")
    require(isinstance(source["targets"], dict)
            and tuple(source["targets"]) == COMPOSED_TARGET_ORDER,
            "composed target order differs")
    # The canonical roster and causal checks apply to both frozen compositions.
    validate_current_composition({**pair, "id": "foundation-current"})
    return [pair]

def selected_gate_files(selection):
    require(selection in SELECTIONS, "unknown gate file selection")
    if selection == "foundation-composed-current":
        return tuple(dict.fromkeys((*GATE_FILES, *COMPOSED_DEPENDENCY_HASHES)))
    if selection == "foundation-current":
        return tuple(dict.fromkeys((*GATE_FILES, *CURRENT_DEPENDENCY_HASHES)))
    return GATE_FILES


def current_dependency_preflight(gate, snapshot, selection="foundation-current"):
    require(selection in CURRENT_PHASE_SELECTIONS, "unknown current dependency profile")
    dependencies = (COMPOSED_DEPENDENCY_HASHES
                    if selection == "foundation-composed-current" else CURRENT_DEPENDENCY_HASHES)
    require(Path(custody.__file__).resolve(strict=True)
            == (gate / "tooling/ci_storage_apply_recovery.py").resolve(strict=True),
            "loaded custody implementation differs from the retained gate")
    require(all(snapshot["files"].get(name, {}).get("sha256") == digest
                for name, digest in dependencies.items()),
            "current phase custody dependency differs from the reviewed canonical source")
    result = {"cohort": "binding4", "files": dict(dependencies),
              "historical_default": "historical5fa",
              "independent_observer_pid_or_snapshot_digest_binding": False}
    if selection == "foundation-composed-current":
        source, tree, _ = custody.binding4_product_profile("current-process")
        require((source, tree) == (COMPOSED_SOURCE_PINS["sha"], COMPOSED_SOURCE_PINS["tree"]),
                "composed custody and product source identities differ")
        result.update(dependency_profile=selection, product_source=source, product_tree=tree)
    return result


def current_phase_environment(pair, role, name, environment, output, phase_root):
    env = dict(environment)
    if pair["id"] not in CURRENT_PHASE_SELECTIONS or role != "repair" or name not in CURRENT_PHASE_CASES:
        return env
    require(phase_root is not None and phase_root.resolve(strict=True) == phase_root
            and phase_root.is_relative_to(output) and phase_root != output,
            "phase output must be a new canonical directory under this attempt")
    require(not (phase_root / custody.CHECKPOINTS[name]).exists(),
            "phase checkpoint already exists; no reuse")
    env[custody.EVIDENCE_ROOT] = str(phase_root)
    return env


def current_phase_scope(result):
    rows = result.get("results", [])
    phases = [row for row in rows if row.get("name") in CURRENT_PHASE_CASES]
    require(len(phases) == 4 and {row["name"] for row in phases} == set(CURRENT_PHASE_CASES),
            "current phase parents are missing or duplicated")
    requests = set()
    for row in phases:
        phase = row.get("phase_custody", {})
        require(row.get("status") == "PASS"
                and phase.get("status") == "COMPLETE_OBSERVED_PHASE_CUSTODY"
                and phase.get("test_name") == row["name"]
                and phase.get("checkpoint") == custody.CHECKPOINTS[row["name"]]
                and phase.get("phases") == list(custody.PHASES)
                and len(phase.get("processes", [])) == 3,
                "current parent PASS lacks its binding4 phase originals")
        request = phase.get("request_id")
        require(isinstance(request, str) and request and request not in requests,
                "current phase request is absent or reused")
        requests.add(request)
    ddl = [row for row in rows if row.get("name") == CURRENT_DDL_CASE]
    require(len(ddl) == 1 and ddl[0].get("status") == "PASS"
            and ddl[0].get("retained_child_originals") == 0
            and ddl[0].get("retained_phase_jsons") == 0
            and all("phase_custody" not in row for row in rows
                    if row.get("name") not in CURRENT_PHASE_CASES),
            "DDL or unrelated parent must not claim retained phase/child originals")
    return {"phase_cases": 4, "phase_jsons_retained": 20,
            "child_process_originals_retained": 12, "ddl_parent_cases": 1,
            "ddl_retained_child_originals": 0, "ddl_retained_phase_jsons": 0,
            "raw_database_or_full_cas_retained": False,
            "independent_observer_pid_or_snapshot_digest_binding": False,
            "physical_power_loss": "NOT_RUN"}


def verify_current_phase_originals(output, result):
    scope = current_phase_scope(result)
    relative = result.get("phase_root")
    require(isinstance(relative, str) and not Path(relative).is_absolute()
            and ".." not in Path(relative).parts, "unsafe current phase root")
    root = output / relative
    require(root.resolve(strict=True) == root and root.is_relative_to(output)
            and root != output, "current phase root is not canonical")
    require({path.name for path in root.iterdir()} == set(custody.CHECKPOINTS.values()),
            "current phase directory inventory differs")
    verified = {}
    for row in result["results"]:
        if row["name"] not in CURRENT_PHASE_CASES:
            continue
        observed = custody.admit_phase_custody(root, row["name"], "binding4")
        require(observed == row["phase_custody"], "current phase originals changed")
        for item in [observed["custody_manifest"], *observed["files"].values()]:
            verified[(Path(relative) / item["path"]).as_posix()] = item
    require(scope == result.get("phase_scope"), "current phase scope changed")
    return verified


def validate_projection_diagnostic():
    source = PROJECTION_DIAGNOSTIC_SOURCE
    require(set(PROJECTION_DIAGNOSTIC) == {"id", "failures", "repair"}
            and PROJECTION_DIAGNOSTIC["id"] == "foundation-projection-diagnostic"
            and PROJECTION_DIAGNOSTIC["repair"] == source,
            "diagnostic source is detached from its one-role selection")
    require(set(source) == {"sha", "tree", "fixture_sha256", "targets"},
            "diagnostic source metadata differs")
    require(SHA.fullmatch(source["sha"] or "") and SHA.fullmatch(source["tree"] or ""),
            "diagnostic source and tree are not frozen; refuse execution")
    require(source["targets"] == {
        "project-media-independence": [
            "all_large_project_pages_reopen_without_selected_imported_blobs",
        ],
    }, "diagnostic requires exactly the complete project-media-independence case")
    require(source["fixture_sha256"] == {
        "crates/service/tests/project_projection_media_independence.rs":
            "c1d9b2ca22a7770390bff776b6bf6aca8f2c154341fe039fb51882569a496b50",
    }, "diagnostic fixture must match the reviewed postimage")
    require(PROJECTION_DIAGNOSTIC["failures"] == {}, "diagnostic has no expected causal failures")
    return [PROJECTION_DIAGNOSTIC]


def selected_pairs(selection):
    require(selection in SELECTIONS, "unknown or unselected verification scope")
    if selection == "foundation-projection-diagnostic":
        return validate_projection_diagnostic()
    if selection == "foundation-composed-current":
        return validate_composed_current()
    if selection == "foundation-current":
        return validate_current_composition()
    return validate_catalogue() if selection == "foundation-causal" else validate_import_budget()


def execution_context(environment, runtime_platform=None, selection="foundation-causal"):
    require(selection in SELECTIONS, "unknown or unselected verification scope")
    runtime_platform = sys.platform if runtime_platform is None else runtime_platform
    require(runtime_platform == "linux" and environment.get("RUNNER_OS") == "Linux"
            and environment.get("RUNNER_ARCH") == "X64", "requires the selected Linux x64 runner")
    require(environment.get("GITHUB_ACTIONS") == "true"
            and environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and environment.get("FOUNDATION_CAUSAL_SELECTION") == selection,
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
    if selection == "foundation-composed-current":
        require(environment["GITHUB_RUN_ATTEMPT"] == "1",
                "composed execution requires the first run attempt; no replay")
        require(environment["GITHUB_SHA"] != COMPOSED_SOURCE_PINS["sha"],
                "composed product and gate require separate frozen sources")
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
    try:
        row = custody.execute(argv, cwd, environment, output, label, timeout, context)
    except custody.UnknownObservation as error:
        raise UnknownExecution("unretained observation; stop without replay: " + label, error.command) from error
    if custody.execution_unknown(row):
        raise UnknownExecution("uncertain command; stop all remaining cases without replay: " + label, row)
    if observations is not None:
        root, items = observations
        try:
            original = custody.fingerprint(output / (label + ".command.json"), root)
            items.append({"folder": output.relative_to(root).as_posix(), "command": row,
                          "receipt": original})
        except BaseException as error:
            raise UnknownExecution("command custody was not retained; stop: " + label, row) from error
    require(row["observation"] == "DIRECT_PROCESS_EXIT_OBSERVED",
            "command did not start or exit observably: " + label)
    require(row["log_custody"] == "VERIFIED_SNAPSHOT", "original command log custody failed")
    return row


def original_stream(output, command, stream):
    """Retain uncertainty if a sealed original can no longer be read."""
    if custody.execution_unknown(command):
        raise UnknownExecution("prior unknown command forbids original readback", command)
    try:
        return custody.read_original(output, command, stream)
    except BaseException as error:
        errors = list(command.get("secondary_observation_errors", []))
        errors.append(type(error).__name__ + ": " + str(error))
        unsealed = {**command, "original_command": command,
                    "observation": "UNKNOWN_CUSTODY_NO_RETRY",
                    "uncertainty_reason": "ORIGINAL_STREAM_READBACK_FAILED",
                    "diagnostic_custody": "UNSEALED_COMMAND_OR_STREAM_READBACK",
                    "secondary_observation_errors": errors}
        raise UnknownExecution("original stream readback failed; stop without replay: " + stream,
                               unsealed) from error


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
    if role == "repair":
        prefixes = spec.get("repair_prefixes", prefixes)
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
    if pair["id"] in CURRENT_PHASE_SELECTIONS and role == "repair":
        require(current_phase_scope(result) == result.get("phase_scope"),
                "current phase originals are incomplete")
    status = ("PASS_EXACT_DIAGNOSTIC_ROLE"
              if pair["id"] == "foundation-projection-diagnostic" else "PASS_EXACT_CAUSAL_ROLE")
    return {"status": status, "cases": len(expected),
            "expected_assertion_failures": len(pair["failures"]) if role == "baseline" else 0,
            "passing_cases": len(expected) - (len(pair["failures"]) if role == "baseline" else 0)}


def tools_identity(gate, output, environment, context, observations=None):
    tools = {}
    for name in ("rustc", "cargo", "clippy-driver"):
        found = command(["rustup", "which", "--toolchain", "1.98.1", name],
                        gate, environment, output, name + "-path", 45, context, observations)
        require(found["exit_code"] == 0, "selected toolchain executable unavailable")
        path = Path(original_stream(output, found, "stdout").decode().strip())
        require(path.is_absolute(), "tool executable path is not absolute")
        path = path.resolve(strict=True)
        identity = custody.fingerprint(path, path.parent)
        version = command([str(path), "-Vv" if name == "rustc" else "-V"],
                          gate, environment, output, name + "-version", 45, context, observations)
        require(version["exit_code"] == 0, "tool version query failed")
        raw = original_stream(output, version, "stdout").decode()
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
        phase_root = None
        if pair["id"] in CURRENT_PHASE_SELECTIONS and role == "repair":
            phase_root = folder / "phases"
            phase_root.mkdir(exist_ok=False)
            result["phase_root"] = phase_root.relative_to(output).as_posix()
        for target_name, names in source["targets"].items():
            spec = TARGETS[target_name]
            result["phase"] = "compile:" + target_name
            build = command(compile_argv(toolchain["cargo"]["absolute_path"], spec),
                            repo, environment, folder, target_name + "-build", BUILD_TIMEOUT, frame, observations)
            binary = select_binary(original_stream(folder, build, "stdout"),
                                   build["exit_code"], repo, target, spec)
            retained = retain_file(Path(binary["absolute_path"]), target, output, "binaries")
            result.setdefault("binaries", {})[target_name] = {**binary, "retained": retained,
                                                           "build": build}
            listing = command([binary["absolute_path"], "--list", "--format", "terse"],
                              repo, environment, folder, target_name + "-list", 45, frame, observations)
            roster = selected_roster(original_stream(folder, listing, "stdout"),
                                     listing["exit_code"], spec, names, role)
            result.setdefault("rosters", {})[target_name] = roster
            result["phase"] = "cases:" + target_name
            for index, name in enumerate(names, 1):
                case_temp = temporary / pair["id"] / role / target_name / str(index)
                case_temp.mkdir(parents=True, exist_ok=False)
                case_env = dict(environment, TMP=str(case_temp), TEMP=str(case_temp),
                                TMPDIR=str(case_temp))
                case_env = current_phase_environment(
                    pair, role, name, case_env, output, phase_root)
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
                        original_stream(folder, executed, "stdout") + b"\n"
                        + original_stream(folder, executed, "stderr"),
                        executed, binary, after, roster, frame)
                    if pair["id"] in CURRENT_PHASE_SELECTIONS and role == "repair":
                        if name in CURRENT_PHASE_CASES:
                            row["phase_custody"] = custody.admit_phase_custody(
                                phase_root, name, "binding4")
                        elif name == CURRENT_DDL_CASE:
                            row.update(retained_child_originals=0, retained_phase_jsons=0)
                except UnknownExecution:
                    raise
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
        if pair["id"] in CURRENT_PHASE_SELECTIONS and role == "repair":
            result["phase_scope"] = current_phase_scope(result)
            verify_current_phase_originals(output, result)
        result.update(role_complete(pair, role, result))
        result["phase"] = "complete"
    except UnknownExecution as error:
        result.update(status="UNKNOWN", error=str(error),
                      unsealed_command=error.command,
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
            if pair["id"] in CURRENT_PHASE_SELECTIONS and role.get("role") == "repair":
                verified.update(verify_current_phase_originals(output, role))
    for original in retained:
        require(custody.fingerprint(output / original["path"], output) == original,
                "retained source or executable changed after observation")
        verified[original["path"]] = original
    require(observations and retained, "no retained observations or source originals")
    return {"status": "VERIFIED_BEFORE_TERMINAL_RECEIPT", "commands": len(observations),
            "files": verified}


def run_pairs(callback, pairs=None):
    pairs = PAIRS if pairs is None else pairs
    results = []
    for pair in pairs:
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


def run_projection_diagnostic(callback, pairs):
    require(len(pairs) == 1 and pairs[0]["id"] == "foundation-projection-diagnostic",
            "diagnostic selection differs")
    observed = callback(pairs[0], "repair")
    status = ("PASS_EXACT_DIAGNOSTIC_EXECUTION"
              if observed["status"] == "PASS_EXACT_DIAGNOSTIC_ROLE"
              else "UNKNOWN" if observed["status"] == "UNKNOWN" else "FAIL")
    return [{"id": pairs[0]["id"], "status": status, "roles": [observed]}]


def run(workspace, output, selection="foundation-causal"):
    pairs = selected_pairs(selection)
    diagnostic = selection == "foundation-projection-diagnostic"
    selected_roles = ("repair",) if diagnostic else ROLES
    context = execution_context(dict(os.environ), selection=selection)
    gate = Path(__file__).resolve().parents[1]
    workspace, output = workspace.resolve(strict=True), output.resolve()
    require(workspace == Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
            and gate.is_relative_to(workspace) and gate != workspace
            and output.is_relative_to(workspace) and output != workspace
            and not output.is_relative_to(gate) and not gate.is_relative_to(output),
            "gate, product worktrees and output require separate workspace paths")
    output.mkdir(parents=True, exist_ok=False)
    receipt = {"schema": "motionwright.foundation-causal-linux/1", "status": "FAIL",
               "selection": selection, "selected_pairs": [p["id"] for p in pairs],
               "not_selected_pairs": [p["id"] for p in [*PAIRS, IMPORT_BUDGET_PAIR]
                                      if p["id"] not in {x["id"] for x in pairs}],
               "not_selected_credit": "NONE",
               "context": context, "pairs": [], "formal_acceptance": False,
               "installed_application": "NOT_RUN", "operations_retried": False,
               "scope": "Only the " + str(len(pairs) * len(selected_roles))
                        + " frozen product sources and selected synthetic Rust contracts",
               "limitations": [
                   "Schema4 is a distinct unexecuted pair.",
                   "Same-payload receipt substitution remains outside the limited receipt repair.",
                   "No provider cancellation, installed application or human acceptance is established.",
                   "Seven original decoded SVG cases are a subset of thirteen, not additional executions.",
               ]}
    if selection == "foundation-import-budget":
        receipt["limitations"] = [
            "Only import resource budgets and source-free recovery contracts are selected.",
            "The four historical pairs, Schema4 migration and SDK/desktop suites are NOT_RUN here.",
            "The SavedMix full-cut limit, physical audio and installed application are NOT_RUN.",
            "Synthetic contract outcomes do not grant normative or human acceptance.",
        ]
    if selection in CURRENT_PHASE_SELECTIONS:
        receipt["limitations"] = [
            "Only the current 130 repair contracts and five OriginalMix causal baseline invocations are selected.",
            "Twelve receipt integrity contracts shared by two impact groups execute once on the current source.",
            "Historical Foundation, Import and Schema4 causal baselines are not re-executed or transferred.",
            "Four process cases retain binding4 phases and child originals; DDL retains only its parent result.",
            "No raw database/CAS archive, independent observer PID/output binding or power-loss claim.",
            "No installed application, Native full-cut adoption, SDK/desktop suite or human acceptance.",
        ]
        receipt["impact_contracts"] = {
            "foundation": 55, "import": 27, "core44": 44, "project08": 1,
            "originalmix": 15, "shared_receipt_contracts": 12,
            "deduplicated_repair": 130, "causal_baseline": 5,
        }
    if selection == "foundation-composed-current":
        receipt.update(
            mode="FROZEN_COMPOSED_CURRENT_CAUSAL_PAIR",
            composed_product_source=COMPOSED_SOURCE_PINS["sha"],
            composed_product_tree=COMPOSED_SOURCE_PINS["tree"],
            case_timeout_seconds=CASE_TIMEOUT,
            historical_result_credit="NONE",
            linux_consumer_contracts="REQUIRED_WORKFLOW_STEP_SEPARATE_ORIGINALS",
        )
        receipt["not_selected_pairs"].extend(
            ["foundation-current", "foundation-projection-diagnostic"])
        receipt["limitations"].extend([
            "Historical current-composition and diagnostic results do not supply PASS for this source.",
            "The complete OriginalMix integration target runs before the complete Project08 target.",
            "Linux consumer contracts retain a separate exact-source receipt and original streams.",
        ])
    if diagnostic:
        receipt["mode"] = "ONE_SOURCE_PHASE_DIAGNOSTIC"
        receipt["diagnostic_base_source"] = "307c7e762cb91e6383922dc2d6cdeeef8d3d4ed7"
        receipt["case_timeout_seconds"] = CASE_TIMEOUT
        receipt["scope"] = "One frozen instrumented source and one complete synthetic CR-PROJECT-08 case"
        receipt["limitations"] = [
            "The original 120-second timeout remains unchanged; no retry or baseline execution.",
            "All 500 scenes, 15000 nodes, page counts and custody assertions remain selected.",
            "Phase markers are direct stderr progress observations, not proof of completion.",
            "No Foundation135, causal baseline, latency contract or installed application credit.",
            "No product repair, normative approval or human acceptance is established.",
        ]
    custody.write_new(output / "started.json", {**receipt, "started_utc": custody.now()})
    try:
        target, temporary = workspace / "foundation-target", workspace / "foundation-tmp"
        require(not target.exists() and not temporary.exists()
                and not (workspace / "foundation-sources").exists(), "prior run state exists; no replay")
        target.mkdir(); temporary.mkdir()
        environment = build_environment(os.environ, target, temporary)
        gate_files = selected_gate_files(selection)
        gate_before = custody.source_snapshot(gate, context["GITHUB_SHA"], gate_files)
        if selection in CURRENT_PHASE_SELECTIONS:
            receipt["phase_dependency"] = current_dependency_preflight(gate, gate_before, selection)
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
        selected_runner = run_projection_diagnostic if diagnostic else run_pairs
        receipt["pairs"] = selected_runner(lambda pair, role: run_role(
            pair, role, gate, workspace, output, target, temporary,
            environment, context, toolchain, observations), pairs)
        if any(p["status"] == "UNKNOWN" for p in receipt["pairs"]):
            receipt.update(status="UNKNOWN", terminal_custody="UNSEALED_DESCENDANTS_NOT_OBSERVED")
        if receipt["status"] != "UNKNOWN":
            gate_after = custody.source_snapshot(gate, context["GITHUB_SHA"], gate_files)
            receipt["gate_source_after"] = gate_after
            custody.write_new(output / "gate-source-after.json", gate_after)
            require(gate_before == gate_after, "gate source changed during execution")
            for tool in toolchain.values():
                path = Path(tool["absolute_path"])
                require({k: tool[k] for k in ("path", "bytes", "sha256", "identity")} ==
                        custody.fingerprint(path, path.parent), "tool executable changed")
            receipt["originals_final"] = verify_originals(output, observations[1], receipt)
            expected_status = ("PASS_EXACT_DIAGNOSTIC_EXECUTION" if diagnostic
                               else "PASS_EXACT_CAUSAL_PAIR")
            if len(receipt["pairs"]) == len(pairs) and all(
                    p["status"] == expected_status for p in receipt["pairs"]):
                receipt["status"] = PASS_STATUSES[selection]
    except UnknownExecution as error:
        receipt.update(status="UNKNOWN", error=str(error),
                       unsealed_command=error.command,
                       terminal_custody="UNSEALED_DESCENDANTS_NOT_OBSERVED")
    except Exception as error:
        receipt.update(error=type(error).__name__ + ": " + str(error))
    receipt["finished_utc"] = custody.now()
    receipt["not_run_pairs"] = [p["id"] for p in pairs
                               if p["id"] not in {row["id"] for row in receipt["pairs"]}]
    receipt["expected_invocations"] = sum(len(names) for p in pairs for role in selected_roles
                                          for names in p[role]["targets"].values())
    receipt["actual_case_results"] = sum(len(r["results"]) for p in receipt["pairs"] for r in p["roles"])
    custody.write_new(output / "receipt.json", receipt)
    print(json.dumps({k: receipt.get(k) for k in
                     ("status", "actual_case_results", "not_run_pairs", "error")}))
    return 0 if receipt["status"] == PASS_STATUSES[selection] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--selection", choices=SELECTIONS, default="foundation-causal")
    parser.add_argument("--execute", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        return run(args.workspace, args.output, args.selection)
    except (ValueError, OSError, UnicodeError) as error:
        print("Foundation causal verification refused: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
