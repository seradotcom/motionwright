"""Bind the same observer-only derivation to both retained FixedTxt sources.

All functions operate on bounded bytes or already parsed plans. They grant no
permission, run no native tool, and make no physical or numerical verdict.
"""
from __future__ import annotations

import hashlib
import json

HISTORICAL = {
    "RED": "dfe6c0fc226d7e9017eb718c7e745d942c4067a54f968d4483b80bbdd51b4fa8",
    "GREEN": "a400b38608e169a20e7ade099aa8632c11629152951b9e62a0fb66858b530f36",
}
SDK = "acc0a089e2f0e3e9936632fb2a4aa0c2fab56199"
MAX_SOURCE_BYTES = 128 * 1024

RED_ATTACHMENT = """   if(parent instanceof Camera){let scene=parent.scene();if(!scene){scene=new Node({});parent.scene(scene);}scene.add(n);}else parent.add(n);
"""
GREEN_ATTACHMENT = """   // Fixed Txt keeps its DOM-enabled inline runs. A non-layout parent makes
   // its own position signal authoritative while preserving the logical Txt.
   // The attachment follows the later layer assignment and any zIndex change.
   const attachment=s.layout.kind==='fixed'&&n instanceof Txt
    ?new Layout({layout:false,position:[0,0],rotation:0,scale:[1,1],
      opacity:1,clip:false,zIndex:()=>n.zIndex(),children:[n]}):n;
   if(parent instanceof Camera){let scene=parent.scene();if(!scene){scene=new Node({});parent.scene(scene);}scene.add(attachment);}else parent.add(attachment);
"""

DRAW_PREIMAGE = "type DrawRecord={canvas:HTMLCanvasElement;matrix:DOMMatrix;bounds:{x:number;y:number;width:number;height:number};opacity:number;serial:number;clip:boolean};"
DRAW_POSTIMAGE = """// This extension records native layout separately from drawable/cache bounds.
// The public 3.17.2 Layout size signal reads computed DOM layout; it is not the
// authored desiredSize and may differ from the intended size in the RED arm.
type NativeNodeType='Node'|'Layout'|'Rect'|'Circle'|'Line'|'Txt'|'Code'|'Img'|'Video'|'Camera';
type LayoutDraw={root:boolean;box:{x:number;y:number;width:number;height:number}};
type DrawRecord={canvas:HTMLCanvasElement;matrix:DOMMatrix;bounds:{x:number;y:number;width:number;height:number};opacity:number;serial:number;clip:boolean;layout?:LayoutDraw};
function nativeNodeType(n:Node):NativeNodeType{
 if(n instanceof Txt)return 'Txt';if(n instanceof Code)return 'Code';
 if(n instanceof Video)return 'Video';if(n instanceof Img)return 'Img';
 if(n instanceof Circle)return 'Circle';if(n instanceof Line)return 'Line';
 if(n instanceof Rect)return 'Rect';if(n instanceof Camera)return 'Camera';
 if(n instanceof Layout)return 'Layout';return 'Node';
}
function readNativeLayout(n:Node):LayoutDraw|undefined{
 if(!(n instanceof Layout))return undefined;
 // Public getters run the native layout resolution used by Layout.drawOverlay.
 // No position/size/layout signal is assigned by this observation.
 const size=n.size();const width=finite(size.width,'observed layout width');
 const height=finite(size.height,'observed layout height');
 requireValue(width>=0&&height>=0&&width<=1e6&&height<=1e6,'observed layout size bounds');
 const root=n.isLayoutRoot();requireValue(typeof root==='boolean','observed layout root');
 return {root,box:{x:-width/2,y:-height/2,width,height}};
}
function nativeLayoutObservation(n:Node,draw:DrawRecord|undefined,coordinateKnown:boolean){
 const native_type=known(nativeNodeType(n));
 const layout_root:Known<boolean>=draw?.layout?known(draw.layout.root):unknown('native layout root unavailable after completed draw');
 let screen_layout_box:Known<LayoutDraw['box']>=unknown('native layout box or output transform unavailable');
 if(coordinateKnown&&draw?.layout){
  try{
   // The captured parent context includes the actual camera/ancestor transforms.
   // Stage 3.17.2 copies the admitted scene buffer at (0,0), without rescaling.
   const box=transformed(draw.layout.box,draw.matrix);
   requireValue(Object.values(box).every(Number.isFinite)&&Math.abs(box.x)<=32768&&Math.abs(box.y)<=32768&&box.width>=0&&box.height>=0&&box.width<=1e6&&box.height<=1e6,'observed screen layout bounds');
   screen_layout_box=known(box);
  }catch{/* unavailable native geometry remains UNKNOWN */}
 }
 return {native_type,layout_root,screen_layout_box};
}"""

CAPTURE_PREIMAGE = "    reg.draws.set(id,{canvas:context.canvas,matrix,bounds:{x:box.x,y:box.y,width:box.width,height:box.height},opacity:n.absoluteOpacity(),serial:reg.drawSerial++,clip:clipAncestor(n)});"
CAPTURE_POSTIMAGE = """    const draw:DrawRecord={canvas:context.canvas,matrix,bounds:{x:box.x,y:box.y,width:box.width,height:box.height},opacity:n.absoluteOpacity(),serial:reg.drawSerial++,clip:clipAncestor(n)};
    reg.draws.set(id,draw);
    try{const layout=readNativeLayout(n);if(layout)draw.layout=layout;}catch{/* keep the independent drawable bounds; layout remains UNKNOWN */}"""

SERIALIZE_PREIMAGE = "  output.push({id:subject.id,local_size:"
SERIALIZE_POSTIMAGE = "  output.push({id:subject.id,...nativeLayoutObservation(n,draw,coordinateKnown),local_size:"

EDITS = (
    ("native_layout_types_and_readers", DRAW_PREIMAGE, DRAW_POSTIMAGE),
    ("completed_native_draw_capture", CAPTURE_PREIMAGE, CAPTURE_POSTIMAGE),
    ("optional_typed_observation_fields", SERIALIZE_PREIMAGE, SERIALIZE_POSTIMAGE),
)


