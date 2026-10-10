#!/usr/bin/env python3
"""Negative-path verification for source-bound native PNG transfer receipts."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
import tempfile
import unittest
import zipfile
from verify_dirty_transfer import rebuild,TransferRejected,SCHEMA
class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='motionwright-dirty-transfer-test-')
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.cache=root/'cache';self.cache.mkdir()
        self.patch=root/'patch.zip';self.receipt=root/'source.json'
        self.output=root/'rebuilt'
        def source(i):
            return b'\x89PNG\r\n\x1a\n'+f'original source {i}'.encode()
        self.old=[source(i) for i in range(2)]
        self.changed=[source(i+200) for i in range(2,5)]
        for i,data in enumerate(self.old):
            (self.cache/f'frame-{i:06}.png').write_bytes(data)
        self.doc={'schema':SCHEMA,'before_source_sha256':'ab'*32,
                  'after_source_sha256':'cd'*32,'total_frames':5,
                  'reusable_intervals':[{'start':0,'end_exclusive':2}],
                  'dirty_intervals':[{'start':2,'end_exclusive':5}],
                  'reusable_frame_receipts':[hashlib.sha256(v).hexdigest()for v in self.old],
                  'dirty_frame_receipts':[hashlib.sha256(v).hexdigest()for v in self.changed],
                  'metadata_reused':False,'native_frames_not_rendered':0,
                  'encoder_stage_avoided':False,'source_only_visual_not_audio':True}
        self.save()
        with zipfile.ZipFile(self.patch,'w')as archive:
            for i,data in enumerate(self.changed,2):
                archive.writestr(f'frames/frame-{i:06}.png',data)
    def save(self):
        self.receipt.write_text(json.dumps(self.doc))
    def verify(self):
        return rebuild(self.patch,self.receipt,self.cache,self.output)
    def test_original_cache_and_dirty_transfer_rebuild_all_exact_frames(self):
        result=self.verify()
        self.assertEqual(result['frames'],5)
        self.assertEqual(result['reused'],2)
        self.assertEqual(result['transferred'],3)
        self.assertEqual(result['actual_native_render_frames_avoided'],0)
        self.assertEqual(result['receipts_verified'],'ALL_FRAME_SHA256')
        self.assertEqual(len(list(self.output.iterdir())),5)
    def test_changes_to_old_cache_fail_closed_and_never_leave_partial_output(self):
        (self.cache/'frame-000001.png').write_bytes(b'\x89PNG\r\n\x1a\nmodified media')
        with self.assertRaisesRegex(TransferRejected,'source receipt'):
            self.verify()
        self.assertFalse(self.output.exists())
    def test_overlap_and_missing_intervals_fail_before_reading_any_file(self):
        self.doc['dirty_intervals']=[{'start':1,'end_exclusive':5}]
        self.save()
        with self.assertRaises(TransferRejected):self.verify()
        self.assertFalse(self.output.exists())
    def test_source_only_receipt_cannot_forge_renderer_or_audio_savings(self):
        for attribute,value in [
            ('metadata_reused',True),('native_frames_not_rendered',30),
            ('encoder_stage_avoided',True),('source_only_visual_not_audio',False)
        ]:
            previous=self.doc[attribute]
            self.doc[attribute]=value
            self.save()
            with self.assertRaisesRegex(TransferRejected,'cannot claim to bypass'):
                self.verify()
            self.assertFalse(self.output.exists())
            self.doc[attribute]=previous
    def test_unknown_manifest_authority_is_rejected(self):
        self.doc['auto_publish']=True;self.save()
        with self.assertRaisesRegex(TransferRejected,'schema'):
            self.verify()
    def test_extra_executable_archive_payload_is_refused(self):
        new=self.patch.with_name('patched.zip')
        with zipfile.ZipFile(self.patch,'r')as src,zipfile.ZipFile(new,'w')as target:
            for name in src.namelist():target.writestr(name,src.read(name))
            target.writestr('execute.sh','false')
        self.patch=new
        with self.assertRaisesRegex(TransferRejected,'exactly the dirty PNGs'):
            self.verify()
    def test_output_cannot_silently_overwrite_an_approved_project(self):
        self.output.mkdir()
        with self.assertRaisesRegex(TransferRejected,'cannot be overwritten'):
            self.verify()
    def test_unapproved_symbolic_cache_path_is_denied(self):
        old=self.cache/'frame-000000.png';old.unlink()
        external=self.cache.parent/'other.png';external.write_bytes(self.old[0])
        old.symlink_to(external)
        with self.assertRaisesRegex(TransferRejected,'cached PNG missing'):
            self.verify()
    def test_corrupted_transmitted_png_is_rejected(self):
        changed=self.patch.with_name('corrupt.zip')
        with zipfile.ZipFile(self.patch,'r')as src,zipfile.ZipFile(changed,'w')as out:
            for name in src.namelist():
                data=src.read(name)
                out.writestr(name,data+b'unauthorized change'if name.endswith('000002.png')else data)
        self.patch=changed
        with self.assertRaisesRegex(TransferRejected,'digest or native magic'):
            self.verify()
if __name__=='__main__':unittest.main()
