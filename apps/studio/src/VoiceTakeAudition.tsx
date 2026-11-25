import {useEffect,useRef,useState} from "react";
import {AudioLines} from "lucide-react";
import {readOriginalVoiceAudition} from "./api";
import {voiceAuditionSource} from "./voiceAudition";
import type {Project} from "./types";

/** Review any retained measured take without selecting it for the project,
 * changing its transcript, or claiming native mix / mastering evidence. */
export default function VoiceTakeAudition({project,desktopMode}:{project:Project;desktopMode:boolean}){
  const [trackId,setTrackId]=useState(()=>project.audio.active_voice_track_id??project.audio.voice_tracks[0]?.id??"");
  const [attested,setAttested]=useState<string|null>(null);
  const [preview,setPreview]=useState<{scope:string;url:string}|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const [expanded,setExpanded]=useState(false);
  const running=useRef(false),mounted=useRef(true),serial=useRef(0);
  const track=project.audio.voice_tracks.find(item=>item.id===trackId);
  const scope=JSON.stringify([project.id,project.generation,project.active_branch,project.revision,
    trackId,track?.source_sha256??null]);
  const latest=useRef(scope);latest.current=scope;
  const confirmed=attested===scope;
  let blocker:string|null=null;
  if(!desktopMode)blocker="Original take listening requires the desktop runtime.";
  else try{voiceAuditionSource(project,trackId);}
  catch(reason){blocker=reason instanceof Error?reason.message:String(reason);}
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;serial.current++;};},[]);
  useEffect(()=>()=>{if(preview)URL.revokeObjectURL(preview.url);},[preview]);
  useEffect(()=>{
    serial.current++;setAttested(null);setPreview(null);setError(null);
  },[scope,desktopMode,expanded]);
  const load=async()=>{
    if(!expanded||blocker||!confirmed||running.current)return;
    running.current=true;setBusy(true);setPreview(null);setError(null);
    const ticket=++serial.current,originalScope=scope;
    try{
      const bytes=await readOriginalVoiceAudition(project,trackId,true);
      if(!mounted.current||serial.current!==ticket||latest.current!==originalScope)return;
      const owned=new Uint8Array(bytes.length);owned.set(bytes);
      setPreview({scope:originalScope,url:URL.createObjectURL(new Blob([owned.buffer],{type:"audio/wav"}))});
    }catch(reason){
      if(mounted.current&&serial.current===ticket&&latest.current===originalScope)
        setError(reason instanceof Error?reason.message:String(reason));
    }finally{running.current=false;if(mounted.current)setBusy(false);}
  };
  const current=expanded&&desktopMode&&preview?.scope===scope?preview:null;
  return <section className="audio-editor-section" aria-label="Original voice take listening">
    <button type="button" className="button button-secondary" aria-expanded={expanded}
      onClick={()=>{serial.current++;setExpanded(v=>!v);setPreview(null);setAttested(null);}}>
      <AudioLines size={15} aria-hidden="true"/> Listen to an original voice take
    </button>
    {expanded&&<div>
      <p className="production-help">Compare retained measured takes locally without making a take active. The original WAV is checked against its saved digest and measured clock. Playback does not change the project, certify timing, or approve a mix.</p>
      <label className="production-field"><span>Take to listen to</span>
        <select aria-label="Take to listen to" value={track?.id??""} onChange={event=>{
          serial.current++;setTrackId(event.target.value);setAttested(null);setPreview(null);setError(null);
        }}>
          <option value="">Select a measured take</option>
          {project.audio.voice_tracks.map(item=><option key={item.id} value={item.id}>
            {item.label}{item.id===project.audio.active_voice_track_id?" (active)":""}
          </option>)}
        </select>
      </label>
      {track&&<p className="production-help">Selected: {track.label}. {track.sample_rate_hz} Hz, {track.channels} channel{track.channels===1?"":"s"}. SHA-256 {track.source_sha256.slice(0,16)}…</p>}
      <p className="production-help">Supported local playback: original PCM16 WAV, mono or stereo, 8–96 kHz, up to 180 seconds and 16 MiB. Other source formats remain retained.</p>
      {blocker&&<p role="status" className="production-help">{blocker}</p>}
      <label className="production-check"><input type="checkbox" aria-label="Confirm original take listening rights"
        checked={confirmed} disabled={!!blocker||busy}
        onChange={event=>{serial.current++;setPreview(null);setError(null);setAttested(event.target.checked?scope:null);}}/>
        I have permission to listen to this exact original take locally.
      </label>
      <button type="button" className="button button-primary" disabled={!!blocker||!confirmed||busy}
        onClick={()=>void load()}>{busy?"Reading verified take…":"Load original take for playback"}</button>
      {error&&<p role="alert" className="portable-message error">{error}</p>}
      {current&&<section aria-label="Verified original take playback" className="audio-narration-source-evidence">
        <strong>Exact original source WAV</strong>
        <audio aria-label="Original take playback" controls preload="none" src={current.url}
          onError={()=>{setPreview(null);setError("The local media decoder could not play this original WAV.");}}/>
        <p className="production-help">Local source playback only. No independent speech, loudness, or final audiovisual approval.</p>
      </section>}
    </div>}
  </section>;
}
