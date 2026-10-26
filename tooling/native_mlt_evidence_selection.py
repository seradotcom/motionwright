#!/usr/bin/env python3
"""Closed private /2 and explicitly admitted /3 native MLT G2 evidence.

Selection reads an explicit source-owned graph; it never walks an owner tree,
starts a native operation, reconstructs a receipt, decrypts a key or uploads.
A complete selection is custody completeness, not AV or human acceptance.
"""
from __future__ import annotations
import hashlib
import io
from pathlib import Path
import re
import struct
import tarfile
import wave

import native_evidence_projection as core
from native_mlt_g2_observation import SEMWRIGHT_PIN as SEM
from native_evidence_projection import Field, Object, Array, String, Number, Nullable, Literal

require = core.require
sha = core.sha
ORIGINAL_C = "c1c8850ff049a8775bb3d24c64be9b9365659773ce019902585c056f5be2f910"
REPAIRED_C = "fd5095ba7a676733b4b0642e92b931b986ee5fbfb23c800066d8996e4f428121"
REPAIRED_ELF = "e0d32f13536d568762be4b426edf9dce11602a3ec0eea4bc334d5346316b515a"
COMPILER = "cefa3ed54d02eaf894245248c07a39439489566d56a94b7f356748eba63cb2e8"
REFERENCE_WAV = "66aeaa3aa6fff53834ab4003671ce79068bcbd6f631609f0f988b3ac9744ebe9"
SHA = String(64, pattern=r"[0-9a-f]{64}")
SHA40 = String(40, pattern=r"[0-9a-f]{40}")
UUID = String(36, pattern=r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
RESOURCE = String(44, pattern=r"project:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
LABEL = String(96, pattern=r"[a-zA-Z0-9_.-]{1,96}")
RELATIVE = String(240, pattern=r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*")
N = Number(0, 2**63-1, integer=True)
POS = Number(1, 2**63-1, integer=True)
REAL = Number(-1e15, 1e15)
FALSE = Literal(False)
TRUE = Literal(True)
EMPTY = Array(Literal(None), 0)
TERMINAL = String(values=("NOT_STARTED", "ERROR_OR_UNKNOWN", "SUCCESS_REPORTED", "RESULT_REPORTED_UNVERIFIED"))


def O(**fields):
    return Object({key: value if isinstance(value, Field) else Field(value) for key, value in fields.items()})


def optional(rule):
    return Field(rule, required=False)


FOUR_CONTEXT = O(source_sha=SHA40, semwright_sha=Literal(SEM), run_id=String(32, pattern=r"[1-9][0-9]*"), run_attempt=Literal("1"))
IDENTITY = dict(project_resource=RESOURCE, generation=UUID, revision=POS, deliverable_id=UUID)
FILE_IDENTITY = Array(N, 5, 5)
COMPILER_CLOSURE = O(original_MLT_source_sha256=SHA, repaired_MLT_source_sha256=SHA,
                     compiled_module_sha256=SHA, compiled_module_bytes=POS, compiler_receipt_sha256=SHA)
ARTIFACT = O(root=Literal("output"), path=RELATIVE, sha256=SHA, bytes=POS)
AUDIO = O(relative_path=RELATIVE, sha256=SHA, sample_rate=Literal(48000), channels=Literal(2))
MEDIA = O(video=bool, audio=bool, codecs=Array(String(values=("h264", "aac", "ffv1", "pcm_s16le")), 2, 1),
          duration_num=N, duration_den=POS, frames=Nullable(N), width=Nullable(N), height=Nullable(N))
MUX = O(artifact=ARTIFACT, audio_sample_frames=POS, audio_sha256=SHA, channels=Literal(2),
        decoded_audio=ARTIFACT, decoded_audio_media=MEDIA, decoded_audio_sample_frames=POS,
        fps_num=Literal(30), fps_den=Literal(1), frame_count=POS, media=MEDIA,
        profile=Literal("h264-aac-mp4"), sample_rate=Literal(48000), video_sha256=SHA)
MEZZANINE = O(artifact=ARTIFACT, codec=Literal("ffv1"), container=Literal("matroska"),
              fps_num=Literal(30), fps_den=Literal(1), frame_count=POS, height=POS, width=POS,
              media=MEDIA, source_manifest_sha256=SHA)
SEGMENT = O(segment_id=LABEL, scene_ids=Array(UUID, 33, 1), output_start_frame=N,
            frame_count=POS, source_manifest_sha256=SHA, artifact_path=RELATIVE,
            artifact_sha256=SHA, artifact_bytes=POS)
PREPARED = O(**IDENTITY, input_sha256=SHA, fps_num=Literal(30), fps_den=Literal(1),
             total_frames=Literal(33), verified_video_segments=Array(SEGMENT, 2, 2))
TIMELINE = O(**IDENTITY, recipe_sha256=SHA, frame_count=Literal(33),
             native_frame_count_observed=Nullable(N), transport_pcm_audio=FALSE,
             native_render_profile=Literal("lossless-video-only"),
             artifact_path=RELATIVE, artifact_sha256=SHA, artifact_bytes=POS, source=PREPARED)
VOICE = O(**IDENTITY, voice_track_id=UUID, audio_sha256=SHA, audio_size_bytes=POS,
          audio_pcm_frames=Literal(52800), audio_sample_rate=Literal(48000), audio_channels=Literal(2),
          video_input_sha256=SHA, video_segment_count=Literal(2), total_frames=Literal(33),
          fps_num=Literal(30), fps_den=Literal(1), source_fingerprint=SHA,
          caption_cue_count=optional(N))
MLT_MASTER = O(**IDENTITY, frame_count=Literal(33), video=TIMELINE, measured_voice=VOICE,
               staged_audio=AUDIO, master=MUX, decoded_audio=ARTIFACT)
MIDSTREAM = O(decoded_master_pcm_frames=POS,
              decoded_stereo_content=Literal("distinct_source_400hz_left_600hz_right"),
              left_400_amplitude=Number(0, 4), right_600_amplitude=Number(0, 4))
PCM_CONTENT = O(schema=Literal("motionwright.native.source-pcm16-stereo-content/1"),
    original_wav_sha256=SHA, decoded_pcm_sha256=SHA, source_stereo_frames=POS,
    decoded_stereo_frames=POS, raw_decoded_frames_after_source=Number(-288000, 288000, integer=True),
    source_start_offset_frames_assumed=Literal(0), source_to_decoded_PTS_and_AAC_priming_verified=FALSE,
    source_declared_channel_map_verified=FALSE, full_source_window_count=POS,
    informative_source_windows_by_channel=Array(N, 2, 2),
    source_window_temporal_identity_checked_by_channel=Array(N, 2, 2),
    minimum_source_window_correlation=Array(Number(-1.001, 1.001), 2, 2),
    maximum_source_window_correlation=Array(Number(-1.001, 1.001), 2, 2),
    minimum_source_window_energy_ratio=Array(Number(0, 1e9), 2, 2),
    maximum_source_window_energy_ratio=Array(Number(0, 1e9), 2, 2),
    terminal_source_window_informative_by_channel=Array(bool, 2, 2),
    not_a_perceptual_quality_measure=TRUE, source_permissions_or_licensing_verified=FALSE,
    native_Provider_effect_or_audio_job_run=FALSE, human_audition_approved=FALSE)
FIXED_ENVELOPE = O(schema=Literal("motionwright.ci.fixed-stereo-full-source-envelope/1"),
    source_frames=Literal(52800), decoded_frames=POS, unexplained_aac_extra_frames=N,
    source_windows_checked_per_channel=POS, minimum_ratio_by_channel=Array(Number(0, 1e9), 2, 2),
    maximum_ratio_by_channel=Array(Number(0, 1e9), 2, 2), existing_midstream_stereo_observation=MIDSTREAM,
    media_packet_priming_and_skip_verified=FALSE, native_provider_receipt_verified=FALSE,
    human_listening_approved=FALSE, external_rights_approved=FALSE)
MLT_CONTENT = O(schema=Literal("motionwright.native.mlt-g2-source-content/1"),
    master_sha256=SHA, decoded_pcm_sha256=SHA, original_wav_sha256=SHA,
    oracles=O(full_source_content=O(state=String(values=("PASS", "FAIL")), witness=optional(PCM_CONTENT)),
              fixed_fixture_envelope=O(state=String(values=("PASS", "FAIL")), witness=optional(FIXED_ENVELOPE))),
    source_start_offset_frames_assumed=Literal(0), packet_PTS_and_AAC_priming_verified=FALSE,
    physical_AV_master_acceptance=FALSE, human_acceptance=FALSE)
SNAPSHOT = O(sha256=SHA, bytes=POS, file_identity=FILE_IDENTITY, links_at_read_time=N,
             mtime_ns=N, ctime_ns=N, scope=Literal("same-open-descriptor-stable-at-read-time"))
CONSUMER_SNAPSHOT = O(**{k:v for k,v in SNAPSHOT.fields.items()}, process_identity=Array(POS, 2, 2),
                      native_terminal_inferred=FALSE, native_operation_retried=FALSE)
LOADED = O(schema=Literal("motionwright.native.mlt-loaded-consumer/1"), actual_live_melt_pid=POS,
    actual_process_executable_scope=Literal("host-sealed-melt"),
    actual_process_executable_namespace_path=Literal("/plugin/tools/melt"),
    actual_process_executable_device=N, actual_process_executable_inode=POS,
    actual_process_cwd_device=N, actual_process_cwd_inode=POS,
    original_melt_sha256=SHA, original_repaired_avformat_module_sha256=SHA,
    source_compiler_module_sha256_must_be_attested_separately=TRUE,
    mapped_native_module=O(mapped_segments=POS, mapped_executable=TRUE, mapped_module_inode=POS,
        mapped_device_major_minor=Array(N, 2, 2),
        mapped_segment_perms=Array(String(4, pattern=r"[r-][w-][x-][ps]"), 64, 1)),
    source_bound_native_consumer_argv=O(bounded_arg_count=Number(1, 512, integer=True),
        actual_original_MLT_consumer=Literal("avformat"), relative_cwd_binding_required=bool),
    native_loader_environment=O(restricted_loader_overrides_absent=TRUE,
        observed_process_env_variable_count=Number(0, 4096, integer=True)),
    module_installed_before_original_effect=FALSE, module_installation_order_requires_separate_evidence=TRUE,
    original_runtime_loaded_module_attested=TRUE, native_av_master_decoded_right_tail_verified=FALSE,
    human_audio_acceptance=FALSE)
LIVE = O(pid=POS, process_start_ticks=POS, daemon_pid=POS, daemon_start_ticks=POS,
    live_module=LOADED, runtime_parent=O(pid=POS, process_start_ticks=POS, sha256=SHA, file_identity=FILE_IDENTITY),
    namespace=O(**{k:String(40, pattern=k+r":\[[0-9]+\]") for k in ("mnt", "pid", "user")}),
    namespace_root_identity=FILE_IDENTITY, private_cwd_identity=FILE_IDENTITY,
    owner_output_identity=FILE_IDENTITY, original_output_file_identity=FILE_IDENTITY,
    original_melt_write_descriptors=Array(N, 64, 1),
    owner_contract=O(manifest_sha256=SHA, runner_sha256=SHA, output_identity=FILE_IDENTITY, owner_identity=FILE_IDENTITY),
    publication_binding=Literal("SAME_ORIGINAL_OPEN_DESCRIPTOR_BYTES"),
    published_output_sha256=SHA, published_output_bytes=POS, source_descriptor_readback=SNAPSHOT,
    publication_source_pin=Literal(SEM), physical_AV_master_acceptance=FALSE, human_acceptance=FALSE)
ALIAS = O(canonical_installed_path=Literal("/usr/bin/melt-7"), installed_alias_path=Literal("/usr/bin/melt"),
          linkname=Literal("melt-7"), sha256=SHA, alias_identity=Array(N, 8, 8))
MLT_INPUT = O(schema=Literal("motionwright.native.mlt-g2-input-custody/1"), current_motionwright_sha=SHA40,
    semwright_sha=Literal(SEM), run_id=String(32, pattern=r"[1-9][0-9]*"), run_attempt=Literal("1"),
    source_compiler=COMPILER_CLOSURE, official_runtime_receipt_sha256=SHA, installation_receipt_sha256=SHA,
    known_original_module_sha256=SHA, known_melt_sha256=SHA, installed_melt_alias=ALIAS, native_effect_started=FALSE)
ACK = O(native_mlt_multisegment_e2e=Literal("PASS"),project_id=UUID,frame_count=Literal(33),segment_count=Literal(2),
    artifact_path=RELATIVE,artifact_sha256=SHA,artifact_bytes=POS,transport_pcm_audio=FALSE,
    native_render_profile=Literal("lossless-video-only"),master_artifact_path=RELATIVE,
    master_artifact_sha256=SHA,master_artifact_bytes=POS,master_audio_sha256=SHA,master_decoded_audio_sha256=SHA,
    native_mlt_cleanup_verified=FALSE,native_captioned_master_requested=FALSE)
MLT_FINAL = O(schema=Literal("motionwright.native.mlt-g2-custody/1"),
    terminal_scope=Literal("canonical_assemble_command"), native_terminal=TERMINAL,
    native_result=Nullable(ACK), secondary_failures=Array(LABEL, 128), source_content_and_live_module_verified=bool,
    native_operation_retried=FALSE, packet_PTS_and_AAC_priming_verified=FALSE,
    physical_AV_master_acceptance=FALSE, human_acceptance=FALSE)
MLT_TERMINAL = O(terminal_scope=Literal("canonical_assemble_command"), native_terminal=TERMINAL,
                  native_result=optional(ACK),native_operation_retried=FALSE)
OWNER = O(schema=Literal("motionwright.native.mlt-g2-owner-workspace/1"),motionwright_sha=SHA40,
    semwright_sha=Literal(SEM),run_id=String(32,pattern=r"[1-9][0-9]*"),run_attempt=Literal("1"),
    device=N,inode=POS,uid=POS,gid=N,custody_policy=Literal("retain_originals_in_place_for_sealing"),native_effect_started=FALSE)
OWNER_TERMINAL = O(schema=Literal("motionwright.native.mlt-g2-owner-workspace-terminal/1"),workspace=OWNER,
    originals_retained=TRUE,workspace_deleted=FALSE,body_exception_type=Nullable(LABEL),native_success_inferred=FALSE)

COMPILED = O(schema=Literal("motionwright.native.mlt.repaired-only-compiler-receipt/1"),
    original_source_sha256=SHA, repaired_source_sha256=SHA, compiled_source_sha256=SHA,
    instrumented_combined_source_compiled=FALSE, compiled_consumer_object_sha256=SHA,
    compiled_consumer_object_linked=TRUE, linker_consumer_object_sha256=SHA,
    linker_command_sha256=SHA, compiler_commands_sha256=SHA,
    compiler_result=Literal("PASS"), source_sanitizers_result=Literal("PASS"), source_frame_oracle_result=Literal("PASS"),
    module_installed=FALSE, source_head=SHA40, run_id=String(32, pattern=r"[1-9][0-9]*"), run_attempt=Literal("1"),
    compiled_module_sha256=SHA, compiled_module_bytes=POS, MLT_version=Literal("7.22.0"),
    actual_native_effect_performed=FALSE, source_G1_attestation_not_a_loaded_module_or_media_PASS=TRUE)
INSTALLATION = O(schema=Literal("motionwright.native.mlt-module-installed/1"),
    original_MLT_source_sha256=SHA, repaired_MLT_source_sha256=SHA, compiled_module_sha256=SHA,
    compiled_module_bytes=POS, compiler_receipt_sha256=SHA,
    original_module_sha256=SHA, original_module_bytes=POS, original_inode=POS,
    module_replaced=TRUE, original_native_effect_started=FALSE, repaired_AAC_right_channel_verified=FALSE,
    actual_installed_module_sha256=SHA, actual_installed_module_inode=POS, original_backup_sha256=SHA,
    new_native_master_issued=FALSE, actual_loaded_PID_and_module_verified=FALSE,
    whole_master_right_channel_verified=FALSE, human_acceptance=FALSE)
OFFICIAL = O(schema=Literal("motionwright.mlt.official-runtime-intake/1"), scope=Literal("signed-distribution-files-only"),
    context=O(helper_sha256=SHA, run_attempt=Literal("1"), run_id=String(32, pattern=r"[1-9][0-9]*"), source_sha=SHA40, source_tree=SHA40),
    distribution=O(architecture=Literal("amd64"), component=Literal("universe"), origin=Literal("Ubuntu"),
        package_version=Literal("7.22.0-1build6"), suite=Literal("noble"), upstream_version=Literal("7.22.0"), version=Literal("24.04")),
    runtime_bindings=O(melt=O(installed_path=Literal("/usr/bin/melt-7"), sha256=SHA),
        original_module=O(installed_path=Literal("/usr/lib/x86_64-linux-gnu/mlt-7/libmltavformat.so"), sha256=SHA)),
    source_authentication=Literal("PASS"), human_acceptance=FALSE, release_authorized=FALSE)
PACKET_SIDE = O(side_data_type=Literal("Skip Samples"), skip_samples=N, discard_padding=N, skip_reason=N, discard_reason=N)
DECIMAL = String(48, pattern=r"-?[0-9]+(?:\.[0-9]+)?")
PACKETS = O(packets=Array(O(codec_type=Literal("audio"), stream_index=N, pts=Number(-2**63+1,2**63-1,integer=True),
    pts_time=DECIMAL, dts=Number(-2**63+1,2**63-1,integer=True), dts_time=DECIMAL,
    duration=POS, duration_time=DECIMAL, size=String(24,pattern=r"[0-9]+"),
    pos=String(24,pattern=r"[0-9]+"), flags=String(8,pattern=r"[KD_]+"), side_data_list=optional(Array(PACKET_SIDE,8))), 512, 1),
    streams=Array(O(index=N, codec_name=Literal("aac"), codec_type=Literal("audio"), sample_rate=Literal("48000"),
        channels=Literal(2), time_base=Literal("1/48000"), start_pts=N, start_time=DECIMAL,
        duration_ts=POS, duration=DECIMAL, nb_frames=String(24,pattern=r"[0-9]+"),
        initial_padding=optional(N), nb_read_packets=optional(String(24,pattern=r"[0-9]+"))),1,1))
RESULT = O(native_mlt_multisegment_e2e=Literal("PASS"), motionwright_sha=SHA40, semwright_sha=Literal(SEM),
    motion_canvas_driver_sha256=SHA, mlt_driver_sha256=SHA, mlt_runner_sha256=SHA, melt_sha256=SHA,
    ffprobe_sha256=SHA, ffmpeg_sha256=SHA, rendered_source_frames=Array(POS,2,2),
    assembled_video_frames=Literal(33), mode=Literal("final-mp4"),
    native_mlt_cleanup_verified=FALSE, assembled_video_codec=Literal("ffv1"), assembled_video_has_audio=FALSE,
    video_bytes=POS, video_sha256=SHA, final_master_frames=Literal(33),
    final_master_sha256=SHA, final_master_bytes=POS, measured_voice_pcm_frames=Literal(52800), decoded_audio_sha256=SHA,
    g2_source_content_and_live_module=Literal("VERIFIED"), g2_original_wav_sha256=SHA,
    g2_repaired_module_sha256=SHA, g2_whole_decoded_pcm_sha256=SHA, g2_packet_observation_sha256=SHA,
    g2_packet_PTS_and_AAC_priming_verified=FALSE, g2_physical_AV_master_acceptance=FALSE)

SOURCE_PATHS = (
    "SOURCE_LOCK.json", ".github/workflows/native-mlt-sequence-e2e.yml",
    "tooling/ci_private_evidence.py", "tooling/ci_test_runner.py",
    "tooling/requirements/mlt-g2-custody.txt",
    "tooling/native_mlt_g2_custody.py", "tooling/native_mlt_g2_observation.py",
    "tooling/native_mlt_sequence_e2e.py", "tooling/native_mlt_runtime_admission.py",
    "tooling/native_mlt_loaded_module.py", "tooling/mlt_official_runtime_intake.py",
    "tooling/native_mlt_ci_pcm_envelope.py", "tooling/native_pcm_source_fidelity.py",
    "tooling/native_aac_clock_inspection.py", "tooling/native_aac_clock_custody.py",
    "crates/native/examples/native-mlt-sequence-e2e.rs", "tooling/mlt-audio-diagnostic/repair_planar_source.py",
    "tooling/native_evidence_projection.py", "tooling/native_mlt_evidence_selection.py",
    "tooling/native_av_failure_guard.py", "tooling/native_mlt_public_artifact.py",
    "tooling/native_mlt_caption_pixels.py", "tooling/mlt_failure_diagnostic.py",
    "tooling/cross_app_public_artifact.py",
    "crates/native/src/production.rs", "crates/native/src/production/mlt_timeline.rs",
    "crates/native/src/production/mlt_mezzanine.rs", "crates/native/src/production/mlt_multisegment_av.rs",
    "crates/native/src/mlt_av_audio.rs",
    "crates/storage/src/lib.rs", "crates/service/src/lib.rs",
)
FILM_SOURCE_PATHS = SOURCE_PATHS + (
    "tooling/native_film180_original_g2.py", "tooling/native_film180_media_readback.py",
    "crates/native/examples/native-film180-e2e.rs",
    "tooling/native_film180_evidence_selection.py",
)


# The only selected source-gate originals are two explicitly named runner
# receipts. Their original bytes are retained, including FAIL/UNKNOWN results;
# raw log files, neighboring files and arbitrary JSON fields are outside scope.
SOURCE_CONTRACT_TESTS = (
    "test_native_mlt_owner_workspace", "test_native_mlt_g2_custody",
    "test_native_mlt_g2_observation", "test_native_mlt_live_observer_diagnostics",
    "test_native_mlt_host_tool_observation", "test_native_mlt_loaded_module",
    "test_native_mlt_executable_alias", "test_native_mlt_compiler_intake",
    "test_native_evidence_projection", "test_native_mlt_evidence_selection",
    "test_native_aac_clock_inspection", "test_native_aac_clock_custody",
)
FILM_CONTRACT_TESTS = (
    "test_native_film180_original_g2", "test_native_film180_media_readback",
    "test_native_film180_evidence_selection",
)



# Optional private diagnostics do not promote an unknown producer terminal.
# The producer fixes these two slots before effects; no directory is enumerated.
FFV1_STAGES = ("zscale-help", "encode", "media-probe", "complete-probe", "decoder-version", "decode")
FFV1_PLAN_PATH = "producer/native-ffv1-stage-plan.original.json"
FFV1_ARGS = O(root=Literal("output"),
    manifest_path=String(80, pattern=r"render-[0-9a-f]{32}/artifact-manifest\.json"),
    expected_manifest_sha256=SHA, output_path=String(80, pattern=r"mw-mlt-[0-9a-f]{20}-s0[01]-ffv1\.mkv"),
    max_bytes=Literal(512*1024*1024))
FFV1_PLAN = O(schema=Literal("motionwright.native.ffv1-stage-plan/1"), **IDENTITY,
    input_sha256=SHA, owner_output_namespace=String(27,pattern=r"mw-mlt-[0-9a-f]{20}"),
    total_frames=Literal(33), slots=Array(O(slot=Number(0,1,integer=True),frame_count=POS,args=FFV1_ARGS),2,2),
    scope=Literal("canonical-preflight-destinations-before-original-FFV1-call"),
    original_host_stdin=FALSE,FFV1_call_started=FALSE,native_operation_retried=FALSE,physical_acceptance=FALSE)


# Original sidecar JSON is admitted by an exact field policy, never by the
# binary-original path. Limits agree with the producer's existing 64 KiB
# metadata and bounded stream records; no original byte is reserialized.
FFV1_OUTPUT = String(80, pattern=r"mw-mlt-[0-9a-f]{20}-s0[01]-ffv1\.mkv")
FFV1_STDIN_PATH = String(160, pattern=r"mw-mlt-[0-9a-f]{20}-s0[01]-ffv1\.mkv\.ffv1-(?:host|catalog)\.stdin\.original\.bin")
FFV1_HOST_STREAM_PATH = String(160, pattern=r"mw-mlt-[0-9a-f]{20}-s0[01]-ffv1\.mkv\.ffv1-(?:host|catalog)\.(?:stdout|stderr)\.original\.bin")
FFV1_PROCESS_STREAM_PATH = String(160, pattern=r"mw-mlt-[0-9a-f]{20}-s0[01]-ffv1\.mkv\.ffv1-(?:zscale-help|encode|media-probe|complete-probe|decoder-version|decode)\.(?:stdout|stderr)\.original\.bin")
FFV1_I32 = Number(-(2**31), 2**31-1, integer=True)
FFV1_U64 = Number(0, 2**64-1, integer=True)
FFV1_ERROR_CODES = (
    "Unsupported", "Unavailable", "PermissionDenied", "ConsentRequired", "PolicyDenied",
    "NotFound", "AmbiguousTarget", "StaleReference", "BackendFailed", "Timeout",
    "InvalidArgument", "PluginProtocolError", "SandboxDenied", "Conflict", "Cancelled",
    "Internal", "ProtocolMismatch", "ResourceExhausted",
)
FFV1_NATIVE_DIAGNOSTIC = O(
    phase=String(values=("Startup", "NativeExchange", "HostRead", "HostWrite")),
    reason=String(values=("ChildExit", "UnexpectedEof", "Timeout", "InvalidFrame", "IoFailure")),
    exit_code=Nullable(Number(0,255,integer=True)),
    signal=Nullable(Number(1,64,integer=True)),
    stderr_tail_sha256=SHA, stderr_tail_bytes=Number(0,8192,integer=True),
    stderr_tail_truncated=bool)
FFV1_HOST_ERROR = O(
    code=String(64,values=FFV1_ERROR_CODES), message=String(65536), outcome_known=bool,
    candidates=optional(Array(String(65536),65536)),
    progress=optional(O(completed_steps=FFV1_U64,failed_step=String(65536),
        state=String(values=("partial_success_or_unknown","failed_before_confirmed_step")))),
    native_diagnostic=optional(FFV1_NATIVE_DIAGNOSTIC))
FFV1_PROCESS_ERROR = O(code=String(128,pattern=r"[A-Za-z][A-Za-z0-9_]*"),
    message=String(65536),outcome_known=bool)
FFV1_HOST_STREAM = O(
    root=Literal("output"),path=FFV1_HOST_STREAM_PATH,sha256=SHA,
    bytes=Number(0,262144,integer=True),delivered_buffer_bytes=FFV1_U64,
    delivered_buffer_sha256=SHA,diagnostic_prefix_truncated=bool,
    retained=bool,stream_eof_attested=FALSE)
FFV1_PROCESS_STREAM = O(
    root=Literal("output"),path=FFV1_PROCESS_STREAM_PATH,sha256=SHA,
    bytes=Number(0,16384,integer=True),returned_buffer_bytes=Number(0,8*1024*1024,integer=True),
    returned_buffer_sha256=SHA,diagnostic_prefix_truncated=bool,
    retained=bool,upstream_stream_eof_attested=FALSE)
FFV1_HOST_TERMINAL = O(
    schema=Literal("semwright.ffv1-host-original/1"),
    host_stage=String(values=("host","catalog")),
    state=String(values=("start-returned-error","wait-returned-error","host-returned-error","completed")),
    output_path=FFV1_OUTPUT,
    runtime_job_id=Nullable(String(128,pattern=r"[A-Za-z0-9-]{1,128}")),
    runtime_job_id_exposed=bool,host_exit_code=Nullable(FFV1_I32),
    stdin=O(root=Literal("output"),path=FFV1_STDIN_PATH,sha256=SHA,
        bytes=Number(0,65536,integer=True)),
    stdin_scope=String(values=("same-empty-Vec-supplied-to-existing-discover-call",
        "same-serialized-Vec-supplied-to-existing-AV-job-start")),
    stdin_delivery_confirmed=Nullable(bool),
    stdout=Nullable(FFV1_HOST_STREAM),stderr=Nullable(FFV1_HOST_STREAM),
    original_error=Nullable(FFV1_HOST_ERROR),
    stream_eof_attested=FALSE,native_execution_retried=FALSE,physical_acceptance=FALSE,
    capture_scope=Literal("same-existing-Host-return-before-parse-and-public-redaction"))
FFV1_PROCESS_SPEC = O(
    encoding=Literal("unix-os-bytes-hex"),
    executable=String(8192,pattern=r"(?:[0-9a-f]{2})*"),
    argv=Array(String(8192,pattern=r"(?:[0-9a-f]{2})*"),128),
    cwd=String(8192,pattern=r"(?:[0-9a-f]{2})*"),
    timeout_millis=FFV1_U64,cpu_seconds=FFV1_U64,address_space_bytes=FFV1_U64,
    environment_included=FALSE,native_execution_retried=FALSE)
FFV1_PROCESS_TERMINAL = O(
    schema=Literal("semwright.ffv1-process-original/1"),
    stage=String(values=FFV1_STAGES),output_path=FFV1_OUTPUT,source_manifest_sha256=SHA,
    source_capture_retained_limit=Number(1,8*1024*1024,integer=True),
    source_capture_total_limit=Number(1,8*1024*1024,integer=True),
    diagnostic_prefix_limit=Literal(16384),spec=Nullable(FFV1_PROCESS_SPEC),
    terminal=O(process_result_received=bool,exit_code=Nullable(FFV1_I32),
        term_signal=Nullable(FFV1_I32),cancelled=Nullable(bool),timed_out=Nullable(bool),
        output_exceeded=Nullable(bool),stdout=Nullable(FFV1_PROCESS_STREAM),
        stderr=Nullable(FFV1_PROCESS_STREAM),original_error=Nullable(FFV1_PROCESS_ERROR)),
    native_execution_retried=FALSE,stream_eof_attested=FALSE,
    capture_scope=Literal("existing-ProcessResult-before-checked-or-parse"),
    physical_acceptance=FALSE)
FFV1_PROCESS_CUSTODY = O(
    schema=Literal("semwright.ffv1-process-custody/1"),
    output_path=FFV1_OUTPUT,source_manifest_sha256=SHA,
    observed_stages=Array(String(values=FFV1_STAGES),6),
    secondary_failures=Array(String(values=("stream-retention","stage-order","spec-retention",
        "terminal-serialization","terminal-retention","stage-coverage","summary-retention")),32),
    original_operation_succeeded=bool,original_error=Nullable(FFV1_PROCESS_ERROR),
    stream_eof_attested=FALSE,native_execution_retried=FALSE,physical_acceptance=FALSE)
FFV1_SIDECAR_POLICY_BY_ROLE = {
    "ffv1-host-terminal":"ffv1_host_terminal",
    "ffv1-catalog-terminal":"ffv1_host_terminal",
    "ffv1-process-terminal":"ffv1_process_terminal",
    "ffv1-process-custody":"ffv1_process_custody",
}


def _ffv1_diagnostic_paths_for_outputs(outputs):
    require(type(outputs) is list and len(outputs)==2,"ffv1-diagnostic-output-count")
    paths=[]
    for output in outputs:
        core.project(output,FFV1_OUTPUT,exact=True)
        for family in ("host","catalog"):
            for stream,limit in (("stdin",65536),("stdout",262144),("stderr",262144)):
                paths.append(("ffv1-"+family+"-"+stream,owner_output(f"{output}.ffv1-{family}.{stream}.original.bin"),limit))
            paths.append(("ffv1-"+family+"-terminal",owner_output(f"{output}.ffv1-{family}.terminal.original.json"),65536))
        for stage in FFV1_STAGES:
            for stream in ("stdout","stderr"):
                paths.append(("ffv1-process-"+stream,owner_output(f"{output}.ffv1-{stage}.{stream}.original.bin"),16384))
            paths.append(("ffv1-process-terminal",owner_output(f"{output}.ffv1-{stage}.terminal.original.json"),65536))
        paths.append(("ffv1-process-custody",owner_output(f"{output}.ffv1-process-custody.original.json"),65536))
    require(len(paths)==54 and len({p for _,p,_ in paths})==54,"ffv1-closed-diagnostic-roster")
    return paths


def ffv1_diagnostic_paths(plan):
    core.project(plan,FFV1_PLAN,exact=True)
    namespace="mw-mlt-"+plan["input_sha256"][:20]
    require(plan["owner_output_namespace"]==namespace,"ffv1-plan-namespace")
    require([s["slot"] for s in plan["slots"]]==[0,1]
            and [s["frame_count"] for s in plan["slots"]]==[32,1],"ffv1-plan-slot-order")
    outputs=[slot["args"]["output_path"] for slot in plan["slots"]]
    require(outputs==[f"{namespace}-s{index:02}-ffv1.mkv" for index in (0,1)],
            "ffv1-plan-output")
    return _ffv1_diagnostic_paths_for_outputs(outputs)

def verify_ffv1_original_stdin(plan, slot, raw):
    """Semantic binding only; byte custody and actual dispatch are separate facts."""
    ffv1_diagnostic_paths(plan)
    require(type(slot) is int and slot in (0,1) and type(raw) is bytes and len(raw)<=65536,"ffv1-stdin-bound")
    value=core.strict(raw)
    require(type(value) is dict and set(value)=={"command","descriptor_sha256","args"}
            and value["command"]=="driver.mlt-video.frames.encode"
            and type(value["descriptor_sha256"]) is str
            and re.fullmatch(r"[0-9a-f]{64}",value["descriptor_sha256"]) is not None
            and value["args"]==plan["slots"][slot]["args"],"ffv1-original-stdin-plan-binding")
    return dict(sha256=sha(raw),bytes=len(raw),slot=slot,
                actual_dispatch_verified=False,native_outcome_known=False,automatic_retry=False)


# /3 authority is an external caller object frozen before selected TAR capture.
# The source seed and the canonical plan remain separate originals; neither
# object is inferred from a selected-evidence manifest.
FFV1_ADMISSION_SCHEMA = "motionwright.native.ffv1-diagnostic-admission/2"
FFV1_SEED_CAPTURE_SCHEMA = "motionwright.native.ffv1-seed-capture/1"
FFV1_SEED_STDOUT_PATH = "producer/native-source-seed.stdout.original.bin"
FFV1_SEED_STDERR_PATH = "producer/native-source-seed.stderr.original.bin"
FFV1_SEED_TERMINAL_PATH = "producer/native-source-seed.terminal.original.json"
FFV1_SELECTED_MAXIMUM = 3_997_696
FFV1_DIAGNOSTIC_MAXIMUM = FFV1_SELECTED_MAXIMUM - 65_536
# The existing FFV1 subgroup remains 54 diagnostics + one ordinary plan.
# Seed carriers are an explicit additional subgroup, never relabelled as FFV1.
FFV1_SEED_MAXIMUM = 393_216
FFV1_CAPTURE_SELECTED_MAXIMUM = 4_390_912
FFV1_CONTEXT = O(source_sha=SHA40, semwright_sha=Literal(SEM),
    run_id=String(32,pattern=r"[1-9][0-9]*"),run_attempt=Literal("1"),
    scenario=Literal("repaired-g2"))
FFV1_REFERENCE_SOURCE = O(workflow_run_id=Literal(39054798740),run_attempt=Literal(1),
    workflow_event=Literal("pull_request"),
    workflow_head_sha=Literal("30f02fd0b744cda36035a4473c98e066f89d3d00"),
    producer_checkout_sha=Literal("a31505e3c0884a419b0b3b1072af0ba4057f9442"),
    artifact_id=Literal(12109323512),
    artifact_zip_sha256=Literal("35ed8f9d2c840dd0d8702eaaf23454da87e49bdee2dd12f190b7be634806b27a"),
    public_manifest_sha256=Literal("ca02511c2e7028a666d8c54f1494af096e19a47640d3cffe40865bfa7049f4b8"),
    original_wav_sha256=SHA,source_is_synthetic_ci_fixture=TRUE,
    historical_right_channel_failure_superseded=FALSE)
FFV1_INPUT_CUSTODY = O(**MLT_INPUT.fields,original_source=FFV1_REFERENCE_SOURCE)
FFV1_SEED = O(project_id=UUID,generation=UUID,revision=POS,resource=RESOURCE,
    width=Literal(1280),height=Literal(720),fps_num=Literal(30),fps_den=Literal(1),
    scene_count=Literal(33),deliverable_count=Literal(3),master_profile_id=UUID)
FFV1_ORIGINAL_IDENTITY = O(root=Literal("observation"),relative_path=RELATIVE,
    sha256=SHA,bytes=Number(0,262144,integer=True))
FFV1_SEED_STREAM = O(relative_path=RELATIVE,sha256=SHA,
    bytes=Number(0,262144,integer=True),returned_buffer_sha256=SHA,
    returned_buffer_bytes=Number(0,2**63-1,integer=True),
    prefix_truncated=bool,retained=bool,stream_eof_attested=FALSE)
FFV1_SEED_CAPTURE = O(schema=Literal(FFV1_SEED_CAPTURE_SCHEMA),
    context=FFV1_CONTEXT,argv=Array(String(4096),3,3),timeout_seconds=Literal(30),
    stdout=FFV1_SEED_STREAM,stderr=FFV1_SEED_STREAM,
    exit_code=Nullable(Number(-(2**31),2**31-1,integer=True)),
    deadline_exceeded=bool,error_class=Nullable(String(128)),
    error=Nullable(String(65536)),automatic_retry=FALSE,
    capture_scope=Literal("existing-run-sanitized-json-callback-before-parse"),
    before_assemble=TRUE,native_outcome_inferred=FALSE)
FFV1_DIAGNOSTIC_ENTRY = O(root=Literal("owner"),relative_path=RELATIVE,
    phase=Literal("original"),layer=Literal("attempt-custody"),role=LABEL,
    type=String(values=("stdout","stderr","stdin","typed-terminal")),
    maximum=Number(1,262144,integer=True),policy=Nullable(LABEL))
FFV1_DIAGNOSTIC_CONTRACT = O(schema=Literal(core.DIAGNOSTIC_CONTRACT_SCHEMA),
    context=FFV1_CONTEXT,entries=Array(FFV1_DIAGNOSTIC_ENTRY,54,54))
FFV1_SEED_DIAGNOSTIC_ENTRY = O(root=Literal("observation"),relative_path=RELATIVE,
    phase=Literal("original"),layer=Literal("attempt-custody"),role=LABEL,
    type=String(values=("stdout","stderr","typed-terminal")),
    maximum=Number(1,262144,integer=True),policy=Nullable(LABEL))
FFV1_SEED_DIAGNOSTIC_CONTRACT = O(schema=Literal(core.DIAGNOSTIC_CONTRACT_SCHEMA),
    context=FFV1_CONTEXT,entries=Array(FFV1_SEED_DIAGNOSTIC_ENTRY,3,3))
FFV1_CAPTURE_DIAGNOSTIC_ENTRY = O(root=String(values=("owner","observation")),
    relative_path=RELATIVE,phase=Literal("original"),layer=Literal("attempt-custody"),
    role=LABEL,type=String(values=("stdout","stderr","stdin","typed-terminal")),
    maximum=Number(1,262144,integer=True),policy=Nullable(LABEL))
FFV1_CAPTURE_DIAGNOSTIC_CONTRACT = O(schema=Literal(core.DIAGNOSTIC_CONTRACT_SCHEMA),
    context=FFV1_CONTEXT,entries=Array(FFV1_CAPTURE_DIAGNOSTIC_ENTRY,57,57))
FFV1_PLAN_IDENTITY = O(**IDENTITY,input_sha256=SHA,
    owner_output_namespace=String(27,pattern=r"mw-mlt-[0-9a-f]{20}"))
FFV1_ADMISSION = O(schema=Literal(FFV1_ADMISSION_SCHEMA),context=FFV1_CONTEXT,
    source_bindings=Array(O(path=RELATIVE,sha256=SHA),128,1),
    fixture_identity=O(**IDENTITY),seed_stdout=FFV1_ORIGINAL_IDENTITY,
    seed_stderr=FFV1_ORIGINAL_IDENTITY,seed_terminal=FFV1_ORIGINAL_IDENTITY,
    input_custody=FFV1_ORIGINAL_IDENTITY,
    stage_plan=O(original=FFV1_ORIGINAL_IDENTITY,identity=FFV1_PLAN_IDENTITY),
    diagnostic_contract=FFV1_DIAGNOSTIC_CONTRACT,
    seed_diagnostic_contract=FFV1_SEED_DIAGNOSTIC_CONTRACT,
    capture_diagnostic_contract=FFV1_CAPTURE_DIAGNOSTIC_CONTRACT,
    selected_optional_members=Literal(55),selected_maximum=Literal(FFV1_SELECTED_MAXIMUM),
    seed_diagnostic_members=Literal(3),seed_diagnostic_maximum=Literal(FFV1_SEED_MAXIMUM),
    capture_diagnostic_members=Literal(57),capture_selected_members=Literal(58),
    capture_selected_maximum=Literal(FFV1_CAPTURE_SELECTED_MAXIMUM),
    scope=Literal("external-source-run-fixture-and-original-plan-before-selected-capture"),
    native_operation_retried=FALSE,physical_acceptance=FALSE)


def ffv1_plan_identity(plan):
    """Source-owned metadata projection; original plan bytes stay native."""
    ffv1_diagnostic_paths(plan)
    return {key:plan[key] for key in (*IDENTITY,"input_sha256","owner_output_namespace")}


def ffv1_contract_for_identity(identity, current):
    core.project(identity,FFV1_PLAN_IDENTITY,exact=True)
    namespace="mw-mlt-"+identity["input_sha256"][:20]
    require(identity["owner_output_namespace"]==namespace,"ffv1-plan-namespace")
    context=checked_context(current,"repaired-g2")
    outputs=[f"{namespace}-s{index:02}-ffv1.mkv" for index in (0,1)]
    entries=[]
    for role,path,maximum in _ffv1_diagnostic_paths_for_outputs(outputs):
        policy=FFV1_SIDECAR_POLICY_BY_ROLE.get(role)
        subtype="typed-terminal" if policy else role.rsplit("-",1)[1]
        entries.append(dict(root="owner",relative_path=path,phase="original",
            layer="attempt-custody",role=role,type=subtype,maximum=maximum,policy=policy))
    require(sum(row["maximum"] for row in entries)==FFV1_DIAGNOSTIC_MAXIMUM,
            "ffv1-diagnostic-total-bound")
    return dict(schema=core.DIAGNOSTIC_CONTRACT_SCHEMA,context=context,entries=entries)


def ffv1_contract_for_plan(plan, current):
    return ffv1_contract_for_identity(ffv1_plan_identity(plan),current)

def ffv1_seed_contract(current):
    context=checked_context(current,"repaired-g2")
    entries=[]
    for role,path,kind,maximum,policy in (
        ("ffv1-seed-stdout",FFV1_SEED_STDOUT_PATH,"stdout",65536,None),
        ("ffv1-seed-stderr",FFV1_SEED_STDERR_PATH,"stderr",262144,None),
        ("ffv1-seed-terminal",FFV1_SEED_TERMINAL_PATH,"typed-terminal",65536,"ffv1_seed_capture"),
    ):
        entries.append(dict(root="observation",relative_path=path,phase="original",
            layer="attempt-custody",role=role,type=kind,maximum=maximum,policy=policy))
    require(len(entries)==3 and sum(row["maximum"] for row in entries)==FFV1_SEED_MAXIMUM,
            "ffv1-seed-closed-roster")
    return dict(schema=core.DIAGNOSTIC_CONTRACT_SCHEMA,context=context,entries=entries)


def ffv1_capture_contract_for_identity(identity, current):
    ffv1=ffv1_contract_for_identity(identity,current)
    seed=ffv1_seed_contract(current)
    require(ffv1["context"]==seed["context"],"ffv1-capture-subgroup-context")
    entries=ffv1["entries"]+seed["entries"]
    require(len(entries)==57
            and len({(row["root"],row["relative_path"]) for row in entries})==57
            and sum(row["maximum"] for row in entries)+65536==FFV1_CAPTURE_SELECTED_MAXIMUM,
            "ffv1-combined-capture-roster")
    return dict(schema=core.DIAGNOSTIC_CONTRACT_SCHEMA,context=ffv1["context"],entries=entries)


def ffv1_original_identity(path, raw):
    require(type(raw) is bytes and len(raw)<=262144,"ffv1-admission-original-bound")
    return dict(root="observation",relative_path=path,sha256=sha(raw),bytes=len(raw))


def checked_ffv1_admission(admission, *, current, source_bindings=None):
    """Validate a caller-supplied admission; never load it from a TAR."""
    frozen = core.strict(core.canonical(admission))
    core.project(frozen,FFV1_ADMISSION,exact=True)
    require(frozen["context"]==checked_context(current,"repaired-g2"),
            "ffv1-admission-context")
    bindings = frozen["source_bindings"]
    require(len(bindings)==len(SOURCE_PATHS)
            and [row["path"] for row in bindings]==sorted(SOURCE_PATHS),
            "ffv1-admission-source-closure")
    if source_bindings is not None:
        require(bindings==sorted(source_bindings,key=lambda row:row["path"]),
                "ffv1-admission-source-bindings")
    for name,path,minimum,maximum in (
        ("seed_stdout",FFV1_SEED_STDOUT_PATH,1,65536),
        ("seed_stderr",FFV1_SEED_STDERR_PATH,0,262144),
        ("seed_terminal",FFV1_SEED_TERMINAL_PATH,1,65536),
        ("input_custody","input-custody.json",1,262144),
    ):
        require(frozen[name]["relative_path"]==path
                and minimum<=frozen[name]["bytes"]<=maximum,"ffv1-admission-original-layout")
    original = frozen["stage_plan"]["original"]
    require(original["relative_path"]==FFV1_PLAN_PATH and 0<original["bytes"]<=65536,
            "ffv1-admission-plan-layout")
    identity=frozen["stage_plan"]["identity"]
    require(frozen["fixture_identity"]=={key:identity[key] for key in IDENTITY},
            "ffv1-admission-fixture-identity")
    require(frozen["diagnostic_contract"]==ffv1_contract_for_identity(identity,current),
            "ffv1-admission-closed-diagnostic-contract")
    require(frozen["seed_diagnostic_contract"]==ffv1_seed_contract(current),
            "ffv1-admission-closed-seed-contract")
    require(frozen["capture_diagnostic_contract"]==ffv1_capture_contract_for_identity(identity,current),
            "ffv1-admission-closed-combined-contract")
    return frozen


def admit_ffv1_diagnostics(*, current, source_bindings, plan_original,
                           seed_stdout_original, seed_stderr_original, seed_terminal_original,
                           input_custody_original, expected_seed_argv):
    """Admit named originals outside the archive, without reserializing any original."""
    for raw,maximum in ((plan_original,65536),(seed_stdout_original,65536),
                        (seed_terminal_original,65536),(input_custody_original,262144)):
        require(type(raw) is bytes and 0<len(raw)<=maximum,"ffv1-admission-original-budget")
    require(type(seed_stderr_original) is bytes and len(seed_stderr_original)<=262144,
            "ffv1-admission-stderr-budget")
    plan=core.project(core.strict(plan_original),FFV1_PLAN,exact=True)
    seed=core.project(core.strict(seed_stdout_original),FFV1_SEED,exact=True)
    terminal=core.project(core.strict(seed_terminal_original),FFV1_SEED_CAPTURE,exact=True)
    inputs=core.project(core.strict(input_custody_original),FFV1_INPUT_CUSTODY,exact=True)
    context=checked_context(current,"repaired-g2")
    require(terminal["context"]==context
            and terminal["exit_code"]==0 and type(terminal["exit_code"]) is int
            and terminal["deadline_exceeded"] is False
            and terminal["error_class"] is None and terminal["error"] is None,
            "ffv1-seed-original-terminal")
    require(type(expected_seed_argv) is list and len(expected_seed_argv)==3
            and expected_seed_argv[1]=="seed" and terminal["argv"]==expected_seed_argv,
            "ffv1-seed-original-command")
    stdout=terminal["stdout"]
    require(stdout["relative_path"]==FFV1_SEED_STDOUT_PATH
            and stdout["retained"] is True and stdout["prefix_truncated"] is False
            and (stdout["sha256"],stdout["bytes"])==(sha(seed_stdout_original),len(seed_stdout_original))
            and (stdout["returned_buffer_sha256"],stdout["returned_buffer_bytes"])
                ==(sha(seed_stdout_original),len(seed_stdout_original)),
            "ffv1-seed-original-stdout")
    stderr=terminal["stderr"]
    require(stderr["relative_path"]==FFV1_SEED_STDERR_PATH
            and stderr["retained"] is True and stderr["prefix_truncated"] is False
            and (stderr["sha256"],stderr["bytes"])==(sha(seed_stderr_original),len(seed_stderr_original))
            and (stderr["returned_buffer_sha256"],stderr["returned_buffer_bytes"])
                ==(sha(seed_stderr_original),len(seed_stderr_original)),
            "ffv1-seed-original-stderr-custody")
    require((inputs["current_motionwright_sha"],inputs["semwright_sha"],
             inputs["run_id"],inputs["run_attempt"])
            ==tuple(current[name] for name in ("source_sha","semwright_sha","run_id","run_attempt"))
            and inputs["native_effect_started"] is False
            and inputs["original_source"]["original_wav_sha256"]==REFERENCE_WAV,
            "ffv1-fixture-input-context")
    fixture_identity=dict(project_resource=seed["resource"],generation=seed["generation"],
        revision=seed["revision"],deliverable_id=seed["master_profile_id"])
    require(seed["resource"]=="project:"+seed["project_id"]
            and fixture_identity=={key:plan[key] for key in IDENTITY},
            "ffv1-admission-fixture-identity")
    result=dict(schema=FFV1_ADMISSION_SCHEMA,context=context,
        source_bindings=sorted(source_bindings,key=lambda row:row["path"]),
        fixture_identity=fixture_identity,
        seed_stdout=ffv1_original_identity(FFV1_SEED_STDOUT_PATH,seed_stdout_original),
        seed_stderr=ffv1_original_identity(FFV1_SEED_STDERR_PATH,seed_stderr_original),
        seed_terminal=ffv1_original_identity(FFV1_SEED_TERMINAL_PATH,seed_terminal_original),
        input_custody=ffv1_original_identity("input-custody.json",input_custody_original),
        stage_plan=dict(original=ffv1_original_identity(FFV1_PLAN_PATH,plan_original),
                        identity=ffv1_plan_identity(plan)),
        diagnostic_contract=ffv1_contract_for_plan(plan,current),
        seed_diagnostic_contract=ffv1_seed_contract(current),
        capture_diagnostic_contract=ffv1_capture_contract_for_identity(ffv1_plan_identity(plan),current),
        selected_optional_members=55,selected_maximum=FFV1_SELECTED_MAXIMUM,
        seed_diagnostic_members=3,seed_diagnostic_maximum=FFV1_SEED_MAXIMUM,
        capture_diagnostic_members=57,capture_selected_members=58,
        capture_selected_maximum=FFV1_CAPTURE_SELECTED_MAXIMUM,
        scope="external-source-run-fixture-and-original-plan-before-selected-capture",
        native_operation_retried=False,physical_acceptance=False)
    return checked_ffv1_admission(result,current=current,source_bindings=source_bindings)


class Ffv1StagePlanAbsent(core.SelectionError):
    """Fixed original plan returned ENOENT; native effects remain unknown."""


def read_ffv1_admission(*, roots, current, source_bindings):
    """Read fixed originals; distinguish only source-bound original-plan ENOENT."""
    require("observation" in roots and "owner" in roots,"ffv1-admission-roots")
    paths={name:str(path) for name,path in roots.items()}
    core.verify_source_bindings(paths["source"],current["source_sha"],source_bindings)
    handles=core.RootHandles(paths)
    selected=((FFV1_PLAN_PATH,65536),(FFV1_SEED_STDOUT_PATH,65536),
              (FFV1_SEED_STDERR_PATH,262144),(FFV1_SEED_TERMINAL_PATH,65536),
              ("input-custody.json",262144))
    originals,marks={},{}
    plan_missing=None
    try:
        try:
            initial_plan=handles.stat_member("observation",core.member_path(FFV1_PLAN_PATH))
        except FileNotFoundError as error:
            plan_missing=error
            # Only the initial stat's ENOENT can enable early failure custody.
            try:
                handles.stat_member("observation",core.member_path(FFV1_PLAN_PATH))
            except FileNotFoundError:
                pass
            else:
                require(False,"ffv1-plan-appeared-during-admission")
        else:
            # Once observed regular, a vanished leaf/open/read failure is a
            # changed original, never an absent-plan downgrade.
            for path,maximum in selected:
                raw,mark=handles.read("observation",core.member_path(path),maximum)
                if path==FFV1_PLAN_PATH:
                    require(mark==initial_plan,"ffv1-plan-changed-before-original-read")
                originals[path]=raw;marks[path]=mark
            for path,_ in selected:
                require(handles.stat_member("observation",core.member_path(path))==marks[path],
                        "ffv1-admission-original-changed")
        require(handles.stable_roots(),"ffv1-admission-root-changed")
    finally:
        handles.close()
    core.verify_source_bindings(paths["source"],current["source_sha"],source_bindings)
    if plan_missing is not None:
        # The caller must still obtain the unchanged Core /2 bilateral guard
        # during capture. This exception grants no diagnostic namespace.
        raise Ffv1StagePlanAbsent("ffv1-original-plan-absent") from plan_missing
    return admit_ffv1_diagnostics(current=current,source_bindings=source_bindings,
        plan_original=originals[FFV1_PLAN_PATH],seed_stdout_original=originals[FFV1_SEED_STDOUT_PATH],
        seed_stderr_original=originals[FFV1_SEED_STDERR_PATH],
        seed_terminal_original=originals[FFV1_SEED_TERMINAL_PATH],
        input_custody_original=originals["input-custody.json"],
        expected_seed_argv=[str(Path(paths["source"])/"target"/"debug"/"examples"/"native-mlt-sequence-e2e"),
            "seed",str(Path(paths["owner"])/"motionwright-data"/"motionwright.sqlite3")])


def checked_ffv1_plan_absence(manifest, *, current, source_bindings=None):
    """Check the capture's fixed bilateral guard; this is not native intake."""
    require(type(manifest) is dict and manifest.get("schema")==core.MANIFEST_SCHEMA
            and manifest.get("context")==checked_context(current,"repaired-g2")
            and manifest.get("source_bytes_bound") is True
            and manifest.get("selected_namespace_stable") is True
            and manifest.get("raw_diagnostics_included") is False
            and manifest.get("lost_scope")==list(core.LOST_SCOPE),
            "ffv1-plan-absence-capture-scope")
    if source_bindings is not None:
        require(sorted(manifest["source_bindings"],key=lambda row:row["path"])==
                sorted(source_bindings,key=lambda row:row["path"]),
                "ffv1-plan-absence-source-bindings")
    require(type(manifest.get("members")) is list
            and all(type(row) is dict and type(row.get("kind")) is str
                    for row in manifest["members"]),"ffv1-plan-absence-member-types")
    require(not any(row["kind"] in core.DIAGNOSTIC_KINDS
                    or (row.get("root"),row.get("relative_path"))==
                        ("observation",FFV1_PLAN_PATH)
                    for row in manifest["members"]),"ffv1-plan-absence-selected-member")
    expected=dict(root="observation",relative_path=FFV1_PLAN_PATH,phase="original",
                  layer="attempt-custody",role="ffv1-stage-plan",
                  absent_before=True,absent_after=True,opened=False,hashed=False)
    guards=[row for row in manifest["absence_guards"]
            if (row.get("root"),row.get("relative_path"))==("observation",FFV1_PLAN_PATH)]
    require(guards==[expected],"ffv1-plan-absence-not-bilateral")
    return manifest

def source_contract_policy(tests):
    status = String(values=("PASS", "PASS_WITH_OMISSIONS", "FAIL", "UNKNOWN", "NOT_RUN"))
    counts = O(terminal_status=String(values=("OK", "FAILED")), tests_run=N,
        passed=Nullable(N), failures=N, errors=N, skipped=N, expected_failures=N,
        unexpected_successes=N)
    log = O(file=String(64, pattern=r"contracts-[0-9]{2}-(?:stdout|stderr)\.log"), sha256=SHA)
    step = O(entry_point=String(values=tuple("dotted:tooling.tests." + name for name in tests)),
        command=Array(String(4096), 32, 6), status=status,
        exit_code=optional(Number(-2**31, 2**31 - 1, integer=True)),
        duration_seconds=optional(Number(0, 86400)), stdout=optional(log), stderr=optional(log),
        counts=optional(counts), summary_error=optional(String(2048)),
        reason=optional(Literal("prior test process timed out; no retry")))
    return O(schema=Literal("motionwright.unittest-execution/1"), executed_source_sha=SHA40,
        tree_sha=SHA40, started_at_utc=String(64), completed_at_utc=String(64),
        runner_sha256=SHA, python_executable_sha256=SHA,
        python_environment=Literal("-B -E -s: no bytecode writes, inherited Python overrides or user site"),
        log_representation=Literal("Original stdout/stderr bytes; decoding and newline normalization only for summary parsing"),
        test_definition_sha256=Object({"tooling/tests/" + name + ".py": optional(SHA) for name in tests}),
        platform=String(1024), python=String(4096),
        source_locks=O(**{name: optional(SHA) for name in
            ("Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json")}),
        github=O(**{name: String(2048) for name in
            ("GITHUB_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB")}),
        require_complete=bool,
        scope=Literal("Selected unittest entry points; not installed/native/human acceptance"),
        steps=Array(step, len(tests), 1), source_postcheck_error=optional(String(4096)),
        exit_code=Number(0, 255, integer=True), status=status)


def policies(scenario):
    import native_aac_clock_custody as clock_custody
    paths = FILM_SOURCE_PATHS if scenario == "film180-g2" else SOURCE_PATHS
    source = O(context=FOUR_CONTEXT, tree=SHA40, files=Object({p:Field(SHA) for p in paths}))
    return dict(mlt_timeline=TIMELINE, mlt_master=MLT_MASTER, mlt_input=MLT_INPUT,
        mlt_terminal=MLT_TERMINAL, mlt_final=MLT_FINAL, mlt_content=MLT_CONTENT,
        decoded_stereo=MIDSTREAM, mlt_result=RESULT, live_mlt=LIVE, consumer_snapshot=CONSUMER_SNAPSHOT,
        compiled_mlt=COMPILED, installation=INSTALLATION, official_runtime=OFFICIAL, packets=PACKETS,
        aac_clock_carrier=clock_custody.carrier_policy(),
        ffv1_stage_plan=FFV1_PLAN,
        ffv1_seed_capture=FFV1_SEED_CAPTURE,
        ffv1_input_custody=FFV1_INPUT_CUSTODY,
        ffv1_host_terminal=FFV1_HOST_TERMINAL,
        ffv1_process_terminal=FFV1_PROCESS_TERMINAL,
        ffv1_process_custody=FFV1_PROCESS_CUSTODY,
        mlt_source_contracts=source_contract_policy(SOURCE_CONTRACT_TESTS),
        film_source_contracts=source_contract_policy(FILM_CONTRACT_TESTS),
        owner_custody=OWNER,owner_terminal=OWNER_TERMINAL,
        source_binding=source, producer_exit=O(context=FOUR_CONTEXT, exit_code=Number(0,255,integer=True),native_operation_retried=FALSE),
        producer_started=O(context=FOUR_CONTEXT, automatic_retry=FALSE, producer_uid=POS,
            command=String(values=("tooling/native_mlt_sequence_e2e.py --g2-inputs","tooling/native_film180_original_g2.py --g2-inputs"))))


def checked_context(current, scenario):
    require(scenario in ("repaired-g2", "film180-g2"), "native-scenario")
    core.project(current, FOUR_CONTEXT, exact=True)
    value = {**current, "scenario": scenario}
    core.context(value)
    return value


def relative(path):
    core.project(path, RELATIVE, exact=True)
    parts = core.member_path(path)
    require(not core.excluded(parts), "native-path-excluded")
    return path


def owner_output(path):
    return "output/" + relative(path)


def common_contract():
    result = {}
    for role in ("original-c", "repaired-c", "compiled-module", "original-module", "compiler-receipt",
                 "official-runtime", "installation", "source-before", "source-before-producer", "source-after", "producer-exit", "producer-started", "owner-custody", "owner-terminal", "mlt-source-contracts"):
        result[("single", "attempt-custody", role)] = (1,1)
    result[("comparison", "persistence", "database")] = (1,1)
    result[("comparison", "persistence", "database-wal")] = (0,1)
    result[("comparison", "persistence", "database-shm")] = (0,1)
    result[("comparison", "persistence", "cas-source")] = (1,1)
    return result


def mlt_contract(*, seed_diagnostics=False):
    require(type(seed_diagnostics) is bool,"ffv1-seed-role-contract-mode")
    result = common_contract()
    if seed_diagnostics:
        for role in ("ffv1-seed-stdout","ffv1-seed-stderr","ffv1-seed-terminal"):
            result[("original","attempt-custody",role)]=(0,1)
    result[("original","attempt-custody","ffv1-stage-plan")]=(0,1)
    for family in ("host","catalog"):
        for member in ("stdin","stdout","stderr","terminal"):
            result[("original","attempt-custody","ffv1-"+family+"-"+member)]=(0,2)
    for member in ("stdout","stderr","terminal"):
        result[("original","attempt-custody","ffv1-process-"+member)]=(0,12)
    result[("original","attempt-custody","ffv1-process-custody")]=(0,2)
    for role in ("timeline-receipt", "native-master-receipt", "native-timeline", "video-only-copy", "native-master",
                 "producer-master-copy", "original-master-copy", "source-wav", "producer-source-copy",
                 "native-decoded-wav", "decoded-pcm", "source-content", "decoded-stereo", "packets", "result", "audio-clock-carrier"):
        result[("original", "readback", role)] = (1,1)
    result[("original", "readback", "source-segment")] = (2,2)
    for role in ("input-custody", "native-terminal", "final-custody", "live-module", "consumer-snapshot", "consumer-original"):
        result[("original", "attempt-custody", role)] = (1,1)
    return result


class Selection:
    """One attempt, including failure. A second capture is never a recovery."""
    def __init__(self, *, roots, current, scenario, source_bindings, role_contract, json_policies,
                 diagnostic_admission=None, ffv1_stage_plan_absent=False):
        self.context = checked_context(current, scenario)
        expected = FILM_SOURCE_PATHS if scenario == "film180-g2" else SOURCE_PATHS
        require(type(source_bindings) is list and {r.get("path") for r in source_bindings} == set(expected)
                and len(source_bindings) == len(expected), "native-source-closure")
        self.roots = {k:str(v) for k,v in roots.items()}
        require(set(self.roots) <= {"source", "control", "observation", "owner", "contracts", "film-contracts"}
                and {"source", "control"} <= set(self.roots), "native-roots")
        require(scenario=="repaired-g2" or diagnostic_admission is None,
                "ffv1-diagnostics-on-film")
        self.admission=(None if diagnostic_admission is None else
            checked_ffv1_admission(diagnostic_admission,current=current,
                                   source_bindings=source_bindings))
        require(type(ffv1_stage_plan_absent) is bool
                and (not ffv1_stage_plan_absent or (scenario=="repaired-g2"
                    and self.admission is None and "observation" in self.roots)),
                "ffv1-plan-absence-mode")
        self.ffv1_stage_plan_absent=ffv1_stage_plan_absent
        self.bindings = source_bindings
        self.contract = role_contract
        self.policies = json_policies
        self.members, self.absence, self.cache = [], [], {}
        self.attempted = False
        # Called by the pre-effect source guard too; capture repeats physical
        # source verification before and after every selected payload.
        core.verify_source_bindings(self.roots["source"], current["source_sha"], source_bindings)

    def add(self, handles, root, path, role, layer="readback", phase="original", policy=None,
            identity=None, kind=None, maximum=None, diagnostic_type=None):
        relative(path)
        actual = (root, path)
        require(actual not in self.cache, "native-duplicate-selected-path")
        key = (phase, layer, role)
        require(key in self.contract, "native-role-unregistered")
        kind = kind or ("json-projection" if policy else "original")
        row = dict(root=root, relative_path=path, role=role, layer=layer, phase=phase,
                   kind=kind, required=diagnostic_type is not None or self.contract[key][0] > 0,
                   expected_sha256=None, bytes=None, policy=policy)
        if diagnostic_type is not None:
            require(self.admission is not None and kind in core.DIAGNOSTIC_KINDS,
                    "ffv1-diagnostic-admission-required")
            row["diagnostic_type"]=diagnostic_type
        value, raw = None, None
        if root in self.roots:
            try:
                raw, _mark = handles.read(root, core.member_path(path), maximum or (core.MAX_JSON if policy else core.MAX_FILE))
                if identity is not None:
                    require(sha(raw) == identity[0] and (identity[1] is None or len(raw) == identity[1]), "native-linked-original-changed")
                if policy:
                    value = core.project(core.strict(raw), self.policies[policy], exact=kind in ("json-original","diagnostic-json-original"))
                row.update(expected_sha256=sha(raw), bytes=len(raw))
            except (FileNotFoundError, OSError, ValueError, TypeError, core.SelectionError):
                raw, value = None, None
        else:
            # No missing root is created or probed. Its mandatory logical role
            # receives an unobserved placeholder in the existing private root.
            row.update(root="control", relative_path="unobserved/"+phase+"/"+role+"-"+str(len(self.members))+(".json" if policy else ".bin"))
        self.members.append(row)
        self.cache[actual] = (value, raw, row)
        return value

    def blob(self, handles, root, path, role, *, phase="original", layer="readback", identity=None):
        return self.add(handles,root,path,role,layer,phase,identity=identity)

    def derived_blob(self, handles, value, path, role, *, phase="original", layer="readback", root="owner"):
        if value is None:
            self.add(handles,root,"unobserved/"+phase+"/"+role+"-"+str(len(self.members))+".bin",role,layer,phase)
            return
        selected = value
        for key in path:
            selected = selected[key]
        self.add(handles,root,owner_output(selected["path"]),role,layer,phase,
                 identity=(selected["sha256"],selected["bytes"]))

    def database(self, handles, name, digest):
        base = "motionwright-data/" + name
        self.blob(handles,"owner",base,"database",phase="comparison",layer="persistence")
        for suffix,role in (("-wal","database-wal"),("-shm","database-shm")):
            path = base + suffix
            if "owner" not in self.roots:
                continue
            try:
                handles.stat_member("owner",core.member_path(path))
            except FileNotFoundError:
                self.absence.append(dict(root="owner",relative_path=path,phase="comparison",layer="persistence",role=role))
            except (OSError,core.SelectionError):
                self.add(handles,"owner",path,role,"persistence","comparison")
            else:
                self.add(handles,"owner",path,role,"persistence","comparison")
        require(digest is None or re.fullmatch(r"[0-9a-f]{64}",digest), "native-cas-digest")
        cas = ("motionwright-data/blobs/sha256/"+digest[:2]+"/"+digest) if digest else "unobserved/cas-source.bin"
        self.add(handles,"owner",cas,"cas-source","persistence","comparison",identity=None if digest is None else (digest,None))

    def common(self, handles):
        self.add(handles, "contracts", "contracts.json", "mlt-source-contracts",
                 "attempt-custody", "single", "mlt_source_contracts", kind="json-original")
        if self.context["scenario"] == "film180-g2":
            self.add(handles, "film-contracts", "contracts.json", "film-source-contracts",
                     "attempt-custody", "single", "film_source_contracts", kind="json-original")
        rows = (
            ("intake/original_c_source","original-c",None,(ORIGINAL_C,96690)),
            ("intake/repaired_c_source","repaired-c",None,(REPAIRED_C,99266)),
            ("intake/compiled_module","compiled-module",None,(REPAIRED_ELF,719856)),
            ("handoff/original_system_mlt_avformat.so","original-module",None,None),
            ("intake/compiler_receipt_path","compiler-receipt","compiled_mlt",(COMPILER,None)),
            ("intake/official-runtime-receipt.json","official-runtime","official_runtime",None),
            ("handoff/installation.json","installation","installation",None),
            ("records/source-before.json","source-before","source_binding",None),
            ("records/source-before-producer.json","source-before-producer","source_binding",None),
            ("records/source-after.json","source-after","source_binding",None),
            ("records/producer-exit.json","producer-exit","producer_exit",None),
            ("records/producer-started.json","producer-started","producer_started",None),
        )
        for path,role,policy,identity in rows:
            self.add(handles,"control",path,role,"attempt-custody","single",policy,identity)
        self.add(handles,"observation","owner-workspace.json","owner-custody","attempt-custody","single","owner_custody")
        self.add(handles,"observation","owner-workspace-terminal.json","owner-terminal","attempt-custody","single","owner_terminal")

    def live(self, handles, value, *, phase="original", directory="", wrapped=False):
        proof = value.get("original_live_pid_observer_receipt") if value and wrapped else value
        if proof:
            basename = "original-melt-consumer-"+str(proof["pid"])+"-"+str(proof["process_start_ticks"])
            identity = (proof["published_output_sha256"],proof["published_output_bytes"])
        else:
            basename, identity = "unobserved-consumer", None
        prefix = directory + "/" if directory else ""
        self.add(handles,"observation",prefix+basename+".json","consumer-snapshot","attempt-custody",phase,"consumer_snapshot")
        self.add(handles,"observation",prefix+basename+".mp4","consumer-original","attempt-custody",phase,identity=identity)

    def optional_ffv1_original(self, handles, root, path, role, *, policy=None, maximum,
                               identity=None, diagnostic_type=None):
        # Match the existing SQLite-companion absence contract: an absent
        # optional member is a two-sided namespace guard, not a failed row.
        # Stat errors and present-but-invalid originals remain failed rows.
        if root not in self.roots:
            return None
        try:
            handles.stat_member(root,core.member_path(path))
        except FileNotFoundError:
            self.absence.append(dict(root=root,relative_path=path,phase="original",
                                     layer="attempt-custody",role=role))
            return None
        except (OSError,core.SelectionError):
            pass
        kind=("diagnostic-json-original" if policy else "diagnostic-original") if diagnostic_type else (
            "json-original" if policy else "original")
        return self.add(handles,root,path,role,"attempt-custody","original",policy,
                         kind=kind,maximum=maximum,identity=identity,diagnostic_type=diagnostic_type)

    def collect_ffv1_diagnostics(self, handles):
        if self.ffv1_stage_plan_absent:
            # Freeze the absence guard, even if the file appeared since admission.
            # Core observes both sides and invalidates that late appearance.
            self.absence.append(dict(root="observation",relative_path=FFV1_PLAN_PATH,
                phase="original",layer="attempt-custody",role="ffv1-stage-plan"))
            return
        expected=None if self.admission is None else self.admission["stage_plan"]
        if self.admission is not None:
            # The three fixed seed carriers have their own admitted subgroup.
            # They remain original bytes in the existing native capsule.
            for entry in self.admission["seed_diagnostic_contract"]["entries"]:
                self.optional_ffv1_original(handles,entry["root"],entry["relative_path"],entry["role"],
                    policy=entry["policy"],maximum=entry["maximum"],diagnostic_type=entry["type"])
        identity=None if expected is None else (
            expected["original"]["sha256"],expected["original"]["bytes"])
        plan=self.optional_ffv1_original(handles,"observation",FFV1_PLAN_PATH,"ffv1-stage-plan",
                                         policy="ffv1_stage_plan",maximum=65536,identity=identity)
        if expected is None:
            require(plan is None,"ffv1-diagnostic-admission-required")
            return
        if plan is None or ffv1_plan_identity(plan)!=expected["identity"]:
            # Only the independently admitted destinations are reserved. A changed
            # plan cannot authorize any payload read, even within an owner root.
            cached=self.cache.get(("observation",FFV1_PLAN_PATH))
            if cached is not None:
                cached[2].update(expected_sha256=None,bytes=None,required=True)
            for entry in self.admission["diagnostic_contract"]["entries"]:
                row={key:entry[key] for key in ("root","relative_path","phase","layer","role","policy")}
                row.update(kind="diagnostic-json-original" if entry["type"]=="typed-terminal"
                           else "diagnostic-original",diagnostic_type=entry["type"],
                           required=True,expected_sha256=None,bytes=None)
                self.members.append(row)
                self.cache[(entry["root"],entry["relative_path"])]=(None,None,row)
            return
        self.cache[("observation",FFV1_PLAN_PATH)][2]["required"]=True
        for entry in self.admission["diagnostic_contract"]["entries"]:
            self.optional_ffv1_original(handles,entry["root"],entry["relative_path"],entry["role"],
                policy=entry["policy"],maximum=entry["maximum"],diagnostic_type=entry["type"])

    def collect_mlt(self, handles):
        self.common(handles)
        self.collect_ffv1_diagnostics(handles)
        fixed = (
            ("producer/native-multisegment-timeline-evidence.json","timeline-receipt","mlt_timeline"),
            ("producer/native-multisegment-av-master-evidence.json","native-master-receipt","mlt_master"),
            ("source-content.json","source-content","mlt_content"),
            ("producer/decoded-stereo-content-proof.json","decoded-stereo","decoded_stereo"),
            ("original-master-packets.json","packets","packets"),
            ("producer/result.json","result","mlt_result"),
        )
        docs = {role:self.add(handles,"observation",path,role,policy=policy) for path,role,policy in fixed}
        self.add(handles,"observation","original-aac-clock-capture.json","audio-clock-carrier",
                 policy="aac_clock_carrier",kind="json-original",maximum=5*1024*1024)
        for path,role,policy in (("input-custody.json","input-custody","mlt_input"),
                ("native-terminal.json","native-terminal","mlt_terminal"),("final-custody.json","final-custody","mlt_final"),
                ("original-live-module.json","live-module","live_mlt")):
            if role=="input-custody" and self.admission is not None:
                docs[role]=self.add(handles,"observation",path,role,"attempt-custody","original",
                    "ffv1_input_custody",kind="json-original",maximum=262144)
            else:
                docs[role] = self.add(handles,"observation",path,role,"attempt-custody","original",policy)
        timeline, master = docs["timeline-receipt"], docs["native-master-receipt"]
        for i in range(2):
            segment = timeline["source"]["verified_video_segments"][i] if timeline else None
            path = owner_output(segment["artifact_path"]) if segment else "unobserved/segment-"+str(i)+".mkv"
            identity = (segment["artifact_sha256"],segment["artifact_bytes"]) if segment else None
            self.blob(handles,"owner",path,"source-segment",identity=identity)
        timeline_identity = None if timeline is None else (timeline["artifact_sha256"],timeline["artifact_bytes"])
        self.blob(handles,"owner",owner_output(timeline["artifact_path"]) if timeline else "unobserved/native-timeline.mkv",
                  "native-timeline",identity=timeline_identity)
        self.blob(handles,"observation","producer/native-two-segment-video-only.mkv","video-only-copy",identity=timeline_identity)
        self.derived_blob(handles,master,("master","artifact"),"native-master")
        self.derived_blob(handles,master,("decoded_audio",),"native-decoded-wav")
        identity = (master["master"]["artifact"]["sha256"],master["master"]["artifact"]["bytes"]) if master else None
        self.blob(handles,"observation","producer/native-multisegment-master.mp4","producer-master-copy",identity=identity)
        self.blob(handles,"observation","original-native-master.mp4","original-master-copy",identity=identity)
        for path,role in (("original-source.wav","source-wav"),("producer/native-master-source.wav","producer-source-copy")):
            self.blob(handles,"observation",path,role,identity=(REFERENCE_WAV,211244))
        proof = docs["source-content"]
        self.blob(handles,"observation","original-master-decoded.s16le","decoded-pcm",identity=None if proof is None else (proof["decoded_pcm_sha256"],None))
        self.live(handles,docs["live-module"])
        self.database(handles,"motionwright.sqlite3",REFERENCE_WAV)

    def capture(self, collect=None):
        require(not self.attempted,"native-selection-repeat-refused")
        self.attempted = True
        handles = core.RootHandles(self.roots)
        try:
            (collect or self.collect_mlt)(handles)
        finally:
            handles.close()
        exclusions = []
        for root,paths in {"control":("logs",),"owner":("runtime","state","home","config","bin","scratch"),
                           "observation":("observer-diagnostic.json",)}.items():
            if root in self.roots:
                exclusions.extend(dict(root=root,relative_path=p,reason="outside-reviewed-scope") for p in paths)
        spec = dict(schema=core.SPEC_SCHEMA_V3 if self.admission is not None else core.SPEC_SCHEMA,
                    context=self.context, roots=self.roots, members=self.members,
                    absence_guards=self.absence, exclusions=exclusions, source_bindings=self.bindings,
                    origin=dict(kind="generated-first-party-fixture",fixture_id=self.context["scenario"],
                        producer_paths=["tooling/native_mlt_sequence_e2e.py"],private_data=True,
                        upload_authorization="NOT_GRANTED_BY_COLLECTOR"))
        return core.capture_selected_originals(spec,policies=self.policies,role_contract=self.contract,
            diagnostic_contract=None if self.admission is None else self.admission["capture_diagnostic_contract"])


class Intake:
    """Authenticated memory-only readback; never extracts or opens SQLite."""
    def __init__(self, raw, current, scenario, contract, json_policies, *,
                 expected_diagnostic_admission=None, require_complete=True):
        require(type(require_complete) is bool,"native-intake-completeness-mode")
        require(require_complete or (scenario=="repaired-g2"
                and expected_diagnostic_admission is not None),"native-partial-needs-external-v3")
        self.context = checked_context(current,scenario)
        require(scenario=="repaired-g2" or expected_diagnostic_admission is None,
                "ffv1-diagnostics-on-film")
        self.admission=(None if expected_diagnostic_admission is None else
            checked_ffv1_admission(expected_diagnostic_admission,current=current))
        self.manifest = core.inspect_selected_tar(raw,expected_context=self.context,
            role_contract=contract,policies=json_policies,
            expected_diagnostic_contract=None if self.admission is None
                else self.admission["capture_diagnostic_contract"],require_complete=require_complete)
        self.data = {}
        with tarfile.open(fileobj=io.BytesIO(raw),mode="r:") as archive:
            for row in self.manifest["members"]:
                if row["state"] == "INCLUDED":
                    payload = archive.extractfile(row["member"]).read()
                    value = (payload if row["kind"] in ("original","diagnostic-original")
                             else core.strict(payload))
                    if row["kind"] == "json-projection": value = value["value"]
                    self.data.setdefault((row["phase"],row["layer"],row["role"]),[]).append((row,value,payload))
        self.contract = contract
        if self.admission is not None:
            self.verify_ffv1_admission()

    def verify_ffv1_admission(self):
        expected=self.admission
        require(sorted(self.manifest["source_bindings"],key=lambda row:row["path"])
                ==expected["source_bindings"],"ffv1-intake-source-bindings")
        row,plan,raw=self.one("ffv1-stage-plan","original","attempt-custody")
        identity=expected["stage_plan"]["original"]
        require((row["root"],row["relative_path"],row["kind"],row["policy"])
                ==("observation",FFV1_PLAN_PATH,"json-original","ffv1_stage_plan")
                and row["original_bytes"] is True
                and (sha(raw),len(raw))==(identity["sha256"],identity["bytes"])
                and ffv1_plan_identity(plan)==expected["stage_plan"]["identity"]
                and ffv1_contract_for_plan(plan,{key:self.context[key] for key in (
                    "source_sha","semwright_sha","run_id","run_attempt")})==expected["diagnostic_contract"],
                "ffv1-intake-original-plan")
        input_row,inputs,input_raw=self.one("input-custody","original","attempt-custody")
        identity=expected["input_custody"]
        require((input_row["root"],input_row["relative_path"],input_row["kind"],input_row["policy"])
                ==("observation","input-custody.json","json-original","ffv1_input_custody")
                and input_row["original_bytes"] is True
                and (sha(input_raw),len(input_raw))==(identity["sha256"],identity["bytes"]),
                "ffv1-intake-original-input")
        originals={}
        for name,role,path,kind,policy in (
            ("seed_stdout","ffv1-seed-stdout",FFV1_SEED_STDOUT_PATH,"diagnostic-original",None),
            ("seed_stderr","ffv1-seed-stderr",FFV1_SEED_STDERR_PATH,"diagnostic-original",None),
            ("seed_terminal","ffv1-seed-terminal",FFV1_SEED_TERMINAL_PATH,
             "diagnostic-json-original","ffv1_seed_capture"),
        ):
            seed_row,seed_value,seed_raw=self.one(role,"original","attempt-custody")
            identity=expected[name]
            require((seed_row["root"],seed_row["relative_path"],seed_row["kind"],seed_row["policy"])
                    ==("observation",path,kind,policy) and seed_row["original_bytes"] is True
                    and (sha(seed_raw),len(seed_raw))==(identity["sha256"],identity["bytes"]),
                    "ffv1-intake-original-seed")
            originals[name]=(seed_value,seed_raw)
        seed=core.project(core.strict(originals["seed_stdout"][1]),FFV1_SEED,exact=True)
        terminal=originals["seed_terminal"][0]
        require(seed["resource"]=="project:"+seed["project_id"]
                and dict(project_resource=seed["resource"],generation=seed["generation"],
                         revision=seed["revision"],deliverable_id=seed["master_profile_id"])
                    ==expected["fixture_identity"],"ffv1-intake-seed-fixture-identity")
        require(terminal["context"]==self.context and terminal["exit_code"]==0
                and type(terminal["exit_code"]) is int and not terminal["deadline_exceeded"]
                and terminal["error_class"] is None and terminal["error"] is None,
                "ffv1-intake-seed-terminal")
        for channel,name,path in (("stdout","seed_stdout",FFV1_SEED_STDOUT_PATH),
                                  ("stderr","seed_stderr",FFV1_SEED_STDERR_PATH)):
            stream=terminal[channel];payload=originals[name][1]
            require(stream["relative_path"]==path and stream["retained"] is True
                    and stream["prefix_truncated"] is False
                    and (stream["sha256"],stream["bytes"])==(sha(payload),len(payload))
                    and (stream["returned_buffer_sha256"],stream["returned_buffer_bytes"])
                        ==(sha(payload),len(payload)),"ffv1-intake-seed-stream-closure")
        require((inputs["current_motionwright_sha"],inputs["semwright_sha"],
                 inputs["run_id"],inputs["run_attempt"])
                ==tuple(self.context[key] for key in ("source_sha","semwright_sha","run_id","run_attempt"))
                and inputs["original_source"]["original_wav_sha256"]==REFERENCE_WAV,
                "ffv1-intake-input-fixture-context")

    def all(self,role,phase="original",layer="readback"):
        return self.data.get((phase,layer,role),[])

    def one(self,role,phase="original",layer="readback"):
        rows=self.all(role,phase,layer)
        require(len(rows)==1,"native-one-role")
        return rows[0]

    def value(self,role,phase="original",layer="readback"):
        return self.one(role,phase,layer)[1]

    def original(self,role,phase="original",layer="readback"):
        row,value,_ = self.one(role,phase,layer)
        require(row["kind"] in ("original","json-original","diagnostic-original","diagnostic-json-original") and row["original_bytes"] is True,"native-original-bytes-required")
        return value

    def linked(self,role,identity,phase="original",layer="readback"):
        row,_value,_raw = self.one(role,phase,layer)
        require(row["kind"] in ("original","json-original","diagnostic-original","diagnostic-json-original") and row["original_bytes"] is True,"native-linked-original-bytes")
        require((row["expected_sha256"],row["bytes"])==identity,"native-linked-identity")
        return row

    def layout(self,role,root,path,phase="original",layer="readback"):
        row,_,_ = self.one(role,phase,layer)
        require((row["root"],row["relative_path"])==(root,path),"native-role-layout")

    def common(self):
        for role,path in (("source-before","records/source-before.json"),("source-before-producer","records/source-before-producer.json"),("source-after","records/source-after.json")):
            self.layout(role,"control",path,"single","attempt-custody")
        before=self.value("source-before","single","attempt-custody")
        require(before==self.value("source-before-producer","single","attempt-custody")
                ==self.value("source-after","single","attempt-custody"),"native-source-before-after")
        require(before["context"]=={k:self.context[k] for k in ("source_sha","semwright_sha","run_id","run_attempt")},"native-source-run-binding")
        require(before["files"]=={r["path"]:r["sha256"] for r in self.manifest["source_bindings"]},"native-source-file-closure")
        reports = [("mlt-source-contracts", "contracts")]
        if self.context["scenario"] == "film180-g2":
            reports.append(("film-source-contracts", "film-contracts"))
        for role, root in reports:
            self.layout(role, root, "contracts.json", "single", "attempt-custody")
            report = self.original(role, "single", "attempt-custody")
            require(report["executed_source_sha"] == self.context["source_sha"]
                    and report["tree_sha"] == before["tree"]
                    and report["runner_sha256"] == before["files"]["tooling/ci_test_runner.py"]
                    and report["github"]["GITHUB_SHA"] == self.context["source_sha"]
                    and report["github"]["GITHUB_RUN_ID"] == self.context["run_id"]
                    and report["github"]["GITHUB_RUN_ATTEMPT"] == "1", "native-source-contract-identity")
            # This is an independent intake prerequisite, not a new gate PASS.
            # Failed or uncertain originals remain in the sealed selection.
            require(report["status"] == "PASS" and report["exit_code"] == 0
                    and report["require_complete"] is True
                    and "source_postcheck_error" not in report, "native-source-contract-not-passed")
            definitions = report["test_definition_sha256"]
            entries = [step["entry_point"] for step in report["steps"]]
            require(len(entries) == len(set(entries)) == len(definitions)
                    and set(entries) == {"dotted:" + path[:-3].replace("/", ".")
                                         for path in definitions}, "native-source-contract-roster")
            for step in report["steps"]:
                counts = step.get("counts", {})
                require(step["status"] == "PASS" and step.get("exit_code") == 0
                        and counts.get("terminal_status") == "OK"
                        and type(counts.get("tests_run")) is int and counts["tests_run"] > 0
                        and counts.get("passed") == counts["tests_run"]
                        and all(counts.get(key) == 0 for key in
                            ("errors", "failures", "skipped", "expected_failures", "unexpected_successes"))
                        and "summary_error" not in step, "native-source-contract-incomplete")
        exit_receipt=self.value("producer-exit","single","attempt-custody")
        require(exit_receipt["context"]==before["context"] and exit_receipt["exit_code"]==0,"native-producer-terminal")
        owner=self.value("owner-custody","single","attempt-custody")
        owner_terminal=self.value("owner-terminal","single","attempt-custody")
        started=self.value("producer-started","single","attempt-custody")
        require(owner_terminal["workspace"]==owner and started["context"]==before["context"]
                and started["producer_uid"]==owner["uid"]
                and (owner["motionwright_sha"],owner["semwright_sha"],owner["run_id"],owner["run_attempt"])
                ==tuple(self.context[k] for k in ("source_sha","semwright_sha","run_id","run_attempt")),"native-owner-run-identity")
        require(started["command"]==("tooling/native_film180_original_g2.py --g2-inputs" if self.context["scenario"]=="film180-g2"
                else "tooling/native_mlt_sequence_e2e.py --g2-inputs"),"native-original-producer-command")
        self.owner=owner
        for role,root,path in (
            ("original-c","control","intake/original_c_source"),("repaired-c","control","intake/repaired_c_source"),
            ("compiled-module","control","intake/compiled_module"),("original-module","control","handoff/original_system_mlt_avformat.so"),
            ("compiler-receipt","control","intake/compiler_receipt_path"),("official-runtime","control","intake/official-runtime-receipt.json"),
            ("installation","control","handoff/installation.json"),("producer-exit","control","records/producer-exit.json"),
            ("producer-started","control","records/producer-started.json"),("owner-custody","observation","owner-workspace.json"),
            ("owner-terminal","observation","owner-workspace-terminal.json")):
            self.layout(role,root,path,"single","attempt-custody")
        compiled=self.value("compiler-receipt","single","attempt-custody")
        install=self.value("installation","single","attempt-custody")
        official=self.value("official-runtime","single","attempt-custody")
        for role,digest,size in (("original-c",ORIGINAL_C,96690),("repaired-c",REPAIRED_C,99266),("compiled-module",REPAIRED_ELF,719856)):
            raw=self.original(role,"single","attempt-custody")
            require(sha(raw)==digest and len(raw)==size,"native-runtime-original-bytes")
        require(compiled["original_source_sha256"]==ORIGINAL_C and compiled["repaired_source_sha256"]==REPAIRED_C
                and compiled["compiled_source_sha256"]==REPAIRED_C and compiled["compiled_module_sha256"]==REPAIRED_ELF
                and compiled["compiled_module_bytes"]==719856
                and compiled["compiled_consumer_object_sha256"]==compiled["linker_consumer_object_sha256"],"native-compiler-closure")
        require(self.one("compiler-receipt","single","attempt-custody")[0]["expected_sha256"]==COMPILER,"native-compiler-original-receipt")
        require(install["original_MLT_source_sha256"]==ORIGINAL_C and install["repaired_MLT_source_sha256"]==REPAIRED_C
                and install["compiled_module_sha256"]==install["actual_installed_module_sha256"]==REPAIRED_ELF
                and install["compiled_module_bytes"]==719856 and install["compiler_receipt_sha256"]==COMPILER,"native-installation-closure")
        old=self.original("original-module","single","attempt-custody")
        require(sha(old)==install["original_module_sha256"]==install["original_backup_sha256"]
                ==official["runtime_bindings"]["original_module"]["sha256"]
                and len(old)==install["original_module_bytes"],"native-backup-original-module")
        require(old.startswith(b"\x7fELF") and self.original("compiled-module","single","attempt-custody").startswith(b"\x7fELF"),"native-elf-bytes")
        database=self.original("database","comparison","persistence")
        require(database.startswith(b"SQLite format 3\0"),"native-database-bytes")
        dbrow=self.one("database","comparison","persistence")[0]
        require(dbrow["root"]=="owner","native-database-root")
        for suffix,role in (("-wal","database-wal"),("-shm","database-shm")):
            rows=self.all(role,"comparison","persistence")
            guards=[g for g in self.manifest["absence_guards"] if (g["phase"],g["layer"],g["role"])==("comparison","persistence",role)]
            require(len(rows)+len(guards)==1,"native-sqlite-companion-custody")
            entry=rows[0][0] if rows else guards[0]
            require(entry["root"]=="owner" and entry["relative_path"]==dbrow["relative_path"]+suffix,"native-sqlite-companion-layout")
        return official,install

    def live(self,phase,master_sha,master_bytes,official,*,wrapped=False,directory=""):
        value=self.value("live-module",phase,"attempt-custody")
        proof=value["original_live_pid_observer_receipt"] if wrapped else value
        if wrapped:
            require(value["actual_mapped_original_MLT_module_sha256"]==REPAIRED_ELF,"native-loaded-wrapper-module")
        observed_owner=proof["owner_contract"]["owner_identity"]
        require((observed_owner[0],observed_owner[1],observed_owner[3],observed_owner[4])
                ==tuple(self.owner[k] for k in ("device","inode","uid","gid")),"native-live-owner-root-identity")
        loaded=proof["live_module"]
        require(loaded["actual_live_melt_pid"]==proof["pid"] and loaded["original_repaired_avformat_module_sha256"]==REPAIRED_ELF
                and loaded["original_melt_sha256"]==official["runtime_bindings"]["melt"]["sha256"],"native-live-module-closure")
        require(proof["runtime_parent"]["sha256"]==proof["owner_contract"]["runner_sha256"]
                and proof["owner_output_identity"]==proof["owner_contract"]["output_identity"]
                and proof["original_output_file_identity"]==proof["source_descriptor_readback"]["file_identity"],"native-live-parent-descriptor")
        snapshot=self.value("consumer-snapshot",phase,"attempt-custody")
        expected=(master_sha,master_bytes)
        require((proof["published_output_sha256"],proof["published_output_bytes"])==expected
                ==(snapshot["sha256"],snapshot["bytes"])
                ==(proof["source_descriptor_readback"]["sha256"],proof["source_descriptor_readback"]["bytes"]),"native-open-descriptor-publication")
        require(snapshot["process_identity"]==[proof["pid"],proof["process_start_ticks"]]
                and snapshot["file_identity"]==proof["original_output_file_identity"],"native-consumer-process-identity")
        basename="original-melt-consumer-"+str(proof["pid"])+"-"+str(proof["process_start_ticks"])
        prefix=directory+"/" if directory else ""
        for role,suffix in (("consumer-snapshot",".json"),("consumer-original",".mp4")):
            self.layout(role,"observation",prefix+basename+suffix,phase,"attempt-custody")
        raw=self.original("consumer-original",phase,"attempt-custody")
        require((sha(raw),len(raw))==expected,"native-consumer-original-bytes")
        return proof


def decoded_wav_shape(original, expected_frames):
    """Inspect selected native decoded WAV bytes, independently of its receipt."""
    require(type(original) is bytes and type(expected_frames) is int and expected_frames>0,
            "native-decoded-wav-shape")
    try:
        with wave.open(io.BytesIO(original),"rb") as stream:
            require((stream.getnchannels(),stream.getsampwidth(),stream.getframerate(),
                     stream.getnframes(),stream.getcomptype())==(2,2,48000,expected_frames,"NONE"),
                    "native-decoded-wav-shape")
            require(len(stream.readframes(expected_frames+1))==expected_frames*4,
                    "native-decoded-wav-truncated")
    except (wave.Error,EOFError):
        require(False,"native-decoded-wav-invalid")
    return expected_frames


def pcm_shape(wav, pcm, expected_frames):
    require(type(wav) is bytes and type(pcm) is bytes and len(pcm)>0 and len(pcm)%4==0,"native-pcm-bytes")
    try:
        with wave.open(io.BytesIO(wav),"rb") as stream:
            require((stream.getnchannels(),stream.getsampwidth(),stream.getframerate(),stream.getnframes(),stream.getcomptype())
                    ==(2,2,48000,expected_frames,"NONE"),"native-source-wav-shape")
            frames=stream.readframes(expected_frames+1)
            require(len(frames)==expected_frames*4,"native-source-wav-truncated")
    except (wave.Error,EOFError):
        require(False,"native-source-wav-invalid")
    return len(pcm)//4-expected_frames


def verify_mlt_intake(raw, *, current, expected_diagnostic_admission=None):
    intake=Intake(raw,current,"repaired-g2",
                  mlt_contract(seed_diagnostics=expected_diagnostic_admission is not None),
                  policies("repaired-g2"),expected_diagnostic_admission=expected_diagnostic_admission)
    official,install=intake.common()
    for role,path,policy in (("timeline-receipt","producer/native-multisegment-timeline-evidence.json","mlt_timeline"),
            ("native-master-receipt","producer/native-multisegment-av-master-evidence.json","mlt_master"),
            ("source-content","source-content.json","mlt_content"),("decoded-stereo","producer/decoded-stereo-content-proof.json","decoded_stereo"),
            ("packets","original-master-packets.json","packets"),("result","producer/result.json","mlt_result")):
        intake.layout(role,"observation",path)
        require(intake.one(role)[0]["policy"]==policy,"native-fixed-json-policy")
    source=intake.value("input-custody","original","attempt-custody")
    require((source["current_motionwright_sha"],source["semwright_sha"],source["run_id"],source["run_attempt"])
            ==tuple(current[k] for k in ("source_sha","semwright_sha","run_id","run_attempt")),"native-input-exact-run")
    for role,key in (("official-runtime","official_runtime_receipt_sha256"),("installation","installation_receipt_sha256")):
        require(intake.one(role,"single","attempt-custody")[0]["expected_sha256"]==source[key],"native-input-admission-receipt")
    require(source["source_compiler"]=={key:install[key] for key in COMPILER_CLOSURE.fields}
            and source["known_melt_sha256"]==source["installed_melt_alias"]["sha256"]==official["runtime_bindings"]["melt"]["sha256"]
            and source["known_original_module_sha256"]==install["original_module_sha256"],"native-input-runtime-closure")
    for role,path in (("input-custody","input-custody.json"),("native-terminal","native-terminal.json"),
            ("final-custody","final-custody.json"),("live-module","original-live-module.json")):
        intake.layout(role,"observation",path,"original","attempt-custody")
    for role,path in (("source-wav","original-source.wav"),("producer-source-copy","producer/native-master-source.wav"),
            ("decoded-pcm","original-master-decoded.s16le"),("video-only-copy","producer/native-two-segment-video-only.mkv"),
            ("producer-master-copy","producer/native-multisegment-master.mp4"),("original-master-copy","original-native-master.mp4")):
        intake.layout(role,"observation",path)
    intake.layout("database","owner","motionwright-data/motionwright.sqlite3","comparison","persistence")
    timeline=intake.value("timeline-receipt"); master=intake.value("native-master-receipt")
    require(master["video"]==timeline,"native-timeline-master-link")
    identities=[{k:d[k] for k in IDENTITY} for d in (timeline,timeline["source"],master,master["measured_voice"])]
    require(all(x==identities[0] for x in identities),"native-project-revision-link")
    if intake.admission is not None:
        plan=intake.admission["stage_plan"]["identity"]
        require({key:plan[key] for key in IDENTITY}==identities[0]
                and plan["input_sha256"]==timeline["source"]["input_sha256"],
                "ffv1-intake-native-source-identity")
    segments=timeline["source"]["verified_video_segments"]
    require([s["frame_count"] for s in segments]==[32,1] and [s["output_start_frame"] for s in segments]==[0,32],"native-two-segment-coverage")
    for index,(row,blob,_raw) in enumerate(intake.all("source-segment")):
        segment=segments[index]
        require(row["root"]=="owner" and row["relative_path"]==owner_output(segment["artifact_path"])
                and (sha(blob),len(blob))==(segment["artifact_sha256"],segment["artifact_bytes"]),"native-segment-original-binding")
    for role in ("native-timeline","video-only-copy"):
        intake.linked(role,(timeline["artifact_sha256"],timeline["artifact_bytes"]))
    intake.layout("native-timeline","owner",owner_output(timeline["artifact_path"]))
    mux=master["master"]; require(mux["video_sha256"]==timeline["artifact_sha256"] and mux["frame_count"]==33,"native-master-video-link")
    identity=(mux["artifact"]["sha256"],mux["artifact"]["bytes"])
    for role in ("native-master","producer-master-copy","original-master-copy"): intake.linked(role,identity)
    intake.layout("native-master","owner",owner_output(mux["artifact"]["path"]))
    require(master["decoded_audio"]==mux["decoded_audio"],"native-decoded-artifact-link")
    intake.linked("native-decoded-wav",(mux["decoded_audio"]["sha256"],mux["decoded_audio"]["bytes"]))
    intake.layout("native-decoded-wav","owner",owner_output(mux["decoded_audio"]["path"]))
    decoded_wav_shape(intake.original("native-decoded-wav"),mux["decoded_audio_sample_frames"])
    wave_bytes=intake.original("source-wav"); pcm=intake.original("decoded-pcm")
    delta=pcm_shape(wave_bytes,pcm,52800)
    require(sha(wave_bytes)==REFERENCE_WAV==master["measured_voice"]["audio_sha256"]==master["staged_audio"]["sha256"]==mux["audio_sha256"],"native-original-wav-binding")
    require(intake.original("producer-source-copy")==wave_bytes==intake.original("cas-source","comparison","persistence"),"native-source-cas-bytes")
    intake.layout("cas-source","owner","motionwright-data/blobs/sha256/"+REFERENCE_WAV[:2]+"/"+REFERENCE_WAV,"comparison","persistence")
    content=intake.value("source-content")
    require((content["master_sha256"],content["decoded_pcm_sha256"],content["original_wav_sha256"])==(identity[0],sha(pcm),REFERENCE_WAV),"native-content-original-link")
    for value in content["oracles"].values(): require(value["state"]=="PASS" and "witness" in value,"native-source-content-terminal")
    witness=content["oracles"]["full_source_content"]["witness"]
    require((witness["original_wav_sha256"],witness["decoded_pcm_sha256"],witness["source_stereo_frames"],witness["decoded_stereo_frames"],witness["raw_decoded_frames_after_source"])
            ==(REFERENCE_WAV,sha(pcm),52800,len(pcm)//4,delta),"native-pcm-complete-observation")
    from native_pcm_source_fidelity import inspect_source_pcm16_stereo_content
    recomputed=inspect_source_pcm16_stereo_content(wave_bytes,pcm,
        original_wav_sha256=sha(wave_bytes),decoded_pcm_sha256=sha(pcm))
    require(core.project(recomputed,PCM_CONTENT)==witness,"native-source-pcm-oracle-differs")
    intake.live("original",*identity,official)
    final=intake.value("final-custody","original","attempt-custody")
    terminal=intake.value("native-terminal","original","attempt-custody")
    require(final["native_terminal"]==terminal["native_terminal"]=="SUCCESS_REPORTED"
            and final["source_content_and_live_module_verified"] is True and final["secondary_failures"]==[],"native-terminal-not-verified")
    ack=terminal.get("native_result")
    require(ack is not None and final["native_result"]==ack
            and ack["project_id"]==timeline["project_resource"][8:]
            and (ack["artifact_path"],ack["artifact_sha256"],ack["artifact_bytes"])
                ==(timeline["artifact_path"],timeline["artifact_sha256"],timeline["artifact_bytes"])
            and (ack["master_artifact_path"],ack["master_artifact_sha256"],ack["master_artifact_bytes"])
                ==(mux["artifact"]["path"],*identity)
            and ack["master_audio_sha256"]==REFERENCE_WAV
            and ack["master_decoded_audio_sha256"]==mux["decoded_audio"]["sha256"],"native-original-ack-artifact-binding")
    result=intake.value("result")
    require(result["motionwright_sha"]==current["source_sha"] and result["g2_repaired_module_sha256"]==REPAIRED_ELF
            and result["g2_original_wav_sha256"]==REFERENCE_WAV and result["g2_whole_decoded_pcm_sha256"]==sha(pcm)
            and result["g2_packet_observation_sha256"]==intake.one("packets")[0]["expected_sha256"]
            and (result["final_master_sha256"],result["final_master_bytes"])==identity,"native-producer-observation-link")
    from native_aac_clock_custody import verify_selected_carrier
    intake.layout("audio-clock-carrier","observation","original-aac-clock-capture.json")
    clock_row=intake.one("audio-clock-carrier")[0]
    require(clock_row["policy"]=="aac_clock_carrier" and clock_row["kind"]=="json-original",
            "native-clock-carrier-policy")
    timeline_row=intake.one("timeline-receipt")[0];packet_row=intake.one("packets")[0]
    verify_selected_carrier(intake.value("audio-clock-carrier"),phase="original",scenario="repaired-g2",
        source_document=timeline,
        source_identity={"sha256":timeline_row["expected_sha256"],"bytes":timeline_row["bytes"]},
        original_wav=wave_bytes,master=intake.original("native-master"),decoded_pcm=pcm,
        packet_identity={"sha256":packet_row["expected_sha256"],"bytes":packet_row["bytes"]})
    return dict(schema="motionwright.native.mlt-selected-intake/3" if intake.admission is not None
                else "motionwright.native.mlt-selected-intake/2",context=intake.context,
        selected_layers_complete=True,original_native_media_bytes_retained=True,
        full_decoded_minus_source_samples=delta,source_bound_reported_native_success=True,
        native_reopen_performed=False,source_content_oracle_recomputed=True,
        packet_PTS_and_AAC_priming_verified=False,privileged_Broker_grant_verified=False,
        native_operation_retried=False,physical_acceptance=False,human_acceptance=False,release_authorized=False)


def verify_mlt_diagnostic_intake(raw, *, current, expected_diagnostic_admission):
    """Verify bounded /3 custody; missing native rows remain explicit."""
    require(expected_diagnostic_admission is not None,"ffv1-partial-external-admission-required")
    intake=Intake(raw,current,"repaired-g2",mlt_contract(seed_diagnostics=True),policies("repaired-g2"),
        expected_diagnostic_admission=expected_diagnostic_admission,require_complete=False)
    manifest=intake.manifest
    return dict(schema="motionwright.native.mlt-diagnostic-intake/3",context=intake.context,
        diagnostic_intake_verified=True,diagnostic_roster_complete=manifest["diagnostic_roster_complete"],
        selected_layers_complete=manifest["required_layers_complete"],
        required_missing_or_failed_members=sum(row["required"] and row["state"]!="INCLUDED"
                                               for row in manifest["members"]),
        selected_intake_verified=False,source_content_and_live_module_verified=False,
        original_native_media_bytes_verified=False,source_bound_reported_native_success=False,
        diagnostic_eof_claimed=False,diagnostic_history_complete_claimed=False,
        native_reopen_performed=False,packet_PTS_and_AAC_priming_verified=False,
        native_operation_retried=False,physical_acceptance=False,human_acceptance=False,
        release_authorized=False)


def verify_decrypted_selection(raw,*,current,scenario,expected_diagnostic_admission=None,
                               require_complete=True):
    require(type(require_complete) is bool,"native-intake-completeness-mode")
    if scenario=="film180-g2":
        require(require_complete and expected_diagnostic_admission is None,"ffv1-diagnostics-on-film")
        from native_film180_evidence_selection import verify_film_intake
        return verify_film_intake(raw,current=current)
    require(scenario=="repaired-g2","native-intake-scenario")
    if not require_complete:
        return verify_mlt_diagnostic_intake(raw,current=current,
            expected_diagnostic_admission=expected_diagnostic_admission)
    return verify_mlt_intake(raw,current=current,expected_diagnostic_admission=expected_diagnostic_admission)


PUBLIC_V2_KEYS = {
    "schema", "context", "scope", "native_terminal", "producer_exit_code",
    "source_content_and_live_module_verified", "job_outcome_at_seal",
    "sealed_originals_sha256", "sealed_originals_bytes", "recipient_spki_sha256",
    "custody_scope", "source_unchanged", "selected_layers_complete", "selected_intake_verified",
    "selected_manifest_sha256", "full_original_json_claimed", "atomic_sqlite_snapshot_claimed",
    "native_operation_retried", "packet_PTS_and_AAC_priming_verified",
    "physical_AV_master_acceptance", "human_acceptance", "release_authorized", "upload_authorization",
}


def validate_public_v2(public):
    require(type(public) is dict and set(public)==PUBLIC_V2_KEYS,"native-public-exact-fields")
    require(public["schema"] in ("motionwright.native.mlt-g2-public/2","motionwright.native.film180-g2-public/2"),"native-public-schema")
    core.project(public["context"],FOUR_CONTEXT,exact=True)
    for name in ("sealed_originals_sha256","recipient_spki_sha256","selected_manifest_sha256"):
        core.project(public[name],SHA,exact=True)
    core.project(public["sealed_originals_bytes"],POS,exact=True)
    core.project(public["native_terminal"],TERMINAL,exact=True)
    core.project(public["producer_exit_code"],Nullable(Number(0,255,integer=True)),exact=True)
    core.project(public["job_outcome_at_seal"],String(values=("success","failure","cancelled")),exact=True)
    for name in ("native_operation_retried","packet_PTS_and_AAC_priming_verified",
                 "physical_AV_master_acceptance","human_acceptance","release_authorized"):
        require(public[name] is False,"native-public-scope-escalation")
    require(public["custody_scope"]=="explicit-source-bound-selected-layers/2"
            and public["upload_authorization"]=="NOT_GRANTED_BY_COLLECTOR"
            and public["full_original_json_claimed"] is False
            and public["atomic_sqlite_snapshot_claimed"] is False,"native-public-selected-scope")
    for name in ("source_unchanged","selected_layers_complete","selected_intake_verified","source_content_and_live_module_verified"):
        require(type(public[name]) is bool,"native-public-boolean")
    require(public["scope"]==("film180-original-and-edited-content-and-live-module-only"
            if public["schema"]=="motionwright.native.film180-g2-public/2" else "source-content-and-live-module-only"),"native-public-scenario-scope")
    return public


PUBLIC_V3_KEYS = PUBLIC_V2_KEYS | {
    "selected_evidence_schema","selected_lost_scope","diagnostic_status",
    "diagnostic_admitted","diagnostic_plan_absence_verified",
    "diagnostic_contract_sha256","diagnostic_admission_sha256",
    "diagnostic_intake_verified","diagnostic_roster_complete","raw_diagnostics_included",
    "diagnostic_eof_claimed","diagnostic_history_complete_claimed","diagnostic_payloads_redacted",
}


def validate_public_v3(public):
    require(type(public) is dict and set(public)==PUBLIC_V3_KEYS,"native-public-v3-exact-fields")
    require(public["schema"]=="motionwright.native.mlt-g2-public/3","native-public-v3-schema")
    # The shared /2 fields keep their exact policy. This local normalized copy is
    # never emitted, decrypted or used to select a capture schema.
    common={key:public[key] for key in PUBLIC_V2_KEYS}
    common.update(schema="motionwright.native.mlt-g2-public/2",
                  custody_scope="explicit-source-bound-selected-layers/2")
    validate_public_v2(common)
    for name in ("diagnostic_admitted","diagnostic_plan_absence_verified",
                 "diagnostic_intake_verified","diagnostic_roster_complete","raw_diagnostics_included"):
        require(type(public[name]) is bool,"native-public-v3-diagnostic-boolean")
    for name in ("diagnostic_eof_claimed","diagnostic_history_complete_claimed","diagnostic_payloads_redacted"):
        require(public[name] is False,"native-public-v3-diagnostic-scope")
    if public["diagnostic_status"]=="FFV1_DIAGNOSTICS_ADMITTED":
        require(public["selected_evidence_schema"]==core.MANIFEST_SCHEMA_V3
                and public["custody_scope"]=="explicit-source-bound-selected-layers/3"
                and public["selected_lost_scope"]==list(core.LOST_SCOPE_V3)
                and public["diagnostic_admitted"] is True
                and public["diagnostic_plan_absence_verified"] is False,
                "native-public-v3-admitted-scope")
        for name in ("diagnostic_contract_sha256","diagnostic_admission_sha256"):
            core.project(public[name],SHA,exact=True)
        require(not public["diagnostic_intake_verified"] or public["source_unchanged"],
                "native-public-v3-diagnostic-closure")
        require(not public["selected_intake_verified"] or
                (public["diagnostic_intake_verified"] and public["diagnostic_roster_complete"]),
                "native-public-v3-complete-without-diagnostics")
    elif public["diagnostic_status"]=="FFV1_DIAGNOSTIC_PLAN_ABSENT":
        require(public["selected_evidence_schema"]==core.MANIFEST_SCHEMA
                and public["custody_scope"]=="explicit-source-bound-selected-layers/2"
                and public["selected_lost_scope"]==list(core.LOST_SCOPE)
                and public["diagnostic_plan_absence_verified"] is True
                and public["diagnostic_contract_sha256"] is None
                and public["diagnostic_admission_sha256"] is None,
                "native-public-v3-plan-absence-scope")
        require(all(public[name] is False for name in (
            "diagnostic_admitted","diagnostic_intake_verified","diagnostic_roster_complete",
            "raw_diagnostics_included","selected_intake_verified","source_content_and_live_module_verified")),
            "native-public-v3-plan-absence-escalation")
    else:
        require(False,"native-public-v3-diagnostic-status")
    return public


def validate_public_evidence(public):
    require(type(public) is dict,"native-public-exact-fields")
    if public.get("schema")=="motionwright.native.mlt-g2-public/3":
        return validate_public_v3(public)
    return validate_public_v2(public)


def verify_sealed_selection(envelope,private_key,public,*,expected_diagnostic_admission=None,
                            require_complete=True):
    """Explicit offline caller provides its key; no file/key lookup or network."""
    from ci_private_evidence import decrypt_snapshot, MAGIC
    validate_public_evidence(public)
    require(type(require_complete) is bool,"native-intake-completeness-mode")
    v3=public["schema"]=="motionwright.native.mlt-g2-public/3"
    has_diagnostics=v3 and public["diagnostic_status"]=="FFV1_DIAGNOSTICS_ADMITTED"
    require(require_complete or has_diagnostics,"native-partial-needs-external-v3")
    require(has_diagnostics or expected_diagnostic_admission is None,"ffv1-admission-on-nondiagnostic-public")
    admitted=(checked_ffv1_admission(expected_diagnostic_admission,current=public["context"])
              if has_diagnostics else None)
    if has_diagnostics:
        require(public["diagnostic_admission_sha256"]==sha(core.canonical(admitted))
                and public["diagnostic_contract_sha256"]==sha(core.canonical(admitted["capture_diagnostic_contract"])),
                "ffv1-public-external-admission")
    require(sha(envelope)==public.get("sealed_originals_sha256") and len(envelope)==public.get("sealed_originals_bytes"),"native-public-ciphertext-identity")
    plaintext,authenticated_context=decrypt_snapshot(envelope,private_key)
    length=int.from_bytes(envelope[len(MAGIC):len(MAGIC)+4],"big")
    header=core.strict(envelope[len(MAGIC)+4:len(MAGIC)+4+length])
    require(header["recipient_spki_sha256"]==public["recipient_spki_sha256"],"native-ciphertext-recipient")
    require(authenticated_context==public.get("context"),"native-ciphertext-exact-context")
    with tarfile.open(fileobj=io.BytesIO(plaintext),mode="r:") as archive:
        manifest_bytes=archive.extractfile("selected-evidence-manifest.json").read(core.MAX_JSON+1)
    require(sha(manifest_bytes)==public["selected_manifest_sha256"],"native-public-selected-manifest")
    if v3:
        original_manifest=core.strict(manifest_bytes)
        require(original_manifest["schema"]==public["selected_evidence_schema"]
                and original_manifest["lost_scope"]==public["selected_lost_scope"]
                and original_manifest["required_layers_complete"] is public["selected_layers_complete"],
                "ffv1-public-selected-version-and-losses")
        if not has_diagnostics:
            checked_ffv1_plan_absence(original_manifest,current=authenticated_context)
            # This API verifies a complete native success. A retained early
            # failure capsule must never be promoted to that result.
            require(False,"ffv1-plan-absence-is-not-complete-native-intake")
        require(original_manifest["schema"]==core.MANIFEST_SCHEMA_V3
                and original_manifest["diagnostic_contract_sha256"]==public["diagnostic_contract_sha256"]
                and original_manifest["diagnostic_roster_complete"]==public["diagnostic_roster_complete"]
                and original_manifest["raw_diagnostics_included"]==public["raw_diagnostics_included"],
                "ffv1-public-selected-diagnostic-scope")
    scenario="film180-g2" if public["schema"]=="motionwright.native.film180-g2-public/2" else "repaired-g2"
    result=verify_decrypted_selection(plaintext,current=authenticated_context,scenario=scenario,
        expected_diagnostic_admission=admitted,require_complete=require_complete)
    if not require_complete:
        require(public["source_unchanged"] is True and public["diagnostic_intake_verified"] is True,
                "ffv1-public-partial-custody-not-verified")
        require(result["selected_layers_complete"] is public["selected_layers_complete"]
                and result["diagnostic_roster_complete"] is public["diagnostic_roster_complete"],
                "ffv1-public-partial-custody-completeness")
        result.update(native_terminal=public["native_terminal"],
                      producer_exit_code=public["producer_exit_code"])
        return result
    require(public.get("source_unchanged") is True and public.get("selected_layers_complete") is True and public.get("selected_intake_verified") is True
            and public.get("source_content_and_live_module_verified") is True
            and public.get("native_terminal")=="SUCCESS_REPORTED"
            and public.get("producer_exit_code")==0 and public.get("job_outcome_at_seal")=="success",
            "native-public-success-mismatch")
    require(all(public.get(k) is False for k in ("native_operation_retried","packet_PTS_and_AAC_priming_verified",
        "physical_AV_master_acceptance","human_acceptance","release_authorized")),"native-public-scope-escalation")
    return result

# Exact SDK source selection; these names are not execution evidence.
AUDIO_SOURCE_SHA = "ccc813e22f4b2d8ae112eb10a88abb8339a54d80"
AUDIO_SOURCE_TREE = "4d2e8799a716f0d494677135a49a3a7c23a635e1"
AUDIO_SOURCE_PARENT = "7a31aab1419b803c93e6527bb4abb2bd59222bf2"
AUDIO_SOURCE_SCOPE = "audio-source-g1"
AUDIO_SOURCE_G0 = "audio-source-g0"
AUDIO_PHYSICAL_CASE = 'real_mlt_video_driver_runs_inside_sandbox'
AUDIO_SOURCE_PINS = {'Cargo.lock': 'ec7f3946d36a3e9e0a6054173220553bd652904cfa48a17daec66ce2e549a18c',
 'crates/driver-host/src/lib.rs': 'da69223ac6f7e1b2a4421349ac77fb9b4c85526889c0722631b030cd3ef65821',
 'crates/driver-mlt-video/Cargo.toml': '3cdb4d26892d2335d8111b845d72980a69b3bf1e647230fa4f582c8cc701c501',
 'crates/driver-mlt-video/fixtures/SHA256SUMS.json': '00c8369bf4536477a30b496ab879f5df6627cab58239569a1ab3c79b6929bb6a',
 'crates/driver-mlt-video/fixtures/expected/capabilities.json': '9efa18cb207381988ef6e5e7601cd0c25f9266e487765cfa589a0ccd860d4168',
 'crates/driver-mlt-video/fixtures/expected/catalog-wire.json': '9be1a19f4026b77ba672bc0fe9ac4952779c08ba469ffddd404f9d1d780b821b',
 'crates/driver-mlt-video/fixtures/expected/digests.json': '715798294f5c34436cde21adc1ff37bdd5f18f434c781c8202b0ac4770efd6bf',
 'crates/driver-mlt-video/src/app.rs': '8ea9da224efc147e1a08dcaf200dbae230f7782d246dc9946a1a6041b5ee614c',
 'crates/driver-mlt-video/src/bin/fake_ffprobe.rs': '98908c9710946fa2a85d62c2218adea06658c93d1da6ff3cae244351515923b8',
 'crates/driver-mlt-video/src/bin/fake_melt.rs': '456ab47c8a7f59f68e0dd064e60f0fae0da5dec9cd7789d2d0f8d15c88ad0840',
 'crates/driver-mlt-video/src/bin/runtime_runner.rs': '18f5fd482f791e4025450cf80b06711ad208f441240594aed8adbacdccbfe035',
 'crates/driver-mlt-video/src/caption_pixels.rs': 'ee82f4125d6a3f57e0931a45c37758619141d24509c863e03c88f9372e95def1',
 'crates/driver-mlt-video/src/catalog.json': '9efa18cb207381988ef6e5e7601cd0c25f9266e487765cfa589a0ccd860d4168',
 'crates/driver-mlt-video/src/color.rs': '57c6f7cd31ded0a7a76b532dedcbfd7ecbc7736019367e89224921d6d413a1a6',
 'crates/driver-mlt-video/src/jobs.rs': '45e9b5dbebeeaa5b0dbcf1a11f0be40df213c3e72f7a3a8b6bd70bbe5bc5ed66',
 'crates/driver-mlt-video/src/lib.rs': '4662f2aa8e93a78ce2105b668e5f8d2fc0f82b986ca497f89d53d9624c0af2b2',
 'crates/driver-mlt-video/src/main.rs': '8f7f8163a6ac767dfdacaebdabb635c6d92b35aeb755ae9d14d663a42a263c62',
 'crates/driver-mlt-video/src/runtime.rs': '78c627fa1aa60430fa80fabd00fd8b3912517d99e40eb17af96fdf82aca554e7',
 'crates/driver-mlt-video/src/sync.rs': '901580d6414d8e2f445b943a08478d21742bdc2060172b61c5316058f9e56bae',
 'crates/driver-mlt-video/tests/conformance.rs': 'd804740747d3551ff9592ccd1d3f2a5f5070d7884cf6c7fa7a39ffd2dae72985',
 'crates/driver-mlt-video/tests/domain_contract.rs': '97027c9a732cd27cac9685c8f7c5efc9522220848c3045141f1bfbb72b878d50',
 'crates/driver-mlt-video/tests/live.rs': 'ce888e7ee5310fb7ddd58cd9e8e2035977e46590ac0f664daba41f063a83fc9b',
 'crates/driver-mlt-video/tests/process.rs': '3a72edccc4f3c88bd126fc31275f35ae18ddf2429604d51e1954ae1bac4d348b',
 'crates/driver-mlt-video/tests/runtime_runner.rs': '00c989cd71bf7bf70413cfb491749893e066f2c6d48c46f519f980255faefebd'}
AUDIO_SOURCE_ROSTERS = {'semwright_mlt_video': ('app::audio_derivative_contract_tests::byte_layout_and_integral_clock_are_checked_without_a_runtime',
                         'app::audio_derivative_contract_tests::descriptor_is_closed_mutating_and_not_a_read_only_audio_observation',
                         'app::audio_derivative_contract_tests::empty_streams_require_real_empty_hash_and_complete_closed_roster',
                         'app::audio_derivative_contract_tests::input_identity_is_checked_before_runtime_or_any_output_publication',
                         'app::audio_derivative_contract_tests::occupied_original_or_target_is_never_replaced_during_preflight',
                         'app::audio_derivative_contract_tests::response_binding_rejects_forged_clock_masks_recipe_and_original_sha',
                         'caption_pixels::tests::cadence_admission_rejects_ambiguous_borders_and_overlap',
                         'caption_pixels::tests::proof_requires_real_source_glyphs_and_retained_lossy_mask',
                         'color::png_completion_tests::png_completion_rejects_bytes_after_iend',
                         'color::png_completion_tests::png_completion_rejects_corrupt_iend_crc',
                         'color::png_completion_tests::png_completion_rejects_missing_iend',
                         'color::tests::a_single_exact_pts_cannot_hide_quantized_duration_or_endpoint',
                         'color::tests::every_observed_frame_color_is_required',
                         'color::tests::exact_pts_are_read_and_quantized_matroska_is_not_rewritten',
                         'color::tests::explicit_srgb_requires_opaque_and_rejects_p3_or_missing',
                         'color::tests::film180_bound_fits_proposed_two_gib_without_raising_existing_limits',
                         'color::tests::geometry_and_duration_must_be_observed_not_defaulted',
                         'color::tests::missing_duplicate_pts_and_incomplete_frames_fail',
                         'color::tests::portable_observation_limits_match_runtime_and_decoder',
                         'color::tests::present_side_data_must_be_an_empty_array',
                         'color::tests::rgba_digest_is_explicit_roster_not_raw_concatenation',
                         'color::tests::stream_audio_and_side_data_cannot_hide',
                         'jobs::native_memory_tests::decoder_bound_keeps_audio_and720_profiles_and_source_clock',
                         'jobs::native_memory_tests::every_base_and_clone_avformat_decoder_is_single_threaded',
                         'jobs::native_memory_tests::explicit_bound_replaces_auto_once_and_is_idempotent',
                         'jobs::native_memory_tests::generators_images_filters_and_consumer_settings_stay_exact',
                         'jobs::native_memory_tests::missing_staged_asset_and_sequence_still_fail_closed',
                         'jobs::native_memory_tests::native_stage_preserves_saved_project_all_entries_and1437_clock',
                         'jobs::native_staging_closure_tests::replaced_staged_media_is_rejected_with_identical_xml',
                         'jobs::native_staging_closure_tests::unchanged_staging_reaches_the_native_process',
                         'runtime::audio_derivative_tests::an_open_descendant_pipe_can_stop_without_a_blocking_join_or_fake_eof',
                         'runtime::audio_derivative_tests::audio_capture_child_fixture',
                         'runtime::audio_derivative_tests::channel_masks_are_observed_instead_of_inferred_from_channel_count',
                         'runtime::audio_derivative_tests::custody_failure_keeps_the_original_error_object_and_unknown_flag',
                         'runtime::audio_derivative_tests::custody_roster_is_audio_specific_closed_and_disjoint_from_ffv1',
                         'runtime::audio_derivative_tests::derived_dual_mono_checks_both_samples_and_exact_target_count',
                         'runtime::audio_derivative_tests::empty_eof_and_a_truncated_prefix_have_different_observations',
                         'runtime::audio_derivative_tests::empty_original_stream_is_published_and_never_overwritten',
                         'runtime::audio_derivative_tests::known_terminal_remains_separate_from_a_later_drain_stop',
                         'runtime::audio_derivative_tests::nonzero_terminal_stays_primary_for_reader_error_panic_or_missing_pipe',
                         'runtime::audio_derivative_tests::original_prefix_survives_read_error_without_fabricating_eof',
                         'runtime::audio_derivative_tests::original_prefix_survives_reader_panic_without_becoming_an_empty_pipe',
                         'runtime::audio_derivative_tests::recipes_are_fixed_and_do_not_trim_pad_or_normalize_original_samples',
                         'runtime::audio_derivative_tests::same_supervisor_retains_real_nonzero_exit_and_complete_original_pipes',
                         'runtime::audio_derivative_tests::source_clock_boundaries_are_integral_and_profiles_are_closed',
                         'runtime::audio_derivative_tests::successful_exit_with_incomplete_capture_is_unknown_instead_of_a_derivative',
                         'runtime::audio_derivative_tests::wave_reader_accepts_original_pcm_chunks_and_observes_every_frame',
                         'runtime::audio_derivative_tests::well_formed_float_and_pcm24_sources_do_not_enter_the_pcm16_profile',
                         'runtime::ffv1_original_custody_tests::ffv1_binary_originals_precede_native_nonzero_classification',
                         'runtime::ffv1_original_custody_tests::ffv1_cancel_timeout_and_overflow_keep_their_original_cause',
                         'runtime::ffv1_original_custody_tests::ffv1_custody_conflict_does_not_replace_original_error_or_original_file',
                         'runtime::ffv1_original_custody_tests::ffv1_empty_original_uses_existing_atomic_root_api',
                         'runtime::ffv1_original_custody_tests::ffv1_existing_sidecar_refuses_preflight_without_overwrite',
                         'runtime::ffv1_original_custody_tests::ffv1_missing_process_result_never_fabricates_exit_or_empty_stream',
                         'runtime::ffv1_original_custody_tests::ffv1_prefix_is_explicit_and_never_claims_upstream_eof',
                         'runtime::ffv1_original_custody_tests::ffv1_probe_bytes_are_retained_before_a_later_parser_error',
                         'runtime::ffv1_original_custody_tests::ffv1_six_closed_stages_preserve_exact_returned_buffers_without_execution',
                         'runtime::ffv1_original_custody_tests::ffv1_success_does_not_hide_incomplete_custody',
                         'runtime::native_closure_tests::final_readback_preserves_changed_artifact_rejection',
                         'runtime::native_closure_tests::final_readback_preserves_unchanged_artifact_roster',
                         'runtime::native_closure_tests::final_readback_rejects_cancellation_arriving_at_hash_eof',
                         'runtime::native_closure_tests::final_readback_rejects_deadline_expiring_at_hash_eof',
                         'runtime::native_closure_tests::output_probe_cannot_complete_past_the_shared_deadline',
                         'runtime::native_closure_tests::probe_fixture_completion_is_observable_with_its_own_budget',
                         'runtime::native_diagnostic_tests::diagnostic_argv_and_xml_caps_fail_closed',
                         'runtime::native_diagnostic_tests::failure_filename_requires_scoped_output_and_exact_job',
                         'runtime::native_diagnostic_tests::local_receipt_write_is_single_link_0600_and_never_replaces',
                         'runtime::native_diagnostic_tests::logs_are_lossless_hex_and_bounded_local_receipt',
                         'runtime::native_diagnostic_tests::raw_probe_envelope_streams_and_scalars_are_bounded',
                         'runtime::native_diagnostic_tests::raw_probe_selects_video_and_preserves_clock_without_audio_packet_confusion',
                         'runtime::tool_owner_tests::decoded_frame_counter_requires_exact_bounded_ffv1_observation',
                         'runtime::tool_owner_tests::h264_profile_uses_bounded_fast_high_quality_encoding',
                         'runtime::tool_owner_tests::h264_uses_synchronous_mlt_but_other_profiles_keep_certified_async_mode',
                         'runtime::tool_owner_tests::owner_policy_allows_only_explicit_trust_cases',
                         'runtime::tool_owner_tests::strict_video_only_receipts_refuse_pcm_and_forged_profiles',
                         'runtime::tool_owner_tests::video_only_profile_is_opt_in_and_preserves_legacy_audio_profiles',
                         'sync::equal_peak_tests::equal_peak_prefers_earliest_presentation_timestamp',
                         'sync::tests::audio_impulse_requires_level_and_contrast',
                         'sync::tests::bright_flash_is_detected_with_frame_uncertainty',
                         'sync::tests::cue_ids_are_strict_data_not_filter_syntax',
                         'sync::tests::full_scan_dataset_resolves_each_cue_inside_its_own_window',
                         'sync::tests::ordinary_frames_do_not_become_markers'),
 'semwright-mlt-video-driver': ('audio_derivative_host_tests::absent_host_result_stays_absent_and_keeps_the_original_unknown_error',
                                'audio_derivative_host_tests::actual_sdk_catalog_keeps_audio_derivation_mutating_and_non_idempotent',
                                'audio_derivative_host_tests::audio_host_preserves_binary_pipes_and_nonzero_before_any_json_parse',
                                'audio_derivative_host_tests::audio_host_retains_the_same_request_and_has_no_ffv1_namespace',
                                'audio_derivative_host_tests::failed_terminal_publication_does_not_replace_original_unknown_or_overwrite',
                                'ffv1_host_original_tests::ffv1_discover_keeps_actual_empty_input_and_binary_buffers_before_parse',
                                'ffv1_host_original_tests::ffv1_host_original_parser_error_is_not_replaced_by_custody',
                                'ffv1_host_original_tests::ffv1_host_original_unknown_is_primary_when_custody_also_fails',
                                'ffv1_host_original_tests::ffv1_host_success_with_unretained_originals_is_uncertain',
                                'ffv1_host_original_tests::ffv1_unobserved_host_failure_does_not_invent_empty_streams_or_exit',
                                'host_native_observation_tests::complete_report_cannot_replace_actual_png_decoding',
                                'host_native_observation_tests::host_binder_accepts_the_exact_complete_report_and_original_sources',
                                'host_native_observation_tests::host_binder_rejects_self_consistent_report_from_other_source',
                                'host_native_observation_tests::host_binder_rejects_swapped_terminal_report_payload',
                                'host_native_observation_tests::host_copy_cancellation_is_not_an_interrupted_retry_loop',
                                'host_native_observation_tests::host_custody_failure_preserves_original_native_terminal',
                                'host_native_observation_tests::host_custody_is_absent_from_other_profiles',
                                'host_native_observation_tests::host_custody_keeps_partial_publication_and_requires_new_destination',
                                'host_native_observation_tests::host_custody_never_replaces_existing_originals',
                                'host_native_observation_tests::host_custody_rejects_substituted_request_or_terminal_before_writes',
                                'host_native_observation_tests::host_custody_retains_exact_original_request_and_terminal',
                                'host_native_observation_tests::host_original_request_rejects_xml_media_and_request_substitution',
                                'host_native_observation_tests::host_terminal_receipt_requires_the_original_request_and_closed_descriptor',
                                'host_native_observation_tests::unknown_publication_precedes_cancellation_state',
                                'tests::host_av_failure_preserves_stale_without_promoting_effect_outcome',
                                'tests::host_catalog_parser_is_strict_and_bounded',
                                'tests::host_runner_failure_is_strict_and_bounded',
                                'tests::sdk_catalog_declares_generic_artifact_ports'),
 'semwright-mlt-runtime-runner': ('audio_derivative_runner_tests::audio_derivative_uses_the_existing_closed_av_operation_selection',
                                  'audio_derivative_runner_tests::audio_selection_never_accepts_a_shell_alias_or_caller_named_operation',
                                  'host_observation_deadline_tests::complete_observation_does_not_reset_the_native_deadline'),
 'domain_contract': ('generated_mlt_exposes_complete_safe_semantic_contract',
                     'kdenlive_contract_reuses_native_support_source_of_truth',
                     'native_warnings_are_not_hidden_inside_portable_project_only',
                     'opaque_native_state_is_structured_as_read_only_projection_loss'),
 'live': ('audio_live_source_tests::audio_live_candidate_original_is_observed_even_when_no_success_value_exists',
          'audio_live_source_tests::audio_live_catalog_absence_is_bound_to_the_original_call_without_inventing_cache_history',
          'audio_live_source_tests::audio_live_catalog_call_binding_rejects_foreign_request_and_descriptor',
          'audio_live_source_tests::audio_live_catalog_caps_use_existing_sdk_limits_without_truncating_originals',
          'audio_live_source_tests::audio_live_catalog_changed_observation_and_custody_failure_preserve_native_unknown',
          'audio_live_source_tests::audio_live_catalog_complete_originals_bind_the_same_output_and_returned_buffers',
          'audio_live_source_tests::audio_live_catalog_partial_roster_and_missing_empty_stdin_are_rejected',
          'audio_live_source_tests::audio_live_catalog_terminal_cannot_promote_nonzero_unknown_or_foreign_scope',
          'audio_live_source_tests::audio_live_catalog_valid_json_prefix_does_not_hide_truncation_or_delivery_mismatch',
          'audio_live_source_tests::audio_live_custody_failure_preserves_the_same_complete_unknown_error',
          'audio_live_source_tests::audio_live_expected_rejection_cannot_consume_unknown_as_a_known_negative',
          'audio_live_source_tests::audio_live_missing_selector_and_nonempty_root_fail_before_setup',
          'audio_live_source_tests::audio_live_oracles_reject_silenced_control_and_unequal_dual_mono',
          'audio_live_source_tests::audio_live_request_and_complete_error_are_originals_before_interpretation',
          'audio_live_source_tests::audio_live_result_collision_retains_prefix_and_never_replaces_unknown',
          'audio_live_source_tests::audio_live_retains_eight_subroots_and_detects_substitution',
          'audio_live_source_tests::audio_live_root_requires_fresh_private_owner_paths_outside_the_checkout',
          'audio_live_source_tests::audio_live_sources_have_the_original_pcm_clocks_before_any_provider',
          'audio_live_source_tests::audio_live_tool_file_prefix_never_claims_eof_or_truncates_original'),
 'conformance': ('av_mux_schema_is_closed_and_declares_only_certified_audio_profile',
                 'capability_namespace_unique_and_bounded',
                 'doctor_satisfies_actual_output_schema',
                 'driver_frame_big_endian_roundtrip',
                 'driver_frame_eof',
                 'driver_frame_oversize_denied',
                 'driver_frame_truncated_header',
                 'driver_frame_zero_denied',
                 'hello_identity_and_executable_pin',
                 'hello_unknown_fields_denied',
                 'lifecycle_hello_catalog_health_shutdown',
                 'mutation_catalog_does_not_claim_transport_dry_run',
                 'native_caption_glyph_observer_is_read_only_and_closed_world',
                 'schema_pages_bounded',
                 'schemas_reject_unknown_arguments',
                 'static_descriptor_golden_is_checked',
                 'wrong_descriptor_digest_rejected_before_execution'),
 'process': ('media_probe_observes_audio_rate_and_channels',
             'media_probe_rational_duration',
             'native_project_cannot_reach_render_runtime',
             'native_render_profiles_project_to_semantic_contract',
             'probe_duration_capacity_is_rational_not_nominal_fps',
             'probe_without_duration_is_not_a_source_handle_guarantee',
             'process_address_space_budget_is_bounded',
             'process_argv_is_literal',
             'process_cancel_before_spawn',
             'process_cancel_running',
             'process_cancellation_kills_same_group_descendant',
             'process_cwd_is_private',
             'process_environment_is_explicit',
             'process_failed_partial_not_success',
             'process_nonzero_not_success',
             'process_relative_executable_denied',
             'process_stderr_flood_is_bounded',
             'process_stdout_flood_is_bounded',
             'process_success_exit_code',
             'process_wall_timeout',
             'profile_unavailable_without_codec_probe',
             'service_discovery_excludes_untrusted_tokens'),
 'runtime_runner': ('runtime_runner_discovers_bounded_catalog_from_explicit_dependency',
                    'runtime_runner_probe_rejects_scratch_traversal',
                    'runtime_runner_probes_one_confined_scratch_file_with_sealed_ffprobe',
                    'runtime_runner_rejects_bundle_entrypoint_that_does_not_match_sealed_tool',
                    'runtime_runner_rejects_entrypoint_symlink_that_escapes_runtime_root',
                    'runtime_runner_rejects_unknown_operations',
                    'runtime_runner_rejects_unknown_render_profile',
                    'runtime_runner_rejects_unmaterialized_relative_dependencies',
                    'runtime_runner_renders_one_curated_profile_inside_scratch')}


def audio_source_context(env, platform):
    """Bind the gate and SDK separately; a manual request cannot pick a new SDK."""
    require(platform == "linux" and env.get("RUNNER_OS") == "Linux"
            and env.get("RUNNER_ARCH") == "X64"
            and env.get("GITHUB_ACTIONS") == "true" and env.get("CI") == "true"
            and env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and env.get("GITHUB_RUN_ATTEMPT") == "1"
            and env.get("GITHUB_JOB") == "audio-source"
            and env.get("MW_MLT_SCOPE") == AUDIO_SOURCE_SCOPE,
            "audio-source-first-manual-linux-selection-required")
    gate = env.get("GITHUB_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", gate) is not None
            and env.get("GITHUB_WORKFLOW_SHA") == gate
            and env.get("MW_EXPECTED_GATE_SHA") == gate
            and env.get("MW_EXPECTED_SDK_SHA") == AUDIO_SOURCE_SHA
            and env.get("GITHUB_REPOSITORY") == "seradotcom/motionwright"
            and env.get("GITHUB_WORKFLOW_REF", "").startswith(
                "seradotcom/motionwright/.github/workflows/native-mlt-sequence-e2e.yml@")
            and re.fullmatch(r"[1-9][0-9]*", env.get("GITHUB_RUN_ID", "")) is not None,
            "audio-source-exact-gate-sdk-context-required")
    return {key: env.get(key, "") for key in (
        "GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_REPOSITORY",
        "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "RUNNER_OS", "RUNNER_ARCH")} | {
        "product_source_sha": AUDIO_SOURCE_SHA, "product_source_tree": AUDIO_SOURCE_TREE,
        "verification_scope": AUDIO_SOURCE_SCOPE}


def audio_source_specs():
    sources = {"semwright_mlt_video": ("lib", "src/lib.rs"),
               "semwright-mlt-video-driver": ("bin", "src/main.rs"),
               "semwright-mlt-runtime-runner": ("bin", "src/bin/runtime_runner.rs")}
    return tuple({"name": name, "kind": sources.get(name, ("test", ""))[0],
                  "source": "crates/driver-mlt-video/" + sources.get(name, ("test", "tests/" + name + ".rs"))[1],
                  "crate": "driver-mlt-video", "package": "semwright-mlt-video-driver",
                  "prefixes": None, "allow_empty": False,
                  "closed_roster": (*names, AUDIO_PHYSICAL_CASE) if name == "live" else names}
                 for name, names in AUDIO_SOURCE_ROSTERS.items())


def audio_source_normal_specs():
    return tuple({"name": name, "kind": "bin", "source": "crates/driver-mlt-video/" + source,
                  "crate": "driver-mlt-video", "package": "semwright-mlt-video-driver",
                  "prefixes": None, "allow_empty": True}
                 for name, source in (("semwright-mlt-video-driver", "src/main.rs"),
                    ("semwright-mlt-runtime-runner", "src/bin/runtime_runner.rs"),
                    ("fake-melt", "src/bin/fake_melt.rs"),
                    ("fake-ffprobe", "src/bin/fake_ffprobe.rs")))


def audio_source_producers(cargo):
    selection = ["--lib", "--bin", "semwright-mlt-video-driver", "--bin", "semwright-mlt-runtime-runner"]
    for name in ("domain_contract", "live", "conformance", "process", "runtime_runner"):
        selection.extend(("--test", name))
    common = ["--locked", "-p", "semwright-mlt-video-driver", "--features", "test-tools"]
    return (
        ("strict-clippy", [cargo, "clippy", *common, *selection, "--", "-D", "warnings"]),
        ("normal-tools-build", [cargo, "build", *common, "--bins", "--message-format=json"]),
        ("affected-test-build", [cargo, "test", *common, *selection, "--no-run", "--message-format=json"]),
    )


def audio_source_list(raw, exit_code, target, *, ignored_only=False):
    require(type(exit_code) is int and exit_code == 0 and target in AUDIO_SOURCE_ROSTERS
            and type(ignored_only) is bool and (not ignored_only or target == "live"),
            "audio-source-list-target-or-exit")
    text = raw.decode("utf-8").replace("\r\n", "\n")
    names = tuple(re.findall(r"(?m)^([A-Za-z_][A-Za-z_0-9:]*): test$", text))
    expected = ((AUDIO_PHYSICAL_CASE,) if ignored_only else
                (*AUDIO_SOURCE_ROSTERS[target], AUDIO_PHYSICAL_CASE) if target == "live" else
                AUDIO_SOURCE_ROSTERS[target])
    require(len(names) == len(set(names)) == len(expected) and set(names) == set(expected),
            "audio-source-list-missing-duplicate-extra")
    count = len(expected)
    footer = str(count) + (" test" if count == 1 else " tests") + ", 0 benchmarks"
    remainder = re.sub(r"(?m)^[A-Za-z_][A-Za-z_0-9:]*: test\n?", "", text)
    require(remainder.strip() == footer, "audio-source-list-pretty-footer-or-noise")
    return {"all": names, "selected": () if ignored_only else AUDIO_SOURCE_ROSTERS[target],
            "not_selected": (AUDIO_PHYSICAL_CASE,) if target == "live" else (),
            "not_selected_credit": "NONE", "ignored_only": ignored_only,
            "footer": footer}


def audio_source_complete(results, rosters):
    expected = {(target, name) for target, names in AUDIO_SOURCE_ROSTERS.items() for name in names}
    observed = [(item.get("target"), item.get("name")) for item in results]
    require(set(rosters) == set(AUDIO_SOURCE_ROSTERS) and len(results) == len(expected) == 184
            and len(set(observed)) == len(observed) and set(observed) == expected
            and all(item.get("status") == "PASS" for item in results),
            "audio-source-incomplete-or-relabelled-cases")
    for target, roster in rosters.items():
        require(tuple(roster.get("selected", ())) == AUDIO_SOURCE_ROSTERS[target]
                and len(roster.get("all", ())) == len(AUDIO_SOURCE_ROSTERS[target]) + (target == "live")
                and roster.get("not_selected_credit") == "NONE",
                "audio-source-roster-changed-after-enumeration")
    return {"unique_selected_tests": 184, "selected_omissions": 0,
            "physical_ignored_tests_not_selected": [AUDIO_PHYSICAL_CASE],
            "physical_audio": "NOT_RUN", "full_sdk_g1": "NOT_RUN", "release_authorized": False}
