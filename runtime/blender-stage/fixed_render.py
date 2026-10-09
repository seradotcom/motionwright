#!/usr/bin/env python3
"""Pinned first-party Blender stage realization; no model-provided Python or shell."""
from __future__ import annotations
import hashlib,json,math,os,sys
from pathlib import Path
import bpy
from mathutils import Vector
MAX_SOURCE=2*1024*1024
def check(yes,message):
    if not yes:raise ValueError("Native Blender stage admission failed: "+message)
def digest(data):return hashlib.sha256(data).hexdigest()
def rgb(value):
    check(isinstance(value,str) and len(value)in(7,9) and value.startswith('#')
        and all(ch in'0123456789abcdefABCDEF' for ch in value[1:]),"Material color requires an inert hex literal")
    vals=[int(value[n:n+2],16)/255 for n in(1,3,5)]
    return tuple(vals)+((int(value[7:9],16)/255,) if len(value)==9 else(1.0,))
def vec(p):
    check(isinstance(p,dict) and set(p)=={'x','y','z'},"Vector requires x,y,z")
    vals=[float(p[k]) for k in('x','y','z')]
    check(all(math.isfinite(n) and abs(n)<=1000 for n in vals),"Vector contains unbounded coordinates")
    return Vector(vals)
def mat(name,color,metal=0,rough=.5,emission=False):
    m=bpy.data.materials.new(name=name);m.use_nodes=True
    node=m.node_tree.nodes.get('Principled BSDF')
    check(node is not None,"Principled shader unavailable")
    node.inputs['Base Color'].default_value=rgb(color)
    node.inputs['Metallic'].default_value=float(metal)
    node.inputs['Roughness'].default_value=float(rough)
    if emission:
        node.inputs['Emission Color'].default_value=rgb(color)
        node.inputs['Emission Strength'].default_value=1.5
    return m
def box(name,center,dims,surface,bevel=0,parent=None):
    bpy.ops.mesh.primitive_cube_add(size=1,location=center)
    obj=bpy.context.object;obj.name=name;obj.dimensions=dims
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
    if parent is not None:obj.parent=parent
    obj.data.materials.append(surface)
    if bevel:
        b=obj.modifiers.new('Physical rounded edge','BEVEL')
        b.width=bevel;b.segments=4
        n=obj.modifiers.new('Light-weighted normals','WEIGHTED_NORMAL');n.keep_sharp=True
    return obj
def source_image(root,declared,id_):
    if id_ is None:return None
    asset=declared.get(id_)
    check(asset is not None and asset['kind'] in('png','jpeg')
          and asset['rights']['use_authorized'] is True,'Screen media is not an authorized raster source')
    sha=asset['sha256'];check(len(sha)==64 and all(ch in'0123456789abcdef'for ch in sha),'Screen digest invalid')
    path=root/'sha256'/sha[:2]/sha
    check(path.is_file() and not path.is_symlink()and path.stat().st_size<=64*1024*1024,
          'Native source image does not exist as a bounded regular file')
    check(digest(path.read_bytes())==sha,'Original source image digest changed')
    image=bpy.data.images.load(str(path),check_existing=False)
    image.name='Owner-supplied original sha256 '+sha[:12]
    image.pack()
    return image