# Cumulative, reversible observation delta; original attachment bytes are retained.
GEOMETRY_EDITS = (
    ('native_geometry_observation_1', r"""  case 'local_region':{const target=node(op.target);const overlay=node(op.overlay);requireValue(overlay instanceof Layout,'region requires Layout');overlay.absolutePosition(()=>vec(op.center).transformAsPoint(target.localToWorld()));overlay.size([op.size.width,op.size.height]);break;}
 }
}
const sceneRegistrations=new Map<string,{data:NativeSceneData;nodes:Map<string,Node>;draws:Map<string,DrawRecord>;rendered:Set<string>;canvas:HTMLCanvasElement|null;drawSerial:number;finished:Set<string>;captionActive:Map<string,boolean>}>();
// This extension records native layout separately from drawable/cache bounds.
// The public 3.17.2 Layout size signal reads computed DOM layout; it is not the
// authored desiredSize and may differ from the intended size in the RED arm.
""", r"""  case 'local_region':{const target=node(op.target);const overlay=node(op.overlay);requireValue(overlay instanceof Layout,'region requires Layout');overlay.absolutePosition(()=>vec(op.center).transformAsPoint(target.localToWorld()));overlay.size([op.size.width,op.size.height]);break;}
 }
}
const sceneRegistrations=new Map<string,ReturnType<typeof registration>>();
// This extension records native layout separately from drawable/cache bounds.
// The public 3.17.2 Layout size signal reads computed DOM layout; it is not the
// authored desiredSize and may differ from the intended size in the RED arm.
"""),
    ('native_geometry_observation_2', r""" const root=n.isLayoutRoot();requireValue(typeof root==='boolean','observed layout root');
 return {root,box:{x:-width/2,y:-height/2,width,height}};
}
function nativeLayoutObservation(n:Node,draw:DrawRecord|undefined,coordinateKnown:boolean){
 const native_type=known(nativeNodeType(n));
 const layout_root:Known<boolean>=draw?.layout?known(draw.layout.root):unknown('native layout root unavailable after completed draw');
""", r""" const root=n.isLayoutRoot();requireValue(typeof root==='boolean','observed layout root');
 return {root,box:{x:-width/2,y:-height/2,width,height}};
}
// Native draw observations. These records do not assign authored signals.
type Matrix6=[number,number,number,number,number,number];
type ScreenQuad=[[number,number],[number,number],[number,number],[number,number]];
type NativeLineageNode={native_key:string;subject_id:string|null;native_type:NativeNodeType;
 parent_key:string|null;local_to_parent:Matrix6;opacity:number;layout_enabled:Known<boolean>;clip:Known<boolean>};
type NativeRectClip={native_key:string;local_box:LayoutDraw['box'];matrix:Matrix6;screen_polygon:ScreenQuad};
type NativeOwnClip={kind:'none'}|{kind:'rect';clip:NativeRectClip};
type NativeGeometry={native_key:string;screen_layout_polygon:Known<ScreenQuad>;
 lineage:Known<NativeLineageNode[]>;own_clip:Known<NativeOwnClip>};
type NativeCameraIdentity={native_key:string;subject_id:string|null};
type NativeCameraScene={native_key:string;original_scene_key:string;current_scene_key:string;
 current_scene_is_original:boolean;draw_scene_key:string;draw_uses_original_scene:boolean;
 scene_transform:Known<Matrix6>;position:[number,number];rotation:number;zoom:number;
 cameras:Known<NativeCameraIdentity[]>};
type NativeClipRecord={canvas:HTMLCanvasElement;value:Known<NativeOwnClip>};
type NativeSceneDraw={scene:Node;canvas:HTMLCanvasElement;matrix:Matrix6;completed:boolean};
type NativeMethod=(this:unknown,...args:unknown[])=>unknown;

function nativeKey(n:Node):string{
 const key=n.key;requireValue(typeof key==='string'&&key.length>0&&key.length<=512&&/^[\x20-\x7e]+$/.test(key),'native node key unavailable');return key;
}
function nativeMatrix(m:DOMMatrix):Matrix6{
 const a:Matrix6=[m.a,m.b,m.c,m.d,m.e,m.f];
 requireValue(m.is2D&&a.every(v=>Number.isFinite(v)&&Math.abs(v)<=1e6),'native matrix unavailable');return a;
}
function nativeQuad(box:LayoutDraw['box'],m:DOMMatrix):ScreenQuad{
 // Preserve ordered native corners before any axis-aligned reduction.
 const points=[[box.x,box.y],[box.x+box.width,box.y],[box.x+box.width,box.y+box.height],[box.x,box.y+box.height]];
 const result=points.map(([x,y])=>{const p=m.transformPoint({x,y});return[p.x,p.y];});
 requireValue(result.every(p=>p.every(v=>Number.isFinite(v)&&Math.abs(v)<=2e6)),'native polygon unavailable');
 return result as ScreenQuad;
}
function withNativeMethods<T>(target:object,names:string[],wrap:(name:string,original:NativeMethod)=>NativeMethod,run:()=>T):T{
 const saved:{name:string;descriptor:PropertyDescriptor|undefined}[]=[];
 const restore=()=>{for(const {name,descriptor}of saved.reverse()){
  if(descriptor)Object.defineProperty(target,name,descriptor);
  else requireValue(Reflect.deleteProperty(target,name),'native observer method restoration failed');
 }};
 try{
  for(const name of names){
   const original=Reflect.get(target,name);requireValue(typeof original==='function','native observation method unavailable');
   const descriptor=Object.getOwnPropertyDescriptor(target,name);
   Object.defineProperty(target,name,{configurable:true,writable:true,enumerable:descriptor?.enumerable??false,value:wrap(name,original as NativeMethod)});
   saved.push({name,descriptor});
  }
 }catch{restore();return run();}
 try{return run();}finally{restore();}
}
function observeRectClip<T>(n:Layout,context:CanvasRenderingContext2D,reg:ReturnType<typeof registration>,run:()=>T):T{
 const record:NativeClipRecord={canvas:context.canvas,value:unknown('native rectangular clip call unavailable')};reg.nativeClips.set(n,record);
 let open=false,closed=false,invalid=false,done=false;
 let rectangle:{box:LayoutDraw['box'];matrix:Matrix6}|null=null;
 return withNativeMethods(context,['beginPath','rect','closePath','clip'],(method,original)=>function(...args){
  const own=this===context&&reg.renderStack[reg.renderStack.length-1]===n&&!done;
  let matrix:Matrix6|undefined;
  if(own&&(method==='rect'||method==='clip')){try{matrix=nativeMatrix(context.getTransform());}catch{invalid=true;}}
  const value=original.apply(this,args); // exactly the real Canvas call
  if(own){try{
   if(method==='beginPath'){open=true;closed=false;invalid=false;rectangle=null;}
   else if(method==='rect'){
    if(!open||rectangle!==null||args.length!==4||!args.every(v=>typeof v==='number'&&Number.isFinite(v)&&Math.abs(v)<=1e6)||!matrix){invalid=true;}
    else{
     const [x,y,width,height]=args as number[];
     if(width<=0||height<=0||Math.abs(x)>32768||Math.abs(y)>32768)invalid=true;
     rectangle={box:{x,y,width,height},matrix};
    }
   }else if(method==='closePath'){closed=true;}
   else if(method==='clip'){
    done=true;
    if(!invalid&&open&&closed&&rectangle&&matrix&&args.length===0&&matrix.every((v,i)=>v===rectangle!.matrix[i])){
     record.value=known({kind:'rect',clip:{native_key:nativeKey(n),local_box:rectangle.box,matrix,
      screen_polygon:nativeQuad(rectangle.box,new DOMMatrix(matrix))}});
    }
   }
  }catch{invalid=true;record.value=unknown('native rectangular clip observation unavailable');}}
  return value;
 },run);
}
function observeCameraDraw<T>(camera:Camera,reg:ReturnType<typeof registration>,run:()=>T):T{
 return withNativeMethods(camera,['drawChildren'],(_method,originalCamera)=>function(...args){
  let scene:Node;
  try{scene=camera.scene();requireValue(scene instanceof Node,'native camera scene unavailable');}
  catch{return originalCamera.apply(this,args);}
  return withNativeMethods(scene,['drawChildren'],(_name,originalScene)=>function(...sceneArgs){
   const ctx=sceneArgs[0] as CanvasRenderingContext2D;
   const own=this===scene&&reg.renderStack[reg.renderStack.length-1]===camera;
   let draw:NativeSceneDraw|undefined;
   if(own){try{draw={scene,canvas:ctx.canvas,matrix:nativeMatrix(ctx.getTransform()),completed:false};}catch{/* unavailable stays UNKNOWN */}}
   if(own){const prior=reg.cameraDraws.get(camera);reg.cameraDraws.set(camera,{count:(prior?.count??0)+1,draw:prior?.draw??draw});}
   const value=originalScene.apply(this,sceneArgs);
   if(draw)draw.completed=true;
   return value;
  },()=>originalCamera.apply(this,args));
 },run);
}
function observeNativeRender<T>(n:Node,context:CanvasRenderingContext2D,reg:ReturnType<typeof registration>,run:()=>T):T{
 reg.renderStack.push(n);
 try{
  if(n instanceof Camera)return observeCameraDraw(n,reg,run);
  // Only the exact 3.17.2 Layout path has this rectangular clipping contract.
  const draw=Reflect.get(n,'draw');
  if(Object.getPrototypeOf(n)===Layout.prototype&&draw===Reflect.get(Layout.prototype,'draw')){
   const layout=n as Layout;
   if(layout.clip())return observeRectClip(layout,context,reg,run);
   const value=run();
   if(!layout.clip())reg.nativeClips.set(n,{canvas:context.canvas,value:known({kind:'none'})});
   return value;
  }
  return run();
 }finally{reg.renderStack.pop();}
}
function nativeLineage(n:Node,reg:ReturnType<typeof registration>):Known<NativeLineageNode[]>{
 try{
  const result:NativeLineageNode[]=[],seen=new Set<Node>();let current:Node|null=n;
  while(current){
   requireValue(result.length<64&&!seen.has(current),'native lineage bound or cycle');seen.add(current);
   const parent=current.parent(),opacity=current.opacity();
   requireValue((parent===null||parent instanceof Node)&&Number.isFinite(opacity)&&opacity>=0&&opacity<=1,'native lineage values');
   result.push({native_key:nativeKey(current),subject_id:reg.semanticIds.get(current)??null,native_type:nativeNodeType(current),
    parent_key:parent?nativeKey(parent):null,local_to_parent:nativeMatrix(current.localToParent()),opacity,
    layout_enabled:current instanceof Layout?known(current.layoutEnabled()):unknown('node is not a native Layout'),
    clip:current instanceof Layout?known(current.clip()):unknown('node is not a native Layout')});
   current=parent;
  }
  return known(result);
 }catch{return unknown('native parent lineage unavailable or ambiguous');}
}
function nativeCameraInventory(reg:ReturnType<typeof registration>):Known<NativeCameraIdentity[]>{
 try{
  requireValue(reg.nativeView instanceof Node,'native view unavailable');
  const seen=new Set<Node>(),active=new Set<Node>(),keys=new Set<string>();
  const stack:{node:Node;exit:boolean}[]=[{node:reg.nativeView,exit:false}],result:NativeCameraIdentity[]=[];
  while(stack.length){
   const {node,exit}=stack.pop()!;
   if(exit){active.delete(node);continue;}
   requireValue(!active.has(node),'native scene graph cycle');if(seen.has(node))continue;
   requireValue(seen.size<4096,'native scene graph bound');seen.add(node);active.add(node);
   const key=nativeKey(node);requireValue(!keys.has(key),'native node keys ambiguous');keys.add(key);
   stack.push({node,exit:true});const children=node.peekChildren();
   requireValue(Array.isArray(children)&&children.length<=1024,'native child roster bound');
   for(const child of children){requireValue(child instanceof Node,'native child type');stack.push({node:child,exit:false});}
   if(node instanceof Camera){
    requireValue(result.length<64,'native camera roster bound');result.push({native_key:key,subject_id:reg.semanticIds.get(node)??null});
    const scene=node.scene();requireValue(scene instanceof Node,'native camera scene type');stack.push({node:scene,exit:false});
   }
  }
  return known(result.sort((a,b)=>a.native_key<b.native_key?-1:a.native_key>b.native_key?1:0));
 }catch{return unknown('native camera graph inventory unavailable or ambiguous');}
}
function nativeGeometryObservation(n:Node,draw:DrawRecord|undefined,coordinateKnown:boolean,layoutBoxKnown:boolean,reg:ReturnType<typeof registration>):Known<NativeGeometry>{
 try{
  let polygon:Known<ScreenQuad>=unknown('native layout polygon or output transform unavailable');
  if(coordinateKnown&&layoutBoxKnown&&draw?.layout){try{polygon=known(nativeQuad(draw.layout.box,draw.matrix));}catch{/* keep UNKNOWN */}}
  const clip=reg.nativeClips.get(n);
  return known({native_key:nativeKey(n),screen_layout_polygon:polygon,
   lineage:draw?nativeLineage(n,reg):unknown('native node was not observed in a completed draw'),
   own_clip:coordinateKnown&&clip&&clip.canvas===draw?.canvas?clip.value:unknown('native own clip path or output transform unavailable')});
 }catch{return unknown('native geometry identity unavailable');}
}
function nativeCameraObservation(n:Node,reg:ReturnType<typeof registration>,canvas:HTMLCanvasElement,inventory:Known<NativeCameraIdentity[]>):Known<NativeCameraScene>{
 if(!(n instanceof Camera))return unknown('node is not a native Camera');
 try{
  const original=reg.cameraOrigins.get(n),current=n.scene(),record=reg.cameraDraws.get(n);
  requireValue(original instanceof Node&&current instanceof Node&&record?.count===1&&record.draw?.completed,'native original camera scene draw unavailable');
  const draw=record.draw,position=n.position(),rotation=n.rotation(),zoom=n.zoom();
  requireValue([position.x,position.y,rotation,zoom].every(v=>Number.isFinite(v)&&Math.abs(v)<=1e6)&&zoom>0,'native camera signals unavailable');
  const mapped=draw.canvas===reg.canvas&&draw.canvas.width===canvas.width&&draw.canvas.height===canvas.height;
  return known({native_key:nativeKey(n),original_scene_key:nativeKey(original),current_scene_key:nativeKey(current),
   current_scene_is_original:current===original,draw_scene_key:nativeKey(draw.scene),draw_uses_original_scene:draw.scene===original,
   scene_transform:mapped?known(draw.matrix):unknown('native camera scene draw has an unmodeled output transform'),
   position:[position.x,position.y],rotation,zoom,cameras:inventory});
 }catch{return unknown('native original Camera.scene draw or identity unavailable');}
}

function nativeLayoutObservation(n:Node,draw:DrawRecord|undefined,coordinateKnown:boolean){
 const native_type=known(nativeNodeType(n));
 const layout_root:Known<boolean>=draw?.layout?known(draw.layout.root):unknown('native layout root unavailable after completed draw');
"""),
    ('native_geometry_observation_3', r"""}
function clipAncestor(n:Node){for(let a:Node|null=n;a;a=a.parent()){if(a instanceof Layout&&a.clip())return true;}return false;}
function instrument(n:Node,id:string,reg:ReturnType<typeof registration>){
 const render=n.render.bind(n);
 n.render=(context:CanvasRenderingContext2D)=>{
  const result=render(context);
  if(n.absoluteOpacity()>0){
   reg.rendered.add(id);
   // Layout/Txt transforms and cache bounds can require the native DOM layout
""", r"""}
function clipAncestor(n:Node){for(let a:Node|null=n;a;a=a.parent()){if(a instanceof Layout&&a.clip())return true;}return false;}
function instrument(n:Node,id:string,reg:ReturnType<typeof registration>){
 reg.semanticIds.set(n,id);
 if(n instanceof Camera){try{const original=n.scene();if(original instanceof Node)reg.cameraOrigins.set(n,original);}catch{/* unavailable scene identity stays UNKNOWN */}}
 const render=n.render.bind(n);
 n.render=(context:CanvasRenderingContext2D)=>{
  const result=observeNativeRender(n,context,reg,()=>render(context));
  if(n.absoluteOpacity()>0){
   reg.rendered.add(id);
   // Layout/Txt transforms and cache bounds can require the native DOM layout
"""),
    ('native_geometry_observation_4', r"""  return result;
 };
}
function registration(data:NativeSceneData,nodes:Map<string,Node>){return {data,nodes,draws:new Map<string,DrawRecord>(),rendered:new Set<string>(),canvas:null as HTMLCanvasElement|null,drawSerial:0,finished:new Set<string>(),captionActive:new Map<string,boolean>()};}
function transformed(box:DrawRecord['bounds'],m:DOMMatrix){const p=[[box.x,box.y],[box.x+box.width,box.y],[box.x,box.y+box.height],[box.x+box.width,box.y+box.height]].map(([x,y])=>m.transformPoint({x,y}));const x=Math.min(...p.map(x=>x.x)),y=Math.min(...p.map(x=>x.y));return{x,y,width:Math.max(...p.map(x=>x.x))-x,height:Math.max(...p.map(x=>x.y))-y};}
async function textHash(text:string){const digest=globalThis.__SEMWRIGHT_TEXT_DIGEST__;requireValue(typeof digest==='function','native text digest binding unavailable');const hash=await digest(text);requireValue(/^[0-9a-f]{64}$/.test(hash),'native text digest binding returned invalid hash');return hash;}
function hasGlyph(face:FontEvidence,cp:number){return face.codepoints.some(([a,b])=>a<=cp&&cp<=b);}
""", r"""  return result;
 };
}
function registration(data:NativeSceneData,nodes:Map<string,Node>){return {data,nodes,draws:new Map<string,DrawRecord>(),rendered:new Set<string>(),canvas:null as HTMLCanvasElement|null,drawSerial:0,finished:new Set<string>(),captionActive:new Map<string,boolean>(),
 nativeView:null as Node|null,semanticIds:new WeakMap<Node,string>(),renderStack:[] as Node[],nativeClips:new Map<Node,NativeClipRecord>(),
 cameraOrigins:new WeakMap<Camera,Node>(),cameraDraws:new Map<Camera,{count:number;draw?:NativeSceneDraw}>()};}
function transformed(box:DrawRecord['bounds'],m:DOMMatrix){const p=[[box.x,box.y],[box.x+box.width,box.y],[box.x,box.y+box.height],[box.x+box.width,box.y+box.height]].map(([x,y])=>m.transformPoint({x,y}));const x=Math.min(...p.map(x=>x.x)),y=Math.min(...p.map(x=>x.y));return{x,y,width:Math.max(...p.map(x=>x.x))-x,height:Math.max(...p.map(x=>x.y))-y};}
async function textHash(text:string){const digest=globalThis.__SEMWRIGHT_TEXT_DIGEST__;requireValue(typeof digest==='function','native text digest binding unavailable');const hash=await digest(text);requireValue(/^[0-9a-f]{64}$/.test(hash),'native text digest binding returned invalid hash');return hash;}
function hasGlyph(face:FontEvidence,cp:number){return face.codepoints.some(([a,b])=>a<=cp&&cp<=b);}
"""),
    ('native_geometry_observation_5', r"""}
async function probeScene(reg:ReturnType<typeof registration>,canvas:HTMLCanvasElement,frame:number,binding:FrameBinding){
 requireValue(binding.native_stage_version==='3.17.2','stage compositor baseline changed');
 const output:unknown[]=[];
 for(const shot of reg.data.shots)for(const subject of shot.subjects){
  const n=reg.nodes.get(subject.id)!;const draw=reg.draws.get(subject.id);
  // 3.17.2 Stage copies same-sized current/previous buffers at (0,0). A cache
""", r"""}
async function probeScene(reg:ReturnType<typeof registration>,canvas:HTMLCanvasElement,frame:number,binding:FrameBinding){
 requireValue(binding.native_stage_version==='3.17.2','stage compositor baseline changed');
 const output:unknown[]=[];const cameras=nativeCameraInventory(reg);
 for(const shot of reg.data.shots)for(const subject of shot.subjects){
  const n=reg.nodes.get(subject.id)!;const draw=reg.draws.get(subject.id);
  // 3.17.2 Stage copies same-sized current/previous buffers at (0,0). A cache
"""),
    ('native_geometry_observation_6', r"""  const rendered=reg.rendered.has(subject.id);
  let asset_ready:Known<boolean>=known(true);
  if(c.kind==='image'||c.kind==='video')asset_ready=rendered?known(true):unknown('asset was not observed in a completed native draw');
  output.push({id:subject.id,...nativeLayoutObservation(n,draw,coordinateKnown),local_size:draw?known({width:draw.bounds.width,height:draw.bounds.height}):unknown('native layout size unavailable'),bounds:coordinateKnown?known(transformed(draw!.bounds,draw!.matrix)):unknown('not drawn in the native scene buffer or unmodeled cache transform'),transform:coordinateKnown?known([draw!.matrix.a,draw!.matrix.b,draw!.matrix.c,draw!.matrix.d,draw!.matrix.e,draw!.matrix.f]):unknown('output transform unavailable'),opacity:known(n.absoluteOpacity()),drawn:rendered,z_order:draw?known(draw.serial):unknown('no native draw order'),clip_active:known(clipAncestor(n)),pixel_visibility:unknown('draw and alpha do not prove pixel contribution after overlap'),text,asset_ready});
 }
 const transitions=[...new Set(reg.data.instructions.map(i=>i.invocation))].map(id=>({invocation_id:id,finished:known(reg.finished.has(id))}));
 const captions=reg.data.shots.flatMap(s=>s.captions.map(c=>{const cue=reg.data.cues[c.cue_id];return{id:c.id,active:known((reg.captionActive.get(c.id)??false)&&reg.draws.has(c.subject)&&reg.nodes.get(c.subject)!.absoluteOpacity()>0&&(reg.nodes.get(c.subject) as Txt).text()===c.text),cue_start:cue?.status==='resolved'?known(cue.start):unknown('unresolved cue'),cue_end:cue?.status==='resolved'?known(cue.end):unknown('unresolved cue')};}));
""", r"""  const rendered=reg.rendered.has(subject.id);
  let asset_ready:Known<boolean>=known(true);
  if(c.kind==='image'||c.kind==='video')asset_ready=rendered?known(true):unknown('asset was not observed in a completed native draw');
  const observedLayout=nativeLayoutObservation(n,draw,coordinateKnown);
  output.push({id:subject.id,...observedLayout,native_geometry:nativeGeometryObservation(n,draw,coordinateKnown,observedLayout.screen_layout_box.status==='known',reg),camera_scene:nativeCameraObservation(n,reg,canvas,cameras),local_size:draw?known({width:draw.bounds.width,height:draw.bounds.height}):unknown('native layout size unavailable'),bounds:coordinateKnown?known(transformed(draw!.bounds,draw!.matrix)):unknown('not drawn in the native scene buffer or unmodeled cache transform'),transform:coordinateKnown?known([draw!.matrix.a,draw!.matrix.b,draw!.matrix.c,draw!.matrix.d,draw!.matrix.e,draw!.matrix.f]):unknown('output transform unavailable'),opacity:known(n.absoluteOpacity()),drawn:rendered,z_order:draw?known(draw.serial):unknown('no native draw order'),clip_active:known(clipAncestor(n)),pixel_visibility:unknown('draw and alpha do not prove pixel contribution after overlap'),text,asset_ready});
 }
 const transitions=[...new Set(reg.data.instructions.map(i=>i.invocation))].map(id=>({invocation_id:id,finished:known(reg.finished.has(id))}));
 const captions=reg.data.shots.flatMap(s=>s.captions.map(c=>{const cue=reg.data.cues[c.cue_id];return{id:c.id,active:known((reg.captionActive.get(c.id)??false)&&reg.draws.has(c.subject)&&reg.nodes.get(c.subject)!.absoluteOpacity()>0&&(reg.nodes.get(c.subject) as Txt).text()===c.text),cue_start:cue?.status==='resolved'?known(cue.start):unknown('unresolved cue'),cue_end:cue?.status==='resolved'?known(cue.end):unknown('unresolved cue')};}));
"""),
    ('native_geometry_observation_7', r"""  // Capture the stage canvas before any authored subtree is attached, then
  // instrument each subject inside createShot before parent.add/view.add can
  // prime Motion Canvas caches.
  const originalRender=view.render.bind(view);
  view.render=(ctx:CanvasRenderingContext2D)=>{reg.canvas=ctx.canvas;reg.draws.clear();reg.rendered.clear();reg.drawSerial=0;return originalRender(ctx);};
  for(const shot of data.shots)view.add(createShot(shot,data,urls,nodes,reg));
  // Native dependency resolution participates in renderer startup; fonts and
  // media are awaited by native promises, never set ready from the desired model.
""", r"""  // Capture the stage canvas before any authored subtree is attached, then
  // instrument each subject inside createShot before parent.add/view.add can
  // prime Motion Canvas caches.
  reg.nativeView=view;const originalRender=view.render.bind(view);
  view.render=(ctx:CanvasRenderingContext2D)=>{reg.canvas=ctx.canvas;reg.draws.clear();reg.rendered.clear();reg.drawSerial=0;reg.nativeClips.clear();reg.cameraDraws.clear();reg.renderStack.length=0;return originalRender(ctx);};
  for(const shot of data.shots)view.add(createShot(shot,data,urls,nodes,reg));
  // Native dependency resolution participates in renderer startup; fonts and
  // media are awaited by native promises, never set ready from the desired model.
"""),
)
EDITS += GEOMETRY_EDITS


