import { nativeFrameAtPlayhead } from "./nativeFrameSelection";
import type { NativeFrameSelection } from "./nativeFrameSelection";
import type { DeliverableProfile, MotionCanvasRenderEvidence, Project, RationalTime, Scene } from "./types";
import { seconds } from "./types";

export interface NativeInspectionSample extends NativeFrameSelection {
  localFrame: number;
  timelineTime: RationalTime;
}
export interface NativeInspectionPlan {
  projectId: string; generation: string; revision: number; sceneId: string; profileId: string;
  width: number; height: number; frameRate: {num:number;den:number}; samples: NativeInspectionSample[];
}
const MAX_SAMPLES=8;
const MAX_DECODE_PIXELS=16_777_216;
export function addFrameOffset(start:RationalTime, frames:number, rate:{num:number;den:number}):RationalTime {
  if(!Number.isSafeInteger(frames) || frames<0 || frames>1_000_000 || !Number.isSafeInteger(rate.num) || !Number.isSafeInteger(rate.den) || rate.num<=0 || rate.den<=0 || rate.num/rate.den<1 || rate.num/rate.den>120 || BigInt(start.den)<=0n || BigInt(start.num)<0n) throw new Error("Invalid bounded native frame time.");
  let numerator=BigInt(start.num)*BigInt(rate.num)+BigInt(frames)*BigInt(rate.den)*BigInt(start.den);
  let denominator=BigInt(start.den)*BigInt(rate.num);
  let a=numerator<0n?-numerator:numerator,b=denominator;
  while(b!==0n){const r=a%b;a=b;b=r;}
  numerator/=a||1n;denominator/=a||1n;
  return {num:String(numerator),den:String(denominator)};
}
/** A bounded read plan, not a render or a grant. Every read still goes through desktop authority. */
export function planNativeInspection(project:Project,scene:Scene|null,profile:DeliverableProfile|null,evidence:MotionCanvasRenderEvidence|null):NativeInspectionPlan|null {
  if(!scene || !profile || !evidence || profile.width*profile.height>MAX_DECODE_PIXELS) return null;
  const first=nativeFrameAtPlayhead(project,scene,profile,evidence,seconds(scene.start));
  if(!first) return null;
  const fps=evidence.frame_rate.num/evidence.frame_rate.den;
  const count=Math.max(1,Math.round(seconds(scene.duration)*fps));
  if(!Number.isSafeInteger(count) || count>1_000_000) return null;
  const last=Math.min(first.frameCount-1,first.frameIndex+count-1);
  const authored=new Set<number>([first.frameIndex,last]);
  for(const node of scene.nodes) for(const key of node.keyframes) {
    const index=first.frameIndex+Math.floor(seconds(key.at)*fps+1e-7);
    if(index>=first.frameIndex && index<=last) authored.add(index);
  }
  const ordered=[...authored].sort((a,b)=>a-b);
  const chosen=new Set<number>();
  if(ordered.length<=MAX_SAMPLES) ordered.forEach(i=>chosen.add(i));
  else for(let i=0;i<MAX_SAMPLES;i++) chosen.add(ordered[Math.round(i*(ordered.length-1)/(MAX_SAMPLES-1))]);
  // Include interior frames even when a static scene has no authored key boundaries.
  for(const fraction of [.5,.25,.75,.125,.375,.625,.875,.0625,.9375]) {
    if(chosen.size>=MAX_SAMPLES || chosen.size>=count) break;
    chosen.add(first.frameIndex+Math.floor((last-first.frameIndex)*fraction));
  }
  const samples=[...chosen].sort((a,b)=>a-b).map(frameIndex=>({
    ...first,frameIndex,localFrame:frameIndex-first.frameIndex,
    timelineTime:addFrameOffset(scene.start,frameIndex-first.frameIndex,evidence.frame_rate),
  }));
  return {projectId:project.id,generation:project.generation,revision:project.revision,sceneId:scene.id,profileId:profile.id,
    width:profile.width,height:profile.height,frameRate:evidence.frame_rate,samples};
}
export interface NativeSampleEvidence { frameIndex:number; timelineTime:RationalTime; pngSha256:string; width:number; height:number; }
export interface ManualCreativeFinding {
  severity:"blocking"|"important"|"suggestion";
  confidence:"certain"|"probable"|"uncertain";
  violatedConstraint:string; observation:string; proposedRepair:string; nodeId:string|null;
}
/** Evidence references are included; a person's interpretation never becomes an automatic quality PASS. */
export function nativeFindingBody(plan:NativeInspectionPlan,sample:NativeSampleEvidence,finding:ManualCreativeFinding):string {
  for(const text of [finding.violatedConstraint,finding.observation,finding.proposedRepair]) {
    if(!text.trim() || text.length>800 || /[\u0000-\u0009\u000b-\u001f\u007f]/u.test(text)) throw new Error("Finding text is empty, contains control characters or exceeds its budget.");
  }
  if(!plan.samples.some(s=>s.frameIndex===sample.frameIndex && s.timelineTime.num===sample.timelineTime.num && s.timelineTime.den===sample.timelineTime.den) || !/^[a-f0-9]{64}$/.test(sample.pngSha256)
    || sample.width!==plan.width || sample.height!==plan.height) throw new Error("Finding evidence is outside this native inspection.");
  return JSON.stringify({schema:"motionwright.manual-native-frame-finding/1",source:{project_id:plan.projectId,generation:plan.generation,revision:plan.revision,
    scene_id:plan.sceneId,profile_id:plan.profileId,frame_index:sample.frameIndex,time:sample.timelineTime,png_sha256:sample.pngSha256},
    interpretation:"manual_review_not_automated_critic_verdict",...finding});
}
