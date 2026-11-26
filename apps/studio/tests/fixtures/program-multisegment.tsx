import {StrictMode,useState} from "react";
import {createRoot} from "react-dom/client";
import {PreviewSurface} from "../../src/App";
import {fixtureBootstrap} from "../../src/fixture";
import type {MotionCanvasRenderEvidence,NativeMultisegmentAvMasterEvidence} from "../../src/types";
import "../../src/styles.css";

const initial=structuredClone(fixtureBootstrap.project);
initial.scenes=initial.scenes.slice(0,2).map((scene,index)=>({...scene,
  name:index===0?"First original segment":"Second original segment",
  start:{num:String(index*2),den:"1"},duration:{num:"2",den:"1"}}));
const profile=initial.deliverables[0];
profile.frame_rate={num:"30",den:"1"};
profile.included_scene_ids=initial.scenes.map(scene=>scene.id);
const visual:MotionCanvasRenderEvidence={
  project_resource:"project:"+initial.id,generation:initial.generation,revision:initial.revision,
  deliverable_id:profile.id,frame_rate:{num:30,den:1},
  segments:initial.scenes.map((scene,index)=>({segment_id:"synthetic-segment-"+index,
    scene_ids:[scene.id],frame_count:60,plan_ref:"synthetic",fingerprint:"synthetic",
    job_ref:"synthetic",artifact:{},verification:{}})),
};
const original:NativeMultisegmentAvMasterEvidence={
  project_resource:visual.project_resource,generation:visual.generation,revision:visual.revision,
  deliverable_id:profile.id,frame_count:120,video_segments:2,
  video_codec:"h264",audio_codec:"aac",audio_sample_rate:48000,audio_channels:2,
  master_sha256:(window as unknown as {__PROGRAM_CLIP_SHA__:string}).__PROGRAM_CLIP_SHA__,
  export_token:"77777777-7777-4777-8777-777777777777",review_only:false,
  provider_project_cleanup:"not_requested_requires_foreground_broker_consent",
  evidence_scope:"native-multisegment-av-verified-technical-output-not-human-approved",
};
function Fixture(){
  const [project,setProject]=useState(initial),[at,setAt]=useState(0),[open,setOpen]=useState(true);
  const scene=at<2?project.scenes[0]:project.scenes[1];
  return <main>
    <p role="note">Synthetic existing-master inputs and a generated H.264/AAC clip. No Native execution or acceptance is claimed.</p>
    <output aria-label="Observed Program playhead">{at.toFixed(6)}</output>
    <output aria-label="Observed source scene">{scene.name}</output>
    <button onClick={()=>setAt(3)}>Seek second original to three seconds</button>
    <button onClick={()=>setProject(p=>({...p,revision:p.revision+1}))}>Change saved revision</button>
    <button onClick={()=>setOpen(false)}>Close Program fixture</button>
    {open&&<div style={{height:600,position:"relative"}}>
      <PreviewSurface scene={scene} project={project} playhead={at} rate={{num:"30",den:"1"}}
        mode="ndf" profile={project.deliverables[0]} renderEvidence={visual} avEvidence={original}
        onInspectRender={()=>{}} onSeek={setAt} onPauseEditorial={()=>{}}
        editorialPlaying={false} desktopMode/>
    </div>}
  </main>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><Fixture/></StrictMode>);