# Read-only glyph operation observations; applied identically to both historical arms.
INK_EDITS = (
    ('native_text_pixel_observation_1', " * semwright-motion-authoring before it enters this module; runtime guards remain.\n */\nimport {Node,Layout,Rect,Circle,Line,Txt,Code,Img,Video,Camera,makeScene2D,lines,LezerHighlighter} from '@motion-canvas/2d';\nimport {Vector2,all,delay,waitFor,tween,linear,easeInCubic,easeOutCubic,easeInOutCubic,easeOutBack,easeOutExpo,easeInOutSine,easeOutElastic,useTime,useThread} from '@motion-canvas/core';\nimport type {ThreadGenerator,TimingFunction} from '@motion-canvas/core';\nimport {parser as jsParser} from '@lezer/javascript';\nimport {parser as pythonParser} from '@lezer/python';\n", " * semwright-motion-authoring before it enters this module; runtime guards remain.\n */\nimport {Node,Layout,Rect,Circle,Line,Txt,Code,Img,Video,Camera,makeScene2D,lines,LezerHighlighter} from '@motion-canvas/2d';\nimport {Vector2,all,delay,waitFor,tween,linear,easeInCubic,easeOutCubic,easeInOutCubic,easeOutBack,easeOutExpo,easeInOutSine,easeOutElastic,useTime,useThread,usePlayback} from '@motion-canvas/core';\nimport type {ThreadGenerator,TimingFunction} from '@motion-canvas/core';\nimport {parser as jsParser} from '@lezer/javascript';\nimport {parser as pythonParser} from '@lezer/python';\n"),
    ('native_text_pixel_observation_2', "export type NativeSceneData={version:1;id:string;start:Rat;end:Rat;width:number;height:number;aspect:string;safe_area:Insets;editorial:Editorial;shots:NativeShot[];instructions:Instruction[];cues:Record<string,ResolvedCue>};\nexport type AssetUrls=Readonly<Record<string,string>>;\nexport type FontEvidence={family:string;sha256:string;weights:number[];codepoints:number[][];face_loaded:boolean};\nexport type FrameBinding={render_input_digest:string;native_stage_version:'3.17.2';font_evidence:FontEvidence[]};\nexport type NativeProbe={frame:number;subjects:unknown[];transitions:unknown[];captions:unknown[];unresolved_cues:string[]};\ndeclare global {\n var __SEMWRIGHT_NATIVE_PROBE__: undefined|((canvas:HTMLCanvasElement,frame:number,binding:FrameBinding)=>Promise<NativeProbe>);\n var __SEMWRIGHT_NATIVE_FRAME_CLOCK__: undefined|{frame:number;fps_num:number;fps_den:number};\n var __SEMWRIGHT_TEXT_DIGEST__: undefined|((text:string)=>Promise<string>);\n}\nconst known=<T>(value:T):Known<T>=>({status:'known',value});\n", "export type NativeSceneData={version:1;id:string;start:Rat;end:Rat;width:number;height:number;aspect:string;safe_area:Insets;editorial:Editorial;shots:NativeShot[];instructions:Instruction[];cues:Record<string,ResolvedCue>};\nexport type AssetUrls=Readonly<Record<string,string>>;\nexport type FontEvidence={family:string;sha256:string;weights:number[];codepoints:number[][];face_loaded:boolean};\nexport type FrameBinding={render_input_digest:string;native_stage_version:'3.17.2';font_evidence:FontEvidence[];\n text_pixel_observation_frames?:number[];text_pixel_background?:string|null};\nexport type NativeProbe={frame:number;subjects:unknown[];transitions:unknown[];captions:unknown[];unresolved_cues:string[]};\ndeclare global {\n var __SEMWRIGHT_NATIVE_PROBE__: undefined|((canvas:HTMLCanvasElement,frame:number,binding:FrameBinding)=>Promise<NativeProbe>);\n var __SEMWRIGHT_NATIVE_FRAME_CLOCK__: undefined|{frame:number;fps_num:number;fps_den:number};\n var __SEMWRIGHT_NATIVE_CONFIG__: undefined|(FrameBinding&{fps_num:number;fps_den:number});\n var __SEMWRIGHT_TEXT_DIGEST__: undefined|((text:string)=>Promise<string>);\n}\nconst known=<T>(value:T):Known<T>=>({status:'known',value});\n"),
    ('native_text_pixel_observation_3', "type NativeSceneDraw={scene:Node;canvas:HTMLCanvasElement;matrix:Matrix6;completed:boolean};\ntype NativeMethod=(this:unknown,...args:unknown[])=>unknown;\n\nfunction nativeKey(n:Node):string{\n const key=n.key;requireValue(typeof key==='string'&&key.length>0&&key.length<=512&&/^[\\x20-\\x7e]+$/.test(key),'native node key unavailable');return key;\n}\n", "type NativeSceneDraw={scene:Node;canvas:HTMLCanvasElement;matrix:Matrix6;completed:boolean};\ntype NativeMethod=(this:unknown,...args:unknown[])=>unknown;\n\n// Read-only contribution observation of real Canvas glyph paint operations.\n// No isolated text render, generated GOLD, ROI proxy or RED/GREEN difference.\ntype TextPixelCall={serial:number;operation:'fill_text'|'stroke_text';matrix:Matrix6;\n text:string;text_digest:string;font:string;color:[number,number,number];x:number;y:number;\n max_width:number|null;changed_pixels:number;global_alpha:1;composite:'source-over';filter:'none'};\ntype TextPixelMask={encoding:'row-runs-u16le-base64';data:string;runs:number;changed_pixels:number};\ntype NativeTextPixels={native_key:string;frame:number;width:number;height:number;playback_fps:number;\n fps_num:number;fps_den:number;selected_frames:number[];render_input_digest:string;\n output_background:string|null;calls:TextPixelCall[];mask:TextPixelMask};\ntype PendingTextCall=Omit<TextPixelCall,'text_digest'>;\ntype TextPixelSubject={node:Txt;visits:number;completed:boolean;calls:PendingTextCall[];bits:Uint8Array};\ntype TextPixelFailure='native glyph operation, style, contrast or readback unsupported'\n |'native glyph before readback shape or color unavailable'|'native glyph before contrast unavailable'\n |'original native glyph operation did not complete'|'native glyph after readback unavailable'\n |'native glyph method restoration failed'|'native glyph observation wrapper unavailable';\ntype TextPixelFrame={frame:number;fps:number;fps_num:number;fps_den:number;samples:number[];\n digest:string;background:string|null;canvas:HTMLCanvasElement;context:CanvasRenderingContext2D;\n width:number;height:number;subjects:Map<string,TextPixelSubject>;union:Uint8Array;\n operations:number;callBytes:number;completed:boolean;restored:boolean;failure:TextPixelFailure|null};\nconst TEXT_PIXEL_AREA=1920*1080,TEXT_PIXEL_MAX_CALLS=64,TEXT_PIXEL_RLE_BYTES=65532;\nfunction textPixelRgb(value:unknown):[number,number,number]|null{\n if(typeof value!=='string')return null;\n const found=/^#([0-9a-f]{6})$/i.exec(value);\n if(!found)return null;\n return [0,2,4].map(at=>parseInt(found[1].slice(at,at+2),16)) as [number,number,number];\n}\n// The native context must negotiate sRGB. Some pinned Firefox versions expose\n// no ImageData.colorSpace attribute; preserve that absence, never synthesize it.\n// An explicit contrary/undefined tag is rejected, as is an unobserved context.\nfunction textPixelReadbackInSrgb(image:ImageData,width:number,height:number,contextSpace:unknown):boolean{\n return contextSpace==='srgb'&&image instanceof ImageData&&image.width===width&&image.height===height\n  &&image.data instanceof Uint8ClampedArray&&image.data.length===width*height*4\n  &&(!('colorSpace' in image)||image.colorSpace==='srgb');\n}\nfunction textPixelBit(bits:Uint8Array,index:number){return (bits[index>>3]&(1<<(index&7)))!==0;}\nfunction textPixelSet(bits:Uint8Array,index:number){bits[index>>3]|=1<<(index&7);}\nfunction textPixelRle(bits:Uint8Array,width:number,height:number):TextPixelMask{\n const encoded=new Uint8Array(TEXT_PIXEL_RLE_BYTES);const view=new DataView(encoded.buffer);\n let used=0,changed=0;\n for(let y=0;y<height;y++)for(let x=0;x<width;){\n  if(!textPixelBit(bits,y*width+x)){x++;continue;}\n  const start=x;while(x<width&&textPixelBit(bits,y*width+x))x++;\n  requireValue(used+6<=encoded.length,'native text pixel RLE budget');\n  view.setUint16(used,y,true);view.setUint16(used+2,start,true);view.setUint16(used+4,x-start,true);\n  used+=6;changed+=x-start;\n }\n let bytes='';for(let i=0;i<used;i+=1024)bytes+=String.fromCharCode(...encoded.subarray(i,Math.min(i+1024,used)));\n const data=btoa(bytes);encoded.fill(0);\n requireValue(data.length<=87376&&data.length%8===0,'native text pixel transport bound');\n return {encoding:'row-runs-u16le-base64',data,runs:used/6,changed_pixels:changed};\n}\nfunction beginTextPixelFrame(reg:ReturnType<typeof registration>,context:CanvasRenderingContext2D):TextPixelFrame|undefined{\n const config=globalThis.__SEMWRIGHT_NATIVE_CONFIG__,samples=config?.text_pixel_observation_frames;\n if(!samples?.length)return undefined;\n const playback=usePlayback();\n const width=context.canvas.width,height=context.canvas.height;\n requireValue(Array.isArray(samples)&&samples.length<=7&&samples.every((v,i)=>Number.isSafeInteger(v)&&v>=0&&(i===0||samples[i-1]<v)),'native text pixel sample roster');\n requireValue(Number.isSafeInteger(playback.frame)&&playback.frame>=0&&Number.isSafeInteger(config.fps_num)&&config.fps_num>0&&Number.isSafeInteger(config.fps_den)&&config.fps_den>0&&playback.fps===config.fps_num/config.fps_den,'native text pixel playback binding');\n if(!samples.includes(playback.frame))return undefined;\n requireValue(width>0&&height>0&&width<=4096&&height<=4096&&width*height<=TEXT_PIXEL_AREA,'native text pixel canvas budget');\n requireValue(config.text_pixel_background===null||textPixelRgb(config.text_pixel_background)!==null,'native text pixel background observation');\n const subjects=new Map<string,TextPixelSubject>();\n for(const shot of reg.data.shots)for(const subject of shot.subjects)if(subject.content.kind==='text'){\n  const node=reg.nodes.get(subject.id);\n  requireValue(node instanceof Txt&&!subjects.has(subject.id),'native text pixel subject identity');\n  requireValue(subjects.size<2,'native text pixel subject budget');\n  subjects.set(subject.id,{node,visits:0,completed:false,calls:[],bits:new Uint8Array(Math.ceil(width*height/8))});\n }\n requireValue(/^[0-9a-f]{64}$/.test(config.render_input_digest),'native text pixel render binding');\n return {frame:playback.frame,fps:playback.fps,fps_num:config.fps_num,fps_den:config.fps_den,\n  samples:[...samples],digest:config.render_input_digest,background:config.text_pixel_background,\n  canvas:context.canvas,context,width,height,subjects,union:new Uint8Array(Math.ceil(width*height/8)),\n  operations:0,callBytes:0,completed:false,restored:false,failure:null};\n}\nfunction paintTextPixels(frame:TextPixelFrame,reg:ReturnType<typeof registration>,method:'fillText'|'strokeText',\n original:Function,receiver:unknown,args:unknown[]){\n const forward=()=>Reflect.apply(original,receiver,args);\n if(frame.failure)return forward();\n const native=reg.renderStack[reg.renderStack.length-1],id=native?reg.semanticIds.get(native):undefined;\n const subject=id?frame.subjects.get(id):undefined;\n let call:PendingTextCall|undefined,before:ImageData|undefined;\n let beforeFailure:TextPixelFailure='native glyph operation, style, contrast or readback unsupported';\n try{\n  requireValue(receiver===frame.context&&subject&&native===subject.node&&frame.canvas===reg.canvas,'native glyph paint owner or canvas unavailable');\n  requireValue(++frame.operations<=TEXT_PIXEL_MAX_CALLS,'native glyph paint operation budget');\n  const context=frame.context;\n  requireValue(args.length>=3&&args.length<=4&&typeof args[0]==='string'&&new TextEncoder().encode(args[0]).length<=8192,\n   'native glyph paint arguments');\n  requireValue(args.slice(1).every(v=>typeof v==='number'&&Number.isFinite(v)&&Math.abs(v)<=2e6)\n   &&(args.length===3||(args[3] as number)>0),'native glyph paint coordinates');\n  requireValue(context.globalAlpha===1&&context.globalCompositeOperation==='source-over'&&context.filter==='none'\n   &&context.shadowBlur===0&&context.shadowOffsetX===0&&context.shadowOffsetY===0\n   &&context.shadowColor==='rgba(0, 0, 0, 0)','native glyph paint context unsupported');\n  const color=textPixelRgb(method==='fillText'?context.fillStyle:context.strokeStyle);\n  requireValue(color!==null,'native glyph paint opaque solid style unavailable');\n  const authored=reg.data.shots.flatMap(s=>s.subjects).find(s=>s.id===id)!;\n  requireValue(authored.content.kind==='text','native glyph source subject type');\n  const fallback=reg.data.editorial.colors.text??Object.values(reg.data.editorial.colors)[0]??'#202020';\n  const allowed=authored.content.runs.map(r=>textPixelRgb(r.color??fallback));\n  requireValue(allowed.every(v=>v!==null)&&allowed.some(v=>v!.every((c,i)=>c===color[i])),'native glyph style differs from observed source runs');\n  const background=frame.background===null?null:textPixelRgb(frame.background);\n  requireValue(background===null||background.some((c,i)=>c!==color[i]),'native glyph and output background have identical color');\n  requireValue(typeof context.font==='string'&&context.font.length>0&&new TextEncoder().encode(context.font).length<=512,'native glyph font style unavailable');\n  const attributes=context.getContextAttributes();\n  requireValue(attributes.colorSpace==='srgb','native glyph readback color space unsupported');\n  call={serial:frame.operations-1,operation:method==='fillText'?'fill_text':'stroke_text',matrix:nativeMatrix(context.getTransform()),\n   text:args[0],font:context.font,color,x:args[1] as number,y:args[2] as number,max_width:args.length===4?args[3] as number:null,\n   changed_pixels:0,global_alpha:1,composite:'source-over',filter:'none'};\n  beforeFailure='native glyph before readback shape or color unavailable';\n  before=context.getImageData(0,0,frame.width,frame.height);\n  requireValue(textPixelReadbackInSrgb(before,frame.width,frame.height,attributes.colorSpace),'native glyph before readback incomplete');\n  beforeFailure='native glyph before contrast unavailable';\n  // Equal opaque destination color could hide glyph coverage. Existing observed\n  // text is already in the frame union; other equal-color pixels deny this proof.\n  for(let p=0;p<frame.width*frame.height;p++){\n   const at=p*4;\n   requireValue(before.data[at+3]===0||textPixelBit(frame.union,p)\n    ||color.some((c,i)=>before!.data[at+i]!==c),'native glyph contrast is not independently decidable');\n  }\n }catch{\n  frame.failure=beforeFailure;\n  before=undefined;\n }\n // The original operation executes exactly once even if observation is denied.\n let result:unknown;\n try{result=forward();}catch(error){frame.failure='original native glyph operation did not complete';throw error;}\n if(!frame.failure&&before&&call&&subject){\n  try{\n   const after=frame.context.getImageData(0,0,frame.width,frame.height);\n   requireValue(textPixelReadbackInSrgb(after,frame.width,frame.height,frame.context.getContextAttributes().colorSpace),\n    'native glyph after readback incomplete');\n   for(let p=0;p<frame.width*frame.height;p++){\n    const at=p*4;\n    if(before.data[at]!==after.data[at]||before.data[at+1]!==after.data[at+1]\n      ||before.data[at+2]!==after.data[at+2]||before.data[at+3]!==after.data[at+3]){\n     textPixelSet(subject.bits,p);textPixelSet(frame.union,p);call.changed_pixels++;\n    }\n   }\n   // This bound is inside the existing 512 KiB observation-line and 8 MiB\n   // FixedTxt stream budgets. Overflow denies observation, never truncates it.\n   frame.callBytes+=new TextEncoder().encode(JSON.stringify(call)).length+82;\n   requireValue(frame.callBytes<=65536,'native glyph call metadata budget');\n   subject.calls.push(call);\n  }catch{frame.failure='native glyph after readback unavailable';}\n }\n before=undefined;\n return result;\n}\nfunction withTextPixelFrame<T>(reg:ReturnType<typeof registration>,context:CanvasRenderingContext2D,run:()=>T):T{\n if(reg.textPixels){\n  reg.textPixels.union.fill(0);\n  for(const subject of reg.textPixels.subjects.values()){subject.bits.fill(0);subject.calls.length=0;}\n }\n reg.textPixels=undefined;\n let frame:TextPixelFrame|undefined;\n try{frame=beginTextPixelFrame(reg,context);reg.textPixels=frame;}catch{/* requested observation remains UNKNOWN */}\n if(!frame)return run();\n frame.restored=true; // No method has been replaced yet.\n const installed:{name:'fillText'|'strokeText';descriptor:PropertyDescriptor|undefined;original:Function}[]=[];\n function restore(){\n  if(installed.length===0)return;\n  let okay=true;\n  for(const row of installed.reverse())try{\n   if(row.descriptor)Object.defineProperty(context,row.name,row.descriptor);else delete (context as unknown as Record<string,unknown>)[row.name];\n   if(context[row.name]!==row.original)okay=false;\n  }catch{okay=false;}\n  installed.length=0;frame!.restored=okay;\n  if(!okay)frame!.failure='native glyph method restoration failed';\n }\n try{\n  for(const name of ['fillText','strokeText'] as const){\n   const descriptor=Object.getOwnPropertyDescriptor(context,name),original=context[name];\n   requireValue(typeof original==='function'&&(!descriptor||descriptor.configurable),'native glyph method cannot be safely observed');\n   Object.defineProperty(context,name,{configurable:true,writable:true,enumerable:descriptor?.enumerable??false,value:function(this:unknown,...args:unknown[]){\n    return paintTextPixels(frame!,reg,name,original,this,args);\n   }});\n   installed.push({name,descriptor,original});\n   frame.restored=false;\n  }\n }catch{frame.failure='native glyph observation wrapper unavailable';restore();}\n try{const result=run();frame.completed=true;return result;}finally{restore();}\n}\nasync function nativeTextPixelObservation(reg:ReturnType<typeof registration>,id:string,canvas:HTMLCanvasElement,frame:number,binding:FrameBinding){\n if(!binding.text_pixel_observation_frames?.length)return {};\n const capture=reg.textPixels;\n let observed:Known<NativeTextPixels>=unknown(capture?.failure??'native glyph contribution not selected or not observed');\n try{\n  const subject=capture?.subjects.get(id),node=reg.nodes.get(id);\n  requireValue(capture&&subject&&node instanceof Txt&&subject.node===node&&!capture.failure\n   &&capture.completed&&capture.restored,'native glyph capture incomplete or unsupported');\n  const invisible=node.absoluteOpacity()===0;\n  requireValue((subject.visits===1&&subject.completed)||(subject.visits===0&&invisible&&subject.calls.length===0),\n   'native glyph subject draw incomplete or repeated');\n  requireValue(capture.frame===frame&&capture.samples.length===binding.text_pixel_observation_frames.length\n   &&capture.samples.every((v,i)=>v===binding.text_pixel_observation_frames![i])&&capture.digest===binding.render_input_digest\n   &&capture.background===binding.text_pixel_background,'native glyph frame or renderer binding changed');\n  const exported=globalThis.__SEMWRIGHT_NATIVE_FRAME_CLOCK__;\n  requireValue(exported&&exported.frame===capture.frame&&exported.fps_num===capture.fps_num\n   &&exported.fps_den===capture.fps_den,'native glyph playback/exporter clock differs');\n  requireValue(capture.canvas===reg.canvas&&capture.width===canvas.width&&capture.height===canvas.height,\n   'native glyph canvas reference or exported dimensions changed');\n  requireValue(subject.calls.length>0||invisible||node.text()==='',\n   'native glyph root-canvas calls absent, possibly cached or offscreen');\n  const calls:TextPixelCall[]=[];\n  for(const call of subject.calls)calls.push({...call,text_digest:await textHash(call.text)});\n  observed=known({native_key:nativeKey(node),frame,width:capture.width,height:capture.height,\n   playback_fps:capture.fps,fps_num:capture.fps_num,fps_den:capture.fps_den,selected_frames:capture.samples,\n   render_input_digest:capture.digest,output_background:capture.background,calls,\n   mask:textPixelRle(subject.bits,capture.width,capture.height)});\n }catch{/* No cropped mask, guessed ink, repeated paint or threshold fallback. */}\n return {native_text_pixels:observed};\n}\n\nfunction nativeKey(n:Node):string{\n const key=n.key;requireValue(typeof key==='string'&&key.length>0&&key.length<=512&&/^[\\x20-\\x7e]+$/.test(key),'native node key unavailable');return key;\n}\n"),
    ('native_text_pixel_observation_4', ' if(n instanceof Camera){try{const original=n.scene();if(original instanceof Node)reg.cameraOrigins.set(n,original);}catch{/* unavailable scene identity stays UNKNOWN */}}\n const render=n.render.bind(n);\n n.render=(context:CanvasRenderingContext2D)=>{\n  const result=observeNativeRender(n,context,reg,()=>render(context));\n  if(n.absoluteOpacity()>0){\n   reg.rendered.add(id);\n   // Layout/Txt transforms and cache bounds can require the native DOM layout\n', ' if(n instanceof Camera){try{const original=n.scene();if(original instanceof Node)reg.cameraOrigins.set(n,original);}catch{/* unavailable scene identity stays UNKNOWN */}}\n const render=n.render.bind(n);\n n.render=(context:CanvasRenderingContext2D)=>{\n  const pixelSubject=reg.textPixels?.subjects.get(id);\n  if(pixelSubject)pixelSubject.visits++;\n  const result=observeNativeRender(n,context,reg,()=>render(context));\n  if(pixelSubject)pixelSubject.completed=true;\n  if(n.absoluteOpacity()>0){\n   reg.rendered.add(id);\n   // Layout/Txt transforms and cache bounds can require the native DOM layout\n'),
    ('native_text_pixel_observation_5', "}\nfunction registration(data:NativeSceneData,nodes:Map<string,Node>){return {data,nodes,draws:new Map<string,DrawRecord>(),rendered:new Set<string>(),canvas:null as HTMLCanvasElement|null,drawSerial:0,finished:new Set<string>(),captionActive:new Map<string,boolean>(),\n nativeView:null as Node|null,semanticIds:new WeakMap<Node,string>(),renderStack:[] as Node[],nativeClips:new Map<Node,NativeClipRecord>(),\n cameraOrigins:new WeakMap<Camera,Node>(),cameraDraws:new Map<Camera,{count:number;draw?:NativeSceneDraw}>()};}\nfunction transformed(box:DrawRecord['bounds'],m:DOMMatrix){const p=[[box.x,box.y],[box.x+box.width,box.y],[box.x,box.y+box.height],[box.x+box.width,box.y+box.height]].map(([x,y])=>m.transformPoint({x,y}));const x=Math.min(...p.map(x=>x.x)),y=Math.min(...p.map(x=>x.y));return{x,y,width:Math.max(...p.map(x=>x.x))-x,height:Math.max(...p.map(x=>x.y))-y};}\nasync function textHash(text:string){const digest=globalThis.__SEMWRIGHT_TEXT_DIGEST__;requireValue(typeof digest==='function','native text digest binding unavailable');const hash=await digest(text);requireValue(/^[0-9a-f]{64}$/.test(hash),'native text digest binding returned invalid hash');return hash;}\nfunction hasGlyph(face:FontEvidence,cp:number){return face.codepoints.some(([a,b])=>a<=cp&&cp<=b);}\n", "}\nfunction registration(data:NativeSceneData,nodes:Map<string,Node>){return {data,nodes,draws:new Map<string,DrawRecord>(),rendered:new Set<string>(),canvas:null as HTMLCanvasElement|null,drawSerial:0,finished:new Set<string>(),captionActive:new Map<string,boolean>(),\n nativeView:null as Node|null,semanticIds:new WeakMap<Node,string>(),renderStack:[] as Node[],nativeClips:new Map<Node,NativeClipRecord>(),\n textPixels:undefined as TextPixelFrame|undefined,cameraOrigins:new WeakMap<Camera,Node>(),cameraDraws:new Map<Camera,{count:number;draw?:NativeSceneDraw}>()};}\nfunction transformed(box:DrawRecord['bounds'],m:DOMMatrix){const p=[[box.x,box.y],[box.x+box.width,box.y],[box.x,box.y+box.height],[box.x+box.width,box.y+box.height]].map(([x,y])=>m.transformPoint({x,y}));const x=Math.min(...p.map(x=>x.x)),y=Math.min(...p.map(x=>x.y));return{x,y,width:Math.max(...p.map(x=>x.x))-x,height:Math.max(...p.map(x=>x.y))-y};}\nasync function textHash(text:string){const digest=globalThis.__SEMWRIGHT_TEXT_DIGEST__;requireValue(typeof digest==='function','native text digest binding unavailable');const hash=await digest(text);requireValue(/^[0-9a-f]{64}$/.test(hash),'native text digest binding returned invalid hash');return hash;}\nfunction hasGlyph(face:FontEvidence,cp:number){return face.codepoints.some(([a,b])=>a<=cp&&cp<=b);}\n"),
    ('native_text_pixel_observation_6', "  let asset_ready:Known<boolean>=known(true);\n  if(c.kind==='image'||c.kind==='video')asset_ready=rendered?known(true):unknown('asset was not observed in a completed native draw');\n  const observedLayout=nativeLayoutObservation(n,draw,coordinateKnown);\n  output.push({id:subject.id,...observedLayout,native_geometry:nativeGeometryObservation(n,draw,coordinateKnown,observedLayout.screen_layout_box.status==='known',reg),camera_scene:nativeCameraObservation(n,reg,canvas,cameras),local_size:draw?known({width:draw.bounds.width,height:draw.bounds.height}):unknown('native layout size unavailable'),bounds:coordinateKnown?known(transformed(draw!.bounds,draw!.matrix)):unknown('not drawn in the native scene buffer or unmodeled cache transform'),transform:coordinateKnown?known([draw!.matrix.a,draw!.matrix.b,draw!.matrix.c,draw!.matrix.d,draw!.matrix.e,draw!.matrix.f]):unknown('output transform unavailable'),opacity:known(n.absoluteOpacity()),drawn:rendered,z_order:draw?known(draw.serial):unknown('no native draw order'),clip_active:known(clipAncestor(n)),pixel_visibility:unknown('draw and alpha do not prove pixel contribution after overlap'),text,asset_ready});\n }\n const transitions=[...new Set(reg.data.instructions.map(i=>i.invocation))].map(id=>({invocation_id:id,finished:known(reg.finished.has(id))}));\n const captions=reg.data.shots.flatMap(s=>s.captions.map(c=>{const cue=reg.data.cues[c.cue_id];return{id:c.id,active:known((reg.captionActive.get(c.id)??false)&&reg.draws.has(c.subject)&&reg.nodes.get(c.subject)!.absoluteOpacity()>0&&(reg.nodes.get(c.subject) as Txt).text()===c.text),cue_start:cue?.status==='resolved'?known(cue.start):unknown('unresolved cue'),cue_end:cue?.status==='resolved'?known(cue.end):unknown('unresolved cue')};}));\n", "  let asset_ready:Known<boolean>=known(true);\n  if(c.kind==='image'||c.kind==='video')asset_ready=rendered?known(true):unknown('asset was not observed in a completed native draw');\n  const observedLayout=nativeLayoutObservation(n,draw,coordinateKnown);\n  const observedPixels=c.kind==='text'?await nativeTextPixelObservation(reg,subject.id,canvas,frame,binding):{};\n  output.push({id:subject.id,...observedLayout,...observedPixels,native_geometry:nativeGeometryObservation(n,draw,coordinateKnown,observedLayout.screen_layout_box.status==='known',reg),camera_scene:nativeCameraObservation(n,reg,canvas,cameras),local_size:draw?known({width:draw.bounds.width,height:draw.bounds.height}):unknown('native layout size unavailable'),bounds:coordinateKnown?known(transformed(draw!.bounds,draw!.matrix)):unknown('not drawn in the native scene buffer or unmodeled cache transform'),transform:coordinateKnown?known([draw!.matrix.a,draw!.matrix.b,draw!.matrix.c,draw!.matrix.d,draw!.matrix.e,draw!.matrix.f]):unknown('output transform unavailable'),opacity:known(n.absoluteOpacity()),drawn:rendered,z_order:draw?known(draw.serial):unknown('no native draw order'),clip_active:known(clipAncestor(n)),pixel_visibility:unknown('draw and alpha do not prove pixel contribution after overlap'),text,asset_ready});\n }\n const transitions=[...new Set(reg.data.instructions.map(i=>i.invocation))].map(id=>({invocation_id:id,finished:known(reg.finished.has(id))}));\n const captions=reg.data.shots.flatMap(s=>s.captions.map(c=>{const cue=reg.data.cues[c.cue_id];return{id:c.id,active:known((reg.captionActive.get(c.id)??false)&&reg.draws.has(c.subject)&&reg.nodes.get(c.subject)!.absoluteOpacity()>0&&(reg.nodes.get(c.subject) as Txt).text()===c.text),cue_start:cue?.status==='resolved'?known(cue.start):unknown('unresolved cue'),cue_end:cue?.status==='resolved'?known(cue.end):unknown('unresolved cue')};}));\n"),
    ('native_text_pixel_observation_7', '  // instrument each subject inside createShot before parent.add/view.add can\n  // prime Motion Canvas caches.\n  reg.nativeView=view;const originalRender=view.render.bind(view);\n  view.render=(ctx:CanvasRenderingContext2D)=>{reg.canvas=ctx.canvas;reg.draws.clear();reg.rendered.clear();reg.drawSerial=0;reg.nativeClips.clear();reg.cameraDraws.clear();reg.renderStack.length=0;return originalRender(ctx);};\n  for(const shot of data.shots)view.add(createShot(shot,data,urls,nodes,reg));\n  // Native dependency resolution participates in renderer startup; fonts and\n  // media are awaited by native promises, never set ready from the desired model.\n', '  // instrument each subject inside createShot before parent.add/view.add can\n  // prime Motion Canvas caches.\n  reg.nativeView=view;const originalRender=view.render.bind(view);\n  view.render=(ctx:CanvasRenderingContext2D)=>{reg.canvas=ctx.canvas;reg.draws.clear();reg.rendered.clear();reg.drawSerial=0;reg.nativeClips.clear();reg.cameraDraws.clear();reg.renderStack.length=0;return withTextPixelFrame(reg,ctx,()=>originalRender(ctx));};\n  for(const shot of data.shots)view.add(createShot(shot,data,urls,nodes,reg));\n  // Native dependency resolution participates in renderer startup; fonts and\n  // media are awaited by native promises, never set ready from the desired model.\n'),
)
EDITS += INK_EDITS


