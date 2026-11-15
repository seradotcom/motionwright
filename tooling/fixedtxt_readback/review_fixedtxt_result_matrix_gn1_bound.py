#!/usr/bin/env python3
"""Read one mandatory FixedTxt context, never render or grant R28.

AEAD/SHA/context precede tar parsing; one authenticated buffer and bounded
views remain under the existing Windows 1536 MiB job cap and 512 MiB reserve.
All source oracle predicates remain, then 180 PNG transports and the exact
three, four or seven original contractual RGBA samples are checked. Frozen numeric measurements grant no P2/G2/F gate.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import tarfile
import time

sys.dont_write_bytecode = True
MIB = 1024 * 1024
RECIPIENT = 'dc27fc3142f7279289e36ee1900c64f04e61cf7f6a55823c1de85ecb87080585'
PURPOSE = 'candidate24'
SPEC_SHA = '6c8edaa4d082c9f980ec6b91a1c99e31481fdd2b4b6d29643f7117a251502946'
NUMERIC_SHA = '6bf3027594f978ffa338fb823be49749bb506374530052b77037bd60c2b3347a'
BOUNDED_SHA = 'eca6aaf897a2845894f73ae3bac1483dd840b41a6c2b6ab2c2bbfd1e13dc639c'
OUTER_SHA = 'e39ecb63e45dbfc4c7c35786da6c1ef4fb9b63d5b1628bb46c802cec288f17f2'
TYPED_PROBE_SHA = '5db186321c1b2864468a8c38fe62ca87703819f12e99848b038dfbcb2e65f162'


class ReadRejected(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise ReadRejected(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load(path, expected, name):
    with Path(path).open('rb') as stream:
        raw = stream.read(2 * MIB + 1)
    need(len(raw) <= 2 * MIB and sha(raw) == expected, 'physical_reader_dependency_digest')
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader=None))
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


FIXTURE_FRAMES = {
    'fixed-causal': (0, 45, 120, 179), 'flow-control-base': (45, 120, 179),
    'flow-control-with-fixed': (45, 120, 179), 'flow-text-control': (0, 45, 120, 179),
    'fixed-multirun': (0, 45, 120, 179), 'fixed-multirun-control': (0, 45, 120, 179),
    'fixed-transform-clip': (45, 120, 179), 'fixed-camera-scene': (0, 45, 120, 179),
    'fixed-counter-captions': (0, 29, 30, 45, 119, 120, 179),
    'fixed-layer-behind': (0, 45, 120, 179), 'fixed-layer-equal': (0, 45, 120, 179),
    'fixed-layer-front': (0, 45, 120, 179),
}
TRANSFORM_SPEC_SHA = 'd1bf9a45d95fb03d1db5edf2c84db7dd3a2975c6739ba95e04a6a2d3a647a3c8'
TRANSFORM_ORACLE_SHA = '5fe1e2c6f5098d8273990fccfc8c414a947b3b0b2b400a9a02dd7ff13e393086'


def closed_selection(arm, fixture):
    need(arm in ('RED', 'GREEN') and fixture in FIXTURE_FRAMES, 'physical_closed_matrix_selection')
    return arm, fixture, FIXTURE_FRAMES[fixture]


def selected_png_limit(fixture, *, session=False):
    # The authenticated session derivation reserves 746 KiB for captions.
    # Individual captions retain 750 KiB. No image or producer limit changes.
    need(fixture in FIXTURE_FRAMES and type(session) is bool,
         'physical_closed_matrix_selection')
    return ((746 if session else 750) if fixture == 'fixed-counter-captions' else 768) * 1024


def matching_source_modules(base, root, render_context):
    """Read current files only after equality with the original render commit.

    Each imported tooling module is checked before execution. HEAD is observed
    separately and never replaces source_sha in any retained run/P/R/Q record.
    """
    class MatchingSourceModules(base.SourceModules):
        def __init__(self):
            self.root, self.loaded = root, {}
            names = base.git(root, 'ls-tree', '-r', '--name-only', render_context['source_sha'], '--', 'tooling')
            need(len(names) <= 1024 * 1024, 'physical_original_tooling_roster_bound')
            self.tracked = set(names.decode('utf-8').splitlines())

        def exec_module(self, module):
            relative = 'tooling/' + module.__name__ + '.py'
            need(relative in self.tracked, 'physical_untracked_original_tooling_import')
            raw = base.read_stable(self.root / relative, 2 * MIB)
            original = base.git(self.root, 'show', render_context['source_sha'] + ':' + relative)
            need(len(original) <= 2 * MIB and raw == original, 'physical_tooling_not_original_relevant_source')
            self.loaded[relative] = {'sha256': sha(raw), 'bytes': len(raw)}
            module.__file__ = str(self.root / relative)
            module.__cached__ = None
            exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
    return MatchingSourceModules()


def relevant_source_identity(root, render_context, base, source_paths):
    base.inspect_path(root, directory=True)
    original_sha = render_context['source_sha']
    need(base.git(root, 'rev-parse', '--verify', original_sha + '^{commit}').decode().strip() == original_sha,
         'physical_original_source_commit_unavailable')
    paths = sorted(set(base.FOUR_LOCKS) | set(source_paths))
    need(0 < len(paths) <= 4096 and all(type(path) is str and not path.startswith('/')
         and '\\' not in path and '..' not in path.split('/') for path in paths), 'physical_source_closure_roster')
    records = {}
    for relative in paths:
        original = base.git(root, 'show', original_sha + ':' + relative)
        need(0 < len(original) <= 16 * MIB, 'physical_original_source_blob_bound')
        current = base.read_stable(root / relative, 16 * MIB)
        need(current == original, 'physical_relevant_source_differs:' + relative)
        records[relative] = {'bytes': len(current), 'sha256': sha(current)}
    lock = base.strict(base.read_stable(root / 'SOURCE_LOCK.json', 4 * MIB))
    need(lock['dependencies']['semwright']['revision'] == render_context['semwright_sha'],
         'physical_relevant_source_sdk_differs')
    return {'render_source_sha': original_sha, 'semwright_sha': render_context['semwright_sha'],
            'render_source_tree': base.git(root, 'rev-parse', original_sha + '^{tree}').decode().strip(),
            'read_HEAD': base.git(root, 'rev-parse', '--verify', 'HEAD').decode().strip(),
            'relevant_files': records, 'relevant_files_sha256': sha(base.json_bytes(records)),
            'original_context_rewritten': False}


def same_relevant_source(before, after):
    return all(before[key] == after[key] for key in
        ('render_source_sha', 'semwright_sha', 'render_source_tree', 'relevant_files', 'relevant_files_sha256'))


def held_from_snapshot(snapshot, dispatch_raw, fixed, bounded, base, context, selection):
    arm, fixture, selected_frames = selection
    requested = {'control/' + fixed.PLAN_MEMBER: 512 * 1024,
                 'inputs/' + fixed.RUNTIME_MEMBER: 65536,
                 'inputs/' + fixed.CYCLE_MEMBER: MIB}
    values, seen = {}, set()
    with bounded.BorrowedReader(snapshot) as stream, tarfile.open(fileobj=stream, mode='r:') as archive:
        for item in archive:
            need(len(seen) < 4096 and item.name not in seen and item.isreg() and not item.pax_headers,
                 'physical_PRQ_roster')
            base.member_parts(item.name)
            seen.add(item.name)
            if item.name in requested:
                need(0 < item.size <= requested[item.name], 'physical_PRQ_size')
                values[item.name] = archive.extractfile(item).read(requested[item.name] + 1)
                need(len(values[item.name]) == item.size, 'physical_PRQ_truncated')
    need(set(values) == set(requested), 'physical_PRQ_missing')
    p, r, q = [values[name] for name in requested]
    runtime = base.strict(r, 65536)
    runner_temp = str(__import__('pathlib').PurePosixPath(runtime['fixedtxt_plan']['path']).parent.parent)
    held = fixed.admit_inputs(dispatch_raw, p, r, q, current=context, runner_temp=runner_temp)
    plan = fixed.strict(p, 512 * 1024)
    need(plan['arm'] == arm and plan['fixture'] == fixture and plan['selected_frames'] == list(selected_frames)
         and plan['render_frames'] == 180 and plan['owner_count'] == 1, 'physical_one_candidate_selection')
    return held, plan, runner_temp


def verify_candidate_request(originals, plan, route, selection, *, source_tree):
    arm, fixture, _ = selection
    raw = originals.small('control/request.original.json', 49152)
    request = route.checked_request(raw, current=plan['context'])
    need((request.get('arm'), request.get('fixture'), request.get('purpose')) == (arm, fixture, PURPOSE)
         and request.get('source_tree') == source_tree
         and all(row['source_sha'] == request['semwright_sha']
                 and row['tree_sha'] == request['semwright_tree'] for row in plan['arm_sources'].values()),
         'physical_original_candidate_selection')
    return {'arm': arm, 'fixture': fixture, 'purpose': PURPOSE,
            'request_sha256': sha(raw), 'candidate24_total_contexts': 24}


def original_transport_binding(originals, plan, route, serial, selection, runner_temp):
    """Resolve one PNG bound only from authenticated P-pinned request/clock originals."""
    from pathlib import PurePosixPath
    arm, fixture, selected_frames = selection
    rows = {(row['root'], row['relative_path']): row for row in plan['members']}
    need(len(rows) == len(plan['members']), 'physical_original_plan_duplicate_member')

    def pinned_input(name, maximum):
        row = rows.get(('control', name))
        need(row is not None and row['role'] == 'input-original'
             and type(row.get('expected_sha256')) is str
             and re.fullmatch(r'[0-9a-f]{64}', row['expected_sha256'])
             and type(row['maximum']) is int and 0 < row['maximum'] <= maximum,
             'physical_transport_original_not_independently_pinned')
        raw = originals.small('control/' + name, row['maximum'])
        need(type(raw) is bytes and 0 < len(raw) <= row['maximum']
             and sha(raw) == row['expected_sha256'], 'physical_transport_original_changed')
        return raw

    request_raw = pinned_input('request.original.json', 49152)
    clock_raw = pinned_input('clock.original.json', 65536)
    request = route.checked_request(request_raw, current=plan['context'])
    # This is a closed-run readback. Bind the original runner clock literally;
    # the reader's local boot ID and monotonic clock cannot represent that host.
    # No live permission or deadline is issued or extended by this validation.
    clock = route.strict(clock_raw, 65536)
    need(set(clock) == {'schema', 'context', 'boot_id', 'started_monotonic',
                       'deadline_monotonic', 'request_sha256'}
         and clock['schema'] == 'motionwright.native.fixedtxt-original-clock/1'
         and clock['context'] == plan['context']
         and type(clock['boot_id']) is str
         and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', clock['boot_id']),
         'physical_original_clock_context')
    start, total = clock['started_monotonic'], clock['deadline_monotonic']
    need(all(type(value) in (int, float) and math.isfinite(value) for value in (start, total))
         and 0 <= start < total
         and abs(total - start - request['limits']['attempt_seconds']) < 0.000001,
         'physical_original_clock_duration')
    need(request['limits'] == plan['limits']
         and (request['arm'], request['fixture']) == (arm, fixture)
         and (plan['arm'], plan['fixture']) == (arm, fixture)
         and clock['request_sha256'] == sha(request_raw), 'physical_original_request_clock_plan_binding')
    present = {name for name in serial.SESSION_MEMBERS if ('control', name) in rows}
    need(not present or present == set(serial.SESSION_MEMBERS), 'physical_partial_session_originals')
    raws = {name: pinned_input(name, maximum) for name, maximum in serial.SESSION_MEMBERS.items()
            if name in present}
    session = serial.validate_clock_bundle(raws, request_raw, clock_raw,
        current=plan['context'], runner_temp=runner_temp)
    need(session is not None or not PurePosixPath(runner_temp).name.startswith('fx'),
         'physical_session_namespace_without_originals')
    png_limit = selected_png_limit(fixture, session=session is not None)
    for role, count in (('native-png', 180), ('selected-png', len(selected_frames))):
        members = [row for row in plan['members'] if row['role'] == role]
        need(type(request['role_limits'][role]) is int and request['role_limits'][role] == png_limit
             and len(members) == count
             and all(type(row['maximum']) is int and row['maximum'] == png_limit for row in members),
             'physical_predispatch_PNG_role_bound')
    return {'mode': 'session' if session is not None else 'individual',
            'png_limit_bytes': png_limit, 'request_sha256': sha(request_raw),
            'clock_sha256': sha(clock_raw),
            'session_index': session['binding']['index'] if session is not None else None,
            'session_original_sha256': {name: sha(raw) for name, raw in raws.items()},
            'producer_limits_modified': False, 'PNG_rewritten': False}


def verify_public(raw, reference, held, fixed, context):
    """The exact public predicates of verify_encrypted; ciphertext is streamed."""
    dispatch = fixed.validate_dispatch(held.dispatch_raw, context)
    plan = fixed.strict(held.plan_raw, 512 * 1024)
    public = fixed.strict(raw, 512 * 1024)
    need(reference['bytes'] + len(raw) <= plan['limits']['capsule_bytes'], 'fixedtxt-v2-encrypted-capsule-budget')
    need(set(public) == {'schema', 'scope', 'context', 'arm', 'fixture', 'plan_sha256',
        'external_dispatch_sha256', 'recipient_spki_sha256', 'sealed_originals_sha256',
        'sealed_originals_bytes', 'planned_originals_complete', 'decoder_originals_verified',
        'supervisor_originals_verified', 'retained_file_count', 'missing_file_count',
        'native_success_verified', 'native_operation_retried', 'physical_acceptance',
        'human_acceptance', 'release_authorized', 'upload_authorization'}
        and fixed.canonical(public) == raw
        and public['schema'] == 'motionwright.native.fixedtxt-custody-public/2'
        and public['scope'] == fixed.SCOPE and public['context'] == context
        and (public['arm'], public['fixture']) == (plan['arm'], plan['fixture'])
        and public['plan_sha256'] == sha(held.plan_raw)
        and public['external_dispatch_sha256'] == sha(held.dispatch_raw)
        and public['recipient_spki_sha256'] == dispatch['recipient']['sha256'] == RECIPIENT
        and type(public['sealed_originals_bytes']) is int and public['sealed_originals_bytes'] == reference['bytes']
        and public['sealed_originals_sha256'] == reference['sha256']
        and public['planned_originals_complete'] is True and public['decoder_originals_verified'] is True
        and public['supervisor_originals_verified'] is True
        and type(public['retained_file_count']) is int and public['retained_file_count'] == len(plan['members'])
        and type(public['missing_file_count']) is int and public['missing_file_count'] == 0
        and public['upload_authorization'] == 'NOT_GRANTED_BY_COLLECTOR'
        and all(public[name] is False for name in ('native_success_verified', 'native_operation_retried',
            'physical_acceptance', 'human_acceptance', 'release_authorized')), 'fixedtxt-v2-public-capsule-binding')
    return public


def decode_digest(png, ffmpeg, bounded, run_bounded, png_limit):
    need(type(png) is bytes and 0 < len(png) <= png_limit, 'physical_decoder_stdin_bound')
    argv = [str(ffmpeg), '-nostdin', '-hide_banner', '-loglevel', 'error', '-xerror',
        '-noautorotate', '-protocol_whitelist', 'pipe', '-threads', '1', '-f', 'image2pipe',
        '-framerate', '30', '-c:v', 'png', '-i', 'pipe:0', '-map', '0:v:0', '-an', '-sn', '-dn',
        '-frames:v', '1', '-pix_fmt', 'rgba', '-threads', '1', '-f', 'framehash', '-hash', 'SHA256', 'pipe:1']
    capture = {}
    try:
        raw, terminal = run_bounded(argv, 100000, 60, input_bytes=png, capture=capture,
                                   max_stdin_bytes=png_limit)
        need(capture.get('stderr') == b'' and terminal.get('exit_code') == 0
             and terminal.get('whole_process_completed') is True, 'physical_decoder_terminal')
        digest = bounded.parse_framehash(raw, 1, 1920, 1080)[0]
        return digest, {'input_png_sha256': sha(png), 'input_bytes': len(png),
                        'framehash_sha256': sha(raw), 'elapsed_seconds': terminal['elapsed_seconds'],
                        'exit_code': 0, 'whole_process_completed': True, 'stderr_bytes': 0}
    except ReadRejected:
        raise
    except BaseException:
        raise ReadRejected('physical_decoder_failed_closed') from None
    finally:
        capture.clear()


def native_profile(originals, plan, base, selection):
    _, fixture, selected_frames = selection
    """Recheck the producer's original font, canvas and observation bindings."""
    prefix = 'observation/' + fixture + '/'
    readback = prefix + 'fixed-text-original-readback/'
    binding = originals.json(prefix + 'native-result.fixed-text/host-binding.observation.json', 2 * MIB)
    config = binding['original_config']
    manifest = originals.json(readback + 'artifact-manifest.json', MIB)
    receipt = originals.json(readback + 'native-observations-receipt.json', MIB)
    requested = config.get('textPixelObservationFrames')
    need(type(requested) is list and requested == list(selected_frames)
         and all(type(frame) is int for frame in requested)
         and manifest['plan'].get('text_pixel_observation_frames') == requested,
         'physical_original_text_pixel_Host_plan_sample_roster')
    view = originals.view(readback + 'native-observations.ndjson', 8 * MIB)
    selected, frames, start = {}, 0, 0
    try:
        need(receipt == manifest['native_observations'] and receipt['sha256'] == sha(view)
             and receipt['bytes'] == len(view) and receipt['frames'] == 180
             and receipt['fps_num'] == 30 and receipt['fps_den'] == 1
             and receipt['render_input_digest'] == config['renderInputDigest']
             and receipt['font_resources_sha256'] == config['fontResourcesDigest'],
             'physical_native_observation_binding')
        for offset, value in enumerate(view):
            need(offset - start <= 524288, 'physical_native_observation_line_budget')
            if value == 10:
                line = view[start:offset]
                try:
                    row = base.strict(bytes(line), 524288)
                finally:
                    line.release()
                need(type(row.get('frame')) is int and row['frame'] == frames and frames < 180,
                     'physical_native_observation_sequence')
                if frames in selected_frames:
                    selected[frames] = row
                frames += 1
                start = offset + 1
        need(start == len(view) and frames == 180, 'physical_native_observation_count')
    finally:
        view.release()
    canvas_raw = originals.small(readback + 'canvas-color-observations.json', 4 * MIB)
    colors = base.strict(canvas_raw, 4 * MIB)
    pin = manifest['canvas_color_observation']
    need(pin['path'] == 'canvas-color-observations.json' and pin['bytes'] == len(canvas_raw)
         and pin['sha256'] == sha(canvas_raw) and colors['schema'] == 'semwright.motion-canvas-context-color/1'
         and colors['render_input_digest'] == config['renderInputDigest'] and len(colors['frames']) == 180,
         'physical_original_canvas_binding')
    for index, row in enumerate(colors['frames']):
        canvas = row['canvas']
        need(row['index'] == row['frame'] == index and row['png_sha256'] == manifest['frames'][index]['sha256']
             and set(canvas) == {'api', 'color_space', 'alpha', 'width', 'height'}
             and canvas['api'] == 'CanvasRenderingContext2D.getContextAttributes'
             and canvas['color_space'] == 'srgb' and type(canvas['alpha']) is bool
             and (canvas['width'], canvas['height']) == (1920, 1080), 'physical_actual_canvas_profile')
        if 'canvas_color' in manifest['frames'][index]:
            need(manifest['frames'][index]['canvas_color'] == canvas, 'physical_canvas_manifest_binding')
    fonts = originals.json(readback + 'font-readiness-receipt.json', MIB)
    ready = fonts['readiness']
    need(fonts['version'] == 1 and fonts['render_input_digest'] == config['renderInputDigest']
         and fonts['font_resources_sha256'] == config['fontResourcesDigest']
         and fonts['managed_fonts'] == config['managedFonts']
         and (fonts['first_frame'], fonts['end_frame_exclusive'], fonts['fps_num'], fonts['fps_den']) == (0, 180, 30, 1)
         and fonts['authoring'] is True and ready['observed_before_renderer_render'] is True
         and ready['status_after_ready'] == 'loaded'
         and len(ready['requests']) == 5 + sum(len(font['weights']) for font in config['managedFonts']),
         'physical_original_browser_font_readiness')
    inventory = originals.json(readback + 'font-source-inventory.json', MIB)
    expected_fonts = [('bundled', pin) for pin in config['fontResourcePins']]
    expected_fonts += [('imported', pin) for pin in config['managedFonts']]
    need(len(expected_fonts) == len(inventory['originals']), 'physical_original_font_roster')
    for row, (role, pin) in zip(inventory['originals'], expected_fonts, strict=True):
        need(row['role'] == role and row['path'] == pin['path'] and row['sha256'] == pin['sha256'],
             'physical_font_config_identity')
    return {'bound_observation_frames': frames, 'bound_sRGB_canvas_frames': 180,
            'native_text_pixel_profile_bound': True, 'native_text_pixel_sample_frames': requested,
            'native_browser_font_readiness_verified': True, 'font_source_config_identities_verified': True,
            'font_readiness_request_count': len(ready['requests']),
            'original_font_readiness_sha256': originals.descriptor(readback + 'font-readiness-receipt.json')['sha256'],
            'selected_observation_sha256': {str(frame): sha(base.json_bytes(row)) for frame, row in selected.items()},
            'selected_observations': selected}


