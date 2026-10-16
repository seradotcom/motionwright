import { AudioLines, LocateFixed } from "lucide-react";
import { useState } from "react";
import type { OriginalSfxWaveform } from "./originalSfxWaveform";
import { originalSfxWaveformFrame } from "./originalSfxWaveform";

function magnitude(min:number,max:number):number {
  return Math.max(Math.abs(min),Math.abs(max));
}
export default function OriginalSfxWaveformPanel({
  report,cueStartFrame,onSeek,
}:{
  report:OriginalSfxWaveform;
  cueStartFrame:number;
  onSeek:(seconds:number)=>void;
}) {
  const [selected,setSelected]=useState(0);
  const frame=originalSfxWaveformFrame(cueStartFrame,selected);
  const tracks=[
    {label:"Left channel",min:report.left_min_pcm16,max:report.left_max_pcm16},
    {label:"Right channel",min:report.right_min_pcm16,max:report.right_max_pcm16},
  ];
  return <section role="region" aria-label="Original SFX measured waveform"
    className="original-sfx-waveform">
    <header>
      <div>
        <strong>Measured original WAV · stereo 48 kHz</strong>
        <span>Exact PCM16 min/max samples from the owner-imported source.</span>
      </div>
      <span className="mono">{report.source_frames} frames · {report.source_frames*1600} samples/channel</span>
    </header>
    <p className="audio-truth-line">
      <AudioLines size={14}/>
      Source SHA-256 {report.source_sha256.slice(0,16)}… ·
      measured peak {report.sample_peak_pcm16.toLocaleString("en-US")} / 32768 PCM16.
      This source proxy is not playback, LUFS, true peak or creative approval.
    </p>
    <div className="original-sfx-waveform-stereo">
      {tracks.map(track=><div className="original-sfx-waveform-track"
        key={track.label} role="img"
        aria-label={track.label+" original PCM waveform, "+report.source_frames+" exact 30-fps frame buckets"}>
        <span>{track.label}</span>
        <div className="original-sfx-waveform-peaks" aria-hidden="true">
          {track.max.map((upper,i)=>{
            const height=100*magnitude(track.min[i],upper)/32768;
            return <span className="original-sfx-waveform-peak"
              key={i} data-selected={i===selected}
              title={"Source frame "+i+", samples min "+track.min[i]+", max "+upper}
              style={{height:Math.max(1,height)+"%"}}/>;
          })}
        </div>
      </div>)}
    </div>
    <div className="original-sfx-waveform-actions">
      <label>Source frame {selected} of {report.source_frames-1}
        <input type="range" min={0} max={report.source_frames-1}
          step={1} value={selected}
          aria-label="Original SFX waveform source frame"
          onChange={event=>setSelected(Number(event.target.value))}/>
      </label>
      <button className="button compact" type="button"
        disabled={frame===null}
        onClick={()=>{if(frame)onSeek(frame.seconds);}}
        aria-label="Seek project timeline to selected original SFX waveform frame">
        <LocateFixed size={13}/>
        {frame?"Seek frame "+frame.frame+" · PCM sample "+frame.sample.toLocaleString("en-US"):"Unavailable source frame"}
      </button>
    </div>
    <p className="audio-truth-line">
      The graph describes source samples only. Current cue gain,
      envelope and native AAC output require separate verification.
    </p>
  </section>;
}
