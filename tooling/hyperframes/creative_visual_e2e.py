#!/usr/bin/env python3
"""Native source-bound visual samples for owner review, not automatic artistic approval."""
from __future__ import annotations
import hashlib,json,os,shutil,subprocess,sys,tempfile
from pathlib import Path
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[2]
RUNTIME=ROOT/'runtime/hyperframes'
RUNNER=ROOT/'target/debug/motionwright-hyperframes-runner'
FIXTURE=ROOT/'target/debug/examples/native_catalog_fixture'
CASES=[
 ('hero-focus','landscape','en'),('contrast-pair','portrait','es'),('measured-number','square','de'),
 ('hero-reveal','landscape','en'),('screen-focus','portrait','en'),('flow-bridge','landscape','en'),
 ('responsive-story','portrait','en'),('evidence-pair','square','en'),('shared-object','landscape','en'),
 ('mask-window','square','en'),('series-reveal','landscape','en'),('state-comparison','square','en'),
 ('causal-diagram','portrait','es'),('seeded-texture','portrait','en'),('repeater','landscape','en'),
 ('grid-response','square','de'),('browser-stage','landscape','en'),('camera-match','portrait','en'),
 ('hard-cut-hold','square','en'),('system-flow','landscape','en'),('context-label','portrait','en'),
 ('caption-emphasis','portrait','es'),('influence-field','square','en'),('focus-transfer','landscape','en'),
 ('comparison','portrait','de'),('reference-board','square','en'),('path-distribution','landscape','en'),
]
def digest(data:bytes)->str:return hashlib.sha256(data).hexdigest()
def run(args:list[str],limit:int=300)->bytes:
 result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=limit,check=False)
 if result.returncode:raise AssertionError('Native E2E failed: '+str(args[:2])+'\n'+result.stderr.decode(errors='replace')[-8000:])
 return result.stdout
def material(root:Path,number:int)->str:
 image=Image.new('RGB',(512,320),(26+number*13,48,68));draw=ImageDraw.Draw(image)
 draw.rectangle((15,15,497,305),outline=(170,206,229),width=4)
 draw.rectangle((22,24,488,64),fill=(43,78,107))
 draw.text((36,36),'SYNTHETIC FIXTURE '+str(number),fill=(250,250,250))
 draw.rectangle((45+number*10,100,170+number*10,190),fill=(102,176,204))
 draw.rectangle((205,118,415,132),fill=(230,230,230))
 draw.rectangle((205,150,345,161),fill=(150,170,183))
 import io
 stream=io.BytesIO();image.save(stream,format='PNG',compress_level=9);data=stream.getvalue();sha=digest(data)
 target=root/'sha256'/sha[:2]/sha;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
 return sha