def adapt_native_profile(originals, plan, profile, spec, base, typed, selection, transform_spec_raw):
    _, fixture, selected_frames = selection
    """Bind exact original observations before discarding the selected rows."""
    probes, adaptation = typed.bind_originals(originals, plan, profile, spec, base, transform_spec_raw=transform_spec_raw)
    details = adaptation['selected_frame_bindings']
    need(len(details) == len(selected_frames) and [row['frame'] for row in details] == list(selected_frames)
         and set(probes) == set(selected_frames), 'physical_typed_probe_frame_roster')
    profile['Txt_classification'] = {
        'basis': 'ORIGINAL_NATIVE_RUNTIME_CLASS_OBSERVATION'
            if all(row['native_type_serialized'] for row in details)
            else 'ABSENT_IN_ORIGINAL_NATIVE_OBSERVATIONS',
        'native_source_sha256': adaptation['producer_native_source_sha256'],
        'native_type_serialized': all(row['native_type_serialized'] for row in details),
        'native_type_known_Txt': all(row['native_type_known_Txt'] for row in details),
        'typed_geometry_probe_present': all(probes[frame] is not None for frame in selected_frames),
        'typed_geometry_known': all(row['native_geometry_complete'] for row in details)}
    profile['missing_producer_geometry_fields'] = sorted({field for row in details
        for field in row['missing_native_observations']})
    profile['physical_font_and_text_binding_complete'] = all(
        row['font_asset_and_text_identity_verified'] for row in details)
    del profile['selected_observations']
    return probes, adaptation