class OverlayRejected(ValueError):
    pass


def need(condition: bool, code: str) -> None:
    if not condition:
        raise OverlayRejected(code)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def descriptor(raw: bytes) -> dict:
    return {"bytes": len(raw), "sha256": sha(raw)}


def derive(raw: bytes, arm: str) -> bytes:
    need(arm in HISTORICAL, "closed_arm")
    need(0 < len(raw) <= MAX_SOURCE_BYTES and sha(raw) == HISTORICAL[arm],
         "historical_source_identity")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise OverlayRejected("historical_source_utf8") from error
    for name, before, after in EDITS:
        need(text.count(before) == 1 and after not in text, "exact_once_preimage:" + name)
        text = text.replace(before, after, 1)
    result = text.encode("utf-8")
    need(0 < len(result) <= MAX_SOURCE_BYTES, "derived_source_bound")
    need(reverse(result) == raw, "observer_only_reversible_derivation")
    return result


def reverse(raw: bytes) -> bytes:
    text = raw.decode("utf-8", errors="strict")
    for name, before, after in reversed(EDITS):
        need(text.count(after) == 1, "exact_once_postimage:" + name)
        text = text.replace(after, before, 1)
    return text.encode("utf-8")



OBSERVED = {
    "RED": "2d8ab6983d0845fe0290d6f25bef277759cb530ed85e6f3bd5e94f263a5f2bb3",
    "GREEN": "0aa5b9bd414f3c7d88e9b871ee96e3523729c02caddd8d420d63d9f14c2c794d",
}
HISTORICAL_BYTES = {"RED": 33809, "GREEN": 34259}
OBSERVED_BYTES = {'RED': 64323, 'GREEN': 64773}
MANIFEST_PATH = "observer-derivation.original.json"
MANIFEST_MAXIMUM = 16384
BUNDLE_PATHS = {
    "historical_red": "source/red_native.original",
    "historical_green": "historical-green-native.original.ts",
    "observed_green": "committed-native.original.ts",
    "observed_red": "observed-red-native.original.ts",
}


