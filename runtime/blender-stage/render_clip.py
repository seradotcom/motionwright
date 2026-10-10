#!/usr/bin/env python3
"""Fixed owner-scoped, bounded actual Blender 4.x clip capture from a generated native .blend.
No arbitrary Python, no model-supplied shell or dependency installation.
"""
from __future__ import annotations
import hashlib,json,sys
from pathlib import Path
import bpy
def check(predicate,message):
 if not predicate:raise ValueError("Native Blender clip profile refused: "+message)
def sha(path:Path)->str:
 digest=hashlib.sha256()
 with path.open('rb')as stream:
  for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
 return digest.hexdigest()
def main()->None:
 marker=sys.argv.index('--')if'--'in sys.argv else -1
 args=sys.argv[marker+1:]if marker>=0 else []
 check(len(args)==4,'Expected previously verified .blend, source manifest, fresh output, expected source SHA')
 source=Path(args[0]);manifest_path=Path(args[1]);target=Path(args[2]);expected=args[3]
 check(len(expected)==64 and all(c in'0123456789abcdef' for c in expected),
       'Native clip source digest must be a full lowercase SHA-256')
 check(source.is_absolute()and manifest_path.is_absolute()and target.is_absolute(),
       'Clip roots must use absolute owner-selected paths')
 check(source.is_file()and not source.is_symlink()and source.stat().st_size<=256*1024*1024,
       'Editable source must be a bounded regular owner-generated Blender file')
 check(manifest_path.is_file()and not manifest_path.is_symlink()and manifest_path.stat().st_size<256*1024,
       'Editable source manifest must be a bounded regular file')
 check(target.is_dir()and not target.is_symlink()and not list(target.iterdir()),
       'Clip output must be a fresh empty writable directory')
 recorded=json.loads(manifest_path.read_text())
 check(recorded['schema']=='motionwright.blender-native-stage-preview/1'
       and recorded['original_source']['sha256']==sha(source)
       and recorded['source_sha256']==expected
       and recorded['creative_approval']=='required',
       'Source and native camera/geometry manifest do not match this exact file')
 bpy.context.preferences.filepaths.use_scripts_auto_execute=False
 bpy.ops.wm.open_mainfile(filepath=str(source),load_ui=False)
 scene=bpy.context.scene
 check(scene.camera is not None and scene.frame_end>=scene.frame_start and scene.frame_end-scene.frame_start<=180,
       'Clip must have a real camera and bounded frame range')
 check(scene.frame_start==1 and scene.frame_end==90,
       'This full-clip acceptance profile must cover the exact 90-frame fixture')
 check(scene.render.fps==recorded['rate']['num'] and round(scene.render.fps_base,9)==round(recorded['rate']['den'],9),
       'Blender timeline clock no longer matches the admitted source')
 check(scene.render.resolution_x==recorded['width'] and scene.render.resolution_y==recorded['height'],
       'Blender original source dimensions changed since approval')
 engine_ids={item.identifier for item in scene.render.bl_rna.properties['engine'].enum_items}
 preview='BLENDER_EEVEE'if'BLENDER_EEVEE'in engine_ids else'BLENDER_EEVEE_NEXT'
 check(preview in engine_ids,'Native Eevee CPU renderer is unavailable')
 scene.render.engine=preview
 if hasattr(scene,'eevee')and hasattr(scene.eevee,'taa_render_samples'):
  scene.eevee.taa_render_samples=16
 scene.render.resolution_percentage=50
 scene.render.image_settings.file_format='PNG'
 scene.render.image_settings.color_mode='RGBA'
 output=target/'frames';output.mkdir()
 native=[]
 for frame in range(1,91):
  scene.frame_set(frame)
  index=frame-1
  scene.render.filepath=str(output/f'frame-{index:06}.png')
  bpy.ops.render.render(write_still=True)
  image=output/f'frame-{index:06}.png'
  check(image.is_file()and image.stat().st_size>64,'Native framebuffer was not produced')
  native.append({'index':index,'sha256':sha(image),'bytes':image.stat().st_size,
     'relative_path':str(image.relative_to(target))})
 proof={
  'schema':'motionwright.blender-real-clip-acceptance/1',
  'native_source_sha256':sha(source),'typed_source_sha256':expected,
  'blender_version':bpy.app.version_string,'engine':preview,
  'original_resolution':[recorded['width'],recorded['height']],
  'preview_resolution':[scene.render.resolution_x//2,scene.render.resolution_y//2],
  'rate':recorded['rate'],'frames':native,'frame_count':len(native),
  'coverage':'all_frames_native_previews_no_merged_audio',
  'creative_approval':'required','video_master_status':'not_mastered',
 }
 (target/'native-clip.json').write_text(json.dumps(proof,indent=2)+'\n')
 print(json.dumps({'real_blender_clip':'PASS','frames':len(native),'source_sha256':expected}))
if __name__=='__main__':main()
