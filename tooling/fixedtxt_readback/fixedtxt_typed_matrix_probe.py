"""Adapt authenticated original FixedTxt observations to the frozen R39 probe.

This module has no file, API, decoder, render, permission or acceptance effect.
Its caller first authenticates the complete capsule and the original P/R/Q,
producer source, native observation stream, font inventory and frame bindings.
The adapter then joins those originals. Desired Film boxes are never copied
into an observed box. Missing/unknown native fields remain unavailable.
"""
from __future__ import annotations

import hashlib
import math
import re

# The frozen R37/R38 contract remains the numerical authority. These pure
# functions establish which native facts are available to that contract; they
# do not interpret a missing fact as a successful predicate or accept a case.

FIXTURES = {
    'fixed-causal': (0, 45, 120, 179),
    'flow-control-base': (45, 120, 179),
    'flow-control-with-fixed': (45, 120, 179),
    'flow-text-control': (0, 45, 120, 179),
    'fixed-multirun': (0, 45, 120, 179),
    'fixed-multirun-control': (0, 45, 120, 179),
    'fixed-transform-clip': (45, 120, 179),
    'fixed-camera-scene': (0, 45, 120, 179),
    'fixed-counter-captions': (0, 29, 30, 45, 119, 120, 179),
    'fixed-layer-behind': (0, 45, 120, 179),
    'fixed-layer-equal': (0, 45, 120, 179),
    'fixed-layer-front': (0, 45, 120, 179),
}
CORNER_GUARD_PX = 2
FIXTURE_ROSTER = frozenset(('fixed-causal', 'flow-control-base', 'flow-control-with-fixed',
    'flow-text-control', 'fixed-multirun', 'fixed-multirun-control', 'fixed-transform-clip',
    'fixed-camera-scene', 'fixed-counter-captions', 'fixed-layer-behind', 'fixed-layer-equal', 'fixed-layer-front'))
MIB = 1024 * 1024
SHA = re.compile(r'[0-9a-f]{64}\Z')
HISTORICAL = {
    'RED': 'dfe6c0fc226d7e9017eb718c7e745d942c4067a54f968d4483b80bbdd51b4fa8',
    'GREEN': 'a400b38608e169a20e7ade099aa8632c11629152951b9e62a0fb66858b530f36',
}
LAYOUT_OBSERVED = {
    'RED': '629ebdec4e7dd42c3d6a9bf71c27c08997d54aa2cabe50c9daac3072e16e3bec',
    'GREEN': '6ea8d223690da0c7f77f6b2ce4cee22857b46a0c9490093646357ccc2f4e9508',
}
GEOMETRY_OBSERVED = {
    'RED': 'ac4df30d0bdeab0a93924d5841ea18a76f269ce99f2903a2cfebc38e254a3c12',
    'GREEN': 'afc197bacf9d73c007833a315b253b1b901005a2c9ef7506eec57b2683aaf3c7',
}
OBSERVED = {'RED': 'ed4f0d8f6630f14d1c3d890b3249fab79381a2fe9c8424bdb302b1ae98488cd2', 'GREEN': 'e787ab2bc03af94f7e432d380b1c5ff058fb881492232ed59b5988f487b69c96'}

GEOMETRY_FIELDS = ('native_geometry', 'camera_scene')
FIXTURE_HELPER_SHA = '372b1aab1412fa61b7003b897729f9ac2e6152f3cc5334890256ba025a1984f6'
IDENTITY = [1, 0, 0, 1, 0, 0]
NATIVE_TYPES = frozenset(('Node', 'Layout', 'Rect', 'Circle', 'Line', 'Txt',
                          'Code', 'Img', 'Video', 'Camera'))
LAYOUT_TYPES = NATIVE_TYPES - {'Node', 'Camera'}
FIELDS = ('native_type', 'layout_root', 'screen_layout_box')
BUNDLED_CSS = {
    'Instrument Sans Variable': 'node_modules/@fontsource-variable/instrument-sans/index.css',
    'IBM Plex Mono': 'node_modules/@fontsource/ibm-plex-mono/400.css',
}