def product(item,assets,asset_root):
    kind=item['model'];name=item['name'];id_=item['id']
    dims={'phone':(.90,.13,1.90),'tablet':(1.80,.12,1.30),'laptop':(2.48,.13,1.53)}
    check(kind in dims and isinstance(name,str)and 0<len(name)<=120,'Unknown generic product geometry')
    w,d,h=dims[kind];scale=float(item['scale'])
    group=bpy.data.objects.new('Native original '+name,None);bpy.context.collection.objects.link(group)
    group.location=vec(item['position']);group.location.z+=h*scale/2
    group.rotation_euler=tuple(math.radians(a)for a in vec(item['rotation_deg']))
    group.scale=(scale,scale,scale)
    alloy=mat('Alloy '+id_,item['body_color'],item['metallic'],item['roughness'])
    shell=box('Rounded generic '+kind,(0,0,0),(w,d,h),alloy,.045,group)
    shell['motionwright_component_id']=id_;shell['original_geometry_no_commercial_brand']=True
    display=mat('Generic display '+id_,item['screen_color'],0,.25,True)
    image=source_image(asset_root,assets,item.get('screen_asset_id'))
    if image is not None:
        shader=display.node_tree.nodes.new('ShaderNodeTexImage');shader.image=image
        p=display.node_tree.nodes.get('Principled BSDF')
        display.node_tree.links.new(shader.outputs['Color'],p.inputs['Base Color'])
        display.node_tree.links.new(shader.outputs['Color'],p.inputs['Emission Color'])
    screen=box('Editable source screen '+id_,(0,-d/2-.008,0),(w*.88,.01,h*.80),display,.01,group)
    screen['motionwright_screen_sha256']=item['screen_asset_id']or 'synthetic_unbranded_material_only'
    if kind=='laptop':
        box('Generic laptop keyboard base',(0,-.65,-h/2+.02),(w,1.30,.10),alloy,.025,group)
    return {'id':id_,'model':kind,'screen_asset_id':item.get('screen_asset_id')}
def lens_aware_camera(knots):
    data=bpy.data.cameras.new('Editable physical camera and depth of field')
    camera=bpy.data.objects.new('Motionwright native camera',data);bpy.context.collection.objects.link(camera)
    bpy.context.scene.camera=camera
    data.dof.use_dof=True
    for knot in knots:
        frame=int(knot['frame'])+1;position=vec(knot['position']);target=vec(knot['target'])
        direction=target-position;check(direction.length>.01,'Camera target equals its position')
        camera.location=position
        camera.rotation_euler=direction.to_track_quat('-Z','Y').to_euler()
        data.lens=float(knot['focal_length_mm'])
        data.dof.focus_distance=float(knot['focus_distance'])
        data.dof.aperture_fstop=float(knot['f_stop'])
        check(knot['curve']['kind']in('hold','linear','ease_out_cubic','ease_in_out','cubic_bezier'),
              'Camera has an unadmitted interpolation')
        camera.keyframe_insert(data_path='location',frame=frame)
        camera.keyframe_insert(data_path='rotation_euler',frame=frame)
        data.keyframe_insert(data_path='lens',frame=frame)
        data.dof.keyframe_insert(data_path='focus_distance',frame=frame)
        data.dof.keyframe_insert(data_path='aperture_fstop',frame=frame)
    return camera
def lighting(items):
    for item in items:
        source=bpy.data.lights.new(item['name'],'AREA')
        source.energy=float(item['power_watts'])
        source.shape='DISK';source.size=float(item['size_m'])
        source.color=rgb(item['color'])[:3]
        light=bpy.data.objects.new(item['name'],source);bpy.context.collection.objects.link(light)
        light.location=vec(item['position'])
        light.rotation_euler=(vec(item['target'])-light.location).to_track_quat('-Z','Y').to_euler()
def scene_floor(source):
    bpy.ops.mesh.primitive_plane_add(size=200,location=(0,0,-.13))
    floor=bpy.context.object;floor.name='Original neutral stage floor'
    floor.data.materials.append(mat('Original floor',source['floor_color'],0,.75))
    world=bpy.data.worlds.new('Neutral world');world.use_nodes=True
    node=world.node_tree.nodes.get('Background')
    node.inputs['Color'].default_value=rgb(source['world_color'])
    node.inputs['Strength'].default_value=.30
    bpy.context.scene.world=world
def entry(path):
    bytes_=path.read_bytes()
    return {'relative_path':path.name,'sha256':digest(bytes_),'bytes':len(bytes_)}
