#!/usr/bin/env python3
"""Closed private /2 evidence for one existing native MLT G2 attempt.

Selection reads an explicit source-owned graph; it never walks an owner tree,
starts a native operation, reconstructs a receipt, decrypts a key or uploads.
A complete selection is custody completeness, not AV or human acceptance.
"""
from __future__ import annotations
import hashlib
import io
import re
import struct
import tarfile
import wave

import native_evidence_projection as core
from native_evidence_projection import Field, Object, Array, String, Number, Nullable, Literal

require = core.require
sha = core.sha
SEM = "54d3459a051adbce4cae9f60c0dea8f60c824be6"
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
    "tooling/ci_private_evidence.py", "tooling/requirements/mlt-g2-custody.txt",
    "tooling/native_mlt_g2_custody.py", "tooling/native_mlt_g2_observation.py",
    "tooling/native_mlt_sequence_e2e.py", "tooling/native_mlt_runtime_admission.py",
    "tooling/native_mlt_loaded_module.py", "tooling/mlt_official_runtime_intake.py",
    "tooling/native_mlt_ci_pcm_envelope.py", "tooling/native_pcm_source_fidelity.py",
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


def policies(scenario):
    paths = FILM_SOURCE_PATHS if scenario == "film180-g2" else SOURCE_PATHS
    source = O(context=FOUR_CONTEXT, tree=SHA40, files=Object({p:Field(SHA) for p in paths}))
    return dict(mlt_timeline=TIMELINE, mlt_master=MLT_MASTER, mlt_input=MLT_INPUT,
        mlt_terminal=MLT_TERMINAL, mlt_final=MLT_FINAL, mlt_content=MLT_CONTENT,
        decoded_stereo=MIDSTREAM, mlt_result=RESULT, live_mlt=LIVE, consumer_snapshot=CONSUMER_SNAPSHOT,
        compiled_mlt=COMPILED, installation=INSTALLATION, official_runtime=OFFICIAL, packets=PACKETS,
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
                 "official-runtime", "installation", "source-before", "source-before-producer", "source-after", "producer-exit", "producer-started", "owner-custody", "owner-terminal"):
        result[("single", "attempt-custody", role)] = (1,1)
    result[("comparison", "persistence", "database")] = (1,1)
    result[("comparison", "persistence", "database-wal")] = (0,1)
    result[("comparison", "persistence", "database-shm")] = (0,1)
    result[("comparison", "persistence", "cas-source")] = (1,1)
    return result


def mlt_contract():
    result = common_contract()
    for role in ("timeline-receipt", "native-master-receipt", "native-timeline", "video-only-copy", "native-master",
                 "producer-master-copy", "original-master-copy", "source-wav", "producer-source-copy",
                 "native-decoded-wav", "decoded-pcm", "source-content", "decoded-stereo", "packets", "result"):
        result[("original", "readback", role)] = (1,1)
    result[("original", "readback", "source-segment")] = (2,2)
    for role in ("input-custody", "native-terminal", "final-custody", "live-module", "consumer-snapshot", "consumer-original"):
        result[("original", "attempt-custody", role)] = (1,1)
    return result


class Selection:
    """One attempt, including failure. A second capture is never a recovery."""
    def __init__(self, *, roots, current, scenario, source_bindings, role_contract, json_policies):
        self.context = checked_context(current, scenario)
        expected = FILM_SOURCE_PATHS if scenario == "film180-g2" else SOURCE_PATHS
        require(type(source_bindings) is list and {r.get("path") for r in source_bindings} == set(expected)
                and len(source_bindings) == len(expected), "native-source-closure")
        self.roots = {k:str(v) for k,v in roots.items()}
        require(set(self.roots) <= {"source", "control", "observation", "owner"}
                and {"source", "control"} <= set(self.roots), "native-roots")
        self.bindings = source_bindings
        self.contract = role_contract
        self.policies = json_policies
        self.members, self.absence, self.cache = [], [], {}
        self.attempted = False
        # Called by the pre-effect source guard too; capture repeats physical
        # source verification before and after every selected payload.
        core.verify_source_bindings(self.roots["source"], current["source_sha"], source_bindings)

    def add(self, handles, root, path, role, layer="readback", phase="original", policy=None,
            identity=None, kind=None, maximum=None):
        relative(path)
        actual = (root, path)
        require(actual not in self.cache, "native-duplicate-selected-path")
        key = (phase, layer, role)
        require(key in self.contract, "native-role-unregistered")
        kind = kind or ("json-projection" if policy else "original")
        row = dict(root=root, relative_path=path, role=role, layer=layer, phase=phase,
                   kind=kind, required=self.contract[key][0] > 0, expected_sha256=None, bytes=None, policy=policy)
        value, raw = None, None
        if root in self.roots:
            try:
                raw, _mark = handles.read(root, core.member_path(path), maximum or (core.MAX_JSON if policy else core.MAX_FILE))
                if identity is not None:
                    require(sha(raw) == identity[0] and (identity[1] is None or len(raw) == identity[1]), "native-linked-original-changed")
                if policy:
                    value = core.project(core.strict(raw), self.policies[policy], exact=kind == "json-original")
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

    def collect_mlt(self, handles):
        self.common(handles)
        fixed = (
            ("producer/native-multisegment-timeline-evidence.json","timeline-receipt","mlt_timeline"),
            ("producer/native-multisegment-av-master-evidence.json","native-master-receipt","mlt_master"),
            ("source-content.json","source-content","mlt_content"),
            ("producer/decoded-stereo-content-proof.json","decoded-stereo","decoded_stereo"),
            ("original-master-packets.json","packets","packets"),
            ("producer/result.json","result","mlt_result"),
        )
        docs = {role:self.add(handles,"observation",path,role,policy=policy) for path,role,policy in fixed}
        for path,role,policy in (("input-custody.json","input-custody","mlt_input"),
                ("native-terminal.json","native-terminal","mlt_terminal"),("final-custody.json","final-custody","mlt_final"),
                ("original-live-module.json","live-module","live_mlt")):
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
        spec = dict(schema=core.SPEC_SCHEMA, context=self.context, roots=self.roots, members=self.members,
                    absence_guards=self.absence, exclusions=exclusions, source_bindings=self.bindings,
                    origin=dict(kind="generated-first-party-fixture",fixture_id=self.context["scenario"],
                        producer_paths=["tooling/native_mlt_sequence_e2e.py"],private_data=True,
                        upload_authorization="NOT_GRANTED_BY_COLLECTOR"))
        return core.capture_selected_originals(spec,policies=self.policies,role_contract=self.contract)


