// Synthetic IPC interface responses only. This page never runs a Native job,
// creates authority, copies media or proves a physical hybrid render.
import {StrictMode} from "react";
import {flushSync} from "react-dom";
import {createRoot} from "react-dom/client";
import DeliveryProfiles from "../../src/DeliveryProfiles";
import {fixtureBootstrap} from "../../src/fixture";
import type {Project} from "../../src/types";

interface Call {command:string;request:Record<string,unknown>}
interface Harness {
  calls():Call[];holdGrant():void;resolveGrant():void;unknownMaster():void;
  changeRevision():void;holdMaster():void;resolveMaster():void;
}
declare global {interface Window {__HYBRID_DELIVERY__:Harness}}
let project:Project=structuredClone(fixtureBootstrap.project);
project.locks=[];project.revision=20;
const layout=new URLSearchParams(location.search).get("layout");
const twoSource=layout==="manim-first";
const grouped=layout==="grouped-motion";
const sceneCount=grouped?5:twoSource?2:3;
const duration=grouped?1:2;
const frameCount=sceneCount*duration*30;
const manimIndex=grouped?2:twoSource?0:1;
const savedScenes=project.scenes;
project.scenes=Array.from({length:sceneCount},(_,index)=>savedScenes[index]??{
  ...savedScenes[0],id:"10000000-0000-4000-8000-"+String(index).padStart(12,"0"),
});
project.scenes.forEach((scene,index)=>{
  scene.renderer=index===manimIndex?"manim-community":"motion-canvas";
  scene.start={num:String(index*duration),den:"1"};scene.duration={num:String(duration),den:"1"};
});
for(const branch of project.branches)if(branch.id===project.active_branch)branch.head_revision=20;
const profile=project.deliverables[0];
profile.width=1920;profile.height=1080;profile.frame_rate={num:"30",den:"1"};
profile.included_scene_ids=[];profile.video_codec="h264";profile.audio_codec="aac";
profile.audio_sample_rate_hz=48000;profile.color_space="rec709";profile.container="mp4";
profile.captions=false;profile.burn_in_captions=false;
project.production_design={...(project.production_design??{plan:null,heroes:[],capsules:[]}),
  delivery_recipes:{[profile.id]:{schema:"motionwright.delivery-recipe/1",
    scene_intents:project.scenes.map(scene=>({scene_id:scene.id,role:"evidence",archetype:"statement"})),
    original_mix:null}}};
const calls:Call[]=[];
const grant="11111111-1111-4111-8111-111111111111";
const mux="22222222-2222-4222-8222-222222222222";
let holdGrant=false,grantReply:(()=>void)|null=null,unknown=false;
let holdMaster=false,masterReply:(()=>void)|null=null;
const hint=()=>({mux_receipt_id:mux,master_sha256:"c".repeat(64),frame_count:frameCount,fps_num:30,fps_den:1});
const scope=(request:Record<string,unknown>)=>({
  project_id:request.project_id,generation:request.generation,revision:request.revision,
  deliverable_id:request.deliverable_id,
});
Object.assign(window,{__TAURI_INTERNALS__:{invoke:async(command:string,args:{request?:Record<string,unknown>}={})=>{
  const request=structuredClone(args.request??{});
  calls.push({command,request});
  if(command==="production_runtime_status")return {
    status:"ready",version_compatible:true,expected_version:"1.0.0",observed_version:"1.0.0",
    executable_sha256:"d".repeat(64),connection_identity:"e".repeat(64),
    pinned_revision:"c2c40c74e5714a3e680415d0dddcf27743f0f0fa",
  };
  if(command==="native_hybrid_preflight")return {
    schema:"motionwright.hybrid-source-preflight/1",...scope(request),
    project_sha256:"a".repeat(64),source_audio_sha256:"b".repeat(64),
    source_count:twoSource?2:3,frame_count:frameCount,width:1920,height:1080,fps_num:30,fps_den:1,
  };
  if(command==="issue_effect_grant"){
    if(holdGrant&&request.effect==="render_local"){
      holdGrant=false;return new Promise(resolve=>{grantReply=()=>resolve({token:grant});});
    }
    return {token:grant};
  }
  if(command==="render_native_hybrid_master"){
    if(unknown)throw {schema:"motionwright.hybrid-desktop-failure/1",status:"unknown",stage:"master",
      outcome_known:false,may_have_native_effects:true,automatic_retry:false,message:"Synthetic original mux reply is unknown."};
    const completed={schema:"motionwright.hybrid-desktop-master/1",status:"completed",...scope(request),
      master_sha256:"c".repeat(64),frame_count:frameCount,fps_num:30,fps_den:1,saved_master:hint(),
      review_warning:null,automatic_retry:false};
    if(holdMaster){holdMaster=false;return new Promise(resolve=>{masterReply=()=>resolve(completed);});}
    return completed;
  }
  if(command==="review_native_av_master")throw new Error("Synthetic reader intentionally does not provide media.");
  if(command==="export_saved_native_av_master")return {
    destination:request.destination,size_bytes:1024,sha256:"c".repeat(64),revision:request.revision,
    deliverable_id:request.deliverable_id,source_current:true,integrity_manifest_path:null,
  };
  if(command==="native_av_saved_master_inventory")return {
    schema:"motionwright.saved-master-inventory/1",...scope(request),items:[hint()],truncated:false,
  };
  throw new Error("Unexpected synthetic IPC command: "+command);
}}});
const host=document.getElementById("hybrid-root");
if(!host)throw new Error("Missing fixture root");
const root=createRoot(host);
const outsideFixture=()=>{throw new Error("Outside synthetic hybrid delivery routing.");};
function render(){
  flushSync(()=>root.render(<StrictMode><DeliveryProfiles
    project={project} desktopMode commit={outsideFixture} commitRecipe={outsideFixture}
    currentProjectState={()=>({project,busy:false,serial:0})}
    renderEvidence={null} onRenderEvidence={outsideFixture}
    avEvidence={null} onAvEvidence={outsideFixture}
    multiAvEvidence={null} onMultiAvEvidence={outsideFixture}
    exportEvidence={null} onExportEvidence={outsideFixture} onOpenAudio={outsideFixture}
  /></StrictMode>));
}
window.__HYBRID_DELIVERY__={
  calls:()=>structuredClone(calls),
  holdGrant:()=>{holdGrant=true;},
  resolveGrant:()=>{const reply=grantReply;grantReply=null;if(!reply)throw new Error("No held grant");reply();},
  unknownMaster:()=>{unknown=true;},
  changeRevision:()=>{project={...project,revision:project.revision+1};render();},
  holdMaster:()=>{holdMaster=true;},
  resolveMaster:()=>{const reply=masterReply;masterReply=null;if(!reply)throw new Error("No held master");reply();},
};
render();
