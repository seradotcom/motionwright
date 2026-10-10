#!/usr/bin/env python3
"""Native Blender 4.x source/editability/sampled-frame acceptance on disposable CI."""
from __future__ import annotations
import hashlib,json,os,shutil,subprocess,tempfile
from pathlib import Path
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[2]
FIXTURE=ROOT/'target/debug/examples/native_catalog_fixture'
BUILDER=ROOT/'runtime/blender-stage/fixed_render.py'
INSPECT=ROOT/'runtime/blender-stage/inspect_scene.py'
FULL=ROOT/'runtime/blender-stage/render_clip.py'
RECIPES=('device-stage','arc-reveal','dolly-focus','detail-return','group-reframe')
def digest(path:Path)->str:
 with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def command(args:list[str],log:Path,timeout:int=360)->bytes:
 observed=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout,check=False)
 log.write_bytes(observed.stdout)
 if observed.returncode or b'Traceback (most recent call last)' in observed.stdout or b'RuntimeError:' in observed.stdout:
  raise AssertionError('Native Blender stage process failed: '+str(args[:2])+'\n'+observed.stdout.decode(errors='replace')[-5500:])
 return observed.stdout
def main()->None:
 if os.getenv('GITHUB_ACTIONS')!='true':raise SystemExit('Blender production acceptance must run on a disposable CI worker')
 version=subprocess.check_output(['blender','--version'],text=True).splitlines()[0]
 if not version.startswith('Blender 4.'):raise AssertionError('Blender 4.x is required to validate this original native stage')
 output=ROOT/'verification/blender-native-stage';output.mkdir(parents=True,exist_ok=False)
 summaries=[];thumbnails=[]
 with tempfile.TemporaryDirectory(prefix='motionwright-blender-native-')as scratch:
  root=Path(scratch);assets=root/'assets';assets.mkdir()
  for name in RECIPES:
   specimen=json.loads(subprocess.check_output([str(FIXTURE),name,'landscape','en','0'*64,'none','none']))
   if specimen['output']['backend']!='blender_stage':raise AssertionError('Native Blender stage recipe was silently projected into another renderer')
   scene=specimen['output']['source']
   scene_bytes=json.dumps(scene,sort_keys=True,separators=(',',':')).encode()
   source=root/(name+'.json');source.write_bytes(scene_bytes)
   case=output/name;case.mkdir()
   command(['blender','-b','--factory-startup','-noaudio','--python',str(BUILDER),'--',str(source),str(case),str(assets),'0,30,89'],
           case/'build.log',360)
   manifest=json.loads((case/'stage.json').read_text())
   if manifest['schema']!='motionwright.blender-native-stage-preview/1' or manifest['source_sha256']!=hashlib.sha256(scene_bytes).hexdigest():
    raise AssertionError('Blender stage output is not bound to the exact typed input')
   if manifest['preview_render_engine'] not in ('BLENDER_EEVEE','BLENDER_EEVEE_NEXT'):
    raise AssertionError('Blender preview engine was not stated or was not Eevee')
   if manifest['sampling']!='sampled_only_not_full_animation' or manifest['sampled_count']!=3:
    raise AssertionError('Blender sample-only render is falsely reported as a completed native master')
   if manifest['native_camera_keys']<2 or not manifest['editable_devices']:
    raise AssertionError('Native Blender camera or editable source geometry was lost')
   if digest(case/'stage.blend')!=manifest['original_source']['sha256'] or digest(case/'stage.glb')!=manifest['interchange']['sha256']:
    raise AssertionError('Editable Blender source or interchange was not digest bound')
   if (case/'stage.glb').read_bytes()[:4]!=b'glTF':raise AssertionError('Stage interchange is not valid GLB')
   for frame in manifest['frames']:
    target=case/frame['relative_path']
    if digest(target)!=frame['sha256']:raise AssertionError('Sample frame changed after capture')
    with Image.open(target) as im:
     if im.size!=(320,180):raise AssertionError('Stage preview frame dimensions are inconsistent with half-size output')
   if len({entry['sha256']for entry in manifest['frames']})<2:raise AssertionError('Native Blender camera movement is visually frozen')
   if manifest['camera_observations'][0]['camera_location']==manifest['camera_observations'][-1]['camera_location']:
    raise AssertionError('Native camera path is flattened or motionless')
   reopen=case/'source-reopen.json'
   command(['blender','-b','--factory-startup','-noaudio','--python',str(INSPECT),'--',str(case/'stage.blend'),str(reopen)],
           case/'inspect.log',90)
   reopened=json.loads(reopen.read_text())
   if reopened['result']!='PASS' or reopened['editable_animation_actions']<1:
    raise AssertionError('Native .blend could not be reopened with editable camera/geometry')
   with Image.open(case/'frames/frame-000030.png')as image:thumbnails.append((name,image.convert('RGB').copy()))
   full_clip=None
   if name=='arc-reveal':
    # One complete native source-bound 90-frame 3D clip for temporal review.
    # Other recipes retain sampled-only coverage until independently admitted.
    clip=case/'actual-native-full-clip';clip.mkdir()
    command(['blender','-b','--factory-startup','--disable-autoexec','-noaudio','--python',str(FULL),
             '--',str(case/'stage.blend'),str(case/'stage.json'),str(clip),manifest['source_sha256']],
            case/'full-clip.log',1200)
    all_frames=json.loads((clip/'native-clip.json').read_text())
    if (all_frames['frame_count']!=90 or all_frames['typed_source_sha256']!=manifest['source_sha256'] or
       all_frames['coverage']!='all_frames_native_previews_no_merged_audio'):
      raise AssertionError('Full native 3D clip is not exact or silently claims audio mastering')
    source_hashes=[item['sha256'] for item in all_frames['frames']]
    if len(source_hashes)!=90 or len(set(source_hashes))<10:
      raise AssertionError('Full Blender arc did not produce 90 distinct valid source-bound frames')
    mp4=case/'native-arc-preview.mp4'
    command(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-framerate','30',
             '-i',str(clip/'frames/frame-%06d.png'),'-an','-c:v','libx264',
             '-preset','veryfast','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart',str(mp4)],
            case/'preview-encode.log',120)
    check=json.loads(command(['ffprobe','-v','error','-show_entries','stream=codec_name,width,height,r_frame_rate,nb_frames',
        '-of','json',str(mp4)],case/'preview-probe.log',40))
    stream=check['streams'][0]
    if (stream['codec_name']!='h264' or stream['width']!=320 or stream['height']!=180 or
       stream['r_frame_rate']!='30/1' or int(stream['nb_frames'])!=90):
      raise AssertionError('Native 3D H.264 review copy has lost exact frame count or frame clock')
    full_clip={'status':'native_90_frame_review_copy','frames':90,
               'editable_source_sha256':manifest['original_source']['sha256'],
               'mp4_sha256':digest(mp4),'compressed_review_copy_is_not_master':True}
   summaries.append({'recipe':name,'source_sha256':manifest['source_sha256'],'blender_version':manifest['blender_version'],
      'actual_blender_source_sha256':manifest['original_source']['sha256'],
      'actual_glb_sha256':manifest['interchange']['sha256'],'sampled_frames':[frame['frame']for frame in manifest['frames']],
      'native_editable':'PASS','sampled_native_pixels':'PASS','full_clip_render':full_clip if full_clip else 'NOT_RUN','creative_review':'required'})
 sheet=Image.new('RGB',(370*len(thumbnails),245),(16,23,29));draw=ImageDraw.Draw(sheet)
 for i,(name,thumb) in enumerate(thumbnails):
  sheet.paste(thumb,(i*370+20,35));draw.text((i*370+20,13),name,fill=(234,238,241))
 sheet.save(output/'stage-review-strip.png')
 receipt={'schema':'motionwright.blender-stage-direct-native-e2e/1',
          'motionwright_sha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
          'native_blender_version':version,'native_editable_stages':'PASS','coverage':'five_sampled_stages_plus_one_90_frame_review_clip',
          'human_art_direction_review':'required','camera_motion_truth':'verified_through_native_blend_and_pixels',
          'rows':summaries}
 (output/'result.json').write_text(json.dumps(receipt,indent=2)+'\n')
 print(json.dumps({'blender_stage_native':'PASS','cases':len(summaries),'evidence':str(output)}))
if __name__=='__main__':main()
