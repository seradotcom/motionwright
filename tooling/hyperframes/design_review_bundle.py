#!/usr/bin/env python3
"""Build an immutable, minimal design-review handoff from SAME-SHA verified CI artifacts.

Never install/render/capture here. Technical PASS is not aesthetic acceptance.
All source media in this test bundle is original synthetic acceptance material.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[2]
READ_ME="""# Motionwright v0.5 | First-party creative review pack

This is a **synthetic, source-bound technical acceptance sample**, not a
Motionwright customer video, not product screenshots, not a publication
approval, and not proof that v0.5 is fully production-ready.

## How to review

1. Open each `visual/shard-*-contact-sheet.png` at native resolution.
   Check hierarchy, typography, rhythm, composition, density, and that the
   layout still makes sense in landscape, portrait, and square.
2. Watch `blender/arc-reveal-preview.mp4` and inspect the corresponding
   `blender/arc-reveal-source.blend`. Compare camera motion, framing,
   material quality, lighting, object continuity and manual editability.
   The preview is half-resolution, no merged audio, **not a mastered clip**.
3. Listen to each `audio/original-*.wav`. The associated waveform PNG and
   EBU R128 numbers are factual signal measurements, **not** assertions of
   intelligibility, voice timing, taste or mastering.
4. Make at least ten consecutive edits in Studio to one original source.
   Check identity, source-preservation, human locks and stale proposal
   rejection. This ZIP alone does not prove desktop interaction.
5. Record the actual creative judgments in `review-sheet-template.json`.
   Keep rejected ideas. Do not convert a renderer technical PASS into an
   automatically human-approved design.

## Human scoring prompts (1-5, or null until observed)

- Narrative / causality: could a newcomer accurately explain the message?
- Composition: distinctive and legible, or a generic AI-template arrangement?
- Type: hierarchy, spacing, appropriate language, no truncation?
- Motion: purposeful acceleration, continuity, camera and holds?
- Art direction: style consistency, material, light and brand fidelity?
- Truth/provenance: no fictional performance claims or invented UI captures?
- Editing: real reusability and preservation after multiple human revisions?
- Sound: useful timing, peak/loudness and listening quality?
- Multi-ratio: all three output ratios deserve independent judgment.
- Comparative advantage: would a skilled human/agent using HyperFrames or
  Blender directly get the same or a better result for less effort?

Do not mark any unreviewed category complete. A product/launch campaign
requires owner-granted source/rights, a human creative director and
release-specific tests. Never redistribute installed npm/browser/font files.

## Source boundaries

