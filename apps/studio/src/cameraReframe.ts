/** MW05-E07-03: bounded, source-owned planar pan. No 3D, focal-length,
 * optical dolly, virtual camera execution or arbitrary external plugin.
 * Rust Project/StudioService CAS remains the durable source of truth.
 */
import type { Project, Scene } from "./types";
import type { Change } from "./types";
import { emptyProductionDesign } from "./creativeProduction";
import { seconds } from "./types";

export const CAMERA_REFRAME_VERSION=1;
export const CAMERA_REFRAME_MANIM_VERSION=2;
export const CAMERA_REFRAME_FPS=30;
export const CAMERA_REFRAME_MAX=16;
export interface CameraReframe {
  id:string;scene_id:string;component_version:number;
  start_center_x:number;start_center_y:number;
  end_center_x:number;end_center_y:number;
  duration_frames:number;
}
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const KEYS=[
  "id","scene_id","component_version","start_center_x","start_center_y",
  "end_center_x","end_center_y","duration_frames",
];
function invalid(message:string):never {throw new Error("CameraReframe: "+message);}
export function validateCameraReframe(source:CameraReframe,scene:Scene):void{
  if(!source || typeof source!=="object" || Array.isArray(source) ||
    Object.keys(source).sort().join("|")!==[...KEYS].sort().join("|"))
    invalid("unknown or missing source parameters");
  if(!UUID.test(source.id) || !UUID.test(source.scene_id) ||
    (source.component_version!==CAMERA_REFRAME_VERSION &&
      source.component_version!==CAMERA_REFRAME_MANIM_VERSION) || source.scene_id!==scene.id)
    invalid("invalid identity, scene or version");
  for(const [label,value,min,max] of [
    ["start x",source.start_center_x,720,1200],
    ["end x",source.end_center_x,720,1200],
    ["start y",source.start_center_y,320,760],
    ["end y",source.end_center_y,320,760],
  ] as const){
    if(!Number.isFinite(value)||value<min||value>max)
      invalid(label+" is outside the admitted source canvas");
  }
  const admittedPair=(source.component_version===CAMERA_REFRAME_VERSION && scene.renderer==="motion-canvas") ||
    (source.component_version===CAMERA_REFRAME_MANIM_VERSION && scene.renderer==="manim-community");
  if(!admittedPair || scene.nodes.length<1 || scene.nodes.length>32 ||
    scene.beats.length || scene.nodes.some(node=>node.keyframes.length ||
      node.parent_id!==null || node.kind==="group"))
    invalid("an original, ungrouped and motion-free Canvas shot with at most 32 objects is required");
  if(scene.camera.zoom!==1 || scene.camera.rotation_deg!==0)
    invalid("this planar reframe cannot represent optical zoom or 3D rotation");
  // Match the existing Rust v2 source contract. Do not admit visual semantics
  // that the selected native contributor would silently discard.
  if(source.component_version===CAMERA_REFRAME_MANIM_VERSION){
    if(scene.camera.safe_margin<.05)
      invalid("Manim camera requires an authored safe margin of at least 0.05");
    for(const node of scene.nodes){
      if(!["rectangle","circle"].includes(node.kind) ||
        node.coordinate_space!=="project_pixels" || node.relations.length ||
        node.opacity!==1 || node.rotation_deg!==0 || node.text!==null ||
        node.style.stroke!==null || node.style.stroke_width!==0 ||
        node.style.blend_mode!=="normal" || node.style.font_family!==null ||
        node.style.font_size!==null || node.style.font_weight!==null ||
        node.style.line_height!==null || (node.kind==="circle" && node.width!==node.height))
        invalid("Manim camera cannot preserve the authored shape semantics of node "+node.id);
      if(typeof node.style.fill!=="string" || !/^#[0-9a-f]{6}$/i.test(node.style.fill))
        invalid("Manim camera requires explicit opaque #RRGGBB fill for node "+node.id);
    }
  }
  const dx=source.end_center_x-source.start_center_x;
  const dy=source.end_center_y-source.start_center_y;
  if(Math.abs(dx)>80 || Math.abs(dy)>80 || (Math.abs(dx)<.001 && Math.abs(dy)<.001))
    invalid("camera movement must be visible and at most 80 source pixels per axis");
  if(!Number.isInteger(source.duration_frames)||source.duration_frames<12 ||
    source.duration_frames>180 || source.duration_frames/CAMERA_REFRAME_FPS>seconds(scene.duration))
    invalid("camera duration must fit 12â€“180 exact frames inside the authored scene");
  if(scene.camera.center_x!==source.end_center_x || scene.camera.center_y!==source.end_center_y)
    invalid("terminal camera center must match the authored Scene camera");
  const marginX=1920*Math.max(.05,scene.camera.safe_margin);
  const marginY=1080*Math.max(.05,scene.camera.safe_margin);
  for(const node of scene.nodes){
    for(const [cx,cy] of [
      [source.start_center_x,source.start_center_y],
      [source.end_center_x,source.end_center_y],
    ]){
      const x=node.x+960-cx,y=node.y+540-cy;
      if(x<marginX || y<marginY || x+node.width>1920-marginX ||
        y+node.height>1080-marginY)
        invalid("original Canvas node "+node.id+" is cropped by this pan");
    }
  }
}
export function validateCameraReframes(project:Project):void{
  const moves=project.production_design?.camera_reframes??[];
  if(moves.length>CAMERA_REFRAME_MAX)invalid("project move count exceeds 16");
  const ids=new Set<string>(),scenes=new Set<string>();
  for(const move of moves){
    if(ids.has(move.id)||scenes.has(move.scene_id))invalid("duplicate camera or scene identity");
    ids.add(move.id);scenes.add(move.scene_id);
    const scene=project.scenes.find(scene=>scene.id===move.scene_id);
    if(!scene)invalid("camera references an absent scene");
    validateCameraReframe(move,scene);
  }
}
type CameraChange=Extract<Change,{type:"upsert_camera_reframe"|"detach_camera_reframe"}>;
export function applyCameraReframeChange(project:Project,change:CameraChange):void{
  const design=project.production_design??emptyProductionDesign();
  const moves=design.camera_reframes??[];
  if(change.type==="upsert_camera_reframe"){
    const {reframe}=change;
    const scene=project.scenes.find(scene=>scene.id===reframe.scene_id);
    if(!scene)invalid("scene not found");
    const locks=project.locks.filter(lock=>
      ["project:"+project.id,"scene:"+scene.id].includes(lock.resource) &&
      ["content","timing","position","renderer"].includes(lock.kind));
    if(locks.length)invalid("source scene or project is locked");
    const index=moves.findIndex(existing=>existing.id===reframe.id);
    if(index>=0 && moves[index].scene_id!==scene.id)
      invalid("camera identity cannot move to another scene");
    if(index<0&&(moves.length>=CAMERA_REFRAME_MAX ||
      moves.some(existing=>existing.scene_id===scene.id)))
      invalid("only one camera move per scene or up to 16 per project");
    scene.camera.center_x=reframe.end_center_x;
    scene.camera.center_y=reframe.end_center_y;
    validateCameraReframe(reframe,scene);
    if(index>=0)moves[index]=structuredClone(reframe);
    else moves.push(structuredClone(reframe));
    scene.status="draft";
    design.camera_reframes=moves;
  } else {
    const found=moves.find(move=>move.id===change.id);
    if(!found)invalid("source camera move not found");
    if(project.locks.some(lock=>
      ["project:"+project.id,"scene:"+found.scene_id].includes(lock.resource) &&
      ["content","timing"].includes(lock.kind)))
      invalid("source camera move is locked");
    design.camera_reframes=moves.filter(move=>move.id!==change.id);
  }
  project.production_design=design;
}
export function cameraPreviewOffset(move:CameraReframe,timeSeconds:number):{x:number;y:number}{
  const duration=move.duration_frames/CAMERA_REFRAME_FPS;
  const progress=Math.max(0,Math.min(1,timeSeconds/duration));
  const cameraX=move.start_center_x+(move.end_center_x-move.start_center_x)*progress;
  const cameraY=move.start_center_y+(move.end_center_y-move.start_center_y)*progress;
  return {x:960-cameraX,y:540-cameraY};
}
