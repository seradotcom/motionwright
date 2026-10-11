/** Closed ProductStage v1: original, author-attested, editable semantic UI.
 * Never renders third-party device models, logos, raw HTML, or a fake capture.
 * The Rust domain module is the source of transactional authority.
 */
import type { CanvasNode, Project, Scene, Change, CanvasKeyframe } from "./types";
import { emptyProductionDesign, mergeComponentNodes, sameValue } from "./creativeProduction";
import { rationalSeconds, seconds } from "./types";

export type StageFrame="browser"|"flat_panel"|"generic_handset";
export interface ProductStageConfig {
  frame:StageFrame;
  screen_heading:string;
  screen_detail:string;
  action_label:string;
  author_attestation:string;
  screen_surface:string;
  foreground:string;
  accent:string;
  focus_x_permille:number;
  focus_y_permille:number;
  animate_focus:boolean;
}
export interface ProductStageInstance {
  id:string;scene_id:string;component_version:number;
  config:ProductStageConfig;baseline:CanvasNode[];
}
export const PRODUCT_STAGE_DISCLOSURE="ORIGINAL UI GRAPHIC / NOT A PRODUCT CAPTURE";
export const STAGE_ROLES=[
  "frame","screen","chrome","chrome_indicator",
  "screen_heading","screen_detail","card","card_highlight",
  "action_button","action_text","focus_marker","focus_caption",
  "screen_caption","disclosure",
] as const;
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const rgb=/^#[0-9a-f]{6}$/i;
const control=/\p{Cc}/u;
export const defaultProductStageConfig=():ProductStageConfig=>({
  frame:"browser",
  screen_heading:"One clear workflow",
  screen_detail:"Author a useful interface study",
  action_label:"Explore",
  author_attestation:"",
  screen_surface:"#132431",
  foreground:"#F2F4F3",
  accent:"#86C8D1",
  focus_x_permille:510,
  focus_y_permille:620,
  animate_focus:false,
});
export function validateProductStageConfig(config:ProductStageConfig):void{
  const expected=Object.keys(defaultProductStageConfig()).sort();
  const actual=Object.keys(config).sort();
  if(expected.length!==actual.length || expected.some((key,index)=>actual[index]!==key))
    throw new Error("Unknown or missing ProductStage input; external assets and executable UI are not admitted.");
  if(!["browser","flat_panel","generic_handset"].includes(config.frame))throw new Error("ProductStage frame must be original and generic.");
  for(const [name,text,max] of [
    ["screen heading",config.screen_heading,40],
    ["screen detail",config.screen_detail,80],
    ["action label",config.action_label,20],
    ["source rights attestation",config.author_attestation,160],
  ] as const){
    if(typeof text!=="string" || !text.trim() || [...text].length>max || control.test(text))
      throw new Error("ProductStage "+name+" requires bounded, original authored content.");
  }
  if([config.screen_surface,config.foreground,config.accent].some(color=>!rgb.test(color)))
    throw new Error("ProductStage colors require #RRGGBB.");
  if(!Number.isInteger(config.focus_x_permille) || config.focus_x_permille<150 || config.focus_x_permille>850 ||
     !Number.isInteger(config.focus_y_permille) || config.focus_y_permille<200 || config.focus_y_permille>800)
    throw new Error("ProductStage focus must remain within authorized screen bounds.");
}
export async function stageNodeId(instance:string,role:string):Promise<string>{
  if(!uuid.test(instance))throw new Error("Invalid ProductStage identity.");
  const domain=new TextEncoder().encode("motionwright.product-stage.v1\0");
  const id=new Uint8Array(instance.replaceAll("-","").match(/../g)!.map(s=>parseInt(s,16)));
  const suffix=new TextEncoder().encode(role);
  const input=new Uint8Array(domain.length+id.length+suffix.length);
  input.set(domain);input.set(id,domain.length);input.set(suffix,domain.length+id.length);
  const hash=new Uint8Array(await crypto.subtle.digest("SHA-256",input));
  hash[6]=(hash[6]&15)|128;hash[8]=(hash[8]&63)|128;
  const hex=Array.from(hash.slice(0,16),b=>b.toString(16).padStart(2,"0")).join("");
  return [hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join("-");
}
interface Rect{x:number;y:number;w:number;h:number}
function sub(r:Rect,x:number,y:number,w:number,h:number):Rect{
  return {x:r.x+r.w*x,y:r.y+r.h*y,w:r.w*w,h:r.h*h};
}
function geometry(frame:StageFrame,w:number,h:number):{outer:Rect;screen:Rect}{
  const portrait=h>w,square=h===w;
  const outer:Rect=frame==="generic_handset" && portrait?
    {x:w*.17,y:h*.08,w:w*.66,h:h*.835} :
    frame==="generic_handset" && square?
    {x:w*.275,y:h*.07,w:w*.45,h:h*.85} :
    frame==="generic_handset"?
    {x:w*.365,y:h*.085,w:w*.27,h:h*.83} :
    portrait?{x:w*.075,y:h*.175,w:w*.85,h:h*.63} :
    square?{x:w*.09,y:h*.18,w:w*.82,h:h*.64} :
    {x:w*.15,y:h*.14,w:w*.70,h:h*.72};
  return {
    outer,
    screen:frame==="browser"?sub(outer,.035,.10,.93,.855):
      frame==="flat_panel"?sub(outer,.035,.053,.93,.894):
      sub(outer,.075,.075,.85,.855),
  };
}
function focusKeys(node:CanvasNode):CanvasKeyframe[]{
  return ([
    ["y",node.y+16,node.y],["opacity",0,1]
  ] as const).flatMap(([property,initial,terminal])=>[
    {at:rationalSeconds(0),property,value:initial,interpolation:"hold" as const},
    {at:{num:"3",den:"10"},property,value:terminal,interpolation:"ease_out_cubic" as const},
  ]);
}
export async function realizeProductStage(id:string,config:ProductStageConfig,w=1920,h=1080):Promise<CanvasNode[]>{
  if(!uuid.test(id))throw new Error("Invalid ProductStage identity.");
  validateProductStageConfig(config);
  if(!Number.isInteger(w)||!Number.isInteger(h)||w<320||h<320||w>7680||h>7680)
    throw new Error("ProductStage output dimensions out of bounds.");
  const {outer,screen}=geometry(config.frame,w,h),scale=Math.min(w,h)/1080;
  const fx=screen.x+screen.w*config.focus_x_permille/1000;
  const fy=screen.y+screen.h*config.focus_y_permille/1000;
  const focusSize=18*scale,calloutX=Math.min(fx+24*scale,screen.x+screen.w*.84);
  const caption=config.frame==="browser"?"GENERIC BROWSER":config.frame==="flat_panel"?"GENERIC PANEL":"GENERIC HANDSET";
  const textFont=Math.max(10,Math.min(32*scale,screen.w*.052));
  const detailFont=Math.max(8,Math.min(20*scale,screen.w*.033));
  const actionFont=Math.max(8,Math.min(18*scale,screen.w*.039));
  const entries:Array<[string,"rectangle"|"circle"|"text",Rect,number,string,string|null,number,number]>=[
    ["frame","rectangle",outer,5,"#223542",null,0,400],
    ["screen","rectangle",screen,6,config.screen_surface,null,0,400],
    ["chrome","rectangle",sub(screen,0,0,1,.062),7,"#2B4552",null,0,400],
    ["chrome_indicator","circle",sub(screen,.035,.018,.015,.028),8,config.accent,null,0,400],
    ["screen_heading","text",sub(screen,.08,.14,.84,.11),9,config.foreground,config.screen_heading,textFont,600],
    ["screen_detail","text",sub(screen,.08,.27,.84,.09),10,config.foreground,config.screen_detail,detailFont,400],
    ["card","rectangle",sub(screen,.08,.41,.84,.27),11,"#29404B",null,0,400],
    ["card_highlight","rectangle",sub(screen,.115,.465,.23,.115),12,config.accent,null,0,400],
    ["action_button","rectangle",sub(screen,.08,.77,.36,.10),13,config.accent,null,0,400],
    ["action_text","text",sub(screen,.10,.79,.29,.055),14,"#132431",config.action_label,actionFont,600],
    ["focus_marker","circle",{x:fx-focusSize/2,y:fy-focusSize/2,w:focusSize,h:focusSize},15,"#F7C87B",null,0,400],
    ["focus_caption","text",{x:calloutX,y:fy-11*scale,w:screen.w*.12,h:26*scale},16,config.foreground,"FOCUS",12*scale,600],
    ["screen_caption","text",sub(screen,.08,.91,.82,.04),17,config.foreground,caption,12*scale,500],
    ["disclosure","text",{x:w*.11,y:h*.905,w:w*.78,h:24*scale},18,config.foreground,PRODUCT_STAGE_DISCLOSURE,14*scale,400],
  ];
  const nodes=await Promise.all(entries.map(async([role,kind,rect,z,fill,text,font,weight]):Promise<CanvasNode>=>{
    const isText=kind==="text";
    const node:CanvasNode={
      id:await stageNodeId(id,role),name:"ProductStage / "+role,kind,parent_id:null,
      x:rect.x,y:rect.y,width:rect.w,height:rect.h,rotation_deg:0,opacity:1,text,
      coordinate_space:"project_pixels",z_index:z,
      style:{fill,stroke:null,stroke_width:0,
        font_family:isText?"Instrument Sans Variable":null,
        font_size:isText?font:null,font_weight:isText?weight:null,
        line_height:isText?1.12:null,blend_mode:"normal"},
      relations:[],property_locks:[],keyframes:[],
    };
    if(config.animate_focus && (role==="focus_marker"||role==="focus_caption"))node.keyframes=focusKeys(node);
    return node;
  }));
  for(const node of nodes.filter(n=>n.kind==="text")){
    if((node.text?.length??0)*(node.style.font_size??0)*.64>node.width*.95)
      throw new Error("ProductStage "+node.name+" copy does not fit its screen anchor; shorten the original content.");
  }
  if(nodes.length!==STAGE_ROLES.length ||
    nodes.some(n=>n.x<w*.04 || n.y<h*.04 || n.x+n.width>w*.96 || n.y+n.height>h*.95 || n.width<=0 || n.height<=0))
    throw new Error("ProductStage subjects exceed native safe area.");
  return nodes;
}
export async function validateProductStages(project:Project):Promise<void>{
  const stages=project.production_design?.stages??[];
  if(stages.length>16)throw new Error("Too many ProductStage instances.");
  const ids=new Set<string>(),scenes=new Set<string>();
  for(const stage of stages){
    if(ids.has(stage.id) || scenes.has(stage.scene_id) ||
      (project.production_design?.heroes??[]).some(hero=>hero.scene_id===stage.scene_id))
      throw new Error("ProductStage identity or scene is duplicated.");
    ids.add(stage.id);scenes.add(stage.scene_id);
    const scene=project.scenes.find(s=>s.id===stage.scene_id && s.renderer==="motion-canvas");
    if(!scene)throw new Error("ProductStage requires a valid Motion Canvas scene.");
    const base=await realizeProductStage(stage.id,stage.config);
    if(stage.component_version!==1 || !sameValue(stage.baseline,base))
      throw new Error("ProductStage baseline differs from exact source realization.");
    const disclosureId=await stageNodeId(stage.id,"disclosure");
    const disclosure=scene.nodes.find(n=>n.id===disclosureId);
    if(disclosure?.kind!=="text" || disclosure.text!==PRODUCT_STAGE_DISCLOSURE)
      throw new Error("ProductStage original-graphic disclosure cannot be changed while bound.");
    if(base.some(node=>!scene.nodes.some(n=>n.id===node.id)))
      throw new Error("ProductStage source node removed; detach first.");
  }
}
function unlocked(project:Project,sceneId:string,kind:("content"|"position"|"style"|"timing"|"renderer")[]):void{
  if(project.locks.some(lock=>["project:"+project.id,"scene:"+sceneId].includes(lock.resource)&&kind.includes(lock.kind)))
    throw new Error("ProductStage scene or project is locked.");
}
export type StageChange=Extract<Change,{type:"upsert_product_stage"|"detach_product_stage"}>;
export async function applyProductStageChange(project:Project,change:StageChange):Promise<void>{
  const design=project.production_design??emptyProductionDesign();
  const stages=design.stages??[];
  if(change.type==="upsert_product_stage"){
    unlocked(project,change.scene_id,["content","position","style","timing","renderer"]);
    const scene=project.scenes.find(s=>s.id===change.scene_id);
    if(!scene || scene.renderer!=="motion-canvas" || seconds(scene.duration)<2)
      throw new Error("ProductStage needs a Motion Canvas scene of at least two seconds.");
    const baseline=await realizeProductStage(change.instance_id,change.config);
    const index=stages.findIndex(stage=>stage.id===change.instance_id);
    if(index>=0){
      if(stages[index].scene_id!==scene.id)throw new Error("ProductStage cannot move scenes via update.");
      scene.nodes=mergeComponentNodes(stages[index].baseline,scene.nodes,baseline);
    }else{
      if(stages.length>=16 || stages.some(stage=>stage.scene_id===scene.id) ||
        design.heroes.some(hero=>hero.scene_id===scene.id) ||
        scene.nodes.some(n=>baseline.some(b=>b.id===n.id)))
        throw new Error("ProductStage capacity or component identity collision.");
      scene.nodes.push(...structuredClone(baseline));
    }
    const staged:ProductStageInstance={
      id:change.instance_id,scene_id:scene.id,component_version:1,
      config:structuredClone(change.config),baseline:structuredClone(baseline),
    };
    if(index>=0)stages[index]=staged;else stages.push(staged);
    scene.status="draft";
  }else{
    const stage=stages.find(stage=>stage.id===change.instance_id);
    if(!stage)throw new Error("ProductStage instance not found.");
    unlocked(project,stage.scene_id,["content"]);
    design.stages=stages.filter(stage=>stage.id!==change.instance_id);
  }
  if(change.type==="upsert_product_stage")design.stages=stages;
  project.production_design=design;
}
