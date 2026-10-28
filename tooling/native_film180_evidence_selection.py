#!/usr/bin/env python3
"""Closed /3 original and edited Film180 selection; no renderer or upload.

The selected saved Projects are the one source-owned first-party fixture,
retained as exact JSON originals after a complete closed fixture validation.
All 180 native PNGs in each phase are mandatory, with independent decode and
PSNR originals. A finite private roster retains original command streams and
errors without inferring EOF or effects from absence. No selected receipt
grants human or audiovisual acceptance.
"""
from __future__ import annotations
import copy
import io
import re
import struct
import tarfile

import native_evidence_projection as core
import native_mlt_evidence_selection as mlt
from native_mlt_evidence_selection import (O,optional,SHA,SHA40,UUID,RESOURCE,LABEL,RELATIVE,N,POS,REAL,
    FALSE,TRUE,EMPTY,TERMINAL,IDENTITY,ARTIFACT,AUDIO,MEDIA,MUX,MEZZANINE,PCM_CONTENT,LIVE,ALIAS,
    COMPILER_CLOSURE,require,sha)
from native_evidence_projection import Field,Object,Array,String,Number,Nullable,Literal

# This is source-owned fixture data, not a captured caller Project. Fresh IDs,
# revisions, timestamps and the generated WAV digest are admitted only through
# the identity correspondence checked below. Every authored literal is fixed.
PROJECT_TEMPLATE = {'schema_version': 2,
 'id': '01a15afd-f698-73ba-abe8-c3f9e0b4b4c1',
 'generation': '01a15afd-f698-73ba-abe8-c3fa3f4a1576',
 'revision': 11,
 'title': 'Original synthetic two-shot native Film',
 'state': 'current',
 'active_branch': '01a15afd-f698-73ba-abe8-c3f836fe3e75',
 'branches': [{'id': '01a15afd-f698-73ba-abe8-c3f836fe3e75',
               'name': 'main',
               'parent_branch': None,
               'base_revision': 0,
               'head_revision': 11,
               'protected': False,
               'created_at': '2026-10-20T20:25:55.608056654Z'}],
 'branch_workspaces': [],
 'reviews': [],
 'merges': [],
 'scenes': [{'id': '01a15afd-f699-756d-b153-4321b12c9752',
             'name': 'Opening evidence',
             'objective': 'Two independently editable authoring scenes, one native run',
             'start': {'num': '0', 'den': '1'},
             'duration': {'num': '3', 'den': '1'},
             'renderer': 'motion-canvas',
             'status': 'draft',
             'beats': [],
             'nodes': [{'id': '01a15afd-f69b-71d4-bdf5-12cbf2548979',
                        'name': 'Source title',
                        'kind': 'text',
                        'parent_id': None,
                        'x': 180.0,
                        'y': 140.0,
                        'width': 1040.0,
                        'height': 120.0,
                        'rotation_deg': 0.0,
                        'opacity': 1.0,
                        'text': 'First authored synthetic title',
                        'coordinate_space': 'project_pixels',
                        'z_index': 1,
                        'style': {'fill': '#F5F5F2',
                                  'stroke': None,
                                  'stroke_width': 0.0,
                                  'font_family': 'Instrument Sans Variable',
                                  'font_size': 44.0,
                                  'font_weight': 700,
                                  'line_height': 1.1,
                                  'blend_mode': 'normal'},
                        'relations': [],
                        'property_locks': [],
                        'keyframes': []},
                       {'id': '01a15afd-f69b-71d4-bdf5-12cc100be9ab',
                        'name': 'Source detail',
                        'kind': 'text',
                        'parent_id': None,
                        'x': 180.0,
                        'y': 330.0,
                        'width': 1040.0,
                        'height': 120.0,
                        'rotation_deg': 0.0,
                        'opacity': 1.0,
                        'text': 'Authorial source remains editable',
                        'coordinate_space': 'project_pixels',
                        'z_index': 2,
                        'style': {'fill': '#F5F5F2',
                                  'stroke': None,
                                  'stroke_width': 0.0,
                                  'font_family': 'Instrument Sans Variable',
                                  'font_size': 44.0,
                                  'font_weight': 700,
                                  'line_height': 1.1,
                                  'blend_mode': 'normal'},
                        'relations': [],
                        'property_locks': [],
                        'keyframes': []},
                       {'id': '01a15afd-f69b-71d4-bdf5-12cd1d2d0eec',
                        'name': 'Source shape',
                        'kind': 'rectangle',
                        'parent_id': None,
                        'x': 1310.0,
                        'y': 360.0,
                        'width': 150.0,
                        'height': 150.0,
                        'rotation_deg': 0.0,
                        'opacity': 1.0,
                        'text': None,
                        'coordinate_space': 'project_pixels',
                        'z_index': 3,
                        'style': {'fill': '#F5B84A',
                                  'stroke': None,
                                  'stroke_width': 0.0,
                                  'font_family': None,
                                  'font_size': None,
                                  'font_weight': None,
                                  'line_height': None,
                                  'blend_mode': 'normal'},
                        'relations': [],
                        'property_locks': [],
                        'keyframes': []}],
             'camera': {'center_x': 960.0,
                        'center_y': 540.0,
                        'zoom': 1.0,
                        'rotation_deg': 0.0,
                        'safe_margin': 0.05}},
            {'id': '01a15afd-f69a-75e8-aca4-7f99b93bb594',
             'name': 'Following evidence',
             'objective': 'Two independently editable authoring scenes, one native run',
             'start': {'num': '3', 'den': '1'},
             'duration': {'num': '3', 'den': '1'},
             'renderer': 'motion-canvas',
             'status': 'draft',
             'beats': [],
             'nodes': [{'id': '01a15afd-f69b-71d4-bdf5-12ce06fca03e',
                        'name': 'Second source title',
                        'kind': 'text',
                        'parent_id': None,
                        'x': 180.0,
                        'y': 160.0,
                        'width': 1040.0,
                        'height': 120.0,
                        'rotation_deg': 0.0,
                        'opacity': 1.0,
                        'text': 'New scene retains original intent',
                        'coordinate_space': 'project_pixels',
                        'z_index': 1,
                        'style': {'fill': '#F5F5F2',
                                  'stroke': None,
                                  'stroke_width': 0.0,
                                  'font_family': 'Instrument Sans Variable',
                                  'font_size': 44.0,
                                  'font_weight': 700,
                                  'line_height': 1.1,
                                  'blend_mode': 'normal'},
                        'relations': [],
                        'property_locks': [],
                        'keyframes': []}],
             'camera': {'center_x': 960.0,
                        'center_y': 540.0,
                        'zoom': 1.0,
                        'rotation_deg': 0.0,
                        'safe_margin': 0.05}}],
 'markers': [],
 'assets': [{'id': '0614b713-3f5a-80a7-a333-5a8ccb514380',
             'name': 'first-party-two-tone.wav',
             'media_type': 'audio/wav',
             'content_sha256': 'd290880330e6e132410f7dacb34d55001b9022fdccf366684a082f2967e69e4a',
             'source_revision': 'measured-audio-import'}],
 'locks': [],
 'deliverables': [{'id': '01a15afd-f698-73ba-abe8-c3fbd7f43f2b',
                   'name': 'Master 16:9',
                   'width': 1920,
                   'height': 1080,
                   'language': 'en',
                   'captions': True,
                   'caption_format': 'web_vtt',
                   'video_codec': 'h264',
                   'audio_codec': 'aac',
                   'audio_sample_rate_hz': 48000,
                   'brand_profile': None,
                   'cut_label': None,
                   'parent_profile_id': None,
                   'source_revision': None,
                   'framing_strategy': 'replan',
                   'crop_approved': False,
                   'timing_locked': False,
                   'voice_track_id': '353f3608-3a8b-8aff-b83f-6419f2358ce2',
                   'text_overrides': {},
                   'included_scene_ids': [],
                   'protected_scene_ids': [],
                   'burn_in_captions': True,
                   'frame_rate': {'num': '30', 'den': '1'},
                   'color_space': 'rec709',
                   'container': 'mp4',
                   'adaptation_notes': []},
                  {'id': '01a15afd-f698-73ba-abe8-c3fc59eb7db8',
                   'name': 'Vertical 9:16',
                   'width': 1080,
                   'height': 1920,
                   'language': 'en',
                   'captions': True,
                   'caption_format': 'web_vtt',
                   'video_codec': 'h264',
                   'audio_codec': 'aac',
                   'audio_sample_rate_hz': 48000,
                   'brand_profile': None,
                   'cut_label': None,
                   'parent_profile_id': None,
                   'source_revision': None,
                   'framing_strategy': 'replan',
                   'crop_approved': False,
                   'timing_locked': False,
                   'voice_track_id': None,
                   'text_overrides': {},
                   'included_scene_ids': [],
                   'protected_scene_ids': [],
                   'burn_in_captions': False,
                   'frame_rate': {'num': '30', 'den': '1'},
                   'color_space': 'rec709',
                   'container': 'mp4',
                   'adaptation_notes': []},
                  {'id': '01a15afd-f698-73ba-abe8-c3fd228fc51c',
                   'name': 'Square 1:1',
                   'width': 1080,
                   'height': 1080,
                   'language': 'en',
                   'captions': True,
                   'caption_format': 'web_vtt',
                   'video_codec': 'h264',
                   'audio_codec': 'aac',
                   'audio_sample_rate_hz': 48000,
                   'brand_profile': None,
                   'cut_label': None,
                   'parent_profile_id': None,
                   'source_revision': None,
                   'framing_strategy': 'replan',
                   'crop_approved': False,
                   'timing_locked': False,
                   'voice_track_id': None,
                   'text_overrides': {},
                   'included_scene_ids': [],
                   'protected_scene_ids': [],
                   'burn_in_captions': False,
                   'frame_rate': {'num': '30', 'den': '1'},
                   'color_space': 'rec709',
                   'container': 'mp4',
                   'adaptation_notes': []}],
 'brief': {'objective': '', 'audience': '', 'constraints': [], 'exclusions': [], 'claims': []},
 'narrative': {'premise': '', 'beats': [], 'protected_sections': []},
 'audio': {'voice_tracks': [{'id': '353f3608-3a8b-8aff-b83f-6419f2358ce2',
                             'asset_id': '0614b713-3f5a-80a7-a333-5a8ccb514380',
                             'label': 'Synthetic six-second source, not actual narration',
                             'sample_rate_hz': 48000,
                             'channels': 2,
                             'measured_duration': {'num': '6', 'den': '1'},
                             'source_sha256': 'd290880330e6e132410f7dacb34d55001b9022fdccf366684a082f2967e69e4a',
                             'loudness_lufs': None,
                             'true_peak_dbfs': None}],
           'active_voice_track_id': '353f3608-3a8b-8aff-b83f-6419f2358ce2',
           'transcript': [{'id': '01a15afd-fb00-746d-91ba-533bf175ffb1',
                           'voice_track_id': '353f3608-3a8b-8aff-b83f-6419f2358ce2',
                           'start': {'num': '1', 'den': '5'},
                           'end': {'num': '9', 'den': '10'},
                           'text': 'Opening source-bound caption',
                           'speaker': None,
                           'alignment': {'kind': 'manual'}},
                          {'id': '01a15afd-fb02-70d6-9b2f-0b4f08ec08a6',
                           'voice_track_id': '353f3608-3a8b-8aff-b83f-6419f2358ce2',
                           'start': {'num': '11', 'den': '10'},
                           'end': {'num': '12', 'den': '5'},
                           'text': 'Second source-bound caption',
                           'speaker': None,
                           'alignment': {'kind': 'manual'}}],
           'cues': [],
           'sfx_cues': [],
           'mix': {'voice_gain_db': 0.0,
                   'music_gain_db': -12.0,
                   'target_lufs': None,
                   'target_true_peak_dbfs': None}},
 'visual_language': {'version': 1,
                     'name': 'Cut Room Ledger',
                     'palette': [{'name': 'ink', 'value': '#F2F4F3'}, {'name': 'surface', 'value': '#0F1216'}],
                     'type_tokens': [],
                     'motion_grammar': [],
                     'anti_slop_rules': [],
                     'fonts': []},
 'proposal_sets': [],
 'model_invocations': [],
 'extensions': [],
 'handoffs': [],
 'production_design': {'delivery_recipes': {'01a15afd-f698-73ba-abe8-c3fbd7f43f2b': {'schema': 'motionwright.delivery-recipe/1',
                                                                                     'scene_intents': [{'scene_id': '01a15afd-f699-756d-b153-4321b12c9752',
                                                                                                        'role': 'evidence',
                                                                                                        'archetype': 'evidence_frame'},
                                                                                                       {'scene_id': '01a15afd-f69a-75e8-aca4-7f99b93bb594',
                                                                                                        'role': 'evidence',
                                                                                                        'archetype': 'statement'}],
                                                                                     'original_mix': None}},
                       'plan': None,
                       'heroes': [],
                       'capsules': [],
                       'patches': [],
                       'procedural_fields': [],
                       'brand_profile': None,
                       'taste_profile': None,
                       'brand_exceptions': [],
                       'project_decisions': [],
                       'stages': [],
                       'captures': [],
                       'stage_capture_cards': [],
                       'measured_tables': [],
                       'measured_bindings': [],
                       'last_measured_impact': None,
                       'camera_reframes': [],
                       'evaluator_bundles': [],
                       'evaluation_reports': [],
                       'identity_origins': [],
                       'identity_revocations': [],
                       'distillations': []},
 'updated_at': '2026-10-20T20:25:56.740863680Z'}

