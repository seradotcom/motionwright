/** Owner-CAS original SFX waveform readback from the installed native
 * backend only. These are exact measured per-frame PCM extrema, NOT
 * imported WAV bytes, invented waveform peaks, LUFS or creative approval.
 */
import type { Project } from "./types";
import type { SemanticSfxCue } from "./semanticSfx";

export interface OriginalSfxWaveform {
  schema:string;
  project_id:string;
  generation:string;
  revision:number;
  cue_id:string;
  asset_id:string;
  source_sha256:string;
  source_frames:number;
  sample_rate_hz:number;
  channels:number;
  samples_per_peak:number;
  left_min_pcm16:number[];
  left_max_pcm16:number[];
  right_min_pcm16:number[];
  right_max_pcm16:number[];
  sample_peak_pcm16:number;
  measured_sha256:string;
  evidence_scope:string;
}
const SHA=/^[0-9a-f]{64}$/;
const SCOPE="owner-CAS-verified-original-PCM16-per-frame-extrema-NOT-listening-or-master";

function exactSigned(value:number):boolean {
  return Number.isInteger(value) && value >= -32768 && value <= 32767;
}

/** Reject foreign/stale/unsourced source measurements even if the WebView
 * receives a syntactically plausible value. Source byte verification is the
 * trusted installed native worker's responsibility, NOT this predicate.
 */
export function originalSfxWaveformMatches(
  project:Project,
  cue:SemanticSfxCue,
  report:OriginalSfxWaveform|null,
):report is OriginalSfxWaveform {
  if(!report || report.schema!=="motionwright.original-sfx-waveform/1"
    || report.project_id!==project.id
    || report.generation!==project.generation
    || report.revision!==project.revision
    || report.cue_id!==cue.id
    || report.asset_id!==cue.source_asset_id
    || report.source_sha256!==cue.source_sha256
    || !SHA.test(report.source_sha256)||!SHA.test(report.measured_sha256)
    || report.evidence_scope!==SCOPE
    || report.source_frames!==cue.source_frames
    || !Number.isInteger(report.source_frames)
    || report.source_frames<1||report.source_frames>180
    || report.samples_per_peak!==1600
    || report.sample_rate_hz!==48000||report.channels!==2
    || !Number.isInteger(report.sample_peak_pcm16)
    || report.sample_peak_pcm16<0||report.sample_peak_pcm16>32768
    || !project.audio.sfx_cues?.some(item=>item.id===cue.id
      && item.source_asset_id===cue.source_asset_id
      && item.source_sha256===cue.source_sha256)
    || !project.assets.some(item=>item.id===cue.source_asset_id
      && item.content_sha256===cue.source_sha256)
  )return false;
  const arrays=[
    report.left_min_pcm16,report.left_max_pcm16,
    report.right_min_pcm16,report.right_max_pcm16,
  ];
  if(arrays.some(values=>!Array.isArray(values)
    ||values.length!==report.source_frames
    ||values.some(value=>!exactSigned(value))))return false;
  let peak=0;
  for(let i=0;i<report.source_frames;i++){
    const bounds=[
      [report.left_min_pcm16[i],report.left_max_pcm16[i]],
      [report.right_min_pcm16[i],report.right_max_pcm16[i]],
    ];
    if(bounds.some(([min,max])=>min>max))return false;
    peak=Math.max(peak,...bounds.flatMap(([min,max])=>[Math.abs(min),Math.abs(max)]));
  }
  return peak===report.sample_peak_pcm16;
}

/** Exact 30-fps source interval used to label/read an already-authorized cue. */
export function originalSfxWaveformFrame(cueStartFrame:number,sourceFrame:number):{
  frame:number;sample:number;seconds:number;
}|null {
  if(!Number.isSafeInteger(cueStartFrame) || cueStartFrame<0
    ||!Number.isSafeInteger(sourceFrame)||sourceFrame<0
    ||cueStartFrame+sourceFrame>36000)return null;
  const frame=cueStartFrame+sourceFrame;
  return {frame,sample:frame*1600,seconds:frame/30};
}
