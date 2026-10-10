import {useEffect,useMemo,useState} from 'react';
import {AudioWaveform,ShieldCheck} from 'lucide-react';
import {nativeOriginalMixAudition} from './api';
import type {Project} from './types';
const explain=(reason:unknown)=>reason instanceof Error?reason.message:String(reason);
export default function NativeOriginalMixReview({project,desktopMode}:{
  project:Project;desktopMode:boolean
}){
  const [music,setMusic]=useState('');
  const [sfx,setSfx]=useState('');
  const [profile,setProfile]=useState('');
  const [sfxGain,setSfxGain]=useState('-8');
  const [duck,setDuck]=useState('14');
  const [attack,setAttack]=useState('2400');
  const [release,setRelease]=useState('4800');
  const [selfAttested,setSelfAttested]=useState(false);
  const [working,setWorking]=useState(false);
  const [auditionUrl,setAuditionUrl]=useState<string|null>(null);
  const [error,setError]=useState<string|null>(null);
  const sourceAudio=useMemo(()=>project.assets.filter(asset=>
    ['audio/wav','audio/x-wav','audio/wave'].includes(asset.media_type)&&
    /^[a-f0-9]{64}$/.test(asset.content_sha256??'')),[project.assets]);
  const profiles=project.deliverables.filter(item=>item.audio_sample_rate_hz===48000&&
    ['en','es','de'].includes(item.language));
  const selectedVoice=project.audio.voice_tracks.find(track=>track.id===project.audio.active_voice_track_id);
  useEffect(()=>()=>{
    if(auditionUrl)URL.revokeObjectURL(auditionUrl);
  },[auditionUrl]);
  useEffect(()=>{
    setAuditionUrl(null);setError(null);setSelfAttested(false);
    setMusic('');setSfx('');setProfile('');
  },[project.id,project.generation,project.revision]);
  const eligible=desktopMode&&!working&&!!selectedVoice&&!!music&&!!sfx&&!!profile&&
    music!==sfx&&music!==selectedVoice.asset_id&&sfx!==selectedVoice.asset_id&&selfAttested;
  const preview=async()=>{
    setWorking(true);setError(null);setAuditionUrl(null);
    try{
      const values=[sfxGain,duck,attack,release].map(Number);
      if(values.some(v=>!Number.isFinite(v))){
        throw new Error('Original mix automation parameters must be finite numbers.');
      }
      const wav=await nativeOriginalMixAudition(project,{
        deliverable_profile_id:profile,music_asset_id:music,sfx_asset_id:sfx,
        sfx_gain_db:values[0],duck_attenuation_db:values[1],
        duck_attack_samples:values[2],duck_release_samples:values[3],
        owner_attests_preview_rights:selfAttested
      });
      setAuditionUrl(URL.createObjectURL(new Blob([Uint8Array.from(wav).buffer],
        {type:'audio/wav'})));
    }catch(failure){setError(explain(failure));}finally{setWorking(false);}
  };
  return <details className="audio-editor-section" aria-label="Original three-bus native PCM audition">
    <summary><AudioWaveform size={15}/> Voice / music / SFX · original source mix</summary>
    <p className="production-help">Locally listen to an original three-bus PCM mix using sources already imported and hashed by this project. Music ducking follows manually aligned or high-confidence voice timing. Nothing is mastered, published, auto-approved or added to the timeline.</p>
    {!desktopMode&&<p className="production-help">Desktop domain service is required. A browser mock cannot produce an audio mix.</p>}
    {sourceAudio.length<3&&<p className="production-help">Import original WAV assets through the existing asset importer. Three distinct stereo 48-kHz source files are required (measured voice, music and SFX).</p>}
    <label className="production-field"><span>Delivery language / profile</span>
      <select value={profile} disabled={!desktopMode||working}
        onChange={event=>{setProfile(event.target.value);setAuditionUrl(null);}}>
        <option value="">Select an existing 48-kHz profile</option>
        {profiles.map(item=><option key={item.id} value={item.id}>{item.name} · {item.language}</option>)}
      </select>
    </label>
    <label className="production-field"><span>Original music asset</span>
      <select value={music} disabled={!desktopMode||working}
        onChange={event=>{setMusic(event.target.value);setAuditionUrl(null);}}>
        <option value="">Select imported WAV music</option>
        {sourceAudio.filter(item=>item.id!==selectedVoice?.asset_id).map(item=>
          <option key={item.id} value={item.id}>{item.name} · sha256:{item.content_sha256?.slice(0,10)}</option>)}
      </select>
    </label>
    <label className="production-field"><span>Original SFX asset</span>
      <select value={sfx} disabled={!desktopMode||working}
        onChange={event=>{setSfx(event.target.value);setAuditionUrl(null);}}>
        <option value="">Select imported WAV effects</option>
        {sourceAudio.filter(item=>item.id!==selectedVoice?.asset_id).map(item=>
          <option key={item.id} value={item.id}>{item.name} · sha256:{item.content_sha256?.slice(0,10)}</option>)}
      </select>
    </label>
    <div className="audio-time-pair">
      <label className="production-field"><span>SFX gain dB</span>
        <input type="number" step="0.5" min="-80" max="12" value={sfxGain}
          disabled={!desktopMode||working} onChange={event=>{setSfxGain(event.target.value);setAuditionUrl(null);}}/>
      </label>
      <label className="production-field"><span>Music duck attenuation dB</span>
        <input type="number" step="0.5" min="0" max="30" value={duck}
          disabled={!desktopMode||working} onChange={event=>{setDuck(event.target.value);setAuditionUrl(null);}}/>
      </label>
    </div>
    <div className="audio-time-pair">
      <label className="production-field"><span>Attack (samples at 48 kHz)</span>
        <input type="number" step="1" min="1" max="48000" value={attack}
          disabled={!desktopMode||working} onChange={event=>{setAttack(event.target.value);setAuditionUrl(null);}}/>
      </label>
      <label className="production-field"><span>Release (samples at 48 kHz)</span>
        <input type="number" step="1" min="1" max="48000" value={release}
          disabled={!desktopMode||working} onChange={event=>{setRelease(event.target.value);setAuditionUrl(null);}}/>
      </label>
    </div>
    <label className="production-check">
      <input type="checkbox" checked={selfAttested} disabled={!desktopMode||working}
        onChange={event=>{setSelfAttested(event.target.checked);setAuditionUrl(null);}}/>
      I confirm I have permission to use these exact original sources for local audition only. This is not an independently verified rights grant.
    </label>
    {error&&<p className="portable-message error" role="alert">{error}</p>}
    <button type="button" className="button button-primary" disabled={!eligible}
      onClick={()=>void preview()}><ShieldCheck size={14}/>
      {working?'Mixing verified sources…':'Create local original PCM audition'}
    </button>
    {auditionUrl&&<div aria-label="Unmastered original PCM preview" className="audio-narration-source-evidence">
      <strong>Local source PCM only · 48 kHz · not a mastered deliverable</strong>
      <audio controls preload="metadata" src={auditionUrl}/>
      <p>Source, clock and clip guards passed. Integrated LUFS, true peak, listening intelligibility and editorial approval still require separate measurement/review.</p>
    </div>}
  </details>;
}