UUID_RE=re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
SHA_RE=re.compile(r"[0-9a-f]{64}\Z")
STAMP_RE=re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z\Z")
PHASES=("original","edited")


def validate_project(value,phase):
    require(phase in PHASES and type(value) is dict,"film-project-phase")
    expected=copy.deepcopy(PROJECT_TEMPLATE)
    if phase=="edited": expected["scenes"][1]["nodes"][0]["text"]="Edited scene retained after reopening"
    mapping={}; reverse={}; hashes={}
    def match(actual,wanted,path=()):
        if type(wanted) is dict:
            require(type(actual) is dict,"film-project-object")
            if path==("production_design","delivery_recipes"):
                require(len(actual)==1 and len(wanted)==1,"film-project-recipe-count")
                old=next(iter(wanted)); new=next(iter(actual))
                match(new,old,path+("key",))
                match(actual[new],wanted[old],path+("recipe",))
            else:
                require(set(actual)==set(wanted),"film-project-unknown-or-missing-field")
                for key in wanted: match(actual[key],wanted[key],path+(key,))
        elif type(wanted) is list:
            require(type(actual) is list and len(actual)==len(wanted),"film-project-fixture-count")
            for index,(a,b) in enumerate(zip(actual,wanted)): match(a,b,path+(index,))
        elif type(wanted) is str and UUID_RE.fullmatch(wanted):
            require(type(actual) is str and UUID_RE.fullmatch(actual),"film-project-uuid")
            require(mapping.setdefault(wanted,actual)==actual and reverse.setdefault(actual,wanted)==wanted,
                    "film-project-identity-alias")
        elif type(wanted) is str and SHA_RE.fullmatch(wanted):
            require(type(actual) is str and SHA_RE.fullmatch(actual) and hashes.setdefault(wanted,actual)==actual,
                    "film-project-source-digest")
        elif type(wanted) is str and STAMP_RE.fullmatch(wanted):
            require(type(actual) is str and len(actual)<=40 and STAMP_RE.fullmatch(actual),"film-project-time")
        elif path in (("revision",),("branches",0,"head_revision")):
            require(type(actual) is int and 1<=actual<=2**53-1 and actual==value["revision"],"film-project-revision")
        elif type(wanted) is float:
            require(type(actual) in (int,float) and actual==wanted,"film-project-authored-number")
        else:
            require(type(actual) is type(wanted) and actual==wanted,"film-project-authored-literal")
    match(value,expected)
    require(value["active_branch"]==value["branches"][0]["id"],"film-project-active-branch")
    return value


def literal_policy(values):
    """A closed DSL object/array rule after independent fixture admission.

    No arbitrary JSON leaf exists: even array element unions are limited to
    fixed source fixture alternatives. Exact positional semantics are checked
    by validate_project before capture and again before intake validation.
    """
    if all(type(x) is dict for x in values):
        require(all(set(x)==set(values[0]) for x in values),"film-project-policy-object-shape")
        return Object({k:Field(literal_policy([x[k] for x in values])) for k in values[0]})
    if all(type(x) is list for x in values):
        items=[item for array in values for item in array]
        return Array(literal_policy(items) if items else Literal(None),max(map(len,values)),min(map(len,values)))
    nonnull=[x for x in values if x is not None]
    if not nonnull: return Literal(None)
    if len(nonnull)!=len(values): return Nullable(literal_policy(nonnull))
    if all(type(x) is str for x in values): return String(max(len(x) for x in values),values=tuple(sorted(set(values))))
    if all(type(x) is bool for x in values): return Literal(values[0]) if all(x==values[0] for x in values) else bool
    if all(type(x) in (int,float) for x in values):
        return Number(min(values),max(values),integer=all(type(x) is int for x in values))
    require(False,"film-project-policy-untyped-leaf")


def project_policy(raw,phase):
    value=validate_project(core.strict(raw),phase)
    return literal_policy([value])


FRAME_ROW=O(bytes=POS,file=String(32,pattern=r"frames/[0-9]{6}\.png"),index=Number(0,179,integer=True),
    sha256=SHA,max_alpha=Nullable(Number(0,255,integer=True)),min_alpha=Nullable(Number(0,255,integer=True)),
    pixel_sha256=Nullable(SHA))
MANIFEST=O(renderer=Literal("motion-canvas-core-renderer-v3.17.2"),frames=Array(FRAME_ROW,180,180),
    native_observations=O(bytes=POS,font_resources_sha256=SHA,fps_den=Literal(1),fps_num=Literal(30),
        frames=Literal(180),render_input_digest=SHA,sha256=SHA,version=Literal(1)),
    pixel_validation=O(mode=String(values=("sampled","exhaustive")),sample_indices=Array(Number(0,179,integer=True),180,1)),
    plan=O(alpha=FALSE,color_space=Literal("srgb"),end_frame_exclusive=Literal(180),first_frame=Literal(0),
        fps=Literal(30),frame_count=Literal(180),height=Literal(1080),project_duration_ms=Literal(6000),
        renderer=Literal("bundled_browser_v1"),timeout_ms=Number(1,1800000,integer=True),width=Literal(1920)))