def physical_readback(originals, plan, held, bounded, base, ffmpeg, ffmpeg_sha, numeric, spec, typed,
                      selection, png_limit, transform_spec_raw=None, transform_numeric=None):
    arm, fixture, selected_frames = selection
    need(type(png_limit) is int and 0 < png_limit <= selected_png_limit(fixture),
         'physical_validated_png_bound')
    import numpy as np
    import PIL
    from manim_native_readback import run_bounded
    from native_motion_canvas_roundtrip import validate_png_crc_bytes
    before = bounded.decoder_file_reference(ffmpeg, ffmpeg_sha, base)
    native_root = 'owner/' + plan['native_directory'] + '/frames/'
    readback = 'observation/' + fixture + '/fixed-text-original-readback/'
    profile = native_profile(originals, plan, base, selection)
    probes, adaptation = adapt_native_profile(originals, plan, profile, spec, base, typed, selection, transform_spec_raw)
    png_chain = hashlib.sha256()
    started = time.monotonic()
    # Every mandatory original is CRC checked; the unchanged fixture roster
    # selects exactly three, four or seven decoded frames. No PNG is transformed, authored or saved.
    for index in range(180):
        bounded.require_reserve()
        view = originals.view(native_root + f'{index:06d}.png', png_limit)
        try:
            png = bytes(view)
        finally:
            view.release()
        header = validate_png_crc_bytes(png, 1920, 1080, max_bytes=png_limit)
        need(header['animated'] is False and header['bit_depth'] == 8
             and header['color_type'] in (2, 6), 'physical_original_png_mode')
        png_chain.update(sha(png).encode('ascii'))
        del png
    frames = []
    with ffmpeg.open('rb') as executable:
        opened = os.fstat(executable.fileno())
        need(bounded.decoder_opened_matches(tuple(before['namespace_identity']), opened, ffmpeg, base),
             'physical_decoder_open_identity')
        for index in selected_frames:
            need(time.monotonic() - started <= 300, 'physical_total_decode_deadline')
            bounded.require_reserve()
            view = originals.view(native_root + f'{index:06d}.png', png_limit)
            try:
                png = bytes(view)
            finally:
                view.release()
            digest, terminal = decode_digest(png, ffmpeg, bounded, run_bounded, png_limit)
            del png
            rgba = originals.view(readback + f'frame-{index:06d}.decoder.stdout', 1920 * 1080 * 4)
            try:
                need(len(rgba) == 1920 * 1080 * 4 and sha(rgba) == digest,
                     'physical_selected_RGBA_differs_from_original_PNG')
                image = np.frombuffer(rgba, dtype=np.uint8).reshape(1080, 1920, 4)
                try:
                    measurement = (transform_numeric.evaluate_transform(base.strict(transform_spec_raw, 4096), spec, image, probes[index])
                                   if fixture == 'fixed-transform-clip' else
                                   numeric.numeric_frame(spec, fixture, index, image, probe=probes[index]))
                    colors = {name: int(np.count_nonzero(np.all(image[:, :, :3] == rgb, axis=2)
                              & (image[:, :, 3] >= spec['thresholds']['alpha_required_min'])))
                              for name, rgb in [('neutral', [244, 244, 244]), ('gold', [212, 166, 42]),
                                                ('blue', [59, 130, 246]), ('background', [17, 20, 24])]}
                finally:
                    del image
                frames.append({'frame': index, 'RGBA_sha256': digest, 'decoder': terminal,
                               'frozen_numeric_oracle': measurement, 'typed_original_probe': probes[index],
                               'exact_authored_sRGB_pixels': colors})
            finally:
                rgba.release()
        need(base.identity(opened) == base.identity(os.fstat(executable.fileno())), 'physical_decoder_handle_changed')
    need(bounded.decoder_file_reference(ffmpeg, ffmpeg_sha, base) == before, 'physical_decoder_changed')
    fonts = originals.json(readback + 'font-source-inventory.json', MIB)
    return {'mandatory_original_PNGs_checked': 180, 'selected_RGBA_decoded_and_exactly_equal': len(selected_frames),
            'original_PNG_chain_sha256': png_chain.hexdigest(), 'selected_frames': frames,
            'font_originals_bound': len(fonts['originals']),
            'font_original_bytes': sum(row['bytes'] for row in fonts['originals']),
            'font_inventory_sha256': originals.descriptor(readback + 'font-source-inventory.json')['sha256'],
            'native_font_color_profile': profile,
            'typed_probe_adapted': all(probes[frame] is not None for frame in selected_frames),
            'typed_original_adaptation': adaptation,
            'typed_probe_adapter_sha256': TYPED_PROBE_SHA,
            'font_glyph_baseline_status': 'ORIGINAL_TYPED_GEOMETRY_FONT_AND_TEXT_BOUND'
                if adaptation['all_selected_geometry_and_font_text_bound']
                else 'INCOMPLETE_TYPED_GEOMETRY_FONT_OR_TEXT_IDENTITY',
            'numeric_oracle_sha256': NUMERIC_SHA, 'numeric_spec_sha256': SPEC_SHA,
            'numeric_thresholds_modified': False, 'numpy_version': np.__version__, 'pillow_version': PIL.__version__,
            'decoder_reference': before, 'decoder_maximum_active_processes': 1,
            'decoder_stdin_limit_bytes': png_limit, 'decoder_stdout_limit_bytes': 100000,
            'decoder_stderr_limit_bytes': 65536, 'decoder_timeout_seconds': 60,
            'total_decode_deadline_seconds': 300, 'private_plaintext_written': False,
            'native_effects_invoked': 0, 'candidate24_accepted': 0, 'P2_accepted': False, 'G2_accepted': False, 'F_accepted': False}


