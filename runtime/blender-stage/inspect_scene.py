#!/usr/bin/env python3
"""Independent source-reopen probe. Blender-generated geometry is not flattened media."""
from __future__ import annotations
import json,os,sys
from pathlib import Path
import bpy
def check(predicate,message):
 if not predicate:raise ValueError("Blender native reopen check failed: "+message)
def main():
 marker=sys.argv.index('--')if'--'in sys.argv else -1
 args=sys.argv[marker+1:]if marker>=0 else []
 check(len(args)==2,'Expected owner-bound source.blend and review-json destination')
 source=Path(args[0]);dest=Path(args[1])
 check(source.is_absolute()and dest.is_absolute(),'Owner outputs must use absolute files')
 check(source.is_file()and not source.is_symlink()and source.stat().st_size<=256*1024*1024,'Untrusted native source file')
 bpy.ops.wm.open_mainfile(filepath=str(source))
 scene=bpy.context.scene;camera=scene.camera
 check(camera is not None and camera.type=='CAMERA','Native project lost its editable camera')
 devices=[obj for obj in bpy.data.objects if obj.get('motionwright_component_id')]
 check(bool(devices),'Native editable geometry is missing')
 screen=[obj for obj in bpy.data.objects if obj.get('motionwright_screen_sha256')]
 check(bool(screen),'Native editable screen meshes are missing')
 lights=[obj for obj in bpy.data.objects if obj.type=='LIGHT']
 check(len(lights)>=1,'Native lighting cannot be reopened')
 check(scene.render.resolution_percentage==100,'Native source was incorrectly saved at preview-only resolution')
 check(scene.frame_end>scene.frame_start,'Native source lost output timeline')
 action_count=sum(1 for item in bpy.data.actions if item.fcurves)
 check(action_count>=1,'Native camera/lens motion curves were flattened')
 result={
  'schema':'motionwright.blender-native-reopen/1','native_source':str(source.name),
  'blender_version':bpy.app.version_string,'editable_meshes':len(devices),
  'editable_screens':len(screen),'lights':len(lights),'camera':camera.name,
  'camera_lens_mm':camera.data.lens,'camera_dof':camera.data.dof.use_dof,
  'editable_animation_actions':action_count,'source_full_resolution':[scene.render.resolution_x,scene.render.resolution_y],
  'preview_only_reduction':False,'creative_review':'required','result':'PASS'
 }
 dest.write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({'native_blender_reopen':'PASS','editable_meshes':len(devices),'actions':action_count}))
if __name__=='__main__':main()
