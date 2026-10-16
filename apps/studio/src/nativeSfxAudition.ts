/** MW05-E08: read-only authored-cue addresses in one verified SFX-only MP4.
 * This is NOT proof that a listener heard the cue, nor an approval ledger.
 */
import type { Change, MltAvMasterEvidence, Project, RationalTime } from "./types";
import { validateSfxProject } from "./semanticSfx";
import { currentOriginalSfxSourceProof } from "./sfxNativeDelivery";

export interface NativeSfxAuditionCue {
  cueId: string;
  sceneId: string;
  sceneName: string;
  event: string;
  atFrame: number;
  endFrame: number;
  atSeconds: number;
  absolutePcmSample: number;
  durationFrames: number;
  originalSourceDigest: string;
}

const EVENTS:Record<string,string> = {
  scene_reveal:"Scene reveal",
  beat_emphasis:"Beat emphasis",
  ui_focus:"UI focus",
  confirmation:"Confirmation",
  impact:"Impact",
  transition:"Transition",
};

/** The only supported cue review clock is exact 30 fps and 48 kHz PCM.
 * A stale, forged, mixed-voice, or incomplete source-proof gives no review.
 */
export function nativeSfxAuditionCues(
  project:Project,
  profileId:string|null,
  receipt:MltAvMasterEvidence|null,
):NativeSfxAuditionCue[] {
  const proof=currentOriginalSfxSourceProof(project,profileId,receipt);
  if(!proof || !receipt || project.deliverables.find(p=>p.id===profileId)?.voice_track_id)
    return [];
  if (receipt.frame_rate.num!==30 || receipt.frame_rate.den!==1
      || proof.master_frames!==receipt.frame_count) return [];
  try {
    const placements=validateSfxProject(project);
    const cues=new Map((project.audio.sfx_cues??[]).map(cue=>[cue.id,cue]));
    const scenes=new Map(project.scenes.map(scene=>[scene.id,scene]));
    if(!placements.length) return [];
    const rows=placements.map(place=>{
      const cue=cues.get(place.cue_id),scene=cue?scenes.get(cue.scene_id):undefined;
      if(!cue || !scene
        || place.absolute_start_frame<0
        || place.absolute_end_frame>proof.master_frames
        || place.sample_count!==cue.source_frames*1600
        || place.first_sample!==place.absolute_start_frame*1600)
        throw new Error("SFX review is not bound to this final native frame clock");
      return {
        cueId:cue.id,sceneId:scene.id,sceneName:scene.name,
        event:EVENTS[cue.event]??"Original sound",
        atFrame:place.absolute_start_frame,
        endFrame:place.absolute_end_frame,
        atSeconds:place.absolute_start_frame/30,
        absolutePcmSample:place.first_sample,
        durationFrames:cue.source_frames,
        originalSourceDigest:cue.source_sha256,
      };
    });
    return rows;
  }catch {
    // Never invent a review timeline when authoring/source frames are stale.
    return [];
  }
}

/** Exact, transport-independent timestamp: frame N of a 30-fps film. */
export function sourceFrameLabel(frame:number):string {
  if(!Number.isSafeInteger(frame) || frame<0 || frame>36000)return "Unavailable";
  const whole=Math.floor(frame/30),remaining=frame%30;
  return String(whole).padStart(2,"0") + ":"
    + String(remaining).padStart(2,"0") + "f";
}


const SOURCE_SHA=/^[0-9a-f]{64}$/;
function exactFrameTime(frame:number):RationalTime {
  if (!Number.isSafeInteger(frame)||frame<0||frame>36000)
    throw new Error("Review frame address must be a bounded original frame.");
  const gcd=(a:number,b:number):number=>b===0?a:gcd(b,a%b);
  const n=gcd(frame,30);
  return {num:String(frame/n),den:String(30/n)};
}

/** An explicit listening issue goes through the EXISTING revisioned creative
 * review contract. No automatic verdict, source patch or approval is created.
 * The returned Change is consumed by the current owner branch through CAS.
 */
export function originalSfxListeningIssue(
  project:Project, profileId:string,
  master:MltAvMasterEvidence|null, cue:NativeSfxAuditionCue,
  observation:string, playedInThisSession:boolean,
):Change {
  if(!playedInThisSession)throw new Error("Listen to the current verified MP4 before recording a manual SFX issue.");
  if(!profileId || typeof observation!=="string")
    throw new Error("A source-bound profile and human observation are required.");
  const normalized=observation.trim();
  if(normalized.length<10 || [...normalized].length>640
    || /[\u0000-\u0009\u000B-\u001F\u007F]/u.test(normalized))
    throw new Error("Manual SFX issue needs 10–640 printable characters.");
  if(!master)throw new Error("No verified source-bound native AV master is present.");
  const matching=nativeSfxAuditionCues(project,profileId,master)
    .find(source=>source.cueId===cue.cueId);
  if(!matching || JSON.stringify(matching)!==JSON.stringify(cue))
    throw new Error("The observed cue is not in the current source-bound native master.");
  const proof=currentOriginalSfxSourceProof(project,profileId,master);
  if(!proof)throw new Error("Original PCM source proof is missing or stale.");
  const sha=master.master?.artifact && typeof (master.master.artifact as Record<string,unknown>).sha256==="string"
    ? (master.master.artifact as Record<string,unknown>).sha256 as string : "";
  if(!SOURCE_SHA.test(sha)||!SOURCE_SHA.test(proof.exact_pcm_sha256)
    ||!SOURCE_SHA.test(cue.originalSourceDigest))
    throw new Error("The original MP4/WAV SHA-256 review identity is unavailable.");
  if(!project.scenes.some(s=>s.id===cue.sceneId)
    ||master.revision!==project.revision
    ||master.generation!==project.generation
    ||master.deliverable_id!==profileId)
    throw new Error("The native SFX review scene or project revision changed.");
  const body=JSON.stringify({
    schema:"motionwright.manual-native-sfx-listening-issue/1",
    source:{
      project_id:project.id,generation:project.generation,revision:project.revision,
      deliverable_id:profileId,scene_id:cue.sceneId,cue_id:cue.cueId,
      start_frame:cue.atFrame,end_frame_exclusive:cue.endFrame,
      absolute_pcm_sample:cue.absolutePcmSample,
      sample_rate_hz:48000,video_frame_rate:"30/1",
      original_wav_sha256:cue.originalSourceDigest,
      exact_mix_pcm_sha256:proof.exact_pcm_sha256,
      native_mp4_sha256:sha,
    },
    interpretation:"human_annotated_after_player_play_event_not_creative_approval",
    verdict:"needs_adjustment",
    observation:normalized,
    proposed_action:"review_original_semantic_sfx_cue_and_reauthor_if_needed",
  });
  if(body.length>3000)throw new Error("Manual native SFX review exceeds the bounded review body.");
  return {
    type:"add_review",kind:"creative",resource:"scene:"+cue.sceneId,body,
    start:exactFrameTime(cue.atFrame),end:exactFrameTime(cue.endFrame),
    locale:null,profile_id:profileId,
  };
}
