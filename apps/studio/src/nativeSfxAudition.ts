/** MW05-E08: read-only authored-cue addresses in one verified SFX-only MP4.
 * This is NOT proof that a listener heard the cue, nor an approval ledger.
 */
import type { MltAvMasterEvidence, Project } from "./types";
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
