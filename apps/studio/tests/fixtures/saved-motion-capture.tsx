import {StrictMode} from "react";
import {flushSync} from "react-dom";
import {createRoot} from "react-dom/client";
import DeliveryProfiles from "../../src/DeliveryProfiles";
import {applyChange} from "../../src/api";
import {fixtureBootstrap} from "../../src/fixture";
import {defaultOriginalMix} from "../../src/deliveryRecipe";
import type {MotionCanvasRenderEvidence,Project} from "../../src/types";

type Call={id:number;command:string;args:Record<string,unknown>|null};
export interface SavedMotionHarness {
  project():Project;calls():Call[];adopted():MotionCanvasRenderEvidence[];
  resolve(id:number,value:unknown):Promise<void>;
  scope(patch:Partial<Pick<Project,"generation"|"revision"|"active_branch">>):Promise<void>;
  unmount():Promise<void>;
}
declare global {interface Window {__SAVED_MOTION__:SavedMotionHarness}}
let project=structuredClone(fixtureBootstrap.project);
project.locks=[];
for(const profile of project.deliverables.slice(0,2)){
  project=await applyChange(project,{type:"set_delivery_recipe",deliverable_profile_id:profile.id,
    recipe:{schema:"motionwright.delivery-recipe/1",original_mix:defaultOriginalMix(),
      scene_intents:project.scenes.filter(scene=>scene.renderer==="motion-canvas").map(scene=>({
        scene_id:scene.id,role:"hook",archetype:"statement",
      }))}});
}
const root=createRoot(document.getElementById("saved-motion-root")!);
const calls:Call[]=[],adopted:MotionCanvasRenderEvidence[]=[];
const replies=new Map<number,(value:unknown)=>void>();
let evidence:MotionCanvasRenderEvidence|null=null,mounted=true;
const paint=()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve())));
(window as unknown as {__TAURI_INTERNALS__:unknown}).__TAURI_INTERNALS__={
  invoke(command:string,args?:Record<string,unknown>){
    calls.push({id:calls.length+1,command,args:args??null});
    if(command==="saved_motion_capture_inventory"||command==="recover_saved_motion_capture"){
      const id=calls.length;
      return new Promise<unknown>(resolve=>replies.set(id,resolve));
    }
    return Promise.reject(new Error("This synthetic transport fixture admits only saved capture reads."));
  },
};
const denied=()=>{throw new Error("Unexpected effect in saved capture fixture.");};
function render(){
  flushSync(()=>root.render(<StrictMode>
    <p role="note">Actual Deliver components and guarded APIs with synthetic Native replies. No SQLite, renderer or product acceptance is claimed.</p>
    {mounted&&<DeliveryProfiles project={project} commit={async()=>denied()} commitRecipe={async()=>denied()}
      desktopMode currentProjectState={()=>({project,busy:false,serial:0})}
      renderEvidence={evidence} onRenderEvidence={value=>{adopted.push(value);evidence=value;render();}}
      avEvidence={null} onAvEvidence={denied} multiAvEvidence={null} onMultiAvEvidence={denied}
      exportEvidence={null} onExportEvidence={denied} onOpenAudio={denied}/>}
  </StrictMode>));
}
window.__SAVED_MOTION__={
  project:()=>structuredClone(project),calls:()=>structuredClone(calls),adopted:()=>structuredClone(adopted),
  async resolve(id,value){const reply=replies.get(id);if(!reply)throw new Error("No pending saved capture read");
    replies.delete(id);reply(value);await paint();},
  async scope(patch){project={...project,...patch};render();await paint();},
  async unmount(){mounted=false;render();await paint();},
};
render();