FILM=O(**IDENTITY,frame_rate=O(num=Literal(30),den=Literal(1)),segments=Array(O(
    segment_id=Literal("segment-0"),scene_ids=Array(UUID,2,2),frame_count=Literal(180),
    plan_ref=SHA,fingerprint=SHA,
    artifact=O(directory=String(64,pattern=r"render-[0-9a-f]{32}"),first_png=RELATIVE,last_png=RELATIVE,
        frame_count=Literal(180),manifest=RELATIVE,manifest_bytes=POS,manifest_sha256=SHA)),1,1))
MASTER=O(**IDENTITY,motion_segment_id=Literal("segment-0"),frame_rate=O(num=Literal(30),den=Literal(1)),
    frame_count=Literal(180),mezzanine=MEZZANINE,source_audio=AUDIO,master=MUX,decoded_audio=ARTIFACT)
REOPEN=O(schema=Literal("motionwright.native.film180.saved-readback/1"),project_id=UUID,generation=UUID,
    revision=POS,saved_film_sha256=SHA,scene_count=Literal(2),frame_count=Literal(180),
    native_dispatch_performed_by_readback=FALSE,source_stamps_verified=TRUE,human_approval=FALSE)
SOURCE_PLAN=O(schema=Literal("motionwright.native.film180.g2-closed-inputs/1"),motionwright_SHA=SHA40,
    Semwright_source_SHA=Literal(mlt.SEM),actual_native_source_project_id=Literal("created_later_by_one_new_CAS_source"),
    MLT_original_installer_receipt_sha256=SHA,repaired_MLT_ELF_sha256=SHA,native_effects_started=FALSE,human_acceptance=FALSE)
SOURCE_CUSTODY=O(origin=Literal("CURRENT_CANONICAL_FILM180_SOURCE"),sha256=SHA,bytes=Literal(1152044),
    pcm_frames=Literal(288000),sample_rate=Literal(48000),channels=Literal(2),sample_width=Literal(2),
    historical_reference_WAV_reused=FALSE,native_effect_started=FALSE)
RUNTIME=O(schema=Literal("motionwright.native.film180-g2-inputs/1"),motionwright_sha=SHA40,
    semwright_sha=Literal(mlt.SEM),source_compiler=COMPILER_CLOSURE,installed_melt_alias=ALIAS,
    known_melt_sha256=SHA,official_runtime_receipt_sha256=SHA,installation_receipt_sha256=SHA,
    historical_reference_WAV_reused=FALSE,own_source_wave_created=FALSE,native_effect_started=FALSE)
WRAPPED_LIVE=O(original_MLT_command_basename=String(160,pattern=r"mw-[0-9a-f]{32}-r[0-9]+-[0-9a-f]{16}-master\.mp4"),
    actual_mapped_original_MLT_module_sha256=SHA,original_live_pid_observer_receipt=LIVE,
    observer_diagnostic_sha256=SHA,native_melt_effect_started_by_witness=FALSE)
SELECTED_FRAMES=(0,5,6,26,27,32,33,71,72,89,90,179)
DECODED=O(schema=Literal("motionwright.native.film180.decoded-media/1"),
    identity=O(project_id=UUID,generation=UUID,revision=POS,deliverable_id=UUID,scene_ids=Array(UUID,2,2),
        node_ids=Array(UUID,4,4),native_motion_segment_id=Literal("segment-0")),
    physical_video=O(video_codec=Literal("h264"),audio_codec=Literal("aac"),video_frames_decoded=Literal(180),
        frame_rate=Literal(30),pixel_size=Array(POS,2,2),first_pts=Number(0,0),last_pts=Number(0,7)),
    native_pixel_boundaries=O(observed_caption_transition_changed_pixels=O(**{key:POS for key in ("5_to_6","26_to_27","32_to_33","71_to_72")}),
        scene_cut_89_to_90_changed_pixels=POS,selected_native_frame_rgbs_sha256=O(**{str(k):SHA for k in SELECTED_FRAMES}),
        actual_selected_frame_count=Literal(12),native_rgbs_selected_only=TRUE,all_180_frame_pixels_compared=FALSE),
    source_bound_PCM=PCM_CONTENT,decoded_PCM_sha256=SHA,master_sha256=SHA,source_WAV_sha256=SHA,
    native_original_right_channel_tail_observed=TRUE,lossy_audio_quality_human_approved=FALSE,
    saved_source_rights_human_accepted=FALSE,original_source_reopened_in_external_NLE=FALSE,privileged_Broker_grant_verified_here=FALSE)
ALL_RGB=O(schema=Literal("motionwright.native.film180.decoded-all180-RGB24/1"),source_frame_count=Literal(180),
    observed_decode_timebase=String(40,pattern=r"[1-9][0-9]*/[1-9][0-9]*"),frame_rate=Literal("30/1"),
    decoded_format=Literal("RGB24"),decoded_pixel_size=Array(POS,2,2),bytes_per_decoded_frame=Literal(6220800),
    all_original_frame_sha256=Array(SHA,180,180),unique_decoded_RGB_frames=Number(1,180,integer=True),
    every_original_decoded_RGB_frame_observed=TRUE,original_PNG_pixel_equality_claim=FALSE,
    H264_perceptual_fidelity_claim=FALSE,human_acceptance=FALSE,source_master_sha256=SHA)
PSNR=O(schema=Literal("motionwright.native.film180.RGB-native-to-H264-PSNR/1"),observed_frame_count=Literal(180),
    observed_RGB_mse_min=Number(0,1e9),observed_RGB_mse_max=Number(0,1e9),observed_RGB_mse_mean=Number(0,1e9),
    observed_RGB_psnr_min_finite=Nullable(Number(0,1e9)),observed_RGB_psnr_mean_finite=Nullable(Number(0,1e9)),
    observed_identical_RGB_frame_count=Number(0,180,integer=True),all_frame_comparisons_performed=TRUE,
    native_PNG_alpha_channel_compared=FALSE,H264_lossy_quality_accepted_by_this_observer=FALSE,
    pixel_perfect_or_visual_human_acceptance=FALSE,
    native_source_PNG_color_type_counts=O(**{k:optional(Number(0,180,integer=True)) for k in ("0","2","3","4","6")}),
    native_RGBA_or_greyscale_alpha_PNG_frames=Number(0,180,integer=True),all180_PNG_IHDR_geometry_verified=TRUE,
    native_alpha_preserved_in_H264=FALSE,original_native_source_Film_manifest_sha256=SHA,H264_master_sha256=SHA,
    full_RGB_PSN_metrics_sha256=SHA,no_H264_quality_floor_assumed=TRUE)
PCM_CAPTURE=O(schema=Literal("motionwright.native.film180.original-pcm-capture/2"),phase=String(values=PHASES),
    master_sha256=SHA,decoded_pcm_sha256=SHA,decoded_pcm_bytes=POS,decoded_stereo_frames=POS,
    channels=Literal(2),sample_rate=Literal(48000),sample_width_bytes=Literal(2),
    decoder_exit_code=Literal(0),deadline_exceeded=FALSE,captured_from_original_decoder_stdout=TRUE,
    decoder_reinvoked_by_custody=FALSE,source_start_offset_frames_assumed=Literal(0),
    packet_PTS_and_AAC_priming_verified=FALSE,human_acceptance=FALSE)
STAGE=O(stage=String(values=PHASES),source_project_SHA=SHA,original_audio_SHA=SHA,
    Film_manifest=O(frame_count=Literal(180),original_manifest_sha256=SHA,first_original_png_sha256=SHA,
        last_original_png_sha256=SHA,every_original_frame_SHA_matched=TRUE),
    master_sha=SHA,independent_content_sha=SHA,decoded_all180_RGB_manifest_sha256=SHA,
    all_original_180_video_frames_decoded=TRUE,all180_native_PNG_vs_H264_RGB_observation_sha256=SHA,
    H264_human_perceptual_quality_acceptance=FALSE,original_H264_png_pixel_equality_claim=FALSE,
    actual_loaded_repaired_MLT_module=SHA,physical_native_media_witness=Literal("PASS"))
COMMANDS=O(**{key:optional(TERMINAL) for key in ("original.render","original.master","edited.render","edited.master")})
FINAL=O(schema=Literal("motionwright.native.film180-g2-custody/1"),
    terminal_scope=Literal("original_and_edited_film180_native_commands"),native_terminal=TERMINAL,
    native_command_terminals=COMMANDS,native_operation_retried=FALSE,source_content_and_live_module_verified=bool,
    secondary_failures=Array(LABEL,128),completed_stages=O(original=optional(STAGE),edited=optional(STAGE)),
    own_six_second_WAV_sha256=Nullable(SHA),historical_reference_WAV_reused=FALSE,
    packet_PTS_and_AAC_priming_verified=FALSE,physical_AV_master_acceptance=FALSE,human_acceptance=FALSE,release_authorized=FALSE)