class ReadbackOutput:
    """Write only into the private output directory this invocation created."""

    def __init__(self, base, value, source, disk_options):
        path = base.absolute_e(value, private=True)
        need(not os.path.lexists(path) and not path.is_relative_to(source),
             'new_private_output_required')
        base.inspect_path(path.parent, directory=True)
        base.disk_admission(path.parent, **disk_options)
        path.mkdir(mode=0o700)
        # A rejected pre-existing path or lost mkdir race cannot construct an
        # owner. Failure reporting therefore has no authority over that path.
        base.inspect_path(path, directory=True)
        observed = path.lstat()
        self.path, self.base, self.disk_options = path, base, disk_options
        self.identity = (observed.st_dev, observed.st_ino)

    def write(self, name, state):
        need(name in {'readback.started.json', 'readback.terminal.json'},
             'readback_output_name')
        self.base.inspect_path(self.path, directory=True)
        observed = self.path.lstat()
        need((observed.st_dev, observed.st_ino) == self.identity,
             'readback_output_identity_changed')
        self.base.new_bytes(self.path / name, self.base.json_bytes(state),
                            disk_options=self.disk_options)


def main(*, configure_private_paths=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source-root', 'expected-source', 'expected-sdk', 'run-id', 'envelope', 'envelope-sha256',
                 'public-original', 'public-sha256', 'external-dispatch', 'external-dispatch-sha256',
                 'private-key', 'result-adapter', 'result-adapter-sha256', 'bounded-helper',
                 'approved-helper', 'preparation-reader', 'typed-probe-adapter', 'numeric-oracle', 'numeric-spec',
                 'ffmpeg', 'ffmpeg-sha256', 'output-dir'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--arm', choices=('RED', 'GREEN'), required=True)
    parser.add_argument('--fixture', choices=tuple(FIXTURE_FRAMES), required=True)
    parser.add_argument('--transform-oracle')
    parser.add_argument('--transform-spec')
    args = parser.parse_args()
    selection = closed_selection(args.arm, args.fixture)
    transform_numeric = transform_spec_raw = None
    snapshot = output = base = None
    phase = 'arguments'
    state = {'schema': 'motionwright.fixedtxt.bounded-authentic-result-readback/1',
             'native_calls': 0, 'R28_issued': False, 'private_plaintext_written': False,
             'candidate24_executed_contexts': 0, 'candidate24_total_contexts': 24, 'candidate24_accepted': 0,
             'G2_accepted': False, 'P2_accepted': False, 'F_accepted': False, 'automatic_retry': False}
    disk_options = {'storage_mode': 'reuse-selected', 'max_new_bytes': 128 * MIB, 'disk_floor_gib': 10}
    try:
        need(os.name == 'nt', 'windows_required')
        bounded = load(args.bounded_helper, BOUNDED_SHA, 'fixedtxt_result_bounded_base')
        limiter = bounded.ProcessMemoryLimit()
        bounded.require_reserve()
        base = bounded.load_approved_helper(args.approved_helper)
        # Root selection belongs to the separately authenticated private wrapper.
        # Direct CLI use keeps the approved helper's original path policy.
        if configure_private_paths is not None:
            configure_private_paths(base)
        outer = load(args.preparation_reader, OUTER_SHA, 'fixedtxt_result_original_reader')
        adapter = load(args.result_adapter, args.result_adapter_sha256, 'fixedtxt_result_storage_seam')
        numeric = load(args.numeric_oracle, NUMERIC_SHA, 'fixedtxt_frozen_numeric_oracle')
        typed = load(args.typed_probe_adapter, TYPED_PROBE_SHA, 'fixedtxt_original_typed_probe')
        spec_path = base.absolute_e(args.numeric_spec)
        spec_raw = base.read_stable(spec_path, 65536)
        need(sha(spec_raw) == SPEC_SHA, 'frozen_numeric_spec_digest')
        spec = numeric.load_spec(spec_path)
        need({key: tuple(row['selected_frames']) for key, row in spec['fixtures'].items()} == FIXTURE_FRAMES
             and typed.FIXTURES == FIXTURE_FRAMES, 'physical_exact_original_matrix_roster')
        if args.fixture == 'fixed-transform-clip':
            need(args.transform_oracle is not None and args.transform_spec is not None, 'physical_original_R38_required')
            transform_spec_raw = base.read_stable(base.absolute_e(args.transform_spec), 4096)
            need(len(transform_spec_raw) == 2393 and sha(transform_spec_raw) == TRANSFORM_SPEC_SHA,
                 'physical_frozen_R38_spec_digest')
            sys.modules['fixedtxt_numeric_oracle'] = numeric
            transform_numeric = load(args.transform_oracle, TRANSFORM_ORACLE_SHA, 'fixedtxt_original_R38_oracle')
        else:
            need(args.transform_oracle is None and args.transform_spec is None, 'physical_no_foreign_oracle_selection')
        need(base.read_stable(spec_path, 65536) == spec_raw, 'frozen_numeric_spec_changed')
        source = base.absolute_e(args.source_root)
        need(all(re.fullmatch(r'[0-9a-f]{40}', value) for value in (args.expected_source, args.expected_sdk))
             and re.fullmatch(r'[1-9][0-9]{0,19}', args.run_id), 'physical_exact_source_context')
        # The explicit reviewed SDK revision must equal the actual SOURCE_LOCK
        # and every P/R/Q binding. This argument cannot move or rewrite the pin.
        context = {'source_sha': args.expected_source, 'semwright_sha': args.expected_sdk,
                   'run_id': args.run_id, 'run_attempt': '1'}
        relevant_source_identity(source, context, base, ())
        loader = matching_source_modules(base, source, context)
        sys.meta_path.insert(0, loader)
        fixed = importlib.import_module('native_fixedtxt_g2_admission')
        route = importlib.import_module('native_fixedtxt_workflow')
        serial = importlib.import_module('native_fixedtxt_session')
        seal = importlib.import_module('ci_private_evidence')
        source_lock = importlib.import_module('source_lock_admission')
        source_lock.verify_source_lock(source, source_lock.load_source_lock(source))
        before = relevant_source_identity(source, context, base, fixed.source_paths())
        validate, io_scope = adapter.make_oracle(fixed, bounded)
        public_path = base.absolute_e(args.public_original, private=True)
        dispatch_path = base.absolute_e(args.external_dispatch, private=True)
        public_raw = base.read_stable(public_path, 512 * 1024)
        dispatch_raw = base.read_stable(dispatch_path, 16384)
        need(sha(public_raw) == args.public_sha256 and sha(dispatch_raw) == args.external_dispatch_sha256,
             'retained_public_or_dispatch_digest')
        dispatch = fixed.validate_dispatch(dispatch_raw, context)
        need(dispatch['recipient']['sha256'] == RECIPIENT, 'same_technical_recipient')
        envelope = base.absolute_e(args.envelope, private=True)
        reference = base.envelope_reference(envelope, args.envelope_sha256)
        need(reference['bytes'] <= dispatch['fixedtxt']['limits']['capsule_bytes'] <= 256 * MIB,
             'physical_capsule_upper_bound')
        prefix = base.read_stable(envelope, base.MAX_ENVELOPE, prefix=len(base.MAGIC) + 4 + 8192)
        header = base.header_from_prefix(prefix, seal, context, RECIPIENT)
        memory = bounded.memory_plan(header['plaintext_bytes'], bounded.available_memory())
        need(memory['admitted'], 'physical_reader_memory_not_admitted')
        ffmpeg = base.absolute_e(args.ffmpeg)
        bounded.decoder_file_reference(ffmpeg, args.ffmpeg_sha256, base)
        output = ReadbackOutput(base, args.output_dir, source, disk_options)
        state.update(context=context, io_scope=io_scope, memory_preflight=memory, envelope=reference,
                     reader_sha256=sha(base.read_stable(Path(__file__).resolve(), 2 * MIB)))
        output.write('readback.started.json', state)
        phase = 'authenticated_original_decryption'
        key_raw = base.read_stable(base.absolute_e(args.private_key, private=True), 65536)
        key = seal.serialization.load_pem_private_key(key_raw, password=None)
        del key_raw
        snapshot = bounded.decrypt_retained(envelope, reference, header, key, seal, base)
        del key
        phase = 'original_PRQ_public_and_selected_snapshot'
        held, plan, runner_temp = held_from_snapshot(snapshot, dispatch_raw, fixed, bounded, base, context, selection)
        verify_public(public_raw, reference, held, fixed, context)
        source_bindings = [{'path': name, 'sha256': sha(base.read_stable(source / name, 16 * MIB))}
                           for name in sorted(fixed.source_paths())]
        need(plan['source_bindings'] == source_bindings, 'physical_exact_source_bindings')
        selected, inventory = validate(snapshot, held, current=context, runner_temp=runner_temp)
        originals = outer.Originals(snapshot, bounded, inventory, base)
        candidate = verify_candidate_request(originals, plan, route, selection,
            source_tree=before['render_source_tree'])
        transport_binding = original_transport_binding(originals, plan, route, serial, selection, runner_temp)
        phase = 'actual_selected_PNG_RGBA_and_frozen_numeric_measurements'
        physical = physical_readback(originals, plan, held, bounded, base, ffmpeg, args.ffmpeg_sha256, numeric, spec, typed,
            selection, transport_binding['png_limit_bytes'], transform_spec_raw, transform_numeric)
        del originals
        snapshot.close()
        snapshot = None
        phase = 'final_source_ciphertext_dependency_integrity'
        after = relevant_source_identity(source, context, base, fixed.source_paths())
        need(same_relevant_source(before, after), 'physical_relevant_source_changed')
        loader.verify_unchanged()
        need(base.envelope_reference(envelope, args.envelope_sha256) == reference
             and base.read_stable(public_path, 512 * 1024) == public_raw
             and base.read_stable(dispatch_path, 16384) == dispatch_raw
             and base.read_stable(spec_path, 65536) == spec_raw, 'physical_original_changed')
        need(sha(base.read_stable(Path(args.numeric_oracle), 2 * MIB)) == NUMERIC_SHA
             and sha(base.read_stable(Path(args.typed_probe_adapter), 2 * MIB)) == TYPED_PROBE_SHA
             and sha(base.read_stable(Path(args.result_adapter), 2 * MIB)) == args.result_adapter_sha256,
             'physical_reader_dependency_changed')
        if transform_spec_raw is not None:
            need(base.read_stable(base.absolute_e(args.transform_spec), 4096) == transform_spec_raw
                 and sha(base.read_stable(Path(args.transform_oracle), 2 * MIB)) == TRANSFORM_ORACLE_SHA,
                 'physical_original_R38_changed')
        bounded.require_reserve()
        state.update(read_HEAD_at_completion=after['read_HEAD'], status='AUTHENTICATED_ONE_CANDIDATE_PHYSICAL_READBACK_WITH_FROZEN_MEASUREMENTS',
            terminal_phase='complete', selected_originals=selected, physical_readback=physical,
            candidate_selection=candidate, transport_binding=transport_binding,
            candidate24_executed_contexts=1, candidate24_total_contexts=24,
            candidate24_accepted=0,
            os_memory_limit=limiter.observe(), source_identity=before, verifier_source_files=loader.loaded,
            verified_original_members=len(inventory), inventory_sha256=sha(base.json_bytes(inventory)),
            public_original_sha256=sha(public_raw), external_dispatch_sha256=sha(dispatch_raw))
        output.write('readback.terminal.json', state)
        print(json.dumps({'status': state['status'], 'receipt_sha256': sha(base.json_bytes(state)),
            'original_PNGs': 180, 'exact_selected_RGBA_comparisons': len(selection[2]),
            'font_glyph_baseline_status': physical['font_glyph_baseline_status'],
            'native_calls': 0, 'candidate24_executed_contexts': 1, 'candidate24_total_contexts': 24,
            'candidate24_accepted': 0, 'G2_accepted': False, 'F_accepted': False}, sort_keys=True))
        return 0
    except BaseException as error:
        candidate = str(error)
        code = candidate if re.fullmatch(r'[A-Za-z0-9_:-]{1,120}', candidate) else 'failed_closed'
        state.update(status='PHYSICAL_READBACK_INCOMPLETE', terminal_phase=phase, error_code=code,
                     error_type=type(error).__name__)
        if output is not None:
            try:
                output.write('readback.terminal.json', state)
            except BaseException:
                pass
        print(json.dumps({'status': state['status'], 'phase': phase, 'error_code': code,
                          'native_calls': 0, 'automatic_retry': False}, sort_keys=True))
        return 2
    finally:
        if snapshot is not None:
            snapshot.close()


if __name__ == '__main__':
    raise SystemExit(main())
