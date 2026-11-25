import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import AudioReviewTransport from "../../src/AudioReviewTransport";
import { audioReviewScope, type AudioReviewRequest } from "../../src/audioReviewClock";
import { useLocalAudioElement } from "../../src/audioReviewPlayback";
import { fixtureBootstrap } from "../../src/fixture";
import "../../src/styles.css";

function wav(seconds: number): Blob {
  const frames=48000*seconds, buffer=new ArrayBuffer(44+frames*4), view=new DataView(buffer);
  const text=(at:number,value:string)=>[...value].forEach((char,i)=>view.setUint8(at+i,char.charCodeAt(0)));
  text(0,"RIFF");view.setUint32(4,buffer.byteLength-8,true);text(8,"WAVEfmt ");
  view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,2,true);
  view.setUint32(24,48000,true);view.setUint32(28,192000,true);view.setUint16(32,4,true);view.setUint16(34,16,true);
  text(36,"data");view.setUint32(40,frames*4,true);
  return new Blob([buffer],{type:"audio/wav"});
}
const initial=structuredClone(fixtureBootstrap.project), source=initial.scenes[0];
const voice="11111111-1111-4111-8111-111111111111",asset="22222222-2222-4222-8222-222222222222",digest="a".repeat(64);
initial.scenes=[{...source,start:{num:"0",den:"1"},duration:{num:"2",den:"1"}},
  {...structuredClone(source),id:"33333333-3333-4333-8333-333333333333",name:"Second scene",
    start:{num:"2",den:"1"},duration:{num:"2",den:"1"}}];
initial.assets.push({id:asset,name:"Synthetic local PCM",media_type:"audio/wav",content_sha256:digest,source_revision:null});
initial.audio.voice_tracks=[{id:voice,asset_id:asset,label:"Synthetic measured voice",source_sha256:digest,
  sample_rate_hz:48000,channels:2,measured_duration:{num:"4",den:"1"},loudness_lufs:null,true_peak_dbfs:null}];
initial.audio.active_voice_track_id=voice;
initial.deliverables[0].voice_track_id=voice;initial.deliverables[0].audio_sample_rate_hz=48000;
initial.deliverables[0].included_scene_ids=[];
const original=wav(4),short=wav(3),independent=URL.createObjectURL(original);
function Fixture() {
  const [project,setProject]=useState(initial),[playhead,setPlayhead]=useState(0),[design,setDesign]=useState(false);
  const [session,setSession]=useState<{key:number;request:AudioReviewRequest}|null>({key:1,request:{
    scope:audioReviewScope(initial),kind:"mix",sourceId:initial.deliverables[0].id,label:"Synthetic full-cut review",wav:original}});
  const other=useLocalAudioElement();
  return <main>
    <h1>Synthetic component transport fixture</h1>
    <output aria-label="Observed project playhead">{playhead.toFixed(6)}</output>
    <output aria-label="Observed design playback">{String(design)}</output>
    <button onClick={()=>setPlayhead(3)}>Seek project to three seconds</button>
    <button onClick={()=>setPlayhead(9)}>Seek outside audio range</button>
    <button onClick={()=>setDesign(true)}>Start design preview fixture</button>
    <button onClick={()=>setProject(p=>({...p,revision:p.revision+1}))}>Change saved revision</button>
    <button onClick={()=>setSession(s=>({key:(s?.key??0)+1,request:{
      scope:audioReviewScope(project),kind:"mix",sourceId:project.deliverables[0].id,label:"Synthetic wrong duration",wav:short}}))}>Use mismatched decoded duration</button>
    <audio aria-label="Independent source playback" src={independent} controls ref={other.bind} onPlay={other.claim}/>
    {session&&<AudioReviewTransport key={session.key} project={project} request={session.request}
      playhead={playhead} editorialPlaying={design} onClose={()=>setSession(null)}
      onSeek={next=>{setPlayhead(next);setDesign(false);}}/>}
  </main>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><Fixture/></StrictMode>);