STATUS=O(schema=Literal("motionwright.native.film180-original-and-edited/1"),product_source_sha=SHA40,
    semwright_source_pin=Literal(mlt.SEM),original=STAGE,edited=STAGE,
    original_edited_pixel_change=O(original_edited_comparison_frame=Literal(110),changed_decoded_pixels=POS,
        source_content_preserved=TRUE,human_visual_acceptance=FALSE),
    MLT_module_installed_SHA=SHA,actual_loaded_MLT_process_observed_here=TRUE,native_media_content_probe_completed=TRUE,
    all180_decoded_RGB24_hashes_source_bound=TRUE,H264_perceptual_pixel_fidelity_claim=FALSE,human_review=FALSE,release_authorized=FALSE)


def policies(project_rules=None):
    result=mlt.policies("film180-g2")
    result.update(film_native=FILM,film_master=MASTER,film_manifest=MANIFEST,film_reopen=REOPEN,
        film_source_plan=SOURCE_PLAN,film_source_custody=SOURCE_CUSTODY,film_runtime=RUNTIME,
        film_live=WRAPPED_LIVE,film_decoded=DECODED,film_all_rgb=ALL_RGB,film_psnr=PSNR,
        film_pcm_capture=PCM_CAPTURE,film_final=FINAL,film_status=STATUS,
        film_packet_capture=O(schema=Literal("motionwright.native.film180.original-packets/2"),
            phase=String(values=PHASES),master_sha256=SHA,original_packet_document_sha256=SHA,
            original_packet_document_bytes=POS,packet_count=Number(1,512,integer=True),
            native_effect_invoked=FALSE,automatic_retry=FALSE,packet_PTS_and_AAC_priming_verified=FALSE))
    if project_rules: result.update(project_rules)
    return result


def role_contract():
    from native_film180_command_custody import role_contract as command_roles
    result=mlt.common_contract()
    result.update(command_roles())
    result[("single", "attempt-custody", "film-source-contracts")] = (1, 1)
    for phase in PHASES:
        for role in ("saved-project","native-film","native-master-receipt","native-frame-manifest","native-master",
                     "native-mezzanine","native-decoded-wav","decoded-pcm","packets","independent-content","decoded-all180-rgb","psnr-observation","psnr-stats","audio-clock-carrier"):
            result[(phase,"readback",role)]=(1,1)
        result[(phase,"readback","native-png")]=(180,180)
        for role in ("reopened-source","live-module","consumer-snapshot","consumer-original","pcm-capture","packet-capture"):
            result[(phase,"attempt-custody",role)]=(1,1)
    for role in ("source-plan","source-custody","runtime-custody","final-custody","phase-status"):
        result[("single","attempt-custody",role)]=(1,1)
    result[("single","readback","source-wav")]=(1,1)
    result[("single","readback","staged-source-wav")]=(1,1)
    return result


class FilmSelection(mlt.Selection):
    def __init__(self,*,roots,current,source_bindings):
        super().__init__(roots=roots,current=current,scenario="film180-g2",source_bindings=source_bindings,
                         role_contract=role_contract(),json_policies=policies())

    def collect_command_originals(self, handles):
        # All destinations and limits precede any stat/read. No listing, glob,
        # wildcard label, alternate owner root or fabricated missing file exists.
        for entry in self.diagnostic_contract["entries"]:
            root, path = entry["root"], entry["relative_path"]
            if root in self.roots:
                try:
                    handles.stat_member(root, core.member_path(path))
                except FileNotFoundError:
                    self.absence.append({key: entry[key] for key in
                        ("root", "relative_path", "phase", "layer", "role")})
                    continue
                except (OSError, core.SelectionError):
                    pass
            self.add(handles, root, path, entry["role"], entry["layer"], entry["phase"],
                kind="diagnostic-original", maximum=entry["maximum"], diagnostic_type=entry["type"])

    def collect_film(self,handles):
        self.collect_command_originals(handles)
        self.common(handles)
        for path,role,policy in (("source-plan.json","source-plan","film_source_plan"),
            ("firstparty-source-custody.json","source-custody","film_source_custody"),
            ("runtime-input-custody.json","runtime-custody","film_runtime"),
            ("final-custody.json","final-custody","film_final"),
            ("original-stage-status.json","phase-status","film_status")):
            self.add(handles,"observation",path,role,"attempt-custody","single",policy)
        wave_sha=None
        for phase in PHASES:
            project_path=phase+"-project.json"; project_rule=phase+"_project"
            try:
                original,_=handles.read("observation",core.member_path(project_path),core.MAX_JSON)
                self.policies[project_rule]=project_policy(original,phase)
            except (KeyError,ValueError,OSError,core.SelectionError):
                self.policies[project_rule]=Object({})
            project=self.add(handles,"observation",project_path,"saved-project",phase=phase,
                             policy=project_rule,kind="json-original")
            if project:
                candidate=project["assets"][0]["content_sha256"]
                require(wave_sha is None or wave_sha==candidate,"film-original-edited-source-wave")
                wave_sha=candidate
            film=self.add(handles,"observation",phase+"-native-Film180-evidence.json","native-film",phase=phase,policy="film_native")
            master=self.add(handles,"observation",phase+"-H264-AAC-native-master.json","native-master-receipt",phase=phase,policy="film_master")
            for suffix,role,policy in (("-independent-AV-content.json","independent-content","film_decoded"),
                    ("-all180-decoded-original-RGB24.json","decoded-all180-rgb","film_all_rgb"),
                    ("-native-PNG-versus-H264-RGB-observation.json","psnr-observation","film_psnr")):
                self.add(handles,"observation",phase+suffix,role,phase=phase,policy=policy)
            self.add(handles,"observation",phase+"-film180-allframe-RGB-PSNR.stats","psnr-stats",phase=phase,maximum=128*1024)
            self.add(handles,"observation",phase+"-original-master-decoded.s16le","decoded-pcm",phase=phase,maximum=2*1024*1024)
            self.add(handles,"observation",phase+"-original-pcm-capture.json","pcm-capture","attempt-custody",phase,"film_pcm_capture")
            self.add(handles,"observation",phase+"-original-master-packets.json","packets",phase=phase,policy="packets")
            self.add(handles,"observation",phase+"-aac-clock-capture.json","audio-clock-carrier",phase=phase,
                     policy="aac_clock_carrier",kind="json-original",maximum=5*1024*1024)
            self.add(handles,"observation",phase+"-original-packet-capture.json","packet-capture","attempt-custody",phase,"film_packet_capture")
            self.add(handles,"observation",phase+"-reopened-before-render.json","reopened-source","attempt-custody",phase,"film_reopen")
            live=self.add(handles,"observation",phase+"-original-live-MLT-loaded-module.json","live-module","attempt-custody",phase,"film_live")
            self.live(handles,live,phase=phase,directory=phase+"-live-observer",wrapped=True)
            for path,role in ((("master","artifact"),"native-master"),(("mezzanine","artifact"),"native-mezzanine"),(("decoded_audio",),"native-decoded-wav")):
                self.derived_blob(handles,master,path,role,phase=phase)
            artifact=film["segments"][0]["artifact"] if film else None
            directory=artifact["directory"] if artifact else "unobserved-"+phase
            manifest_path="output/"+directory+"/artifact-manifest.json"
            identity=(artifact["manifest_sha256"],artifact["manifest_bytes"]) if artifact else None
            manifest=self.add(handles,"owner",manifest_path,"native-frame-manifest",phase=phase,policy="film_manifest",identity=identity,kind="json-original")
            if artifact:
                require(artifact["manifest"]==directory+"/artifact-manifest.json"
                        and artifact["first_png"]==directory+"/frames/000000.png"
                        and artifact["last_png"]==directory+"/frames/000179.png","film-artifact-path-closure")
            for index in range(180):
                row=manifest["frames"][index] if manifest else None
                if row: require(row["index"]==index and row["file"]==f"frames/{index:06d}.png","film-native-frame-order")
                self.add(handles,"owner",f"output/{directory}/frames/{index:06d}.png","native-png",phase=phase,
                    identity=(row["sha256"],row["bytes"]) if row else None,maximum=16*1024*1024)
        identity=(wave_sha,1152044) if wave_sha else None
        self.add(handles,"observation","firstparty-source.wav","source-wav",phase="single",identity=identity)
        self.add(handles,"owner","output/firstparty-film180-source.wav","staged-source-wav",phase="single",identity=identity)
        self.database(handles,"original-film180.sqlite3",wave_sha)

    def capture(self):
        return super().capture(self.collect_film)


def intake_policies(raw,current,*,require_complete=True):
    expected=mlt.checked_context(current,"film180-g2")
    rules={}
    with tarfile.open(fileobj=io.BytesIO(raw),mode="r:") as archive:
        manifest_info=archive.getmember("selected-evidence-manifest.json")
        require(manifest_info.isreg() and manifest_info.size<=core.MAX_JSON,"film-manifest-intake-budget")
        manifest=core.strict(archive.extractfile(manifest_info).read())
        from native_film180_command_custody import contract as command_contract
        core.validate_selected_manifest(manifest,expected_context=expected,role_contract=role_contract(),
            expected_diagnostic_contract=command_contract(expected),require_complete=require_complete)
        for phase in PHASES:
            rows=[row for row in manifest["members"] if (row["phase"],row["layer"],row["role"])==(phase,"readback","saved-project")]
            require(len(rows)==1,"film-source-role-count")
            row=rows[0]
            if not require_complete and row["state"] != "INCLUDED":
                rules[phase+"_project"] = Object({})
                continue
            require(row["root"]=="observation" and row["relative_path"]==phase+"-project.json"
                    and row["policy"]==phase+"_project" and row["kind"]=="json-original","film-source-role-layout")
            info=archive.getmember(row["member"])
            require(info.isreg() and not info.pax_headers and 0<info.size<=core.MAX_JSON,"film-source-intake-budget")
            data=archive.extractfile(info).read()
            require(sha(data)==row["expected_sha256"]==row["retained_sha256"] and len(data)==row["bytes"],"film-source-original-identity")
            rules[phase+"_project"]=project_policy(data,phase)
    return policies(rules)