def main()->None:
 if os.getenv('GITHUB_ACTIONS')!='true':raise SystemExit('Heavy render tests must run on disposable CI')
 if len(sys.argv)!=2 or sys.argv[1] not in ('0','1','2'):raise SystemExit('usage: creative_visual_e2e.py SHARD(0..2)')
 shard=int(sys.argv[1]);cases=[(i,*row)for i,row in enumerate(CASES)if i%3==shard]
 rev=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
 evidence=ROOT/'verification/hyperframes-creative'/rev/f'shard-{shard}';evidence.mkdir(parents=True,exist_ok=False)
 runtime_digest=digest((RUNTIME/'runtime.json').read_bytes())
 node=Path(shutil.which('node')).resolve();ffmpeg=Path(shutil.which('ffmpeg')).resolve()
 rows=[];contact=[]
 with tempfile.TemporaryDirectory(prefix='mw-creative-visual-') as tmp:
  root=Path(tmp);work=root/'work';out=root/'out';assets=root/'assets'
  for path in [work,out,assets]:path.mkdir()
  first=material(assets,1);second=material(assets,2)
  for index,recipe,aspect,locale in cases:
   raw=run([str(FIXTURE),recipe,aspect,locale,runtime_digest,first,second])
   fixture=json.loads(raw)
   if fixture.get('output')!='native_html':raise AssertionError('Recipe cannot be lowered to another renderer silently')
   body=fixture['plan_json'].encode();html=fixture['source_html'].encode()
   if digest(html)!=fixture['source_sha256']:raise AssertionError('Native HTML source digest changed in transport')
   job='hf-'+format(index+1,'032x');(work/job).mkdir();(out/job).mkdir()
   (work/job/'plan.json').write_bytes(body);(work/job/'index.html').write_bytes(html)
   command=[str(RUNNER),'render','--runtime-root',str(RUNTIME),'--node-sealed',str(node),'--ffmpeg-sealed',str(ffmpeg),
     '--work-root',str(work),'--output-root',str(out),'--assets-root',str(assets),'--job',job,
     '--plan-sha256',digest(body),'--source-sha256',fixture['source_sha256']]
   run(command,240)
   manifest=json.loads((out/job/'frames.json').read_text())
   if manifest['frame_count']!=90 or manifest['source_sha256']!=fixture['source_sha256'] or manifest['observation']['coverage']!='all_frames':
    raise AssertionError('Native frame count/identity/observation coverage differs')
   if manifest['external_requests']!=0 or manifest['creative_approval']!='required':raise AssertionError('Native render made an unapproved source request or claimed approval')
   observed=[json.loads(s)for s in (out/job/'observations.ndjson').read_text().splitlines()]
   if len(observed)!=90 or any(row['frame']!=i or abs(row['time']-row['observed_time'])>1e-7 for i,row in enumerate(observed)):
    raise AssertionError('Native transport quantized or omitted a requested output frame')
   if any(node.get('truncated') for row in observed for node in row['nodes']):
    raise AssertionError(f'{recipe} {aspect} {locale}: native text is truncated')
   frame_hashes=[item['sha256']for item in manifest['frames']]
   if len(set(frame_hashes))<2:raise AssertionError('Animated native source is visually frozen')
   case=evidence/f'{recipe}-{aspect}-{locale}';case.mkdir()
   for frame in (0,15,30,60,89):
    original=out/job/f'frames/frame-{frame:06}.png';image=original.read_bytes()
    if digest(image)!=frame_hashes[frame]:raise AssertionError('Visual sample is not bound to the reported source/frame')
    shutil.copyfile(original,case/f'frame-{frame:06}.png')
   (case/'readback.json').write_text(json.dumps({
    'native_source_sha256':fixture['source_sha256'],'frame_hashes':frame_hashes,
    'frame_count':90,'runtime_receipt_sha256':runtime_digest,'source_classification':fixture['source_classification'],
    'observations_sha256':digest((out/job/'observations.ndjson').read_bytes()),
    'status':'native_technical_readback_only_creative_review_required'
   },indent=2)+'\n')
   (case/'source.html').write_bytes(html)
   selected=case/'frame-000060.png'
   with Image.open(selected)as im:
    if im.size!=((640,360)if aspect=='landscape' else ((360,640)if aspect=='portrait' else (640,640))):
     raise AssertionError('Native output dimensions drifted')
    thumb=im.convert('RGB');thumb.thumbnail((290,190),Image.Resampling.LANCZOS)
    contact.append((f'{recipe} | {aspect} | {locale}',thumb.copy()))
   rows.append({'recipe':recipe,'aspect':aspect,'locale':locale,'source_sha256':fixture['source_sha256'],'frames':90,'animated':True,'native_approval':'required'})
 # Source-bound proof is also a human-review contact sheet; generated thumbnails are never technical PASSes.
 width=3*330;height=((len(contact)+2)//3)*255
 sheet=Image.new('RGB',(width,height),(17,23,31));draw=ImageDraw.Draw(sheet)
 for index,(label,thumb)in enumerate(contact):
  x=(index%3)*330+17;y=(index//3)*255+15;sheet.paste(thumb,(x,y+24));draw.text((x,y+2),label,fill=(238,238,238))
 sheet.save(evidence/'contact-sheet.png')
 (evidence/'result.json').write_text(json.dumps({'schema':'motionwright.native-creative-art-direction-review/1','source_sha':rev,
    'shard':shard,'engine':'HyperFrames Core 0.8.143 / Chromium / direct Native SDK profile',
    'renderer_native_samples':'PASS','sampled_components':len(rows),'all_frames_hashed':True,'text_truncation_check':'PASS',
    'synthetic_media_not_product_claims':True,'human_creative_quality':'NOT_REVIEWED','rows':rows},indent=2)+'\n')
 print(json.dumps({'native_visual_e2e':'PASS','shard':shard,'recipes':len(rows),'source_sha':rev}))
if __name__=='__main__':main()
