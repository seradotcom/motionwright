import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import NativeInspectionWorkbench from "../../src/NativeInspectionWorkbench";
import { fixtureBootstrap } from "../../src/fixture";
import type { Change, MotionCanvasRenderEvidence, Project, ProjectEvent } from "../../src/types";

// Isolated interaction fixture only. Native IPC and the Project response here
// are synthetic. Real grants/StudioService/SQLite use the separate service spec.
type Harness = { project:Project; profile:number; calls:number; mode:string; release:(()=>void)|null; events:ProjectEvent[] };
const harness:Harness={project:structuredClone(fixtureBootstrap.project),profile:0,calls:0,mode:new URLSearchParams(location.search).get("mode")??"normal",release:null,events:[]};
Object.assign(window,{__NATIVE_FINDING_TEST__:harness,__TAURI_INTERNALS__:{invoke:async(command:string)=>{
  if(command==="preview_native_frame"){
    const profile=harness.project.deliverables[harness.profile],canvas=document.createElement("canvas");
    canvas.width=profile.width;canvas.height=profile.height;const context=canvas.getContext("2d")!;
    context.fillStyle="#283441";context.fillRect(0,0,canvas.width,canvas.height);
    return Uint8Array.from(atob(canvas.toDataURL("image/png").split(",")[1]),character=>character.charCodeAt(0));
  }
  if(command==="bootstrap"||command==="open_project")return {...structuredClone(fixtureBootstrap),project:structuredClone(harness.project)};
  if(command==="project_history")return structuredClone(harness.events);
  throw new Error("Synthetic native-finding interaction fixture refuses operation: "+command);
}}});
function evidence(project:Project,profileIndex:number):MotionCanvasRenderEvidence {
  const profile=project.deliverables[profileIndex];
  return {project_resource:"project:"+project.id,generation:project.generation,revision:project.revision,deliverable_id:profile.id,
    frame_rate:{num:30,den:1},segments:[{segment_id:"synthetic-native-finding",scene_ids:project.scenes.map(scene=>scene.id),frame_count:3600,
      plan_ref:"synthetic-only",fingerprint:"synthetic-only",job_ref:"synthetic-only",artifact:{},verification:{}}],
    preview:[{token:"synthetic-native-finding-token",segment_id:"synthetic-native-finding",scene_ids:project.scenes.map(scene=>scene.id),frame_count:3600}],
  } as MotionCanvasRenderEvidence;
}
function Harness() {
  const [project,setProject]=useState(harness.project),[visible,setVisible]=useState(true),[sceneIndex,setSceneIndex]=useState(0),[profileIndex,setProfileIndex]=useState(0);
  harness.project=project;harness.profile=profileIndex;
  const commit=async(change:Change):Promise<Project>=>{
    harness.calls++;
    if(change.type!=="add_review")throw new Error("Interaction fixture accepts only add_review.");
    if(harness.mode==="void")return undefined as unknown as Project; // Deliberate malformed legacy ACK.
    const next=structuredClone(project);next.revision++;
    next.reviews.push({id:crypto.randomUUID(),kind:change.kind,anchor:{resource:change.resource,branch_id:project.active_branch,revision:project.revision,
      start:change.start,end:change.end,locale:change.locale,profile_id:change.profile_id},body:change.body,status:"open",resolution:null,
      created_at:new Date().toISOString(),resolved_at:null});
    if(harness.mode==="held")await new Promise<void>(resolve=>{harness.release=resolve;});
    harness.events.push({revision:next.revision,change,created_at:new Date().toISOString()});
    harness.project=next;setProject(next);return structuredClone(next);
  };
  return <main><h1>Isolated synthetic finding interaction fixture</h1>
    <p>This page proves local UI behavior only. It is not native rendering or service persistence evidence.</p>
    <nav><button onClick={()=>setVisible(false)}>Navigate away</button><button onClick={()=>setVisible(true)}>Return to inspection</button>
      <button onClick={()=>setSceneIndex(index=>index===0?1:0)}>Switch scene</button><button onClick={()=>setProfileIndex(index=>index===0?1:0)}>Switch profile</button>
      <button onClick={()=>harness.release?.()}>Release original response</button></nav>
    {visible&&<NativeInspectionWorkbench project={project} scene={project.scenes[sceneIndex]} displayProfile={project.deliverables[profileIndex]}
      evidence={evidence(project,profileIndex)} busy={false} onSeek={()=>undefined} onOpenRender={()=>undefined} onDraftRepair={()=>undefined}
      // The assertion adapts the legacy Promise<void> prop for the causal RED.
      // GREEN's product callsite uses Promise<Project> without this test adapter.
      commit={commit as unknown as React.ComponentProps<typeof NativeInspectionWorkbench>["commit"]}/>}
  </main>;
}
createRoot(document.getElementById("root")!).render(<Harness/>);