class ProbeRejected(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise ProbeRejected(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def number(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def digest(value):
    return type(value) is str and SHA.fullmatch(value) is not None


def known(value, validator, field):
    need(type(value) is dict and value.get('status') in ('known', 'unknown'),
         'typed_probe_Known_shape:' + field)
    if value['status'] == 'unknown':
        need(set(value) == {'status', 'reason'} and type(value['reason']) is str
             and 0 < len(value['reason'].encode()) <= 256, 'typed_probe_unknown_reason:' + field)
        return False, None
    need(set(value) == {'status', 'value'} and validator(value['value']),
         'typed_probe_known_value:' + field)
    return True, value['value']


def valid_box(box):
    return (type(box) is dict and set(box) == {'x', 'y', 'width', 'height'}
            and number(box['x'], -32768, 32768) and number(box['y'], -32768, 32768)
            and number(box['width'], 0, 1e6) and number(box['height'], 0, 1e6))


def actual_layout(row, *, arm, source_sha):
    need(arm in OBSERVED and source_sha in (OBSERVED[arm], GEOMETRY_OBSERVED[arm], LAYOUT_OBSERVED[arm], HISTORICAL[arm]),
         'typed_probe_exact_observer_source')
    present = [key in row for key in FIELDS]
    need(all(present) or not any(present), 'typed_probe_partial_extension')
    if not any(present):
        return None, list(FIELDS)
    need(source_sha in (OBSERVED[arm], GEOMETRY_OBSERVED[arm], LAYOUT_OBSERVED[arm]), 'typed_probe_fields_not_emitted_by_historical_source')
    type_known, native_type = known(row['native_type'],
        lambda value: type(value) is str and value in NATIVE_TYPES, 'native_type')
    root_known, root = known(row['layout_root'], lambda value: type(value) is bool, 'layout_root')
    box_known, box = known(row['screen_layout_box'], valid_box, 'screen_layout_box')
    if root_known:
        need(row.get('drawn') is True and type_known and native_type in LAYOUT_TYPES,
             'typed_probe_root_requires_native_Layout_draw')
    if box_known:
        transform_known, _ = known(row.get('transform'),
            lambda value: type(value) is list and len(value) == 6
                and all(number(component, -1e6, 1e6) for component in value), 'transform')
        need(row.get('drawn') is True and type_known and native_type in LAYOUT_TYPES
             and root_known and transform_known, 'typed_probe_box_requires_observed_transform')
    node = {'kind': native_type if type_known else None,
            'layout_root': root if root_known else None,
            'screen_layout_box': [box[key] for key in ('x', 'y', 'width', 'height')] if box_known else None}
    missing = [key for key, observed in zip(FIELDS, (type_known, root_known, box_known), strict=True)
               if not observed]
    return node, missing


def one_role(plan, role):
    rows = [row for row in plan['members'] if row['role'] == role]
    need(len(rows) == 1, 'typed_probe_unique_original_role:' + role)
    row = rows[0]
    return row['root'] + '/' + row['relative_path']


def subject_map(film):
    need(type(film) is dict and film.get('version') == 1, 'typed_probe_original_Film')
    output = film.get('output', {})
    need((output.get('width'), output.get('height'), output.get('aspect')) == (1920, 1080, 'landscape'),
         'typed_probe_original_Film_profile')
    sequences = film.get('sequences')
    need(type(sequences) is list and 0 < len(sequences) <= 128, 'typed_probe_Film_sequence_bound')
    subjects = {}
    shots = 0
    for sequence in sequences:
        beats = sequence.get('beats')
        need(type(beats) is list and 0 < len(beats) <= 128, 'typed_probe_Film_beat_bound')
        for beat in beats:
            native_shots = beat.get('shots')
            need(type(native_shots) is list and 0 < len(native_shots) <= 128, 'typed_probe_Film_shot_bound')
            for shot in native_shots:
                shots += 1
                rows = shot.get('subjects')
                need(shots <= 128 and type(rows) is list and len(rows) <= 1024,
                     'typed_probe_Film_subject_bound')
                for row in rows:
                    name = row.get('id')
                    need(type(name) is str and 0 < len(name.encode()) <= 128
                         and name not in subjects and len(subjects) < 1024,
                         'typed_probe_Film_subject_identity')
                    subjects[name] = row
    return subjects


def text_digest(subject):
    content = subject.get('content', {})
    runs = content.get('runs')
    need(content.get('kind') == 'text' and type(runs) is list and 0 < len(runs) <= 128,
         'typed_probe_original_authored_text')
    need(all(type(run) is dict and type(run.get('text')) is str for run in runs),
         'typed_probe_original_text_run_shape')
    raw = ''.join(run['text'] for run in runs).encode()
    need(len(raw) <= 65536, 'typed_probe_original_text_bound')
    return sha(raw)


def font_identity(film, config, inventory):
    """Recompute the renderer's existing resource binding, never substitute a font."""
    pins = config.get('fontResourcePins')
    managed = config.get('managedFonts')
    rows = inventory.get('originals')
    need(type(pins) is list and 0 < len(pins) <= 128 and type(managed) is list
         and len(managed) <= 2 and type(rows) is list and len(rows) == len(pins) + len(managed),
         'typed_probe_font_original_roster')
    resource_hash = hashlib.sha256()
    previous = None
    expected = []
    for pin in pins:
        need(type(pin) is dict and set(pin) == {'path', 'sha256'}
             and type(pin['path']) is str and 0 < len(pin['path']) <= 512
             and digest(pin['sha256']) and (previous is None or previous < pin['path']),
             'typed_probe_font_source_pin')
        previous = pin['path']
        resource_hash.update(pin['path'].encode() + b'\0' + pin['sha256'].encode() + b'\0')
        expected.append(('bundled', pin))
    for item in managed:
        need(type(item) is dict and type(item.get('path')) is str and digest(item.get('sha256')),
             'typed_probe_managed_font_source_pin')
        resource_hash.update(b'managed-font\0' + item['path'].encode() + b'\0'
                             + item['sha256'].encode() + b'\0')
        expected.append(('imported', item))
    need(resource_hash.hexdigest() == config.get('fontResourcesDigest'),
         'typed_probe_original_font_resource_digest')
    for row, (role, pin) in zip(rows, expected, strict=True):
        need(row.get('role') == role and row.get('path') == pin['path'] and row.get('sha256') == pin['sha256']
             and type(row.get('bytes')) is int and 0 < row['bytes'] <= 16 * MIB,
             'typed_probe_font_inventory_original')
    font = film.get('editorial', {}).get('font', {})
    family = font.get('family')
    need(type(family) is str and 0 < len(family.encode()) <= 128
         and font.get('fallback') == 'deny' and font.get('permitted_fallbacks') == [],
         'typed_probe_authored_font_no_fallback')
    asset = font.get('asset_digest')
    if asset is None:
        css = BUNDLED_CSS.get(family)
        need(css is not None and any(pin['path'] == css for pin in pins)
             and any(pin['path'].startswith(css.rsplit('/', 1)[0] + '/files/')
                     and pin['path'].endswith('.woff2') for pin in pins),
             'typed_probe_original_bundled_font_family')
    else:
        need(digest(asset) and any(item.get('family') == family and item.get('sha256') == asset for item in managed)
             and any(item.get('sha256') == asset for item in film.get('assets', [])),
             'typed_probe_original_managed_font_asset')
    return family


def native_key(value):
    return type(value) is str and 0 < len(value) <= 512 and all(32 <= ord(c) <= 126 for c in value)


def subject_id(value):
    return type(value) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,96}', value) is not None


def matrix(value):
    return type(value) is list and len(value) == 6 and all(number(v, -1e6, 1e6) for v in value)


def polygon(value):
    return (type(value) is list and len(value) == 4
            and all(type(point) is list and len(point) == 2
                    and all(number(v, -2e6, 2e6) for v in point) for point in value))


def extent_agrees(declared, minimum, maximum):
    # Same finite internal IEEE-754 allowance as the paired Rust ABI.
    # This does not change the independent frozen 2 px physical corner guard.
    scale = max(1, abs(minimum), abs(maximum), abs(declared))
    return abs(declared - (maximum-minimum)) <= 4 * math.ulp(1.0) * scale


def value_of(value, validator, field):
    present, result = known(value, validator, field)
    return result if present else None


def closed(value, fields, code):
    need(type(value) is dict and set(value) == set(fields), 'typed_geometry_closed_shape:' + code)


def validate_lineage(rows, row, identity, kind):
    need(type(rows) is list and 0 < len(rows) <= 64 and all(type(node) is dict for node in rows)
         and row.get('drawn') is True,
         'typed_geometry_lineage_bounds_or_draw')
    keys, subjects = set(), set()
    for index, node in enumerate(rows):
        closed(node, ('native_key', 'subject_id', 'native_type', 'parent_key',
                      'local_to_parent', 'opacity', 'layout_enabled', 'clip'), 'lineage')
        key, sid, parent = node['native_key'], node['subject_id'], node['parent_key']
        need(native_key(key) and key not in keys, 'typed_geometry_lineage_duplicate_or_key')
        keys.add(key)
        need(sid is None or subject_id(sid) and sid not in subjects,
             'typed_geometry_lineage_semantic_identity')
        if sid is not None:
            subjects.add(sid)
        need(parent is None or native_key(parent), 'typed_geometry_lineage_parent_key')
        need(parent == (rows[index + 1].get('native_key') if index + 1 < len(rows) else None),
             'typed_geometry_incomplete_parent_edge')
        need(type(node['native_type']) is str and node['native_type'] in NATIVE_TYPES
             and matrix(node['local_to_parent']) and number(node['opacity'], 0, 1),
             'typed_geometry_lineage_values')
        for signal in ('layout_enabled', 'clip'):
            available, _ = known(node[signal], lambda v: type(v) is bool, 'lineage.' + signal)
            need(not available or node['native_type'] in LAYOUT_TYPES,
                 'typed_geometry_Layout_signal_on_other_type')
    need(rows[0]['native_key'] == identity and rows[0]['subject_id'] == row['id']
         and rows[0]['native_type'] == kind, 'typed_geometry_lineage_subject_start')


def validate_clip(value, geometry, row, kind, transform):
    need(type(value) is dict and value.get('kind') in ('none', 'rect'), 'typed_geometry_clip_kind')
    closed(value, ('kind',) if value['kind'] == 'none' else ('kind', 'clip'), 'own_clip')
    need(row.get('drawn') is True and kind == 'Layout', 'typed_geometry_clip_requires_exact_Layout')
    lineage = value_of(geometry['lineage'], lambda v: type(v) is list, 'lineage')
    if lineage is not None:
        signal = value_of(lineage[0]['clip'], lambda v: type(v) is bool, 'lineage.clip')
        need(signal is (value['kind'] == 'rect'), 'typed_geometry_clip_signal_disagreement')
    if value['kind'] == 'rect':
        clip = value['clip']
        closed(clip, ('native_key', 'local_box', 'matrix', 'screen_polygon'), 'rect_clip')
        need(clip['native_key'] == geometry['native_key'] and valid_box(clip['local_box'])
             and clip['local_box']['width'] > 0 and clip['local_box']['height'] > 0
             and matrix(clip['matrix']) and polygon(clip['screen_polygon']) and transform is not None,
             'typed_geometry_clip_owner_mapping_or_rectangle')


def validate_camera(value, geometry, row, kind):
    closed(value, ('native_key', 'original_scene_key', 'current_scene_key',
                  'current_scene_is_original', 'draw_scene_key', 'draw_uses_original_scene',
                  'scene_transform', 'position', 'rotation', 'zoom', 'cameras'), 'Camera.scene')
    need(row.get('drawn') is True and kind == 'Camera' and geometry is not None
         and value['native_key'] == geometry['native_key'], 'typed_geometry_camera_draw_identity')
    for name in ('native_key', 'original_scene_key', 'current_scene_key', 'draw_scene_key'):
        need(native_key(value[name]), 'typed_geometry_camera_key')
    for flag, key in (('current_scene_is_original', 'current_scene_key'),
                      ('draw_uses_original_scene', 'draw_scene_key')):
        need(type(value[flag]) is bool, 'typed_geometry_camera_reference_flag')
        need(not value[flag] or value[key] == value['original_scene_key'],
             'typed_geometry_camera_true_reference_key_disagreement')
    need(type(value['position']) is list and len(value['position']) == 2
         and all(number(v, -1e6, 1e6) for v in value['position'])
         and number(value['rotation'], -1e6, 1e6) and number(value['zoom'], 0, 1e6)
         and value['zoom'] > 0, 'typed_geometry_camera_signals')
    known(value['scene_transform'], matrix, 'camera.scene_transform')
    cameras = value_of(value['cameras'], lambda v: type(v) is list and len(v) <= 64, 'cameras')
    if cameras is not None:
        keys, subjects = set(), set()
        for camera in cameras:
            closed(camera, ('native_key', 'subject_id'), 'camera_inventory')
            key, sid = camera['native_key'], camera['subject_id']
            need(native_key(key) and key not in keys, 'typed_geometry_camera_inventory_key')
            need(sid is None or subject_id(sid) and sid not in subjects,
                 'typed_geometry_camera_inventory_semantic_identity')
            keys.add(key)
            if sid is not None:
                subjects.add(sid)


def actual_geometry(row, *, arm, source_sha):
    """Validate only serialized native facts. Legacy absence remains unavailable."""
    need(type(row) is dict and subject_id(row.get('id')), 'typed_geometry_subject_id')
    layout, missing = actual_layout(row, arm=arm, source_sha=source_sha)
    present = [field in row for field in GEOMETRY_FIELDS]
    need(all(present) or not any(present), 'typed_geometry_partial_extension')
    transform = value_of(row.get('transform'), matrix, 'transform')
    result = {'id': row['id'], 'layout': layout, 'transform': transform,
              'geometry': None, 'camera': None, 'missing': list(missing)}
    if not any(present):
        result['missing'].extend(GEOMETRY_FIELDS)
        return result
    need(source_sha in (OBSERVED[arm], GEOMETRY_OBSERVED[arm]) and layout is not None,
         'typed_geometry_fields_not_emitted_by_bound_source')
    geometry = value_of(row['native_geometry'], lambda v: type(v) is dict, 'native_geometry')
    camera = value_of(row['camera_scene'], lambda v: type(v) is dict, 'camera_scene')
    kind = layout['kind']
    if geometry is not None:
        closed(geometry, ('native_key', 'screen_layout_polygon', 'lineage', 'own_clip'), 'geometry')
        need(native_key(geometry['native_key']) and kind in NATIVE_TYPES, 'typed_geometry_native_identity')
        points = value_of(geometry['screen_layout_polygon'], polygon, 'screen_layout_polygon')
        if points is not None:
            need(row.get('drawn') is True and kind in LAYOUT_TYPES and layout['layout_root'] is not None
                 and transform is not None and layout['screen_layout_box'] is not None,
                 'typed_geometry_polygon_requires_mapped_Layout_draw')
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            x, y, width, height = layout['screen_layout_box']
            need(x == min(xs) and y == min(ys) and extent_agrees(width, min(xs), max(xs))
                 and extent_agrees(height, min(ys), max(ys)),
                 'typed_geometry_polygon_AABB_disagreement')
        lineage = value_of(geometry['lineage'], lambda v: type(v) is list, 'lineage')
        if lineage is not None:
            validate_lineage(lineage, row, geometry['native_key'], kind)
        clip = value_of(geometry['own_clip'], lambda v: type(v) is dict, 'own_clip')
        if clip is not None:
            validate_clip(clip, geometry, row, kind, transform)
    if camera is not None:
        validate_camera(camera, geometry, row, kind)
    result.update(geometry=geometry, camera=camera)
    result['missing'].extend(field for field, value in (('native_geometry', geometry), ('camera_scene', camera))
                             if value is None)
    return result


def multiply(left, right):
    a, b, c, d, e, f = left
    g, h, i, j, k, l = right
    return [a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j, a*k+c*l+e, b*k+d*l+f]


def project(transform, points):
    a, b, c, d, e, f = transform
    return [[a*x+c*y+e, b*x+d*y+f] for x, y in points]


def rectangle(box):
    x, y, w, h = (box[key] for key in ('x', 'y', 'width', 'height'))
    return [[x, y], [x+w, y], [x+w, y+h], [x, y+h]]


def invert(transform):
    a, b, c, d, e, f = transform
    determinant = a*d-b*c
    if determinant == 0 or not math.isfinite(determinant):
        return None
    result = [d/determinant, -b/determinant, -c/determinant, a/determinant,
              (c*f-d*e)/determinant, (b*e-a*f)/determinant]
    return result if matrix(result) else None


def check(verified, reason=None, **measurements):
    need(verified is None or type(verified) is bool, 'typed_geometry_predicate_state')
    return {'verified': verified, 'state': 'UNKNOWN' if verified is None else 'TRUE' if verified else 'FALSE',
            'reason': reason, **measurements}


def combine(checks):
    values = [entry['verified'] for entry in checks]
    return False if False in values else None if None in values else True


def compare_corners(actual, expected):
    if not polygon(actual) or not polygon(expected):
        return check(None, 'native_corner_mapping_unavailable')
    deltas = [[a-b for a, b in zip(point, wanted, strict=True)]
              for point, wanted in zip(actual, expected, strict=True)]
    maximum = max(abs(value) for point in deltas for value in point)
    return check(maximum <= CORNER_GUARD_PX, None if maximum <= CORNER_GUARD_PX else 'native_CTM_composition_disagrees',
                 corner_deltas_px=deltas, maximum_corner_axis_delta_px=maximum,
                 frozen_corner_axis_guard_px=CORNER_GUARD_PX)


def geometry_value(observation, field, validator):
    geometry = observation['geometry']
    return None if geometry is None else value_of(geometry[field], validator, field)


def neutral_wrapper(node):
    if node['subject_id'] is not None or node['native_type'] != 'Layout':
        return check(False, 'intervening_node_is_not_the_neutral_native_Layout')
    if node['local_to_parent'] != IDENTITY or node['opacity'] != 1:
        return check(False, 'neutral_wrapper_actual_transform_or_opacity_changed')
    flags = [value_of(node[name], lambda v: type(v) is bool, 'wrapper.' + name)
             for name in ('layout_enabled', 'clip')]
    if any(value is True for value in flags):
        return check(False, 'neutral_wrapper_native_layout_or_clip_enabled')
    return check(None, 'neutral_wrapper_native_signals_unavailable') if None in flags else check(True)


def mapped_composition(observation, outer, local_chain):
    """Compare two native mappings on the measured quadrilateral, never an AABB.

    Undo the actual text CTM solely to recover its native local corners, then
    project through the independently observed ancestor/local composition. No
    Film position or desired size enters these observed coordinates.
    """
    actual = observation['transform']
    corners = geometry_value(observation, 'screen_layout_polygon', polygon)
    if outer is None or actual is None or corners is None:
        return check(None, 'native_polygon_or_CTM_unavailable')
    inverse = invert(actual)
    if inverse is None:
        return check(None, 'native_CTM_singular_or_unbounded_inverse')
    local = project(inverse, corners)
    area = sum(local[i][0]*local[(i+1)%4][1]-local[(i+1)%4][0]*local[i][1] for i in range(4))
    if not polygon(local) or area == 0 or not math.isfinite(area):
        return check(None, 'native_polygon_degenerate_or_unbounded')
    composed = list(outer)
    for node in reversed(local_chain):
        composed = multiply(composed, node['local_to_parent'])
        if not matrix(composed):
            return check(None, 'native_composition_outside_observation_bounds')
    result = compare_corners(project(composed, local), corners)
    result.update(actual_native_draw_matrix=actual, composed_native_draw_matrix=composed,
                  observed_local_corners=local,
                  applied_native_keys=[node['native_key'] for node in reversed(local_chain)])
    return result


def verify_original_parent(text, parent):
    """R38: actual parent identity, own rect clip and one measured composition."""
    lineage = geometry_value(text, 'lineage', lambda v: type(v) is list)
    parent_lineage = geometry_value(parent, 'lineage', lambda v: type(v) is list)
    if lineage is None or parent_lineage is None:
        return check(None, 'original_parent_native_lineage_unavailable')
    matches = [i for i, node in enumerate(lineage) if node['subject_id'] == parent['id']]
    if len(matches) != 1 or matches[0] == 0:
        return check(False, 'original_parent_not_found_once_in_actual_chain')
    index = matches[0]
    if lineage[index:] != parent_lineage:
        return check(False, 'parent_and_text_observe_different_native_ancestry')
    if text['layout']['kind'] != 'Txt':
        return check(False, 'original_native_text_type_changed')
    if parent['layout']['kind'] != 'Layout':
        return check(None, 'native_parent_clip_type_unsupported')
    checks = [neutral_wrapper(node) for node in lineage[1:index]]
    # An additional clipping ancestor makes this single-original-clip proof
    # unavailable. Known enabled intervening clipping is a genuine mismatch.
    for node in lineage[:index] + lineage[index+1:]:
        clipping = value_of(node['clip'], lambda v: type(v) is bool, 'ancestor.clip')
        checks.append(check(None, 'ancestor_clip_unresolved') if clipping is None else
                      check(not clipping, None if not clipping else 'additional_native_clip_in_chain'))
    clip = geometry_value(parent, 'own_clip', lambda v: type(v) is dict)
    if clip is None:
        checks.append(check(None, 'original_parent_native_rect_clip_unavailable'))
        return check(combine(checks), 'parent_clip_or_chain_incomplete', checks=checks)
    if clip['kind'] != 'rect':
        checks.append(check(False, 'original_parent_did_not_execute_rect_clip'))
        return check(combine(checks), 'original_parent_rect_clip_missing', checks=checks)
    actual = clip['clip']
    checks.append(compare_corners(project(actual['matrix'], rectangle(actual['local_box'])), actual['screen_polygon']))
    checks.append(check(None, 'parent_output_CTM_unavailable') if parent['transform'] is None else
                  compare_corners(project(parent['transform'], rectangle(actual['local_box'])), actual['screen_polygon']))
    checks.append(mapped_composition(text, parent['transform'], lineage[:index]))
    return check(combine(checks), None, checks=checks, original_parent_native_key=parent_lineage[0]['native_key'],
                 actual_native_text_screen_polygon=geometry_value(text, 'screen_layout_polygon', polygon),
                 actual_native_clip_screen_polygon=actual['screen_polygon'])


def verify_original_camera(camera, descendants, *, canvas_size):
    """R37: the actually drawn original scene, one camera, and native CTM joins."""
    need(type(canvas_size) in (list, tuple) and len(canvas_size) == 2
         and all(type(v) is int and 16 <= v <= 4096 for v in canvas_size), 'typed_geometry_output_canvas')
    need(type(descendants) is list and 0 < len(descendants) <= 2
         and len({node['id'] for node in descendants}) == len(descendants), 'typed_geometry_camera_descendants')
    value = camera['camera']
    if value is None:
        return check(None, 'original_Camera_scene_observation_unavailable')
    checks = [check(value['current_scene_is_original'], None if value['current_scene_is_original'] else 'Camera_scene_replaced'),
              check(value['draw_uses_original_scene'], None if value['draw_uses_original_scene'] else 'Camera_drew_another_scene')]
    inventory = value_of(value['cameras'], lambda v: type(v) is list, 'cameras')
    only_original = [{'native_key': value['native_key'], 'subject_id': camera['id']}]
    checks.append(check(None, 'native_camera_inventory_unavailable') if inventory is None else
                  check(inventory == only_original, None if inventory == only_original else
                        'additional_or_missing_original_native_camera'))
    checks.append(check(value['position'] == [0, 0] and value['rotation'] == 0 and value['zoom'] == 1,
                        'original_camera_identity_signals_compared'))
    scene_transform = value_of(value['scene_transform'], matrix, 'camera.scene_transform')
    w, h = canvas_size
    output_corners = [[-w/2, -h/2], [w/2, -h/2], [w/2, h/2], [-w/2, h/2]]
    checks.append(check(None, 'camera_scene_output_CTM_unavailable') if scene_transform is None else
                  compare_corners(project(scene_transform, output_corners), [[0, 0], [w, 0], [w, h], [0, h]]))
    for descendant in descendants:
        lineage = geometry_value(descendant, 'lineage', lambda v: type(v) is list)
        if lineage is None:
            checks.append(check(None, 'camera_descendant_lineage_unavailable', subject_id=descendant['id']))
            continue
        terminal = lineage[-1]
        if terminal['native_key'] != value['draw_scene_key'] or terminal['native_type'] != 'Node' or terminal['subject_id'] is not None:
            checks.append(check(False, 'descendant_not_in_actual_Camera_scene', subject_id=descendant['id']))
            continue
        checks.extend(neutral_wrapper(node) for node in lineage[1:-1])
        # Camera.drawChildren calls scene.drawChildren directly. Its terminal
        # Node local transform is not applied by Motion Canvas 3.17.2.
        checks.append(mapped_composition(descendant, scene_transform, lineage[:-1]))
    return check(combine(checks), None, checks=checks, original_camera_native_key=value['native_key'],
                 original_scene_native_key=value['original_scene_key'], drawn_scene_native_key=value['draw_scene_key'])


def bind_fixture_sources(originals, plan, film_path, expectation_path, effective, base):
    """Join the retained compiler closure and original generated fixture roster."""
    common_path = 'inputs/common-sources.original.json'
    common_raw = originals.small(common_path, 4 * MIB)
    common = base.strict(common_raw, 4 * MIB)
    cycle = originals.json(one_role(plan, 'cycle-inputs'), MIB)
    need(common.get('schema') == 'motionwright.fixed-text-common-compiler-sources/1'
         and common.get('semwright_revision') == plan['arm_sources'][plan['arm']]['source_sha']
         and cycle.get('schema') == 'motionwright.fixed-text-native-cycle-inputs/4'
         and cycle.get('arm') == plan['arm'], 'typed_geometry_original_compiler_closure')
    bound = cycle.get('common_sources', {})
    need((bound.get('bytes'), bound.get('sha256')) == (len(common_raw), sha(common_raw)),
         'typed_geometry_original_common_source_binding')
    files = common.get('files')
    need(type(files) is list and 0 < len(files) <= 4096, 'typed_geometry_compiler_source_roster')
    helpers = [item for item in files if item.get('path') ==
               'crates/driver-motion-canvas/examples/support/fixed_text_fixtures_v4.rs']
    need(len(helpers) == 1 and helpers[0].get('bytes') == 44617
         and helpers[0].get('sha256') == FIXTURE_HELPER_SHA, 'typed_geometry_original_fixture_helper_source')
    fixture_set = originals.json(one_role(plan, 'fixture-set'), MIB)
    need(fixture_set.get('schema') == 'motionwright-fixedtxt-film-fixture-set/4'
         and fixture_set.get('fixture_count') == 12
         and fixture_set.get('compiler', {}).get('runtime_source_sha256') == effective,
         'typed_geometry_original_fixture_set_compiler')
    roster = fixture_set.get('fixtures')
    need(type(roster) is list and len(roster) == 12 and all(type(item) is dict for item in roster)
         and {item.get('key') for item in roster} == FIXTURE_ROSTER,
         'typed_geometry_original_fixture_roster')
    matches = [item for item in roster if item.get('key') == plan['fixture']]
    need(len(matches) == 1, 'typed_geometry_original_fixture_entry')
    item = matches[0]
    for name, path in (('film', film_path), ('expectations', expectation_path)):
        descriptor = item.get(name, {})
        need(descriptor == {'path': 'fixtures/' + plan['fixture'] + '/' +
                           ('film.original.json' if name == 'film' else 'expectations.json'),
                           **originals.descriptor(path)}, 'typed_geometry_fixture_original_roster_binding')
    return {'common_compiler_sources_original': originals.descriptor(common_path),
            'fixture_helper_source_sha256': FIXTURE_HELPER_SHA,
            'fixture_set_original': originals.descriptor(one_role(plan, 'fixture-set'))}


ALIASES = {
    'fixed-causal': {'title': 'text', 'body': 'text', 'gold': 'rectangle'},
    'flow-control-base': {'flowA': 'rectangle', 'flowB': 'rectangle'},
    'flow-control-with-fixed': {'flowA': 'rectangle', 'flowB': 'rectangle', 'fixedText': 'text'},
    'flow-text-control': {'flowTitle': 'text', 'flowBody': 'text'},
    'fixed-multirun': {'text': 'text'},
    'fixed-multirun-control': {'disabledParent': 'group', 'text': 'text'},
    'fixed-transform-clip': {'parent': 'group', 'text': 'text'},
    'fixed-camera-scene': {'camera': 'camera', 'text': 'text', 'gold': 'rectangle'},
    'fixed-counter-captions': {'counter': 'text', 'caption': 'text'},
    **{f'fixed-layer-{name}': {'opaqueControl': 'rectangle', 'text': 'text'}
       for name in ('behind', 'equal', 'front')},
}
COUNTER_VALUES = {0: '0.0%', 29: '16.1%', 30: '16.7%', 45: '25.0%',
                  119: '66.1%', 120: '66.7%', 179: '99.4%'}
CAPTION_TEXT = 'An editable caption stays in place.'


def authored_text(subject):
    text_digest(subject)  # Existing bounded original run validation.
    return ''.join(run['text'] for run in subject['content']['runs'])


def single_shot(film):
    sequences = film['sequences']
    need(len(sequences) == 1 and len(sequences[0]['beats']) == 1
         and len(sequences[0]['beats'][0]['shots']) == 1, 'typed_matrix_original_one_shot')
    return sequences[0]['beats'][0]['shots'][0]


def rational(value):
    return (type(value) is dict and set(value) == {'num', 'den'}
            and all(type(v) is str and re.fullmatch(r'-?(0|[1-9][0-9]{0,17})', v)
                    for v in value.values()) and int(value['den']) > 0)


def expected_box_join(expectations, aliases, frozen, frames):
    """Join frame-specific source boxes, including the primary title's motion."""
    boxes = expectations['expected'].get('boxes')
    need(type(boxes) is list and len(boxes) <= 32, 'typed_matrix_original_box_roster')
    by_id = {sid: role for role, sid in aliases.items()}
    observed = {}
    for box in boxes:
        need(type(box) is dict and set(box) == {'subject', 'frames', 'x', 'y', 'width', 'height'}
             and box['subject'] in by_id and type(box['frames']) is list
             and 0 < len(box['frames']) <= len(frames), 'typed_matrix_original_box_row')
        coords = [box[k] for k in ('x', 'y', 'width', 'height')]
        need(all(number(v, -32768, 1e6) for v in coords) and coords[2] > 0 and coords[3] > 0,
             'typed_matrix_original_box_components')
        role = by_id[box['subject']]
        for frame in box['frames']:
            need(type(frame) is int and frame in frames and (role, frame) not in observed,
                 'typed_matrix_duplicate_or_foreign_box_frame')
            observed[(role, frame)] = coords
    frozen_boxes = {**frozen.get('text_boxes', {}), **frozen.get('control_boxes', {})}
    expected = {(role, int(frame)): box for role, samples in frozen_boxes.items()
                for frame, box in samples.items()}
    need(observed == expected, 'typed_matrix_original_boxes_differ_from_frozen_R37')


def bind_caption_contract(film, expectations, aliases, originals, plan):
    shot = single_shot(film)
    captions, cues = shot.get('captions'), film.get('cues', {}).get('cues')
    need(type(captions) is list and len(captions) == 1 and type(cues) is list and len(cues) == 1,
         'typed_matrix_original_caption_cue_roster')
    caption, cue = captions[0], cues[0]
    need(caption == {'id': 'fixed-counter-captions-caption-binding', 'subject': aliases['caption'],
                    'cue_id': 'fixed-counter-captions-caption-cue', 'language': 'en', 'text': CAPTION_TEXT}
         and cue == {'id': caption['cue_id'], 'anchor': {'kind': 'absolute', 'time': {'num': '1', 'den': '1'}},
                     'duration': {'num': '3', 'den': '1'}, 'source': sha(CAPTION_TEXT.encode()),
                     'method': 'manual-timing', 'version': 1, 'confidence': None},
         'typed_matrix_original_caption_clock_source')
    cycle = originals.json(one_role(plan, 'cycle-inputs'), MIB)
    source = cycle.get('caption_source', {})
    need(source.get('bytes') == len(CAPTION_TEXT.encode()) and source.get('sha256') == sha(CAPTION_TEXT.encode()),
         'typed_matrix_original_caption_asset_binding')
    motion = shot.get('motion')
    need(type(motion) is list and len(motion) == 1
         and motion[0].get('primitive') == {'kind': 'counter', 'target': aliases['counter'], 'from': 0,
            'to': 100, 'decimal_places': 1, 'prefix': '', 'suffix': '%'}
         and motion[0].get('easing') == 'linear', 'typed_matrix_original_counter_operation')
    spans = film.get('timing', {}).get('spans')
    need(type(spans) is list and len(spans) <= 8, 'typed_matrix_original_counter_span_roster')
    matches = [row for row in spans if row.get('id') == motion[0].get('span_id')]
    six = {'num': '6', 'den': '1'}
    need(len(matches) == 1 and all(matches[0].get(key) == six for key in ('minimum', 'preferred', 'maximum'))
         and matches[0].get('anchor') == {'kind': 'absolute', 'time': {'num': '0', 'den': '1'}},
         'typed_matrix_original_counter_clock')
    rows = expectations['expected'].get('texts')
    expected = [{'subject': aliases['counter'], 'frame': f, 'text': value} for f, value in COUNTER_VALUES.items()]
    expected += [{'subject': aliases['caption'], 'frame': f, 'text': CAPTION_TEXT} for f in (30, 45, 119)]
    need(rows == expected, 'typed_matrix_original_counter_and_caption_values')
    return {'id': caption['id'], 'cue_id': caption['cue_id'], 'start': {'num': '1', 'den': '1'},
            'end': {'num': '4', 'den': '1'}}


def bind_authored_fixture(fixture, film, expectations, spec, *, transform_spec_raw, base):
    frames = FIXTURES[fixture]
    aliases = expectations.get('aliases')
    need(type(aliases) is dict and set(aliases) == set(ALIASES[fixture])
         and len(set(aliases.values())) == len(aliases)
         and all(subject_id(value) for value in aliases.values()), 'typed_matrix_original_alias_roster')
    authored = subject_map(film)
    need(set(authored) == set(aliases.values()) and all(authored[sid].get('content', {}).get('kind') ==
         ALIASES[fixture][role] for role, sid in aliases.items()), 'typed_matrix_original_subject_roster_and_types')
    frozen = spec['fixtures'][fixture]
    need(spec['thresholds']['authored_box_geometry_guard_pixels'] == CORNER_GUARD_PX
         and frozen['selected_frames'] == list(frames)
         and expectations.get('expected', {}).get('frames') == list(frames), 'typed_matrix_frozen_selected_roster')
    expected_box_join(expectations, aliases, frozen, frames)
    parents = {}
    if fixture in ('fixed-multirun-control', 'fixed-transform-clip', 'fixed-camera-scene'):
        parent_role = {'fixed-multirun-control': 'disabledParent', 'fixed-transform-clip': 'parent',
                       'fixed-camera-scene': 'camera'}[fixture]
        parents['text'] = aliases[parent_role]
        if fixture == 'fixed-camera-scene': parents['gold'] = aliases['camera']
    need(all(authored[sid].get('parent') == parents.get(role) for role, sid in aliases.items()),
         'typed_matrix_original_parent_graph')
    if fixture == 'fixed-transform-clip':
        need(type(transform_spec_raw) is bytes and len(transform_spec_raw) == 2393
             and sha(transform_spec_raw) == 'd1bf9a45d95fb03d1db5edf2c84db7dd3a2975c6739ba95e04a6a2d3a647a3c8',
             'typed_geometry_exact_frozen_R38_original')
        transform = base.strict(transform_spec_raw, 4096)
        need(transform.get('schema') == 'motionwright.a5.fixedtxt-transformed-clip-preregistered/1'
             and transform.get('source_prereg_sha256') == '6c8edaa4d082c9f980ec6b91a1c99e31481fdd2b4b6d29643f7117a251502946'
             and transform['measurement']['corner_axis_delta_max_px'] == CORNER_GUARD_PX
             and transform['frames'] == list(frames), 'typed_geometry_frozen_R38_target')
        text, parent = authored[aliases['text']], authored[aliases['parent']]
        need(text.get('layout') == {'kind': 'fixed', 'position': {'x': 250, 'y': 0}, 'size': {'width': 700, 'height': 160}}
             and parent.get('layout') == {'kind': 'fixed', 'position': {'x': 0, 'y': 0}, 'size': {'width': 900, 'height': 500}}
             and parent.get('clip_intentional') is True, 'typed_geometry_authored_R38_local_intent')
    if fixture == 'fixed-camera-scene':
        need(authored[aliases['camera']]['content'] == {'kind': 'camera', 'zoom': 1},
             'typed_geometry_original_identity_camera')
    return aliases, authored, {role: authored_text(authored[sid]) for role, sid in aliases.items()
                               if ALIASES[fixture][role] == 'text'}


def normalized_lineage(node):
    rows = geometry_value(node, 'lineage', lambda v: type(v) is list)
    if rows is None: return None
    # Runtime-generated object keys are not stable across browser instances.
    # Their complete edge relation was checked before this normalization.
    return [{key: value for key, value in row.items() if key not in ('native_key', 'parent_key')}
            for row in rows]


def flow_observation(node):
    lineage = geometry_value(node, 'lineage', lambda v: type(v) is list)
    box = node['layout']['screen_layout_box'] if node['layout'] else None
    if lineage is None or box is None: return check(None, 'actual_flow_geometry_or_hierarchy_unavailable')
    if len(lineage) < 2: return check(False, 'flow_native_parent_missing')
    enabled = value_of(lineage[1]['layout_enabled'], lambda v: type(v) is bool, 'flow_parent.layout_enabled')
    if enabled is None: return check(None, 'actual_flow_parent_layout_unavailable')
    intact = (enabled is True and all(row['subject_id'] is None for row in lineage[1:])
              and box[2] > 0 and box[3] > 0)
    return check(intact, None if intact else 'flow_hierarchy_wrapper_or_nonpositive_box',
                 native_screen_layout_box=box, observed_lineage=normalized_lineage(node))


def actual_text_identity(row, authored, expected_current, family):
    text = row.get('text')
    need(type(text) is dict and text.get('logical_text_digest') == text_digest(authored)
         and digest(text.get('observed_text_digest')), 'typed_matrix_actual_text_source_identity')
    current_matches = text['observed_text_digest'] == sha(expected_current.encode())
    native_family = value_of(text.get('font_family'), lambda v: type(v) is str and 0 < len(v.encode()) <= 128, 'font_family')
    ready = value_of(text.get('font_ready'), lambda v: type(v) is bool, 'font_ready')
    fallback = value_of(text.get('fallback_used'), lambda v: type(v) is bool, 'fallback_used')
    return {'font_and_text_verified': current_matches and native_family == family and ready is True and fallback is False,
            'native_observed_text_digest': text['observed_text_digest'], 'logical_text_digest': text['logical_text_digest'],
            'current_text_matches_original': current_matches,
            'actual_text_value': expected_current if current_matches else None,
            'font_family': native_family, 'font_ready': ready, 'fallback_used': fallback}


def caption_observation(observation, source):
    rows = observation.get('captions')
    need(type(rows) is list and len(rows) == 1 and rows[0].get('id') == source['id']
         and type(observation.get('unresolved_cues')) is list, 'typed_matrix_actual_caption_identity')
    row = rows[0]
    active = value_of(row.get('active'), lambda v: type(v) is bool, 'caption.active')
    start = value_of(row.get('cue_start'), rational, 'caption.cue_start')
    end = value_of(row.get('cue_end'), rational, 'caption.cue_end')
    if start is None or end is None or active is None:
        return check(None, 'native_caption_clock_or_activity_unavailable', active=active, start=start, end=end)
    valid = start == source['start'] and end == source['end'] and observation['unresolved_cues'] == []
    return check(valid, None if valid else 'native_caption_clock_differs_from_original',
                 active=active, start=start, end=end)


def layer_observation(fixture, rows, aliases):
    serial = {role: value_of(rows[aliases[role]].get('z_order'),
              lambda v: type(v) is int and 0 <= v < 2**53, 'z_order') for role in ('text', 'opaqueControl')}
    if any(value is None for value in serial.values()): return check(None, 'actual_draw_order_unavailable')
    behind = fixture == 'fixed-layer-behind'
    valid = serial['text'] < serial['opaqueControl'] if behind else serial['text'] > serial['opaqueControl']
    return check(valid, None if valid else 'actual_layer_draw_order_differs', native_draw_order=serial)


# Native masks are optional observations. They never replace the frozen R39
# foreground/GOLD classifier or its numerical thresholds.
def validate_text_pixel_mask(mask, width, height):
    import base64
    import binascii
    import struct
    closed(mask, ('encoding', 'data', 'runs', 'changed_pixels'), 'text_pixel_mask')
    need(mask['encoding'] == 'row-runs-u16le-base64' and type(mask['data']) is str
         and len(mask['data']) <= 87376 and len(mask['data']) % 8 == 0
         and type(mask['runs']) is int and mask['runs'] == len(mask['data']) // 8
         and type(mask['changed_pixels']) is int and 0 <= mask['changed_pixels'] <= width * height,
         'typed_pixels_mask_transport')
    try:
        raw = base64.b64decode(mask['data'], validate=True)
    except (ValueError, binascii.Error) as error:
        raise ProbeRejected('typed_pixels_mask_base64') from error
    need(base64.b64encode(raw).decode() == mask['data'] and len(raw) == mask['runs'] * 6,
         'typed_pixels_mask_canonical_encoding')
    previous, changed = None, 0
    for y, x, size in struct.iter_unpack('<HHH', raw):
        need(y < height and x < width and size > 0 and x + size <= width
             and (previous is None or y > previous[0] or y == previous[0] and x > previous[1]),
             'typed_pixels_mask_run_extent_or_order')
        previous = y, x + size
        changed += size
    need(changed == mask['changed_pixels'], 'typed_pixels_mask_changed_count')
    return raw


def text_pixel_font_family(font, family):
    # CSSOM emits one actual family. A fallback list or another suffix is not
    # silently accepted merely because it contains the authored family name.
    return type(font) is str and any(font.endswith(' ' + suffix) for suffix in
                                    (family, '"' + family + '"', "'" + family + "'"))


def bind_text_pixel_frame(*, rows, texts, aliases, actual, authored, film, config,
                         frames, frame, source_sha, arm, base):
    need(len(texts) <= 2, 'typed_pixels_original_subject_budget')
    fields = {sid for sid, row in rows.items() if 'native_text_pixels' in row}
    wanted = {aliases[role] for role in texts}
    need(fields <= wanted, 'typed_pixels_nontext_observation')
    if fields:
        need(source_sha == OBSERVED[arm], 'typed_pixels_not_emitted_by_bound_source')
    requested = config.get('textPixelObservationFrames')
    if source_sha == OBSERVED[arm]:
        need(type(requested) is list and requested == list(frames)
             and all(type(value) is int for value in requested), 'typed_pixels_original_Host_sample_roster')
    elif requested is not None:
        need(False, 'typed_pixels_legacy_source_with_new_Host_roster')
    output, serials, total_calls = {}, set(), 0
    for role, text in texts.items():
        row = rows[aliases[role]]
        raw = row.get('native_text_pixels')
        if raw is None:
            output[role] = {'state': 'UNKNOWN', 'usable': False, 'subject_id': row['id'],
                            'reason': 'native_pixel_observation_absent', 'mask': None}
            continue
        observed = value_of(raw, lambda value: type(value) is dict, 'native_text_pixels')
        if observed is None:
            output[role] = {'state': 'UNKNOWN', 'usable': False, 'subject_id': row['id'],
                            'reason': raw['reason'], 'mask': None,
                            'native_field_sha256': sha(base.json_bytes(raw))}
            continue
        closed(observed, ('native_key', 'frame', 'width', 'height', 'playback_fps', 'fps_num',
            'fps_den', 'selected_frames', 'render_input_digest', 'output_background', 'calls', 'mask'),
            'native_text_pixels')
        geometry = actual[role]['geometry']
        need(actual[role]['layout'] is not None and actual[role]['layout']['kind'] == 'Txt'
             and geometry is not None and observed['native_key'] == geometry['native_key'],
             'typed_pixels_original_native_node_identity')
        need(type(observed['frame']) is int and observed['frame'] == frame
             and type(observed['width']) is int and type(observed['height']) is int
             and (observed['width'], observed['height']) == (1920, 1080)
             and type(observed['fps_num']) is int and observed['fps_num'] == config['fpsNum'] == 30
             and type(observed['fps_den']) is int and observed['fps_den'] == config['fpsDen'] == 1
             and number(observed['playback_fps'], 1, 120) and observed['playback_fps'] == 30
             and observed['selected_frames'] == requested and type(observed['selected_frames']) is list
             and all(type(value) is int for value in observed['selected_frames'])
             and digest(observed['render_input_digest']) and observed['render_input_digest'] == config['renderInputDigest']
             and observed['output_background'] == (None if config['alpha'] else config['background']),
             'typed_pixels_actual_playback_export_Host_binding')
        background = observed['output_background']
        need(background is None or type(background) is str and re.fullmatch(r'#[0-9a-fA-F]{6}', background),
             'typed_pixels_background_identity')
        calls = observed['calls']
        need(type(calls) is list and len(calls) <= 64, 'typed_pixels_calls_budget')
        total_calls += len(calls)
        need(total_calls <= 64, 'typed_pixels_frame_calls_budget')
        color_fallback = film['editorial']['colors'].get('text')
        if color_fallback is None:
            color_fallback = next(iter(film['editorial']['colors'].values()), '#202020')
        allowed = []
        for run in authored[row['id']]['content']['runs']:
            color = run.get('color') or color_fallback
            need(type(color) is str and re.fullmatch(r'#[0-9a-fA-F]{6}', color),
                 'typed_pixels_original_opaque_run_color')
            allowed.append([int(color[index:index+2], 16) for index in (1,3,5)])
        previous, changed, native_fonts = None, 0, True
        for call in calls:
            closed(call, ('serial', 'operation', 'matrix', 'text', 'text_digest', 'font', 'color',
                'x', 'y', 'max_width', 'changed_pixels', 'global_alpha', 'composite', 'filter'), 'text_pixel_call')
            serial = call['serial']
            need(type(serial) is int and 0 <= serial < 64 and serial not in serials
                 and (previous is None or previous < serial), 'typed_pixels_call_serial')
            serials.add(serial); previous = serial
            need(call['operation'] in ('fill_text', 'stroke_text') and matrix(call['matrix'])
                 and type(call['text']) is str and len(call['text'].encode()) <= 8192
                 and call['text_digest'] == sha(call['text'].encode())
                 and type(call['font']) is str and 0 < len(call['font'].encode()) <= 512
                 and type(call['color']) is list and len(call['color']) == 3
                 and all(type(value) is int and 0 <= value <= 255 for value in call['color'])
                 and call['color'] in allowed and number(call['x'], -2e6, 2e6) and number(call['y'], -2e6, 2e6)
                 and (call['max_width'] is None or number(call['max_width'], 0, 2e6) and call['max_width'] > 0)
                 and type(call['changed_pixels']) is int and 0 <= call['changed_pixels'] <= 1920*1080
                 and number(call['global_alpha'], 1, 1) and call['composite'] == 'source-over'
                 and call['filter'] == 'none', 'typed_pixels_actual_call_binding')
            if background is not None:
                need(call['color'] != [int(background[index:index+2],16) for index in (1,3,5)],
                     'typed_pixels_no_output_contrast')
            changed += call['changed_pixels']
            native_fonts = native_fonts and text_pixel_font_family(call['font'], text['font_family'] or '')
        # The exact bound source accounts for JSON.stringify bytes at capture;
        # do not replace that count with a differently formatted Python JSON.
        encoded = validate_text_pixel_mask(observed['mask'], 1920, 1080)
        need(observed['mask']['changed_pixels'] <= changed
             and (observed['mask']['changed_pixels'] > 0 or changed == 0), 'typed_pixels_union_calls_consistency')
        if not calls:
            opacity = value_of(row.get('opacity'), lambda v: number(v, 0, 1), 'opacity')
            need(opacity == 0 or text['native_observed_text_digest'] == sha(b''),
                 'typed_pixels_absent_calls_without_observed_inactivity')
        usable = text['font_and_text_verified'] and native_fonts
        output[role] = {'state': 'KNOWN' if usable else 'UNKNOWN', 'usable': usable,
            'reason': None if usable else 'actual_glyph_font_or_original_text_identity_unavailable',
            'subject_id': row['id'], 'native_key': observed['native_key'],
            'frame': frame, 'canvas': [1920,1080], 'selected_frames': list(requested),
            'render_input_digest': observed['render_input_digest'], 'producer_native_source_sha256': source_sha,
            'original_native_field_sha256': sha(base.json_bytes(raw)),
            'original_call_roster_sha256': sha(base.json_bytes(calls)), 'actual_call_count': len(calls),
            'actual_call_font_family_verified': native_fonts,
            'native_observed_text_digest': text['native_observed_text_digest'],
            'font_asset_and_text_identity_verified': text['font_and_text_verified'],
            'mask': dict(observed['mask']) if usable else None,
            'original_mask_encoding_sha256': sha(encoded),
            'scope': 'pixels_changed_by_real_glyph_paint_before_later_overlap',
            'GOLD_baseline_claimed': False, 'pixel_visibility_inferred': False}
    return output


def bind_originals(originals, plan, profile, spec, base, *, transform_spec_raw=None):
    """Bind all twelve original fixtures; no mutation, owner, Native or gate grant."""
    fixture, arm = plan.get('fixture'), plan.get('arm')
    need(fixture in FIXTURES and arm in OBSERVED, 'typed_matrix_closed_fixture_scope')
    frames = FIXTURES[fixture]
    effective = plan['arm_sources'][arm]['native_source_sha256']
    need(effective in (OBSERVED[arm], GEOMETRY_OBSERVED[arm], LAYOUT_OBSERVED[arm], HISTORICAL[arm]), 'typed_matrix_exact_observer_source')
    film_path, expectation_path = one_role(plan, 'authored-film'), one_role(plan, 'fixture-expectations')
    film_raw = originals.small(film_path, MIB); film = base.strict(film_raw, MIB)
    expectations = originals.json(expectation_path, MIB)
    need(expectations.get('schema') == 'motionwright-fixedtxt-film-expectations/4'
         and expectations.get('key') == fixture and expectations.get('film_sha256') == sha(film_raw)
         and originals.descriptor(film_path) == {'bytes': len(film_raw), 'sha256': sha(film_raw)},
         'typed_matrix_original_Film_expectation_binding')
    source_binding = bind_fixture_sources(originals, plan, film_path, expectation_path, effective, base)
    aliases, authored, original_texts = bind_authored_fixture(fixture, film, expectations, spec,
        transform_spec_raw=transform_spec_raw, base=base)
    caption_source = bind_caption_contract(film, expectations, aliases, originals, plan) if fixture == 'fixed-counter-captions' else None
    prefix = 'observation/' + fixture + '/'
    config = originals.json(prefix + 'native-result.fixed-text/host-binding.observation.json', 2 * MIB)['original_config']
    inventory_path = prefix + 'fixed-text-original-readback/font-source-inventory.json'
    family = font_identity(film, config, originals.json(inventory_path, MIB))
    need(profile.get('bound_observation_frames') == 180 and profile.get('bound_sRGB_canvas_frames') == 180
         and profile.get('native_browser_font_readiness_verified') is True
         and profile.get('font_source_config_identities_verified') is True, 'typed_matrix_preceding_physical_original_bindings')
    selected = profile['selected_observations']
    need(set(selected) == set(frames), 'typed_matrix_selected_original_frames')
    probes, details = {}, []
    for frame in frames:
        observation = selected[frame]
        need(observation.get('frame') == frame and type(observation.get('subjects')) is list, 'typed_matrix_native_frame_identity')
        rows = observation['subjects']
        need(len(rows) <= 1024 and all(type(row) is dict and subject_id(row.get('id')) for row in rows)
             and len({row['id'] for row in rows}) == len(rows), 'typed_matrix_native_subject_roster')
        rows = {row['id']: row for row in rows}
        need(set(rows) == set(aliases.values()), 'typed_matrix_original_native_subject_roster')
        actual = {role: actual_geometry(rows[sid], arm=arm, source_sha=effective) for role, sid in aliases.items()}
        texts = {}
        for role, original in original_texts.items():
            current = COUNTER_VALUES[frame] if caption_source and role == 'counter' else (
                CAPTION_TEXT if caption_source and role == 'caption' and frame >= 30 else original)
            texts[role] = actual_text_identity(rows[aliases[role]], authored[aliases[role]], current, family)
        text_pixels = bind_text_pixel_frame(rows=rows, texts=texts, aliases=aliases, actual=actual,
            authored=authored, film=film, config=config, frames=frames, frame=frame,
            source_sha=effective, arm=arm, base=base)
        pixel_profile_bound = (effective == OBSERVED[arm]
            and profile.get('native_text_pixel_profile_bound') is True
            and config.get('textPixelObservationFrames') == list(frames))
        font_verified = all(row['font_and_text_verified'] for row in texts.values())
        targets = {role for role, samples in spec['fixtures'][fixture].get('text_boxes', {}).items() if str(frame) in samples}
        if fixture == 'fixed-transform-clip': targets.add('text')
        if fixture == 'flow-text-control': targets.update(original_texts)
        geometry_known = lambda node: node is not None and node['kind'] == 'Txt' and node['layout_root'] is not None and node['screen_layout_box'] is not None
        complete = all(geometry_known(actual[role]['layout']) for role in targets)
        nodes = {role: {**actual[role]['layout'], 'subject_id_verified': True} for role in original_texts if actual[role]['layout'] is not None}
        probe = {'schema': 'motionwright.a5.fixedtxt-typed-native-probe/1', 'fixture': fixture, 'arm': arm,
                 'frame': frame, 'nodes': nodes, 'font_asset_and_text_identity_verified': font_verified}
        checks = {}
        if fixture == 'fixed-transform-clip':
            checks['original_parent'] = verify_original_parent(actual['text'], actual['parent'])
            points = geometry_value(actual['text'], 'screen_layout_polygon', polygon)
            own_clip = geometry_value(actual['parent'], 'own_clip', lambda v: type(v) is dict)
            clip_points = own_clip['clip']['screen_polygon'] if own_clip is not None and own_clip['kind'] == 'rect' else None
            complete = complete and points is not None and clip_points is not None and checks['original_parent']['verified'] is not None
            node = actual['text']['layout']
            probe = {'schema': 'motionwright.a5.fixedtxt-transformed-clip-probe/1', 'fixture': fixture, 'arm': arm, 'frame': frame,
                'subject_kind': node['kind'] if node else None, 'subject_id_verified': True,
                'font_assets_source_and_text_verified': font_verified,
                'single_original_parent_transform_verified': checks['original_parent']['verified'],
                'actual_native_text_screen_polygon': points, 'actual_native_clip_screen_polygon': clip_points,
                'TXT_isLayoutRoot': node['layout_root'] if node else None}
        elif fixture == 'fixed-camera-scene':
            checks['original_camera'] = verify_original_camera(actual['camera'], [actual['text'], actual['gold']], canvas_size=(1920, 1080))
            probe['single_original_camera_verified'] = checks['original_camera']['verified']
        elif caption_source:
            checks['caption_clock'] = caption_observation(observation, caption_source)
            probe['text_signals'] = {role: row['actual_text_value'] for role, row in texts.items()}
            probe['caption_active'] = checks['caption_clock']['active']
            # The frozen numeric function coerces activity with bool(). Do not
            # let an unknown clock/activity be mistaken for a valid inactive cue.
            if checks['caption_clock']['verified'] is not True: probe = None
        if fixture.startswith('flow-'):
            for role in ('flowA', 'flowB') if fixture != 'flow-text-control' else ('flowTitle', 'flowBody'):
                checks[role] = flow_observation(actual[role])
        if fixture.startswith('fixed-layer-'):
            checks['draw_order'] = layer_observation(fixture, rows, aliases)
        complete = complete and all(value['verified'] is not None for value in checks.values())
        probes[frame] = probe
        details.append({'frame': frame, 'native_type_serialized': all(actual[r]['layout'] is not None for r in aliases),
            'native_type_known_Txt': all(actual[r]['layout'] is not None and actual[r]['layout']['kind'] == 'Txt' for r in original_texts) if original_texts else None,
            'missing_native_observations': [r + '.' + field for r in targets for field, key in
                (('native_type', 'kind'), ('layout_root', 'layout_root'), ('screen_layout_box', 'screen_layout_box'))
                if actual[r]['layout'] is None or actual[r]['layout'][key] is None],
            'native_geometry_complete': complete, 'font_asset_and_text_identity_verified': font_verified,
            'typed_text_identities': texts, 'native_geometry_predicates': checks,
            'observed_text_pixels': text_pixels, 'native_text_pixel_profile_bound': pixel_profile_bound,
            'native_text_pixel_coverage_known': pixel_profile_bound
                and all(row['usable'] is True for row in text_pixels.values()),
            'all_structural_predicates_true': combine(list(checks.values())),
            'original_native_layouts': {role: actual[role]['layout'] for role in aliases},
            'original_native_lineages': {role: normalized_lineage(actual[role]) for role in aliases},
            'native_observation_original_sha256': sha(base.json_bytes(observation))})
    report = {'schema': 'motionwright.fixedtxt.original-typed-matrix-adaptation/4', 'fixture': fixture, 'arm': arm,
        'producer_native_source_sha256': effective, 'authored_Film_original': originals.descriptor(film_path),
        'fixture_expectations_original': originals.descriptor(expectation_path),
        'font_inventory_original': originals.descriptor(inventory_path), 'font_resources_digest': config['fontResourcesDigest'],
        **source_binding, 'selected_frame_bindings': details,
        'native_text_pixel_sample_frames': list(frames) if effective == OBSERVED[arm] else None,
        'all_selected_native_text_pixel_coverage_known': all(row['native_text_pixel_coverage_known'] for row in details),
        'native_text_pixel_mask_source': 'per-arm before/after actual glyph operations',
        'R39_GOLD_baseline_or_final_pixel_visibility_claimed': False,
        'all_selected_geometry_and_font_text_bound': all(row['native_geometry_complete'] and row['font_asset_and_text_identity_verified'] for row in details),
        'all_selected_structural_predicates_true': combine([check(row['all_structural_predicates_true'], None) for row in details]),
        'pair_comparisons_required': fixture in ('fixed-causal', 'fixed-camera-scene', 'flow-control-base',
            'flow-control-with-fixed', 'flow-text-control', 'fixed-multirun', 'fixed-layer-behind', 'fixed-layer-equal', 'fixed-layer-front'),
        'geometry_copied_from_Film_expectations': False, 'native_calls': 0, 'candidate24_accepted': 0, 'G2_accepted': False, 'F_accepted': False}
    return probes, report