class Intake:
    """Authenticated memory-only readback; never extracts or opens SQLite."""
    def __init__(self, raw, current, scenario, contract, json_policies):
        self.context = checked_context(current,scenario)
        self.manifest = core.inspect_selected_tar(raw,expected_context=self.context,role_contract=contract,policies=json_policies)
        self.data = {}
        with tarfile.open(fileobj=io.BytesIO(raw),mode="r:") as archive:
            for row in self.manifest["members"]:
                if row["state"] == "INCLUDED":
                    payload = archive.extractfile(row["member"]).read()
                    value = core.strict(payload) if row["kind"] != "original" else payload
                    if row["kind"] == "json-projection": value = value["value"]
                    self.data.setdefault((row["phase"],row["layer"],row["role"]),[]).append((row,value,payload))
        self.contract = contract

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
        require(row["kind"] in ("original","json-original") and row["original_bytes"] is True,"native-original-bytes-required")
        return value

    def linked(self,role,identity,phase="original",layer="readback"):
        row,_value,_raw = self.one(role,phase,layer)
        require(row["kind"] in ("original","json-original") and row["original_bytes"] is True,"native-linked-original-bytes")
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


def verify_mlt_intake(raw, *, current):
    intake=Intake(raw,current,"repaired-g2",mlt_contract(),policies("repaired-g2"))
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
    return dict(schema="motionwright.native.mlt-selected-intake/2",context=intake.context,
        selected_layers_complete=True,original_native_media_bytes_retained=True,
        full_decoded_minus_source_samples=delta,source_bound_reported_native_success=True,
        native_reopen_performed=False,source_content_oracle_recomputed=True,
        packet_PTS_and_AAC_priming_verified=False,privileged_Broker_grant_verified=False,
        native_operation_retried=False,physical_acceptance=False,human_acceptance=False,release_authorized=False)


def verify_decrypted_selection(raw,*,current,scenario):
    if scenario=="film180-g2":
        from native_film180_evidence_selection import verify_film_intake
        return verify_film_intake(raw,current=current)
    require(scenario=="repaired-g2","native-intake-scenario")
    return verify_mlt_intake(raw,current=current)



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

def verify_sealed_selection(envelope,private_key,public):
    """Explicit offline caller provides its key; no file/key lookup or network."""
    from ci_private_evidence import decrypt_snapshot, MAGIC
    validate_public_v2(public)
    require(sha(envelope)==public.get("sealed_originals_sha256") and len(envelope)==public.get("sealed_originals_bytes"),"native-public-ciphertext-identity")
    plaintext,authenticated_context=decrypt_snapshot(envelope,private_key)
    length=int.from_bytes(envelope[len(MAGIC):len(MAGIC)+4],"big")
    header=core.strict(envelope[len(MAGIC)+4:len(MAGIC)+4+length])
    require(header["recipient_spki_sha256"]==public["recipient_spki_sha256"],"native-ciphertext-recipient")
    require(authenticated_context==public.get("context"),"native-ciphertext-exact-context")
    with tarfile.open(fileobj=io.BytesIO(plaintext),mode="r:") as archive:
        manifest_bytes=archive.extractfile("selected-evidence-manifest.json").read(core.MAX_JSON+1)
    require(sha(manifest_bytes)==public["selected_manifest_sha256"],"native-public-selected-manifest")
    scenario="film180-g2" if public["schema"]=="motionwright.native.film180-g2-public/2" else "repaired-g2"
    result=verify_decrypted_selection(plaintext,current=authenticated_context,scenario=scenario)
    require(public.get("source_unchanged") is True and public.get("selected_layers_complete") is True and public.get("selected_intake_verified") is True
            and public.get("source_content_and_live_module_verified") is True
            and public.get("native_terminal")=="SUCCESS_REPORTED"
            and public.get("producer_exit_code")==0 and public.get("job_outcome_at_seal")=="success",
            "native-public-success-mismatch")
    require(all(public.get(k) is False for k in ("native_operation_retried","packet_PTS_and_AAC_priming_verified",
        "physical_AV_master_acceptance","human_acceptance","release_authorized")),"native-public-scope-escalation")
    return result
