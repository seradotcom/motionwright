#!/usr/bin/env python3
"""I/O-only seam for the exact FixedTxt selected-original oracle.

The caller authenticates one sealed snapshot before this function. Original
P/R/Q, external dispatch, host joins, all 180 PNG digests, selected RGBA and
supervision predicates remain in the pinned source function. No keys, files,
Native calls or R28 are generated here.
"""
from __future__ import annotations
import ast
from collections.abc import Mapping
import hashlib
from pathlib import Path
import tarfile

MIB = 1024 * 1024
SOURCE_SHA256 = '430b8d41980cd1abf70eaef46056118d89919a6258c5c6d886cdfe3f553555e2'
CHUNK = MIB
MAX_CAPSULE = 256 * MIB
MAX_METADATA = 8 * MIB
BORROWED_ROLES = {'native-png', 'selected-png', 'selected-rgba', 'font-original', 'native-observations'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def make_oracle(fixed, bounded):
    with Path(fixed.__file__).open('rb') as stream:
        raw = stream.read(2 * MIB + 1)
    fixed.need(len(raw) <= 2 * MIB and sha(raw) == SOURCE_SHA256, 'bounded_selected_source_digest')
    tree = ast.parse(raw)
    snapshot_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'verify_snapshot')
    original = ast.get_source_segment(raw.decode(), snapshot_node)
    changes = (
        ('type(plaintext) is bytes and 0 < len(plaintext) <= plan["limits"]["capsule_bytes"]',
         'type(plaintext) is _bounded.AuthenticatedSnapshot and 0 < plaintext.size <= min(plan["limits"]["capsule_bytes"], _max_capsule)'),
        ('with tarfile.open(fileobj=io.BytesIO(plaintext), mode="r:") as archive:',
         'with _bounded.BorrowedReader(plaintext) as _input, tarfile.open(fileobj=_input, mode="r:") as archive:'),
        ('manifest_raw = archive.extractfile(member).read(512 * 1024 + 1)',
         'manifest_raw = _small_member(plaintext, member, 512 * 1024)'),
        ('data = archive.extractfile(member).read(row["maximum"] + 1)',
         'data = _borrow_member(plaintext, member)'),
        ('_pack_originals(originals, manifest_raw) == plaintext',
         '_canonical_matches(plaintext, originals, manifest_raw)'),
        ('resolved = resolve_native_directory(plan, originals, cycle_raw=held.cycle_raw)',
         'resolved = resolve_native_directory(plan, _metadata_view(originals, plan), cycle_raw=held.cycle_raw)'),
        ('== descriptor(originals[key])', '== _descriptor_view(originals[key])'),
        ('selected = fixedtxt_validate_selected_frames(originals, plan=plan)',
         'selected = fixedtxt_validate_selected_frames(_metadata_view(originals, plan), plan=plan)'),
        ('verify_supervisor_originals(originals, plan=plan, current=current, runner_temp=runner_temp)',
         'verify_supervisor_originals(_metadata_view(originals, plan), plan=plan, current=current, runner_temp=runner_temp)'),
    )
    transformed = original
    for old, new in changes:
        fixed.need(transformed.count(old) == 1, 'bounded_selected_exact_io_seam_changed')
        transformed = transformed.replace(old, new, 1)
    borrowed = []
    inventory = []

    def member(snapshot, info):
        fixed.need(type(info.offset_data) is int and type(info.size) is int
                   and 0 <= info.offset_data <= info.offset_data + info.size <= snapshot.size,
                   'bounded_selected_member_extent')
        whole = snapshot.view()
        try:
            view = whole[info.offset_data:info.offset_data + info.size]
        finally:
            whole.release()
        borrowed.append(view)
        inventory.append({'archive_path': info.name, 'bytes': len(view), 'sha256': sha(view),
                          'kind': 'authenticated-selected-original'})
        return view

    def small_member(snapshot, info, maximum):
        fixed.need(info.size <= maximum <= MAX_METADATA, 'bounded_selected_metadata_budget')
        view = member(snapshot, info)
        return bytes(view)

    class MetadataView(Mapping):
        def __init__(self, values, plan):
            self.values = values
            self.rows = {row['root'] + '/' + row['relative_path']: row for row in plan['members']}

        def __getitem__(self, key):
            value = self.values[key]
            if self.rows[key]['role'] in BORROWED_ROLES:
                return value
            fixed.need(len(value) <= MAX_METADATA, 'bounded_selected_metadata_budget')
            return bytes(value)

        def __iter__(self):
            return iter(self.values)

        def __len__(self):
            return len(self.values)

    def descriptor(value):
        fixed.need(type(value) in (bytes, memoryview), 'bounded_selected_buffer_kind')
        return {'bytes': len(value), 'sha256': sha(value)}

    def canonical_matches(snapshot, originals, manifest):
        whole, offset = snapshot.view(), 0
        def same(expected):
            nonlocal offset
            fixed.need(offset + len(expected) <= len(whole), 'bounded_selected_tar_extent')
            for start in range(0, len(expected), CHUNK):
                block = expected[start:start + CHUNK]
                observed = whole[offset + start:offset + start + len(block)]
                try:
                    fixed.need(observed == block, 'fixedtxt-v2-canonical-ustar-originals')
                finally:
                    observed.release()
                    if isinstance(block, memoryview):
                        block.release()
            offset += len(expected)
        try:
            for name, value in [*sorted(originals.items()), ('snapshot-manifest.json', manifest)]:
                header = tarfile.TarInfo(name)
                header.size, header.mode, header.mtime = len(value), 0o600, 0
                same(header.tobuf(format=tarfile.USTAR_FORMAT))
                same(value)
                same(b'\0' * (-len(value) % 512))
            same(b'\0' * 1024)
            same(b'\0' * (-offset % tarfile.RECORDSIZE))
            fixed.need(offset == snapshot.size, 'fixedtxt-v2-canonical-ustar-originals')
            return True
        finally:
            whole.release()

    scope = dict(fixed.__dict__)
    scope.update(_bounded=bounded, _max_capsule=MAX_CAPSULE, _small_member=small_member,
                 _borrow_member=member, _canonical_matches=canonical_matches,
                 _metadata_view=MetadataView, _descriptor_view=descriptor)
    exec(compile(transformed, str(fixed.__file__) + '::bounded-selected-io', 'exec'), scope)
    pure = scope['verify_snapshot']

    def validate(snapshot, held, *, current, runner_temp):
        fixed.need(not borrowed and not inventory, 'bounded_selected_concurrent_call')
        try:
            result = pure(snapshot, held, current=current, runner_temp=runner_temp)
            return result, list(inventory)
        finally:
            for view in borrowed:
                view.release()
            borrowed.clear()
            inventory.clear()

    meta = {'source_sha256': SOURCE_SHA256, 'original_function_sha256': sha(original.encode()),
            'bounded_function_sha256': sha(transformed.encode()), 'exact_io_replacements': len(changes),
            'oracle_predicates_removed': 0, 'whole_plaintext_copies': 0, 'whole_repacked_tar_copies': 0,
            'maximum_metadata_copy_bytes': MAX_METADATA, 'private_plaintext_written': False}
    return validate, meta
