import { useState } from "react";
import { createRoot } from "react-dom/client";
import CompositionStudy from "../../src/CompositionStudy";
import ProfileCompositionStudy from "../../src/ProfileCompositionStudy";
import { fixtureProject } from "../../src/fixture";
import type { BlendMode, CanvasNode } from "../../src/types";
const base: CanvasNode = {...structuredClone(fixtureProject.scenes[0].nodes[0]),
  id:"foreground",kind:"rectangle",parent_id:null,keyframes:[],relations:[],coordinate_space:"project_pixels",
  x:25,y:25,width:50,height:50,z_index:2,rotation_deg:0,opacity:.5,
  style:{...fixtureProject.scenes[0].nodes[0].style,fill:"#80C040",stroke:null,stroke_width:0,blend_mode:"normal"}};
function Fixture() {
  const [mode,setMode]=useState<BlendMode>("normal"),[clipped,setClipped]=useState(false);
  const [transformed,setTransformed]=useState(false),[transparent,setTransparent]=useState(false);
  const [raised,setRaised]=useState(false),[framed,setFramed]=useState(false);
  const clip={...base,id:"clip",kind:"clip_group",x:40,y:20,width:20,height:60,opacity:1,
    z_index:raised?2:1,rotation_deg:transformed?15:0,style:{...base.style,blend_mode:"normal" as const}};
  const under={...base,id:"under",x:0,y:0,width:100,height:100,z_index:raised?1:4,
    opacity:1,style:{...base.style,fill:"#223344",blend_mode:"normal" as const}};
  const leaf={...base,parent_id:clipped?clip.id:null,style:{...base.style,blend_mode:mode}};
  const project=structuredClone(fixtureProject);
  project.visual_language.palette=[{name:"Background",value:"#406080"},{name:"surface",value:"#FFFFFF"}];
  project.scenes[0].nodes=[];
  project.scenes[0].camera={center_x:960,center_y:540,zoom:1,rotation_deg:0,safe_margin:0};
  project.production_design=undefined;
  return <main style={{background:"#FF0000"}}>
    <label>Mode <select aria-label="Fixture blend" value={mode} onChange={e=>setMode(e.target.value as BlendMode)}>
      {["normal","multiply","screen","add"].map(value=><option key={value}>{value}</option>)}
    </select></label>
    <label><input type="checkbox" checked={clipped} onChange={e=>setClipped(e.target.checked)}/>Clip object</label>
    <label><input type="checkbox" checked={transformed} onChange={e=>setTransformed(e.target.checked)}/>Transform group</label>
    <label><input type="checkbox" checked={transparent} onChange={e=>setTransparent(e.target.checked)}/>Transparent surface</label>
    <label><input type="checkbox" checked={raised} onChange={e=>setRaised(e.target.checked)}/>Add lower layer</label>
    <label><input type="checkbox" checked={framed} onChange={e=>setFramed(e.target.checked)}/>Move camera</label>
    <div style={{width:200,height:200}}>
      <CompositionStudy nodes={[...(clipped?[clip]:[]),leaf,...(raised?[under]:[])]} width={100} height={100}
        time={0} background={transparent?"#40608080":"#406080"} label="Authored composition blend"
        camera={framed?{center_x:60,center_y:50,zoom:1,rotation_deg:0,safe_margin:0}:undefined}/>
    </div>
    <ProfileCompositionStudy project={project} scene={project.scenes[0]}
      profile={{...project.deliverables[0],width:320,height:320,framing_strategy:"replan"}}
      time={0} label="Saved output background"/>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);