def png_header(blob):
    require(type(blob) is bytes and len(blob)>=33 and blob[:8]==b"\x89PNG\r\n\x1a\n"
            and blob[8:16]==b"\0\0\0\rIHDR","film-original-png-header")
    width,height,depth,color,compression,filter_,interlace=struct.unpack(">IIBBBBB",blob[16:29])
    require((width,height)==(1920,1080) and depth in (8,16) and color in (0,2,3,4,6)
            and compression==filter_==0 and interlace in (0,1),"film-original-png-geometry")
    return color


def verify_film_command_intake(raw, *, current):
    """Partial original custody only; never reopens, reruns or accepts a Film."""
    from native_film180_command_custody import contract as command_contract
    expected = mlt.checked_context(current, "film180-g2")
    manifest = core.inspect_selected_tar(raw, expected_context=expected,
        role_contract=role_contract(), policies=intake_policies(raw,current,require_complete=False),
        expected_diagnostic_contract=command_contract(expected), require_complete=False)
    require({row["path"] for row in manifest["source_bindings"]} == set(mlt.FILM_SOURCE_PATHS),
            "film-command-source-closure")
    return dict(schema="motionwright.native.film180-command-intake/1", context=expected,
        command_originals=manifest["film_command_originals"],
        diagnostic_roster_complete=manifest["diagnostic_roster_complete"],
        physical_acceptance=False, human_acceptance=False, release_authorized=False,
        native_operation_retried=False)


