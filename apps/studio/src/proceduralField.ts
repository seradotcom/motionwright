/** A bounded, cross-language integer specification, not a visual simulation of rendering. */
import type { CanvasNode, Project } from "./types";
import { mergeComponentNodes, sameValue } from "./creativeProduction";
import type { ProductionDesign } from "./creativeProduction";

export type FieldDistribution = "grid" | "adaptive_grid" | "path" | "staggered" | "scatter" | "radial" | "spiral";
export type ProceduralShape = "square" | "circle";
export interface ProceduralInfluence {
  shape: "radial" | "horizontal" | "vertical"; falloff: "linear" | "smoothstep";
  center_x: number; center_y: number; radius: number; offset_x: number; offset_y: number;
  scale_percent: number; rotation_deg: number; fill: string;
}
export const defaultProceduralInfluence = (): ProceduralInfluence => ({
  shape: "radial", falloff: "linear", center_x: 630, center_y: 450, radius: 450,
  offset_x: 0, offset_y: 0, scale_percent: 100, rotation_deg: 0, fill: "#A5C8DF",
});

export interface ProceduralPath {
  closed: boolean; points: { x_percent: number; y_percent: number }[];
}
export const defaultProceduralPath = (): ProceduralPath => ({
  closed: false, points: [{ x_percent: 0, y_percent: 50 }, { x_percent: 50, y_percent: 0 }, { x_percent: 100, y_percent: 50 }],
});
export interface ProceduralConfig {
  seed: number; count: number; columns: number; distribution: FieldDistribution;
  /** Optional on legacy version-1 projects, default square. */
  shape?: ProceduralShape;
  origin_x: number; origin_y: number; area_width: number; area_height: number;
  size: number; opacity_percent: number; fill: string;
  reveal_step_frames: number; reveal_duration_frames: number;
  /** Omitted on original v1 fields; their baseline remains identical. */
  influence?: ProceduralInfluence;
  /** Authored polyline, only for the path distribution. */
  path?: ProceduralPath;
}
export interface ProceduralFieldInstance {
  id: string; scene_id: string; generator_version: number;
  config: ProceduralConfig; baseline: CanvasNode[];
}
export const defaultProceduralConfig=():ProceduralConfig=>({
  seed:41,count:12,columns:4,distribution:"scatter",shape:"square",
  origin_x:180,origin_y:170,area_width:900,area_height:560,
  size:32,opacity_percent:100,fill:"#A5C8DF",
  reveal_step_frames:0,reveal_duration_frames:12,
});
const hex=/^#[0-9a-f]{6}$/i;
const uuid=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
/** Retain the meaning of version-1 non-sequenced field documents. */
export function normalizedProceduralConfig(c:ProceduralConfig):ProceduralConfig {
  return {...c,shape:c.shape??"square",reveal_step_frames:c.reveal_step_frames??0,reveal_duration_frames:c.reveal_duration_frames??12};
}
export function validateProceduralConfig(input:ProceduralConfig):void {
  const c=normalizedProceduralConfig(input);
  const integers=[c.seed,c.count,c.columns,c.origin_x,c.origin_y,c.area_width,c.area_height,c.size,c.opacity_percent,c.reveal_step_frames,c.reveal_duration_frames];
  if(integers.some(n=>!Number.isInteger(n)) || c.seed<0 || c.seed>0xffffffff ||
    c.count<1 || c.count>64 || c.columns<1 || c.columns>16 ||
    c.origin_x<0 || c.origin_y<0 || c.area_width<1 || c.area_height<1 ||
    c.size<4 || c.size>128 || c.opacity_percent<1 || c.opacity_percent>100 ||
    (c.reveal_step_frames>0 && c.opacity_percent!==100) ||
    c.reveal_step_frames<0 || c.reveal_step_frames>10 || c.reveal_duration_frames<1 || c.reveal_duration_frames>60 ||
    !hex.test(c.fill) || !["grid","adaptive_grid","path","staggered","scatter","radial","spiral"].includes(c.distribution) ||
    !["square","circle"].includes(c.shape??"square")) {
    throw new Error("Procedural item, style or CPU budget exceeded.");
  }
  if(c.origin_x+c.area_width>1920 || c.origin_y+c.area_height>1080)
    throw new Error("Procedural field exceeds 1920x1080 authored stage.");
  if (c.influence !== undefined) {
    validateProceduralInfluence(c.influence);
  }
  if (c.distribution === "path") { pathVertices(c); return; }
  if (c.path !== undefined) throw new Error("Procedural path is only valid with the path distribution.");
  const columns=fieldColumns(c), rows=Math.ceil(c.count/columns);
  if(c.distribution==="radial" || c.distribution==="spiral"){
    if(c.area_width<c.size*2 || c.area_height<c.size*2)
      throw new Error("Procedural orbit requires a field at least twice the item size.");
  }else if(Math.floor(c.area_width/columns)<c.size || Math.floor(c.area_height/rows)<c.size)
    throw new Error("Procedural item does not fit its bounded cell.");
}
function fieldColumns(config: ProceduralConfig): number {
  if (config.distribution !== "adaptive_grid") return Math.min(config.columns, config.count);
  let best = { cell: 0, unused: Infinity, columns: 1 };
  for (let columns = 1; columns <= Math.min(config.count, 16); columns++) {
    const rows = Math.ceil(config.count / columns);
    const cell = Math.min(Math.floor(config.area_width / columns), Math.floor(config.area_height / rows));
    const unused = columns * rows - config.count;
    if (cell > best.cell || (cell === best.cell && unused < best.unused)) best = { cell, unused, columns };
  }
  return best.columns;
}
export function proceduralGridDimensions(config: ProceduralConfig): { columns: number; rows: number } {
  validateProceduralConfig(config);
  const columns = fieldColumns(config);
  return { columns, rows: Math.ceil(config.count / columns) };
}
function pathVertices(config: ProceduralConfig): { x: number; y: number }[] {
  const path = config.path;
  if (!path) throw new Error("Path distribution requires an editable polyline.");
  if (typeof path !== "object" || Object.keys(path).length !== 2 || typeof path.closed !== "boolean"
    || !Array.isArray(path.points) || path.points.length < (path.closed ? 3 : 2) || path.points.length > 16
    || path.points.some(point => !point || typeof point !== "object" || Object.keys(point).length !== 2
      || !Number.isInteger(point.x_percent) || !Number.isInteger(point.y_percent)
      || point.x_percent < 0 || point.x_percent > 100 || point.y_percent < 0 || point.y_percent > 100)
    || config.area_width < config.size || config.area_height < config.size)
    throw new Error("Procedural path requires 2..16 points (3 if closed), whole percentages and room for each object.");
  const vertices = path.points.map(point => ({
    x: config.origin_x + Math.floor((config.area_width - config.size) * point.x_percent / 100),
    y: config.origin_y + Math.floor((config.area_height - config.size) * point.y_percent / 100),
  }));
  const same = (a: typeof vertices[number], b: typeof vertices[number]) => a.x === b.x && a.y === b.y;
  if (vertices.some((point, index) => index > 0 && same(point, vertices[index - 1]))
    || (path.closed && same(vertices[0], vertices[vertices.length - 1])))
    throw new Error("Procedural path has a zero-length segment at the authored pixel resolution.");
  return vertices;
}
type PathSegment = { from: { x: number; y: number }; to: { x: number; y: number }; length: number };
/** Same bounded Q256 arc distance and whole-pixel placement as the Rust profile. */
function pathSegments(config: ProceduralConfig): PathSegment[] {
  const vertices = pathVertices(config);
  if (config.path!.closed) vertices.push(vertices[0]);
  return vertices.slice(1).map((to, index) => {
    const from = vertices[index], squared = ((to.x - from.x) ** 2 + (to.y - from.y) ** 2) * 65536;
    let low = 0, high = 1048576;
    while (low <= high) {
      const middle = Math.floor((low + high) / 2);
      if (middle * middle <= squared) low = middle + 1; else high = middle - 1;
    }
    return { from, to, length: high };
  });
}
function pathPosition(index: number, config: ProceduralConfig, segments: PathSegment[]): { x: number; y: number } {
  const divisions = config.path!.closed ? config.count : Math.max(1, config.count - 1);
  const distance = Math.floor(segments.reduce((total, segment) => total + segment.length, 0) * index / divisions);
  let elapsed = 0;
  for (const segment of segments) {
    if (distance <= elapsed + segment.length) {
      const offset = distance - elapsed;
      return {
        x: segment.from.x + Math.trunc((segment.to.x - segment.from.x) * offset / segment.length),
        y: segment.from.y + Math.trunc((segment.to.y - segment.from.y) * offset / segment.length),
      };
    }
    elapsed += segment.length;
  }
  return segments[segments.length - 1].to;
}
export function proceduralHash(seed:number,index:number,salt:number):number {
  let v=(seed ^ Math.imul(index,0x9e3779b9) ^ salt)>>>0;
  v^=v>>>16;v=Math.imul(v,0x7feb352d)>>>0;
  v^=v>>>15;v=Math.imul(v,0x846ca68b)>>>0;
  return (v^(v>>>16))>>>0;
}
/** Immutable Q1 sine lookup. Same integer table/symmetry as the Rust generator. */
const quarterSine64=[0,980,1951,2903,3827,4714,5556,6344,7071,7730,8315,8819,9239,9569,9808,9952,10000] as const;
export function sine64(phase:number):number {
  const p=phase%64;
  if(p<=16)return quarterSine64[p];
  if(p<=32)return quarterSine64[32-p];
  if(p<=48)return -quarterSine64[p-32];
  return -quarterSine64[64-p];
}
function orbitPosition(index:number,c:ProceduralConfig):{x:number;y:number}{
  const phase=(Math.floor(index*64/c.count)+proceduralHash(c.seed,0,0x7c3e8a71)%64)%64;
  const rx=Math.floor((c.area_width-c.size)/2),ry=Math.floor((c.area_height-c.size)/2);
  const fraction=c.distribution==="spiral"?index+1:c.count;
  return {
    x:c.origin_x+rx+Math.trunc(rx*fraction*sine64(phase+16)/(10000*c.count)),
    y:c.origin_y+ry+Math.trunc(ry*fraction*sine64(phase)/(10000*c.count)),
  };
}
function frameTime(frames:number):{num:string;den:string}{
  let a=frames,b=30;
  while(b!==0){const rem=a%b;a=b;b=rem;}
  return {num:String(frames/a),den:String(30/a)};
}
export async function proceduralNodeId(identity:string,index:number):Promise<string> {
  if(!uuid.test(identity) || !Number.isInteger(index) || index<0 || index>63)
    throw new Error("Invalid procedural identity or index.");
  const prefix=new TextEncoder().encode("motionwright.procedural.v1\0");
  const hexId=identity.replaceAll("-","");
  const id=new Uint8Array(hexId.match(/../g)!.map(v=>parseInt(v,16)));
  const input=new Uint8Array(prefix.length+20);
  input.set(prefix);input.set(id,prefix.length);
  input[prefix.length+16]=index&255;
  input[prefix.length+17]=(index>>>8)&255;
  input[prefix.length+18]=(index>>>16)&255;
  input[prefix.length+19]=(index>>>24)&255;
  const output=new Uint8Array(await crypto.subtle.digest("SHA-256",input));
  output[6]=(output[6]&15)|128;output[8]=(output[8]&63)|128;
  const h=Array.from(output.slice(0,16),v=>v.toString(16).padStart(2,"0")).join("");
  return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
}
export function validateProceduralInfluence(field: ProceduralInfluence): void {
  const keys = ["shape", "falloff", "center_x", "center_y", "radius", "offset_x", "offset_y", "scale_percent", "rotation_deg", "fill"];
  if (!field || typeof field !== "object" || Object.keys(field).length !== keys.length || keys.some(key => !Object.hasOwn(field, key))
    || !["radial", "horizontal", "vertical"].includes(field.shape) || !["linear", "smoothstep"].includes(field.falloff)
    || [field.center_x, field.center_y, field.radius, field.offset_x, field.offset_y, field.scale_percent, field.rotation_deg].some(value => !Number.isSafeInteger(value))
    || field.center_x < 0 || field.center_x > 1920 || field.center_y < 0 || field.center_y > 1080
    || field.radius < 1 || field.radius > 4096 || field.offset_x < -1920 || field.offset_x > 1920
    || field.offset_y < -1080 || field.offset_y > 1080 || field.scale_percent < 25 || field.scale_percent > 400
    || field.rotation_deg < -360 || field.rotation_deg > 360 || typeof field.fill !== "string" || !hex.test(field.fill))
    throw new Error("Procedural influence exceeds its position, style or CPU bounds.");
}
/** Half-pixel distance and Q10_000 weights exactly match the Rust integer profile. */
function influenceDistance(value: number): number {
  let low = 0, high = 8192;
  while (low <= high) {
    const middle = Math.floor((low + high) / 2);
    if (middle * middle <= value) low = middle + 1; else high = middle - 1;
  }
  return high;
}
function influencedItem(x: number, y: number, config: ProceduralConfig) {
  const item = { x, y, size: config.size, rotation: 0, fill: config.fill }, field = config.influence;
  if (!field) return item;
  const centerX2 = x * 2 + config.size, centerY2 = y * 2 + config.size;
  const dx = centerX2 - field.center_x * 2, dy = centerY2 - field.center_y * 2;
  const distance = field.shape === "horizontal" ? Math.abs(dx) : field.shape === "vertical" ? Math.abs(dy) : influenceDistance(dx * dx + dy * dy);
  const radius = field.radius * 2;
  let weight = Math.floor(Math.max(0, radius - distance) * 10000 / radius);
  if (field.falloff === "smoothstep") weight = Math.floor(weight * weight * (30000 - 2 * weight) / 100000000);
  if (weight === 0) return item;
  const size = config.size + Math.trunc(config.size * (field.scale_percent - 100) * weight / 1000000);
  if (size < 4 || size > 128) throw new Error("Procedural influence must keep every object between 4 and 128 pixels.");
  item.size = size; item.rotation = Math.trunc(field.rotation_deg * weight / 10000);
  item.x = (centerX2 - size) / 2 + Math.trunc(field.offset_x * weight / 10000);
  item.y = (centerY2 - size) / 2 + Math.trunc(field.offset_y * weight / 10000);
  if (config.fill.toUpperCase() !== field.fill.toUpperCase()) {
    item.fill = "#" + [0, 1, 2].map(index => {
      const from = parseInt(config.fill.slice(1 + index * 2, 3 + index * 2), 16), to = parseInt(field.fill.slice(1 + index * 2, 3 + index * 2), 16);
      return (from + Math.trunc((to - from) * weight / 10000)).toString(16).padStart(2, "0").toUpperCase();
    }).join("");
  }
  const half = (config.shape ?? "square") === "square" && item.rotation % 90 !== 0 ? Math.ceil(size * 141422 / 200000) : size / 2;
  const centerX = item.x + size / 2, centerY = item.y + size / 2, entrance = config.reveal_step_frames > 0 ? 12 : 0;
  if (centerX - half < 0 || centerY - half < 0 || centerX + half > 1920 || centerY + half + entrance > 1080)
    throw new Error("Procedural influence or entrance exceeds the 1920x1080 authored stage; no clipping was applied.");
  return item;
}
export async function realizeProceduralField(id:string,input:ProceduralConfig):Promise<CanvasNode[]> {
  const c=normalizedProceduralConfig(input);
  validateProceduralConfig(c); // reject cost and frame bounds before hashing/allocating
  const columns=fieldColumns(c),rows=Math.ceil(c.count/columns);
  const cellW=Math.floor(c.area_width/columns),cellH=Math.floor(c.area_height/rows);
  const path=c.distribution==="path"?pathSegments(c):null;
  const nodes:CanvasNode[]=[];
  for(let index=0;index<c.count;index++){
    let x:number,y:number;
    if(path){
      ({x,y}=pathPosition(index,c,path));
    }else if(c.distribution==="radial" || c.distribution==="spiral"){
      ({x,y}=orbitPosition(index,c));
    }else{
      const col=index%columns,row=Math.floor(index/columns);
      const freeX=cellW-c.size,freeY=cellH-c.size;
      let dx=Math.floor(freeX/2),dy=Math.floor(freeY/2);
      if(c.distribution==="staggered"){
        const quarter=Math.floor(freeX/4);
        dx=row%2===0?dx+quarter:dx-quarter;
      }else if(c.distribution==="scatter"){
        dx=proceduralHash(c.seed,index,0x71a5029b)%(freeX+1);
        dy=proceduralHash(c.seed,index,0xda39c617)%(freeY+1);
      }
      x=c.origin_x+col*cellW+dx;y=c.origin_y+row*cellH+dy;
    }
    const influenced = influencedItem(x, y, c);
    x = influenced.x; y = influenced.y;
    nodes.push({
      id:await proceduralNodeId(id,index),
      name:`ProceduralField / item ${String(index).padStart(3,"0")}`,
      kind:c.shape==="circle"?"circle":"rectangle",parent_id:null,
      x,y,
      width:influenced.size,height:influenced.size,rotation_deg:influenced.rotation,opacity:c.opacity_percent/100,text:null,
      coordinate_space:"project_pixels",z_index:-32,
      style:{fill:influenced.fill,stroke:null,stroke_width:0,font_family:null,font_size:null,font_weight:null,line_height:null,blend_mode:"normal"},
      relations:[],property_locks:[],keyframes: c.reveal_step_frames===0?[]:(()=>{
        const start=index*c.reveal_step_frames,end=start+c.reveal_duration_frames;
        const keys:CanvasNode["keyframes"]=[];
        for(const property of ["y","opacity"] as const){
          const initial=property==="y"?y+12:0,terminal=property==="y"?y:1;
          keys.push({at:{num:"0",den:"1"},property,value:initial,interpolation:"hold"});
          if(start>0)keys.push({at:frameTime(start),property,value:initial,interpolation:"hold"});
          keys.push({at:frameTime(end),property,value:terminal,interpolation:"ease_out_cubic"});
        }
        return keys;
      })(),
    });
  }
  return nodes;
}
/** New authoring must fit the entire declared entrance. Keep the v1 generator
 * unchanged so stored baselines remain readable and detachable without migration. */
