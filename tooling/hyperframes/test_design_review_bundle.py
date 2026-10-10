#!/usr/bin/env python3
"""Synthetic zero-network contract tests for exact-SHA design review archives."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import design_review_bundle as review

SHA='a'*40
def digest(value:bytes)->str:return hashlib.sha256(value).hexdigest()
def write(path:Path,value:bytes)->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(value)
def obj(path:Path,value:dict)->None:write(path,(json.dumps(value)+'\n').encode())
class DesignReviewBundleTests(unittest.TestCase):
 def setUp(self):
    self.temp=tempfile.TemporaryDirectory(prefix='mw-review-contract-')
    self.addCleanup(self.temp.cleanup)
    self.root=Path(self.temp.name)
    self.visual=self.root/'visual';self.blender=self.root/'blender';self.audio=self.root/'audio'
    self.visual.mkdir();self.blender.mkdir();self.audio.mkdir()
    for shard in range(3):
        base=self.visual/SHA/f'shard-{shard}'
        obj(base/'result.json',{'source_sha':SHA,'renderer_native_samples':'PASS',
             'sampled_components':9,'human_creative_quality':'NOT_REVIEWED'})
        write(base/'contact-sheet.png',b'SYNTHETIC BOUNDED REVIEW PIXELS '+bytes([shard]))
    obj(self.blender/'result.json',{'motionwright_sha':SHA,
        'native_editable_stages':'PASS','rows':[{'recipe':str(i)}for i in range(5)]})
    write(self.blender/'stage-review-strip.png',b'SYNTHETIC ORIGINAL STAGE STRIP')
    arc=self.blender/'arc-reveal'
    blender=b'BLENDER original synthetic fixture';glb=b'glTF0000original synthetic fixture'
    write(arc/'stage.blend',blender);write(arc/'stage.glb',glb)
    write(arc/'native-arc-preview.mp4',b'SYNTHETIC noncommercial video fixture')
    obj(arc/'stage.json',{'creative_approval':'required',
        'original_source':{'sha256':digest(blender)},
        'interchange':{'sha256':digest(glb)}})
    rows=[]
    for recipe in ('focus-hit','transition-tail','energy-bed'):
        wav=(recipe+' original PCM synthetic fixture').encode()
        write(self.audio/f'{recipe}.wav',wav)
        rows.append({'recipe':recipe,'native_wav_sha256':digest(wav)})
    obj(self.audio/'result.json',{'source_sha':SHA,'decoded_pcm':'PASS',
        'mastering':'not_performed','rows':rows})
 def test_exact_shas_and_review_status_survive_into_a_deterministic_zip(self):
    archive=self.root/'review.zip'
    result=review.create(self.visual,self.blender,self.audio,archive,SHA)
    self.assertEqual(result['status'],'SOURCE_BOUND_HUMAN_REVIEW_REQUIRED')
    with zipfile.ZipFile(archive)as zip:
        entries=set(zip.namelist())
        self.assertIn('README.md',entries)
        self.assertIn('blender/arc-reveal-source.blend',entries)
        self.assertIn('blender/arc-reveal-preview.mp4',entries)
        self.assertIn('audio/original-energy-bed.wav',entries)
        self.assertIn('visual/shard-2-contact-sheet.png',entries)
        manifest=json.loads(zip.read('review-manifest.json'))
        self.assertEqual(manifest['motionwright_sha'],SHA)
        self.assertEqual(manifest['creative_approval'],'NOT_REVIEWED')
        self.assertEqual(manifest['canonical_broker_certification'],'SEPARATE_GATE')
        self.assertTrue(all(digest(zip.read(row['name']))==row['sha256']
            for row in manifest['files']))
        form=json.loads(zip.read('review-sheet-template.json'))
        self.assertTrue(all(value is None for value in form['categories'].values()))
        self.assertFalse(form['approved_for_publication'])
    duplicate=self.root/'second.zip'
    another=review.create(self.visual,self.blender,self.audio,duplicate,SHA)
    self.assertEqual(result['zip_sha256'],another['zip_sha256'])
 def test_missing_source_bound_shard_is_not_a_review_bundle(self):
    shard=self.visual/SHA/'shard-1'/'result.json'
    content=json.loads(shard.read_text());content['source_sha']='b'*40;obj(shard,content)
    with self.assertRaisesRegex(AssertionError,'source-bound'):
        review.create(self.visual,self.blender,self.audio,self.root/'untrusted.zip',SHA)
 def test_unapproved_or_mismatched_media_is_rejected(self):
    changed=self.blender/'arc-reveal'/'stage.blend'
    changed.write_bytes(b'Different synthetic source')
    with self.assertRaisesRegex(AssertionError,'SHA'):
        review.create(self.visual,self.blender,self.audio,self.root/'wrong.zip',SHA)
 def test_missing_sound_cannot_silently_claim_three_sources(self):
    (self.audio/'transition-tail.wav').unlink()
    with self.assertRaisesRegex(AssertionError,'immutable regular'):
        review.create(self.visual,self.blender,self.audio,self.root/'missing.zip',SHA)
if __name__=='__main__':unittest.main()