The SHA recorded in `review-manifest.json` is the specific Motionwright
commit tested in CI. The source recipes and runtime identities must be
confirmed against the reported evidence at that exact revision. The
Semwright canonical Broker, the 320 MiB Chromium sealed-tool candidate,
security approval, publish permission and final mastering are **separate
gates**, not implied by this synthetic pack.
"""
def file_sha(data:bytes)->str:return hashlib.sha256(data).hexdigest()
def check(value:bool,why:str)->None:
    if not value:raise AssertionError(why)
def safe_read(path:Path,root:Path,budget:int)->bytes:
    check(path.is_relative_to(root),"Review file escaped its artifact root")
    check(path.is_file() and not path.is_symlink(),"Review file is not an immutable regular artifact")
    size=path.stat().st_size
    check(0<size<=budget,f"Review file byte budget exceeded: {path.name}")
    return path.read_bytes()
def read_json(path:Path,root:Path)->dict:
    return json.loads(safe_read(path,root,512*1024).decode('utf-8'))
def create(shard_dir:Path,blender_dir:Path,audio_dir:Path,target:Path,commit:str)->dict:
    check(len(commit)==40 and all(c in'0123456789abcdef' for c in commit),"Exact commit must be canonical lowercase SHA-1")
    check(not target.exists(),"Review ZIP output must never overwrite evidence")
    entries:dict[str,bytes]={}
    results=[]
    for shard in range(3):
        root=shard_dir/commit/f'shard-{shard}'
        info=read_json(root/'result.json',shard_dir)
        check(info['source_sha']==commit and info['renderer_native_samples']=='PASS',
              "Visual shard is not source-bound or native accepted")
        check(info['sampled_components']>=8 and info['human_creative_quality']=='NOT_REVIEWED',
              "Visual shard omitted samples or falsely claimed creative approval")
        entries[f'visual/shard-{shard}-contact-sheet.png']=safe_read(root/'contact-sheet.png',shard_dir,12*1024*1024)
        results.append({'type':'native_visual','shard':shard,'sampled_components':info['sampled_components'],
                        'technical_status':'PASS','creative_quality':'NOT_REVIEWED'})
    blender=read_json(blender_dir/'result.json',blender_dir)
    check(blender['motionwright_sha']==commit and blender['native_editable_stages']=='PASS',
          "Blender source is not tied to exact commit and its native authoring evidence")
    check(len(blender['rows'])>=3,"Native Blender stage result must include multiple independent recipes")
    entries['blender/stage-review-strip.png']=safe_read(blender_dir/'stage-review-strip.png',blender_dir,20*1024*1024)
    arc=blender_dir/'arc-reveal'
    arc_receipt=read_json(arc/'stage.json',blender_dir)
    check(arc_receipt['creative_approval']=='required',"Synthetic Blender cannot self-approve")
    for name,location,maxbytes in [
        ('arc-reveal-preview.mp4',arc/'native-arc-preview.mp4',45*1024*1024),
        ('arc-reveal-source.blend',arc/'stage.blend',80*1024*1024),
        ('arc-reveal-interchange.glb',arc/'stage.glb',40*1024*1024),
    ]:
        data=safe_read(location,blender_dir,maxbytes)
        if name.endswith('.blend'):
            check(file_sha(data)==arc_receipt['original_source']['sha256'],"Editable native Blender source SHA differs")
        if name.endswith('.glb'):
            check(file_sha(data)==arc_receipt['interchange']['sha256'],"Editable GLB interchange digest differs")
        entries['blender/'+name]=data
    results.append({'type':'native_blender','sampled_recipes':len(blender['rows']),
        'full_clip':'90_frame_native_preview_review_copy','creative_quality':'NOT_REVIEWED'})
    sound=read_json(audio_dir/'result.json',audio_dir)
    check(sound['source_sha']==commit and sound['decoded_pcm']=='PASS' and sound['mastering']=='not_performed',
          "Original audio has no exact source/decode evidence or falsely claims mastering")
    check(len(sound['rows'])==3,"All three original synthetic source audio samples are required")
    for recipe in ('focus-hit','transition-tail','energy-bed'):
        audio=next((row for row in sound['rows'] if row['recipe']==recipe),None)
        check(audio is not None,"Sound acceptance row is missing")
        wav=safe_read(audio_dir/f'{recipe}.wav',audio_dir,40*1024*1024)
        check(file_sha(wav)==audio['native_wav_sha256'],"Audio WAV digest disagrees with independent decode")
        entries[f'audio/original-{recipe}.wav']=wav
        png=audio_dir/f'{recipe}-waveform.png'
        if png.is_file():
            entries[f'audio/original-{recipe}-waveform.png']=safe_read(png,audio_dir,8*1024*1024)
            check(file_sha(entries[f'audio/original-{recipe}-waveform.png'])==
                audio['independent_audio_measurement']['waveform_png_sha256'],"Original waveform digest mismatched")
    results.append({'type':'native_audio','synthetic_sources':3,'mastered':False,'creative_quality':'NOT_REVIEWED'})
    template={'schema':'motionwright.v05-human-creative-review/1','motionwright_sha':commit,
        'reviewer':None,'date_utc':None,'source_rights_attested':False,'approved_for_publication':False,
        'categories':{name:None for name in ('narrative','composition','typography','motion',
          'art_direction','truth_and_provenance','editing_roundtrip','audio',
          'multi_ratio','direct_renderer_comparison')},
        'observations':[],'rejected_concepts':[],'must_retest_after_source_edit':True}
    entries['README.md']=READ_ME.encode()
    entries['review-sheet-template.json']=(json.dumps(template,indent=2)+'\n').encode()
    manifest={'schema':'motionwright.v05-human-review-bundle/1','motionwright_sha':commit,
        'creative_approval':'NOT_REVIEWED','canonical_broker_certification':'SEPARATE_GATE',
        'source_material':'original_synthetic_fixtures_only','tests':results,
        'files':[{'name':name,'sha256':file_sha(data),'bytes':len(data)}
            for name,data in sorted(entries.items())]}
    entries['review-manifest.json']=(json.dumps(manifest,indent=2)+'\n').encode()
    target.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name,data in sorted(entries.items()):
            info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o644<<16
            archive.writestr(info,data,compress_type=zipfile.ZIP_DEFLATED,compresslevel=6)
    check(target.stat().st_size<=160*1024*1024,"Compact review ZIP exceeded its distribution budget")
    return {'status':'SOURCE_BOUND_HUMAN_REVIEW_REQUIRED','sha':commit,
        'files':len(entries),'zip_sha256':file_sha(target.read_bytes()),
        'zip_bytes':target.stat().st_size}
def main()->None:
    if os.environ.get('GITHUB_ACTIONS')!='true':
        raise SystemExit('Source-attested review archives are built on disposable CI only')
    if len(sys.argv)!=6:
        raise SystemExit('usage: design_review_bundle.py VISUAL_ARTIFACT_DIR BLENDER_ARTIFACT_DIR AUDIO_ARTIFACT_DIR OUT_ZIP HEAD_SHA')
    visual,blender,audio,output=map(Path,sys.argv[1:5])
    commit=sys.argv[5]
    for root in (visual,blender,audio):
        check(root.is_dir() and not root.is_symlink(),"CI review artifact directory is absent")
    print(json.dumps(create(visual,blender,audio,output,commit)))
if __name__=='__main__':main()