def verify_film_intake(raw,*,current):
    from native_film180_command_custody import require_successful_capture
    intake=mlt.Intake(raw,current,"film180-g2",role_contract(),intake_policies(raw,current))
    require_successful_capture(intake.manifest["film_command_originals"])
    official,installation=intake.common()
    source=intake.value("source-custody","single","attempt-custody")
    wav=intake.original("source-wav","single")
    require((sha(wav),len(wav))==(source["sha256"],source["bytes"])
            and wav==intake.original("staged-source-wav","single")
            ==intake.original("cas-source","comparison","persistence"),"film-source-wave-cas-closure")
    intake.layout("cas-source","owner","motionwright-data/blobs/sha256/"+sha(wav)[:2]+"/"+sha(wav),"comparison","persistence")
    intake.layout("database","owner","motionwright-data/original-film180.sqlite3","comparison","persistence")
    runtime=intake.value("runtime-custody","single","attempt-custody")
    plan=intake.value("source-plan","single","attempt-custody")
    require(runtime["motionwright_sha"]==plan["motionwright_SHA"]==current["source_sha"]
            and runtime["source_compiler"]["compiled_module_sha256"]==plan["repaired_MLT_ELF_sha256"]==mlt.REPAIRED_ELF,
            "film-source-plan-runtime-binding")
    for role,key in (("official-runtime","official_runtime_receipt_sha256"),("installation","installation_receipt_sha256")):
        require(intake.one(role,"single","attempt-custody")[0]["expected_sha256"]==runtime[key],"film-runtime-admitted-receipt")
    require(plan["MLT_original_installer_receipt_sha256"]==runtime["installation_receipt_sha256"],"film-plan-installation-receipt")
    require(runtime["source_compiler"]=={key:installation[key] for key in COMPILER_CLOSURE.fields}
            and runtime["known_melt_sha256"]==runtime["installed_melt_alias"]["sha256"]==official["runtime_bindings"]["melt"]["sha256"],"film-runtime-compiler-closure")
    for role,path in (("source-plan","source-plan.json"),("source-custody","firstparty-source-custody.json"),
            ("runtime-custody","runtime-input-custody.json"),("final-custody","final-custody.json"),("phase-status","original-stage-status.json")):
        intake.layout(role,"observation",path,"single","attempt-custody")
    intake.layout("source-wav","observation","firstparty-source.wav","single")
    intake.layout("staged-source-wav","owner","output/firstparty-film180-source.wav","single")
    status=intake.value("phase-status","single","attempt-custody")
    final=intake.value("final-custody","single","attempt-custody")
    require(status["product_source_sha"]==current["source_sha"] and status["MLT_module_installed_SHA"]==mlt.REPAIRED_ELF,
            "film-status-exact-source")
    require(final["native_terminal"]=="SUCCESS_REPORTED" and final["source_content_and_live_module_verified"] is True
            and final["secondary_failures"]==[] and final["own_six_second_WAV_sha256"]==sha(wav)
            and set(final["native_command_terminals"])=={"original.render","original.master","edited.render","edited.master"}
            and all(v=="SUCCESS_REPORTED" for v in final["native_command_terminals"].values()),"film-native-command-terminals")
    projects={}; observed={}
    for phase in PHASES:
        for role,suffix in (("native-film","-native-Film180-evidence.json"),("native-master-receipt","-H264-AAC-native-master.json"),
            ("independent-content","-independent-AV-content.json"),("decoded-all180-rgb","-all180-decoded-original-RGB24.json"),
            ("psnr-observation","-native-PNG-versus-H264-RGB-observation.json"),("psnr-stats","-film180-allframe-RGB-PSNR.stats"),
            ("decoded-pcm","-original-master-decoded.s16le"),("packets","-original-master-packets.json")):
            intake.layout(role,"observation",phase+suffix,phase)
        for role,suffix in (("reopened-source","-reopened-before-render.json"),("live-module","-original-live-MLT-loaded-module.json"),
            ("pcm-capture","-original-pcm-capture.json"),("packet-capture","-original-packet-capture.json")):
            intake.layout(role,"observation",phase+suffix,phase,"attempt-custody")
        project=intake.original("saved-project",phase); projects[phase]=validate_project(project,phase)
        require(project["assets"][0]["content_sha256"]==sha(wav),"film-project-source-wave-link")
        film=intake.value("native-film",phase); receipt=intake.value("native-master-receipt",phase); mux=receipt["master"]
        identity={"project_resource":"project:"+project["id"],"generation":project["generation"],"revision":project["revision"],"deliverable_id":project["deliverables"][0]["id"]}
        require(all({key:doc[key] for key in IDENTITY}==identity for doc in (film,receipt)),"film-project-receipt-stamp")
        reopened=intake.value("reopened-source",phase,"attempt-custody")
        require((reopened["project_id"],reopened["generation"],reopened["revision"])==(project["id"],project["generation"],project["revision"]),"film-reopened-source-stamp")
        artifact=film["segments"][0]["artifact"]
        require(film["segments"][0]["scene_ids"]==[s["id"] for s in project["scenes"]],"film-scene-identity")
        manifest=intake.original("native-frame-manifest",phase)
        intake.linked("native-frame-manifest",(artifact["manifest_sha256"],artifact["manifest_bytes"]),phase)
        directory=artifact["directory"]
        intake.layout("native-frame-manifest","owner","output/"+directory+"/artifact-manifest.json",phase)
        rows=intake.all("native-png",phase)
        require(len(rows)==180,"film-all180-native-originals")
        # No ordering is trusted from a TAR. Indexed native manifest paths are
        # matched one-to-one; phases cannot borrow a neighbor's original PNG.
        by_path={row["relative_path"]:(row,value) for row,value,_ in rows}
        require(len(by_path)==180,"film-frame-path-alias")
        color_counts={}
        for index,frame in enumerate(manifest["frames"]):
            path=f"output/{directory}/frames/{index:06d}.png"
            require(frame["index"]==index and frame["file"]==f"frames/{index:06d}.png" and path in by_path,"film-native-frame-index")
            row,png=by_path[path]
            require(row["root"]=="owner" and row["kind"]=="original" and (sha(png),len(png))==(frame["sha256"],frame["bytes"]),"film-native-frame-original-link")
            color=str(png_header(png));color_counts[color]=color_counts.get(color,0)+1
        master_identity=(mux["artifact"]["sha256"],mux["artifact"]["bytes"])
        for role,art in (("native-master",mux["artifact"]),("native-mezzanine",receipt["mezzanine"]["artifact"]),("native-decoded-wav",receipt["decoded_audio"])):
            intake.linked(role,(art["sha256"],art["bytes"]),phase); intake.layout(role,"owner",mlt.owner_output(art["path"]),phase)
        require(receipt["decoded_audio"]==mux["decoded_audio"] and mux["frame_count"]==180
                and mux["video_sha256"]==receipt["mezzanine"]["artifact"]["sha256"]
                and receipt["mezzanine"]["source_manifest_sha256"]==artifact["manifest_sha256"]
                and mux["audio_sha256"]==receipt["source_audio"]["sha256"]==sha(wav),"film-master-input-chain")
        mlt.decoded_wav_shape(intake.original("native-decoded-wav",phase),mux["decoded_audio_sample_frames"])
        decoded=intake.value("independent-content",phase); pcm=intake.original("decoded-pcm",phase)
        delta=mlt.pcm_shape(wav,pcm,288000)
        require(decoded["identity"]==dict(project_id=project["id"],generation=project["generation"],revision=project["revision"],
                    deliverable_id=project["deliverables"][0]["id"],scene_ids=[s["id"] for s in project["scenes"]],
                    node_ids=[n["id"] for s in project["scenes"] for n in s["nodes"]],native_motion_segment_id="segment-0")
                and decoded["physical_video"]["pixel_size"]==[1920,1080],"film-decoded-source-identity")
        require(decoded["master_sha256"]==master_identity[0] and decoded["decoded_PCM_sha256"]==sha(pcm)
                and decoded["source_WAV_sha256"]==sha(wav),"film-decoded-original-links")
        audio=decoded["source_bound_PCM"]
        require((audio["original_wav_sha256"],audio["decoded_pcm_sha256"],audio["source_stereo_frames"],audio["decoded_stereo_frames"],audio["raw_decoded_frames_after_source"])
                ==(sha(wav),sha(pcm),288000,len(pcm)//4,delta),"film-whole-decoded-pcm-observation")
        from native_pcm_source_fidelity import inspect_source_pcm16_stereo_content
        recomputed=inspect_source_pcm16_stereo_content(wav,pcm,
            original_wav_sha256=sha(wav),decoded_pcm_sha256=sha(pcm))
        require(core.project(recomputed,PCM_CONTENT)==audio,"film-source-pcm-oracle-differs")
        custody=intake.value("pcm-capture",phase,"attempt-custody")
        require((custody["phase"],custody["master_sha256"],custody["decoded_pcm_sha256"],custody["decoded_pcm_bytes"],custody["decoded_stereo_frames"])
                ==(phase,master_identity[0],sha(pcm),len(pcm),len(pcm)//4),"film-direct-pcm-capture-link")
        packet_capture=intake.value("packet-capture",phase,"attempt-custody")
        packet_row=intake.one("packets",phase)[0]
        require(packet_capture["phase"]==phase and packet_capture["master_sha256"]==master_identity[0]
                and (packet_capture["original_packet_document_sha256"],packet_capture["original_packet_document_bytes"])
                ==(packet_row["expected_sha256"],packet_row["bytes"])
                and packet_capture["packet_count"]==len(intake.value("packets",phase)["packets"]),"film-original-packet-source-link")
        all_rgb=intake.value("decoded-all180-rgb",phase);psnr=intake.value("psnr-observation",phase)
        require(all_rgb["source_master_sha256"]==psnr["H264_master_sha256"]==master_identity[0]
                and all_rgb["decoded_pixel_size"]==[1920,1080]
                and len(set(all_rgb["all_original_frame_sha256"]))==all_rgb["unique_decoded_RGB_frames"],"film-all180-decoded-digests")
        require(psnr["original_native_source_Film_manifest_sha256"]==artifact["manifest_sha256"]
                and psnr["full_RGB_PSN_metrics_sha256"]==sha(intake.original("psnr-stats",phase))
                and psnr["native_source_PNG_color_type_counts"]==color_counts,"film-original-psnr-links")
        for index in SELECTED_FRAMES:
            require(decoded["native_pixel_boundaries"]["selected_native_frame_rgbs_sha256"][str(index)]==all_rgb["all_original_frame_sha256"][index],"film-selected-all180-decoded-link")
        live=intake.live(phase,*master_identity,official,wrapped=True,directory=phase+"-live-observer")
        stage=status[phase]
        require(stage["Film_manifest"]["original_manifest_sha256"]==artifact["manifest_sha256"]
                and stage["Film_manifest"]["first_original_png_sha256"]==manifest["frames"][0]["sha256"]
                and stage["Film_manifest"]["last_original_png_sha256"]==manifest["frames"][-1]["sha256"],"film-stage-native-frame-boundary")
        require(stage==final["completed_stages"].get(phase) and stage["stage"]==phase
                and stage["master_sha"]==master_identity[0] and stage["original_audio_SHA"]==sha(wav)
                and stage["source_project_SHA"]==intake.one("saved-project",phase)[0]["expected_sha256"]
                and stage["actual_loaded_repaired_MLT_module"]==mlt.REPAIRED_ELF,"film-final-phase-originals")
        for role,key in (("independent-content","independent_content_sha"),("decoded-all180-rgb","decoded_all180_RGB_manifest_sha256"),("psnr-observation","all180_native_PNG_vs_H264_RGB_observation_sha256")):
            require(stage[key]==intake.one(role,phase)[0]["expected_sha256"],"film-stage-original-observation")
        from native_aac_clock_custody import verify_selected_carrier
        intake.layout("audio-clock-carrier","observation",phase+"-aac-clock-capture.json",phase)
        clock_row=intake.one("audio-clock-carrier",phase)[0]
        require(clock_row["policy"]=="aac_clock_carrier" and clock_row["kind"]=="json-original",
                "film-clock-carrier-policy")
        project_row=intake.one("saved-project",phase)[0]
        verify_selected_carrier(intake.value("audio-clock-carrier",phase),phase=phase,scenario="film180-g2",
            source_document=project,
            source_identity={"sha256":project_row["expected_sha256"],"bytes":project_row["bytes"]},
            original_wav=wav,master=intake.original("native-master",phase),decoded_pcm=pcm,
            packet_identity={"sha256":packet_row["expected_sha256"],"bytes":packet_row["bytes"]})
        observed[phase]=dict(full_decoded_minus_source_samples=delta,original_png_count=len(rows),
            master_sha256=master_identity[0],actual_live_melt_pid=live["pid"],actual_live_melt_start_ticks=live["process_start_ticks"])
    original=projects["original"];edited=projects["edited"]
    require(edited["id"]==original["id"] and edited["generation"]==original["generation"]
            and edited["revision"]==original["revision"]+1,"film-original-edited-revision")
    normalized=copy.deepcopy(edited)
    normalized["revision"]=original["revision"];normalized["branches"][0]["head_revision"]=original["revision"]
    normalized["updated_at"]=original["updated_at"]
    normalized["scenes"][1]["nodes"][0]["text"]=original["scenes"][1]["nodes"][0]["text"]
    require(normalized==original,"film-edit-preserves-every-other-source-field")
    require(observed["original"]["master_sha256"]!=observed["edited"]["master_sha256"]
            and (observed["original"]["actual_live_melt_pid"],observed["original"]["actual_live_melt_start_ticks"])
            !=(observed["edited"]["actual_live_melt_pid"],observed["edited"]["actual_live_melt_start_ticks"]),"film-independent-phase-effect-identities")
    require(status["original_edited_pixel_change"]["changed_decoded_pixels"]>=250,"film-reported-edit-pixel-change")
    return dict(schema="motionwright.native.film180-selected-intake/2",context=intake.context,
        selected_layers_complete=True,source_bound_reported_native_success=True,phases=observed,
        saved_source_edit_preservation_verified=True,all_360_native_PNG_originals_retained=True,
        native_reopen_performed=False,decode_or_psnr_recomputed=False,source_content_oracle_recomputed=True,
        packet_PTS_and_AAC_priming_verified=False,privileged_Broker_grant_verified=False,
        physical_acceptance=False,human_acceptance=False,release_authorized=False,native_operation_retried=False)

# FixedTXT is a different, closed evidence family. This source preparation does
# not select a native job, authorize an attempt or inherit Film180 acceptance.
FIXEDTXT_SCOPE = "fixedtxt-v4-one-fixture"
FIXEDTXT_PLAN_SCHEMA = "motionwright.native.fixedtxt-originals-plan/1"
FIXEDTXT_KEYS = (
    "fixed-causal", "flow-control-base", "flow-control-with-fixed",
    "flow-text-control", "fixed-multirun", "fixed-multirun-control",
    "fixed-transform-clip", "fixed-camera-scene", "fixed-counter-captions",
    "fixed-layer-behind", "fixed-layer-equal", "fixed-layer-front",
)
FIXEDTXT_ARMS = ("RED", "GREEN")
FIXEDTXT_RGBA_BYTES = 1920 * 1080 * 4
FIXEDTXT_ROOTS = ("control", "inputs", "observation", "owner")
FIXEDTXT_PLAN_PATH = "fixedtxt-plan.original.json"
FIXEDTXT_MAX_JSON = 512 * 1024
FIXEDTXT_SOURCE_PATHS = tuple(sorted(set(mlt.FILM_SOURCE_PATHS) | {
    "tooling/native_film180_evidence_selection.py",
    "tooling/native_film180_original_g2.py",
    "tooling/native_evidence_projection.py",
    "tooling/ci_private_evidence.py",
    "tooling/mlt_official_runtime_intake.py",
}))


def fixedtxt_frames(fixture):
    require(type(fixture) is str and fixture in FIXEDTXT_KEYS, "fixedtxt-fixture")
    if fixture == "fixed-counter-captions":
        return (0, 29, 30, 45, 119, 120, 179)
    if fixture in ("flow-control-base", "flow-control-with-fixed", "fixed-transform-clip"):
        return (45, 120, 179)
    return (0, 45, 120, 179)


def fixedtxt_case_roster():
    """The finite source roster is not a dispatch plan or an execution result."""
    return [dict(arm=arm, fixture=fixture) for arm in FIXEDTXT_ARMS for fixture in FIXEDTXT_KEYS]


def fixedtxt_member_path(value):
    require(type(value) is str and 0 < len(value) <= 88
            and re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", value) is not None
            and all(part not in (".", "..") for part in value.split("/")),
            "fixedtxt-member-path")
    # Short ASCII paths keep the existing TAR in its regular-header grammar;
    # PAX/GNU long-name records are not another way to add unlisted originals.
    return value


def fixedtxt_required_members(fixture, native_directory):
    """Exact known V4 originals, including all 180 source PNGs and one owner."""
    frames = fixedtxt_frames(fixture)
    fixedtxt_member_path(native_directory)
    require(native_directory.startswith("output/") and native_directory.count("/") == 1,
            "fixedtxt-native-directory")
    rows = {
        ("control", FIXEDTXT_PLAN_PATH): "selection-plan",
        ("observation", "fixed-text-inputs.original.json"): "cycle-inputs",
        ("observation", "fixed-text-cycle.observation.json"): "cycle-observation",
        ("observation", "final-custody.json"): "final-custody",
        ("observation", "canonical-fixture-set/evidence/fixture-set-component.json"): "fixture-set",
        ("observation", "canonical-fixture-set/fixtures/" + fixture + "/film.original.json"): "authored-film",
        ("observation", "canonical-fixture-set/fixtures/" + fixture + "/expectations.json"): "fixture-expectations",
    }
    for name, role in (
        ("fixed-text-case.json", "case-inputs"), ("native-result.json", "native-result"),
        ("final-custody.json", "case-final-custody"),
        ("fixed.project-inspect.stdout", "project-snapshot-original"),
        ("canonical-component/evidence/project.derived.json", "compiled-project"),
        ("canonical-component/evidence/compile-component.json", "compile-component"),
        ("canonical-component/evidence/generated-inventory.json", "generated-inventory"),
        ("native-result.fixed-text/host-binding.observation.json", "host-source-binding"),
        ("owner-workspace.json", "owner-workspace"),
        ("owner-workspace-terminal.json", "owner-workspace-terminal"),
        ("owner-custody-close.observation.json", "owner-close"),
        ("owner-closed.original.json", "owner-closed-original"),
        ("owner-shutdown.intent.json", "owner-shutdown-intent"),
        ("owner-shutdown.terminal.json", "owner-shutdown-terminal"),
        ("native-result.host-stdin/session.original.json", "host-session"),
        ("native-result.host-stdin/readback.intent.json", "host-readback-intent"),
        ("native-result.host-stdin/native-return.snapshot.json", "host-return-snapshot"),
        ("native-result.host-stdin/capture-0001.stdin", "host-stdin"),
        ("native-result.host-stdin/capture-0001.json", "host-capture"),
        ("native-result.host-stdin/capture-0001.terminal.json", "host-terminal"),
        ("native-result.host-stdin/readback.observation.json", "host-readback"),
    ):
        rows[("observation", fixture + "/" + name)] = role
    readback = fixture + "/fixed-text-original-readback/"
    for name, role in (
        ("artifact-manifest.json", "native-frame-manifest"),
        ("native-observations.ndjson", "native-observations"),
        ("native-observations-receipt.json", "native-observations-receipt"),
        ("canvas-color-observations.json", "canvas-observations"),
        ("font-readiness-receipt.json", "font-readiness"),
        ("font-source-inventory.json", "font-inventory"),
        ("selected-originals.json", "selected-originals"),
    ):
        rows[("observation", readback + name)] = role
    for frame in range(180):
        rows[("owner", native_directory + f"/frames/{frame:06d}.png")] = "native-png"
    for frame in frames:
        for suffix, role in (("original.png", "selected-png"), ("decoder.stdout", "selected-rgba"),
                             ("decoder.stderr", "decoder-stderr"), ("decoder.capture.json", "decoder-terminal")):
            rows[("observation", readback + f"frame-{frame:06d}." + suffix)] = role
    return rows


def fixedtxt_validate_plan(value, *, current):
    """Validate an independent plan before paths, capture, crypto or native I/O.

    The real catalogue is empty. Positive unit fixtures are not a resource
    estimate and cannot select a source, owner, renderer or physical attempt.
    """
    import ci_private_evidence as crypto
    crypto.validate_context(current)
    require(current["run_attempt"] == "1", "fixedtxt-first-attempt")
    fields = {"schema", "scope", "context", "arm", "fixture", "case_roster",
              "source_bindings", "arm_sources", "cycle_input_sha256", "decoder_sha256",
              "native_directory", "owner_count", "expected_fixture_count",
              "default_fixture_count", "render_frames", "selected_frames", "limits", "members"}
    require(type(value) is dict and set(value) == fields and value["schema"] == FIXEDTXT_PLAN_SCHEMA
            and value["scope"] == FIXEDTXT_SCOPE and value["context"] == current,
            "fixedtxt-plan-fields")
    require(type(value["arm"]) is str and value["arm"] in FIXEDTXT_ARMS,
            "fixedtxt-arm")
    frames = fixedtxt_frames(value["fixture"])
    require(value["case_roster"] == fixedtxt_case_roster(), "fixedtxt-exact-24-case-roster")
    for name, expected in (("owner_count", 1), ("expected_fixture_count", 1),
                           ("default_fixture_count", 12), ("render_frames", 180)):
        require(type(value[name]) is int and value[name] == expected, "fixedtxt-one-case-counters")
    require(type(value["selected_frames"]) is list
            and all(type(frame) is int for frame in value["selected_frames"])
            and value["selected_frames"] == list(frames), "fixedtxt-selected-frame-roster")
    arms = value["arm_sources"]
    require(type(arms) is dict and set(arms) == set(FIXEDTXT_ARMS), "fixedtxt-two-arm-source-roster")
    for arm in FIXEDTXT_ARMS:
        source = arms[arm]
        require(type(source) is dict and set(source) == {"source_sha", "tree_sha", "native_source_sha256"},
                "fixedtxt-arm-source-fields")
        for name in ("source_sha", "tree_sha"):
            core.project(source[name], SHA40, exact=True)
        core.project(source["native_source_sha256"], SHA, exact=True)
    require(arms[value["arm"]]["source_sha"] == current["semwright_sha"]
            and (arms["RED"]["source_sha"] != arms["GREEN"]["source_sha"]
                 or arms["RED"]["tree_sha"] == arms["GREEN"]["tree_sha"])
            and arms["RED"]["native_source_sha256"] != arms["GREEN"]["native_source_sha256"],
            "fixedtxt-distinct-exact-arm-sources")
    for name in ("cycle_input_sha256", "decoder_sha256"):
        core.project(value[name], SHA, exact=True)
    bindings = value["source_bindings"]
    require(type(bindings) is list and len(bindings) == len(FIXEDTXT_SOURCE_PATHS),
            "fixedtxt-source-binding-roster")
    for row in bindings:
        require(type(row) is dict and set(row) == {"path", "sha256"}, "fixedtxt-source-binding-fields")
        require(type(row["path"]) is str, "fixedtxt-source-binding-path")
        core.project(row["sha256"], SHA, exact=True)
    require([row["path"] for row in bindings] == list(FIXEDTXT_SOURCE_PATHS),
            "fixedtxt-source-binding-roster")
    limits = value["limits"]
    require(type(limits) is dict and set(limits) == {
        "capsule_bytes", "files", "attempt_seconds", "custody_seconds"}, "fixedtxt-limit-fields")
    require(all(type(n) is int for n in limits.values())
            and 0 < limits["capsule_bytes"] <= min(crypto.MAX_TOTAL, crypto.MAX_FILE)
            and 0 < limits["files"] <= crypto.MAX_FILES
            and 0 < limits["custody_seconds"] < limits["attempt_seconds"] <= 2**31 - 1,
            "fixedtxt-finite-whole-attempt-limits")
    mandatory = fixedtxt_required_members(value["fixture"], value["native_directory"])
    members = value["members"]
    require(type(members) is list and len(mandatory) <= len(members) <= limits["files"],
            "fixedtxt-member-count")
    seen, total, tar_bound = {}, 0, 0
    extra_roles = {"input-original", "font-original", "attempt-original", "owner-original"}
    for row in members:
        require(type(row) is dict and set(row) == {
            "root", "relative_path", "role", "maximum", "expected_sha256"}, "fixedtxt-member-fields")
        require(type(row["root"]) is str and row["root"] in FIXEDTXT_ROOTS,
                "fixedtxt-root-label")
        path = fixedtxt_member_path(row["relative_path"])
        key = (row["root"], path)
        require(key not in seen, "fixedtxt-duplicate-original")
        require(type(row["role"]) is str and (row["role"] == mandatory.get(key)
                or key not in mandatory and row["role"] in extra_roles), "fixedtxt-role-layout")
        require(type(row["maximum"]) is int and 0 <= row["maximum"] <= crypto.MAX_FILE,
                "fixedtxt-member-budget")
        require(row["maximum"] > 0 or row["role"] == "decoder-stderr", "fixedtxt-empty-member-bound")
        if row["expected_sha256"] is not None:
            core.project(row["expected_sha256"], SHA, exact=True)
        if row["role"] == "selected-rgba":
            require(row["maximum"] == FIXEDTXT_RGBA_BYTES, "fixedtxt-full-rgba-bound")
        if row["role"] == "cycle-inputs":
            require(row["expected_sha256"] == value["cycle_input_sha256"], "fixedtxt-original-input-pin")
        if row["role"] == "selection-plan":
            require(row["maximum"] == FIXEDTXT_MAX_JSON and row["expected_sha256"] is None,
                    "fixedtxt-plan-no-self-hash")
        if row["root"] == "observation":
            first = path.split("/", 1)[0]
            require(first not in FIXEDTXT_KEYS or first == value["fixture"], "fixedtxt-other-fixture-original")
        seen[key] = row
        total += row["maximum"]
        tar_bound += 512 + ((row["maximum"] + 511) // 512) * 512
    require(set(mandatory) <= set(seen), "fixedtxt-required-original-missing")
    require({root for root, _path in seen} == set(FIXEDTXT_ROOTS), "fixedtxt-complete-root-roster")
    require([(row["root"], row["relative_path"]) for row in members] == sorted(seen),
            "fixedtxt-canonical-member-order")
    # The old generic artifact intake limits each cipher to MAX_FILE. Keeping
    # that stricter bound also preserves Core's total 512 MiB ceiling. Metadata,
    # TAR padding, the existing envelope and the public projection are included.
    tar_bound += 512 + FIXEDTXT_MAX_JSON + 1024
    tar_bound = ((tar_bound + 10239) // 10240) * 10240
    require(total <= crypto.MAX_TOTAL and tar_bound + 8192 + 64 + FIXEDTXT_MAX_JSON
            <= limits["capsule_bytes"], "fixedtxt-whole-capsule-budget")
    require(len(crypto.json_bytes(value)) <= FIXEDTXT_MAX_JSON, "fixedtxt-plan-size")
    return value


def fixedtxt_validate_selected_frames(originals, *, plan):
    """Read existing RGBA and terminal bytes; never decode or rerun anything."""
    import ci_private_evidence as crypto
    readback = "observation/" + plan["fixture"] + "/fixed-text-original-readback/"
    selected = crypto.strict(originals[readback + "selected-originals.json"])
    fields = {"schema", "arm", "fixture", "manifest_sha256", "native_observation_sha256",
        "original_Host_stdin_sha256", "original_font_readiness_sha256", "selected_frames",
        "actual_browser_readiness_observed", "native_effect_repeated", "oracle_v3_reused",
        "causal_pixel_verdict", "glyph_fidelity_accepted", "formal_acceptance", "human_acceptance"}
    require(set(selected) == fields and selected["schema"] == "motionwright.native.fixed-text-selected-originals/4"
            and selected["arm"] == plan["arm"] and selected["fixture"] == plan["fixture"],
            "fixedtxt-selected-original-context")
    require(selected["actual_browser_readiness_observed"] is True
            and selected["causal_pixel_verdict"] == "NOT_EVALUATED"
            and all(selected[name] is False for name in ("native_effect_repeated", "oracle_v3_reused",
                "glyph_fidelity_accepted", "formal_acceptance", "human_acceptance")),
            "fixedtxt-selected-original-scope")
    for name, path in (
        ("manifest_sha256", readback + "artifact-manifest.json"),
        ("native_observation_sha256", readback + "native-observations.ndjson"),
        ("original_font_readiness_sha256", readback + "font-readiness-receipt.json"),
        ("original_Host_stdin_sha256", "observation/" + plan["fixture"] + "/native-result.host-stdin/capture-0001.stdin"),
    ):
        require(selected[name] == sha(originals[path]), "fixedtxt-selected-linked-original")
    manifest = core.strict(originals[readback + "artifact-manifest.json"])
    native_frames = manifest.get("frames")
    require(type(native_frames) is list and len(native_frames) == 180, "fixedtxt-all180-native-roster")
    for index, frame_row in enumerate(native_frames):
        require(type(frame_row) is dict and type(frame_row.get("index")) is int
                and frame_row["index"] == index and frame_row.get("file") == f"frames/{index:06d}.png"
                and type(frame_row.get("bytes")) is int, "fixedtxt-all180-native-row")
        original = originals["owner/" + plan["native_directory"] + f"/frames/{index:06d}.png"]
        require((frame_row.get("sha256"), frame_row["bytes"]) == (sha(original), len(original)),
                "fixedtxt-all180-original-binding")
    fonts = crypto.strict(originals[readback + "font-source-inventory.json"])
    require(set(fonts) == {"originals", "glyph_fidelity_accepted", "browser_readiness_is_glyph_fidelity"}
            and fonts["glyph_fidelity_accepted"] is False and fonts["browser_readiness_is_glyph_fidelity"] is False
            and type(fonts["originals"]) is list and 1 <= len(fonts["originals"]) <= 256,
            "fixedtxt-font-original-inventory")
    font_paths = set()
    for index, font in enumerate(fonts["originals"]):
        require(type(font) is dict and set(font) == {"role", "path", "retained", "bytes", "sha256"}
                and type(font["role"]) is str and font["role"] in ("bundled", "imported")
                and type(font["path"]) is str and 0 < len(font["path"]) <= 320
                and font["retained"] == f"font-{index:04d}.original.bin"
                and type(font["bytes"]) is int, "fixedtxt-font-original-row")
        path = readback + font["retained"]
        require(path not in font_paths and path in originals
                and 0 < len(originals[path]) <= 16 * 1024 * 1024
                and (font["sha256"], font["bytes"]) == (sha(originals[path]), len(originals[path])),
                "fixedtxt-font-original-binding")
        font_paths.add(path)
    require(font_paths == {row["root"] + "/" + row["relative_path"]
                           for row in plan["members"] if row["role"] == "font-original"}
            and sum(len(originals[path]) for path in font_paths) <= 128 * 1024 * 1024,
            "fixedtxt-complete-font-roster")
    rows = selected["selected_frames"]
    frames = list(fixedtxt_frames(plan["fixture"]))
    require(type(rows) is list and len(rows) == len(frames), "fixedtxt-decoder-roster")
    for row, frame in zip(rows, frames):
        require(type(row) is dict and set(row) == {"frame", "png_sha256", "png_bytes", "rgba_sha256",
            "rgba_bytes", "original_rgba_file", "compared_with_original_SDK_pixel_sample"}
            and type(row["frame"]) is int and row["frame"] == frame
            and type(row["compared_with_original_SDK_pixel_sample"]) is bool, "fixedtxt-decoder-row")
        stem = f"frame-{frame:06d}"
        png = originals[readback + stem + ".original.png"]
        rgba = originals[readback + stem + ".decoder.stdout"]
        stderr = originals[readback + stem + ".decoder.stderr"]
        native = originals["owner/" + plan["native_directory"] + f"/frames/{frame:06d}.png"]
        require(png == native and type(row["png_bytes"]) is int
                and (row["png_sha256"], row["png_bytes"]) == (sha(png), len(png)),
                "fixedtxt-selected-png-original")
        require(type(row["rgba_bytes"]) is int and row["rgba_bytes"] == len(rgba) == FIXEDTXT_RGBA_BYTES
                and row["rgba_sha256"] == sha(rgba) and row["original_rgba_file"] == stem + ".decoder.stdout",
                "fixedtxt-original-rgba-truncated-or-changed")
        capture = crypto.strict(originals[readback + stem + ".decoder.capture.json"])
        terminal = capture.get("terminal")
        require(type(terminal) is dict and capture.get("state") == "TERMINAL_OBSERVED"
                and capture.get("automatic_retry") is False
                and type(terminal.get("exit_code")) is int and terminal["exit_code"] == 0
                and terminal.get("whole_process_completed") is True
                and terminal.get("timed_out") is False and terminal.get("output_budget_exceeded") is False
                and terminal.get("pipes_closed") is True and terminal.get("stdin_complete") is True
                and terminal.get("pipe_errors") == [] and stderr == b"", "fixedtxt-decoder-original-terminal")
        streams = capture.get("original_streams")
        require(type(streams) is dict and set(streams) == {"stdout", "stderr"}, "fixedtxt-decoder-stream-roster")
        for name, data in (("stdout", rgba), ("stderr", stderr)):
            stream = streams[name]
            require(type(stream) is dict and set(stream) == {"present", "bytes", "sha256", "buffer_collected_from_process"}
                    and stream["present"] is True and stream["buffer_collected_from_process"] is True
                    and type(stream["bytes"]) is int and (stream["sha256"], stream["bytes"]) == (sha(data), len(data)),
                    "fixedtxt-decoder-stream-binding")
        invocation = capture.get("invocation")
        require(type(invocation) is dict and set(invocation) == {
            "callable", "width", "height", "decoder", "decoder_sha256", "input_png_sha256"}
            and invocation["callable"] == "decoded_rgba_png_capture"
            and type(invocation["width"]) is int and type(invocation["height"]) is int
            and (invocation["width"], invocation["height"]) == (1920, 1080)
            and type(invocation["decoder"]) is str and 0 < len(invocation["decoder"]) <= 4096
            and invocation["decoder_sha256"] == plan["decoder_sha256"]
            and invocation["input_png_sha256"] == sha(png), "fixedtxt-decoder-invocation-binding")
    return {"selected_decode_count": len(frames), "original_rgba_reused": True,
            "decoder_invoked_by_intake": False, "pair_comparison_performed": False,
            "native_success_verified": False, "formal_acceptance": False, "human_acceptance": False}