def canonical(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def derivation_manifest() -> dict:
    return {
        "schema": "motionwright.fixedtxt.observation-overlay/4",
        "sdk_original_revision": SDK,
        "motion_canvas_api": "3.17.2",
        "historical": {arm: {"bytes": HISTORICAL_BYTES[arm], "sha256": digest}
                       for arm, digest in HISTORICAL.items()},
        "derived": {arm: {"bytes": OBSERVED_BYTES[arm], "sha256": digest}
                    for arm, digest in OBSERVED.items()},
        "edits": [{"id": name, "before": descriptor(before.encode()),
                   "after": descriptor(after.encode())} for name, before, after in EDITS],
        "same_observer_in_both_arms": True,
        "original_attachment_delta_preserved_exactly": True,
        "reversal_verified_for_both_originals": True,
        "old_drawable_bounds_unchanged": True,
        "new_observations": ["native_type", "layout_root", "screen_layout_box", "native_geometry", "camera_scene", "native_text_pixels"],
        "cache_or_unmodeled_output_transform": "UNKNOWN",
        "native_effects": 0,
        "G2_accepted": False,
        "F_accepted": False,
    }


def pair(red: bytes, green: bytes) -> tuple[dict, dict]:
    originals = {"RED": red, "GREEN": green}
    derived = {arm: derive(raw, arm) for arm, raw in originals.items()}
    for arm in HISTORICAL:
        need(len(originals[arm]) == HISTORICAL_BYTES[arm]
             and descriptor(derived[arm]) == {"bytes": OBSERVED_BYTES[arm], "sha256": OBSERVED[arm]},
             "exact_reviewed_observer_postimage")
    need(red.count(RED_ATTACHMENT.encode()) == 1, "one_historical_attachment_delta")
    need(red.replace(RED_ATTACHMENT.encode(), GREEN_ATTACHMENT.encode(), 1) == green,
         "historical_pair_not_only_the_reviewed_attachment_delta")
    need(derived["RED"].replace(RED_ATTACHMENT.encode(), GREEN_ATTACHMENT.encode(), 1)
         == derived["GREEN"], "observer_changed_the_causal_pair_delta")
    return derived, derivation_manifest()


def build_bundle(historical_red: bytes, committed_green: bytes) -> dict[str, bytes]:
    need(type(historical_red) is bytes and type(committed_green) is bytes,
         "observer_original_bytes_required")
    need(len(committed_green) == OBSERVED_BYTES["GREEN"]
         and sha(committed_green) == OBSERVED["GREEN"], "committed_observer_green_identity")
    historical_green = reverse(committed_green)
    derived, proof = pair(historical_red, historical_green)
    need(derived["GREEN"] == committed_green, "committed_green_not_reviewed_derivation")
    manifest = canonical(proof)
    need(0 < len(manifest) <= MANIFEST_MAXIMUM, "observer_manifest_bound")
    return {
        BUNDLE_PATHS["historical_red"]: historical_red,
        BUNDLE_PATHS["historical_green"]: historical_green,
        BUNDLE_PATHS["observed_green"]: committed_green,
        BUNDLE_PATHS["observed_red"]: derived["RED"],
        MANIFEST_PATH: manifest,
    }


def bundle_descriptors() -> dict[str, dict]:
    return {
        BUNDLE_PATHS["historical_red"]: {"bytes": HISTORICAL_BYTES["RED"], "sha256": HISTORICAL["RED"]},
        BUNDLE_PATHS["historical_green"]: {"bytes": HISTORICAL_BYTES["GREEN"], "sha256": HISTORICAL["GREEN"]},
        BUNDLE_PATHS["observed_green"]: {"bytes": OBSERVED_BYTES["GREEN"], "sha256": OBSERVED["GREEN"]},
        BUNDLE_PATHS["observed_red"]: {"bytes": OBSERVED_BYTES["RED"], "sha256": OBSERVED["RED"]},
        MANIFEST_PATH: descriptor(canonical(derivation_manifest())),
    }


def validate_bundle_plan(plan: dict) -> bool:
    """Require exact source custody if either reviewed observer arm is selected.

    Historical plans remain untyped; this function does not approve their
    source pair or turn their absent observations into a known result.
    """
    need(type(plan) is dict and type(plan.get("arm_sources")) is dict
         and set(plan["arm_sources"]) == set(HISTORICAL), "observer_two_arm_sources")
    sources = plan["arm_sources"]
    need(all(type(sources[arm]) is dict for arm in HISTORICAL), "observer_arm_source_rows")
    selected = {arm: sources[arm].get("native_source_sha256") for arm in HISTORICAL}
    if not any(value in OBSERVED.values() for value in selected.values()):
        return False
    need(selected == OBSERVED, "observer_pair_partial_or_foreign")
    need(type(plan.get("context")) is dict, "observer_context")
    sdk = plan["context"].get("semwright_sha")
    tree = sources["GREEN"].get("tree_sha")
    need(all(type(value) is str and len(value) == 40 and all(c in "0123456789abcdef" for c in value)
             for value in (sdk, tree))
         and all(sources[arm].get("source_sha") == sdk and sources[arm].get("tree_sha") == tree
                 for arm in HISTORICAL),
         "observer_pair_common_pinned_sdk")
    need(type(plan.get("members")) is list, "observer_member_roster")
    expected = bundle_descriptors()
    actual = {}
    for row in plan["members"]:
        need(type(row) is dict, "observer_member_row")
        if row.get("root") == "inputs" and row.get("relative_path") in expected:
            name = row["relative_path"]
            need(name not in actual, "observer_duplicate_original")
            actual[name] = row
    need(set(actual) == set(expected), "observer_custody_original_missing")
    for name, desc in expected.items():
        row = actual[name]
        need(row.get("role") == "input-original" and type(row.get("maximum")) is int
             and row["maximum"] == desc["bytes"] and row.get("expected_sha256") == desc["sha256"],
             "observer_exact_planned_original")
    return True


def verify_bundle_originals(originals, *, plan: dict) -> dict:
    """Verify original bytes from the authenticated snapshot; no filesystem I/O."""
    if not validate_bundle_plan(plan):
        return {"status": "HISTORICAL_OR_UNREVIEWED_NO_TYPED_CUSTODY", "typed_source_bound": False}
    actual = {}
    for name, desc in bundle_descriptors().items():
        try:
            raw = originals["inputs/" + name]
        except KeyError as error:
            raise OverlayRejected("observer_original_missing") from error
        need(type(raw) is bytes and len(raw) <= MAX_SOURCE_BYTES and descriptor(raw) == desc,
             "observer_original_bytes_changed")
        actual[name] = raw
    expected = build_bundle(actual[BUNDLE_PATHS["historical_red"]], actual[BUNDLE_PATHS["observed_green"]])
    need(actual == expected, "observer_original_derivation_mismatch")
    return {"status": "OBSERVATION_DERIVATION_BYTES_BOUND", "typed_source_bound": True,
            "historical_pair": dict(HISTORICAL), "observed_pair": dict(OBSERVED),
            "native_effects": 0, "G2_accepted": False, "F_accepted": False}