export function validateProceduralAuthoringBounds(nodes:readonly CanvasNode[]):void {
  if(nodes.some(node=>node.keyframes.some(key=>key.property==="y" && key.value+node.height>1080)))
    throw new Error("Procedural entrance exceeds 1920x1080 authored stage; move the field upward or disable sequencing.");
}
type ProceduralChange =
  | {type:"upsert_procedural_field";instance_id:string;scene_id:string;config:ProceduralConfig}
  | {type:"detach_procedural_field";instance_id:string};
function assertUnlocked(p:Project, sceneId:string, kinds:string[]) {
  if(p.locks.some(lock => (lock.resource===`project:${p.id}` || lock.resource===`scene:${sceneId}`)
      && kinds.includes(lock.kind))) throw new Error("Procedural scope is locked.");
}
/** Stages all mutations on the cloned editorial Project; the existing service is authoritative. */
export async function applyProceduralChange(p:Project,change:ProceduralChange):Promise<void> {
  const design:ProductionDesign=structuredClone(p.production_design??{plan:null,heroes:[],capsules:[],patches:[],procedural_fields:[]});
  const fields=design.procedural_fields??[];
  const existing=fields.find(field=>field.id===change.instance_id);
  if(change.type==="detach_procedural_field"){
    if(!existing) throw new Error("Procedural field not found.");
    assertUnlocked(p,existing.scene_id,["content"]);
    design.procedural_fields=fields.filter(field=>field.id!==change.instance_id);
    p.production_design=design;return; // nodes are still normal editable Canvas nodes
  }
  assertUnlocked(p,change.scene_id,["content","position","style","timing","renderer"]);
  const scene=p.scenes.find(s=>s.id===change.scene_id);
  if(!scene || scene.renderer!=="motion-canvas") throw new Error("Procedural fields require a Motion Canvas scene.");
  const config=normalizedProceduralConfig(change.config);
  validateProceduralConfig(config);
  // Reject overlong sequences before hashing/allocating Canvas nodes.
  if(config.reveal_step_frames>0){
    const last=(config.count-1)*config.reveal_step_frames+config.reveal_duration_frames;
    if(BigInt(last)*BigInt(scene.duration.den)>=BigInt(scene.duration.num)*30n)
      throw new Error("Procedural sequence exceeds scene duration; shorten step/duration or extend scene.");
  }
  const baseline=await realizeProceduralField(change.instance_id,config);
  validateProceduralAuthoringBounds(baseline);
  let result=structuredClone(scene.nodes);
  if(existing){
    if(existing.generator_version!==1 || !sameValue(existing.baseline,await realizeProceduralField(existing.id,existing.config)))
      throw new Error("Procedural generator version or baseline mismatch.");
    if(existing.scene_id!==scene.id) throw new Error("Procedural field cannot move scenes via update.");
    const overlap=Math.min(existing.baseline.length,baseline.length);
    result=mergeComponentNodes(existing.baseline.slice(0,overlap),result,baseline.slice(0,overlap));
    for(const old of existing.baseline.slice(overlap)){
      const live=result.find(n=>n.id===old.id);
      if(!live || !sameValue(live,old)) throw new Error("Cannot shrink procedural field over human edits or deleted nodes; detach first.");
      if(result.some(n=>n.parent_id===old.id || n.relations.some(rel=>rel.target_id===old.id)))
        throw new Error("Procedural shrink would orphan references.");
    }
    const removed=new Set(existing.baseline.slice(overlap).map(n=>n.id));
    result=result.filter(n=>!removed.has(n.id));
  }else if(fields.length>=16 || fields.some(field=>field.scene_id===scene.id)){
    throw new Error("One procedural field per scene; at most 16 per project.");
  }
  const overlap=existing?Math.min(existing.baseline.length,baseline.length):0;
  const seen=new Set(result.map(n=>n.id));
  for(const node of baseline.slice(overlap)){
    if(seen.has(node.id)) throw new Error("Procedural node identity collision.");
    seen.add(node.id);result.push(node);
  }
  const instance:ProceduralFieldInstance={id:change.instance_id,scene_id:scene.id,
    generator_version:1,config:structuredClone(config),baseline:structuredClone(baseline)};
  scene.nodes=result;scene.status="draft";
  design.procedural_fields=[...fields.filter(field=>field.id!==change.instance_id),instance];
  p.production_design=design;
}
