import { useState } from "react";
import { createRoot } from "react-dom/client";
import ManimEditableSourceWorkbench from "../../src/ManimEditableSourceWorkbench";
import { MANIM_EDITABLE_MIME, MANIM_CONTROLS_RATIONALE } from "../../src/manimEditableSource";
import { emptyProductionDesign } from "../../src/creativeProduction";
import { fixtureProject } from "../../src/fixture";
import type { Change, Project } from "../../src/types";
import "../../src/production-design.css";
const original="50000000-0000-4000-8000-000000000001",edited="50000000-0000-4000-8000-000000000002";
const initial=structuredClone(fixtureProject);
initial.scenes[0].renderer="manim-community";initial.locks=[];
initial.production_design=emptyProductionDesign();
initial.assets=[
  {id:original,name:"original.mwedit.zip",media_type:MANIM_EDITABLE_MIME,content_sha256:"a".repeat(64),source_revision:null},
  {id:edited,name:"edited.mwedit.zip",media_type:MANIM_EDITABLE_MIME,content_sha256:"b".repeat(64),source_revision:null},
];
const before=structuredClone(initial.scenes[0].nodes[0]);
let live=initial,applies=0,altered=false,dropReply=false;
const runtime=window as unknown as {
  __TAURI_INTERNALS__:{invoke:(command:string,args:Record<string,unknown>)=>Promise<unknown>};
  __MANIM_SIMULATE_CHANGE__:(project:Project,change:Change)=>Promise<Project>;
  __MANIM_COMPONENT__:{project:()=>Project;applies:()=>number};
};
runtime.__MANIM_COMPONENT__={project:()=>structuredClone(live),applies:()=>applies};
// Explicit synthetic read transport. No imported Python, Native engine, grant,
// Store or real source package is fabricated by this component fixture.
runtime.__TAURI_INTERNALS__={invoke:async(command,args)=>{
  const request=args.request as Record<string,any>;
  if(command==="inspect_attached_manim_editable_source")return {
    schema:"motionwright.manim-editable-source-attached/1",source:request.source,
    asset_id:request.asset_id,source_sha256:request.expected_sha256,
    original_plan_sha256:"c".repeat(64),current_plan_sha256:"d".repeat(64),
    origin_revision:initial.revision,revision_only_rebound:live.revision!==initial.revision,
    node_ids:[before.id],editable_parameters:["one_node.transform","one_node.text","one_node.style","one_node.controls"],
    native_execution_performed:false,imported_source_authority_used:false,
  };
  if(command==="preview_manim_editable_source_edit") {
    const change:Change={type:"apply_creative_patch",patch:{scene_id:request.source.scene_id,
      base_revision:request.source.revision,rationale:MANIM_CONTROLS_RATIONALE,edits:[
        {kind:"transform",node_id:before.id,transform:{x:before.x+12,y:before.y+20,width:before.width,
          height:before.height,rotation_deg:7,opacity:.8}},
        {kind:"text",node_id:before.id,text:"Returned complete object"},
        {kind:"style",node_id:altered?original:before.id,style:{...before.style,fill:"#86C8D1",font_size:40}},
      ]}};
    return {schema:"motionwright.manim-editable-source-edit/1",request,preview:{
      expected:{resource:"project:"+live.id,generation:live.generation,revision:live.revision},
      original_source_asset_id:request.original_asset_id,edited_source_asset_id:request.edited_asset_id,
      original_source_sha256:request.original_sha256,edited_source_sha256:request.edited_sha256,
      current_plan_sha256:"d".repeat(64),proposed_plan_sha256:"e".repeat(64),change,
      native_execution_performed:false,imported_source_authority_used:false,
    }};
  }
  throw new Error("Unexpected synthetic Manim command: "+command);
}};
function Fixture() {
  const [project,setProject]=useState(initial),[shown,setShown]=useState(true);
  const commit=async(change:Change)=>{
    const saved=await runtime.__MANIM_SIMULATE_CHANGE__(live,change);applies++;
    live=saved;setProject(saved);
    if(dropReply)throw new Error("Synthetic reply lost after source commit");
    return saved;
  };
  return <main>
    <h1>Synthetic Manim source review component</h1>
    <output aria-label="Source revision">r{project.revision}</output>
    <button onClick={()=>setShown(value=>!value)}>{shown?"Close":"Reopen"} source review</button>
    <button onClick={()=>{altered=true}}>Return an unrelated source object</button>
    <button onClick={()=>{dropReply=true}}>Lose the next commit reply</button>
    <button disabled={!project.production_design?.patches?.length} onClick={()=>void commit({
      type:"undo_creative_patch",patch_id:project.production_design!.patches!.at(-1)!.id,
    })}>Undo saved object patch</button>
    {shown&&<ManimEditableSourceWorkbench project={project} scene={project.scenes[0]} profile={project.deliverables[0]}
      busy={false} commit={commit} refreshProject={async()=>live}/>}
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);