def main():
    marker=sys.argv.index('--')if'--'in sys.argv else -1
    args=sys.argv[marker+1:]if marker>=0 else []
    check(len(args)==4,'Fixed runner requires plan, output root, asset root and exact preview frame list')
    source=Path(args[0]);out=Path(args[1]);assets=Path(args[2])
    check(all(p.is_absolute()for p in(source,out,assets)),'Stage paths must be absolute Host-controlled roots')
    check(source.is_file()and not source.is_symlink()and source.stat().st_size<=MAX_SOURCE,'Plan file invalid')
    check(out.is_dir()and not out.is_symlink()and assets.is_dir()and not assets.is_symlink(),'Stage output/assets root invalid')
    bytes_=source.read_bytes();plan=json.loads(bytes_)
    check(plan['version']==1 and plan['source_classification'].startswith('original_generic_geometry'),'Unsupported native source version/provenance')
    canvas=plan['output'];width=int(canvas['width']);height=int(canvas['height'])
    frames=int(canvas['frames']);rate=canvas['rate']
    check(320<=width<=4096 and 240<=height<=4096 and width*height<=8294400 and 2<=frames<=3600,
          'Native stage canvas outside bounded profile')
    check(1<=len(plan['devices'])<=12 and 2<=len(plan['cameras'])<=64 and 1<=len(plan['lights'])<=8,
          'Stage complexity exceeds bounds')
    check(plan['units']=='metres_degrees_millimetres'and plan['color_management']=='standard_scene_linear_to_srgb_rgba',
          'Stage physical/color convention differs')
    indices=[int(v)for v in args[3].split(',')]
    check(1<=len(indices)<=16 and indices==sorted(set(indices))and all(0<=v<frames for v in indices),
          'Stage preview requires at most 16 unique sorted in-range frames')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene_floor(plan)
    declared={asset['id']:asset for asset in plan['assets']}
    built=[product(item,declared,assets)for item in plan['devices']]
    camera=lens_aware_camera(plan['cameras'])
    lighting(plan['lights'])
    scene=bpy.context.scene;scene.frame_start=1;scene.frame_end=frames
    scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=min(int(plan['samples']),12)
    scene.render.resolution_x=width;scene.render.resolution_y=height;scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGBA'
    scene.render.film_transparent=canvas['background']is None
    scene.render.fps=int(rate['num']);scene.render.fps_base=float(rate['den'])
    scene.view_settings.view_transform='Standard'
    (out/'frames').mkdir(exist_ok=False)
    scene.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=str(out/'stage.blend'),compress=True,relative_remap=False)
    bpy.ops.export_scene.gltf(filepath=str(out/'stage.glb'),export_format='GLB',
                              export_cameras=True,export_lights=True,export_animations=True)
    # Preview samples use half resolution. The persisted .blend and exported GLB
    # remain full native resolution and editable; no output-time crop/retime.
    scene.render.resolution_percentage=50
    observations=[];captures=[]
    for frame in indices:
        scene.frame_set(frame+1)
        scene.render.filepath=str(out/'frames'/f'frame-{frame:06}.png')
        bpy.ops.render.render(write_still=True)
        file=out/'frames'/f'frame-{frame:06}.png'
        captures.append({'frame':frame,'relative_path':str(file.relative_to(out)),'sha256':digest(file.read_bytes()),'bytes':file.stat().st_size})
        observations.append({'frame':frame,'rational_time':{'num':str(frame*rate['den']),'den':str(rate['num'])},
            'camera_location':[round(float(v),7)for v in camera.location],
            'camera_rotation':[round(float(v),7)for v in camera.rotation_euler],
            'focal_length_mm':round(float(camera.data.lens),4),
            'focus_distance':round(float(camera.data.dof.focus_distance),4)})
    doc={'schema':'motionwright.blender-native-stage-preview/1','source_sha256':digest(bytes_),
        'instance_id':plan['instance_id'],'recipe':plan['recipe'],'blender_version':bpy.app.version_string,
        'width':width,'height':height,'rate':rate,'native_total_frames':frames,'sampling':'sampled_only_not_full_animation',
        'sample_resolution_percent':50,'sampled_count':len(indices),'native_camera_keys':len(plan['cameras']),
        'editable_devices':built,'original_source':entry(out/'stage.blend'),
        'interchange':entry(out/'stage.glb'),'frames':captures,'camera_observations':observations,
        'creative_approval':'required','native_editable':True,'source_classification':plan['source_classification']}
    (out/'stage.json').write_text(json.dumps(doc,indent=2)+'\n')
    print(json.dumps({'native_stage':'PASS','source_sha256':doc['source_sha256'],'samples':len(indices),
                      'blender_version':bpy.app.version_string}))
if __name__=='__main__':main()
