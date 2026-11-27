import type {Project} from "./types";
import {parseSavedMasterInventory,type SavedMasterHint} from "./savedNativeMedia";

export interface HybridSourcePreflight {
  schema:"motionwright.hybrid-source-preflight/1";
  project_id:string;generation:string;revision:number;deliverable_id:string;
  project_sha256:string;source_count:2|3;frame_count:number;width:1920;height:1080;
  fps_num:30;fps_den:1;source_audio_sha256:string;
}
export interface HybridMasterStreamClock {
  exact_video_clock:boolean;audio_start_sample:number|null;
  presented_audio_samples:number|null;authored_audio_samples:number;
}
export interface HybridMasterResult {
  schema:"motionwright.hybrid-desktop-master/1"|"motionwright.hybrid-desktop-master/2";status:"completed";
  project_id:string;generation:string;revision:number;deliverable_id:string;
  master_sha256:string;frame_count:number;fps_num:30;fps_den:1;
  stream_clock?:HybridMasterStreamClock;
  saved_master:SavedMasterHint|null;review_warning:string|null;automatic_retry:false;
}
export interface HybridFailure {
  schema:"motionwright.hybrid-desktop-failure/1";
  status:"blocked"|"stopped"|"unknown";stage:string;outcome_known:boolean;
  may_have_native_effects:boolean;automatic_retry:false;message:string;
}
const sha=/^[0-9a-f]{64}$/;
function validFrameCount(value:unknown):value is number {
  return typeof value==="number"&&Number.isSafeInteger(value)&&value>=30&&value<=180;
}
function closed(value:unknown,keys:readonly string[]):Record<string,unknown> {
  if(!value||typeof value!=="object"||Array.isArray(value)
    ||Object.keys(value).length!==keys.length||keys.some(key=>!Object.hasOwn(value,key)))
    throw new Error("The hybrid response is not a closed original result. Inspect Jobs before another render.");
  return value as Record<string,unknown>;
}
function scope(value:Record<string,unknown>,project:Project,profile:string):void {
  if(value.project_id!==project.id||value.generation!==project.generation
    ||value.revision!==project.revision||!Number.isSafeInteger(value.revision)||Number(value.revision)<0
    ||value.deliverable_id!==profile||!project.deliverables.some(item=>item.id===profile))
    throw new Error("The hybrid response belongs to another saved project or profile.");
}
export function parseHybridPreflight(value:unknown,project:Project,profile:string):HybridSourcePreflight {
  const data=closed(value,["schema","project_id","generation","revision","deliverable_id",
    "project_sha256","source_count","frame_count","width","height","fps_num","fps_den","source_audio_sha256"]);
  scope(data,project,profile);
  if(data.schema!=="motionwright.hybrid-source-preflight/1"||(data.source_count!==2&&data.source_count!==3)
    ||!validFrameCount(data.frame_count)||data.width!==1920||data.height!==1080||data.fps_num!==30||data.fps_den!==1
    ||typeof data.project_sha256!=="string"||!sha.test(data.project_sha256)
    ||typeof data.source_audio_sha256!=="string"||!sha.test(data.source_audio_sha256))
    throw new Error("The hybrid source projection exceeds the supported original route.");
  return data as unknown as HybridSourcePreflight;
}
export function parseHybridMasterStreamClock(value:unknown,frames:number):HybridMasterStreamClock {
  const clock=closed(value,["exact_video_clock","audio_start_sample","presented_audio_samples","authored_audio_samples"]);
  if(typeof clock.exact_video_clock!=="boolean"||clock.authored_audio_samples!==frames*1600
    ||(clock.audio_start_sample!==null&&!Number.isSafeInteger(clock.audio_start_sample))
    ||(clock.presented_audio_samples!==null&&(!Number.isSafeInteger(clock.presented_audio_samples)||Number(clock.presented_audio_samples)<=0)))
    throw new Error("The original hybrid stream measurement is invalid.");
  return clock as unknown as HybridMasterStreamClock;
}
export function streamClockIssue(clock?:HybridMasterStreamClock):string|null {
  if(!clock) return null;
  if(!clock.exact_video_clock) return "The rendered video timing differs from the saved cut.";
  const {audio_start_sample:start,presented_audio_samples:presented,authored_audio_samples:authored}=clock;
  if(start===null||presented===null) return "The original MP4 has no complete audio timing measurement.";
  if(start!==0) return "The original audio starts at sample "+start.toLocaleString()+". The saved cut starts at zero.";
  if(presented!==authored) return "The original audio presents "+presented.toLocaleString()+
    " samples. The saved cut requires "+authored.toLocaleString()+".";
  return null;
}

export function parseHybridMaster(value:unknown,project:Project,profile:string,expectedFrames:number):HybridMasterResult {
  const version2=!!value&&typeof value==="object"&&"schema" in value
    &&value.schema==="motionwright.hybrid-desktop-master/2";
  const keys=["schema","status","project_id","generation","revision","deliverable_id",
    "master_sha256","frame_count","fps_num","fps_den","saved_master","review_warning","automatic_retry"];
  const data=closed(value,version2?[...keys,"stream_clock"]:keys);
  scope(data,project,profile);
  if((data.schema!=="motionwright.hybrid-desktop-master/1"&&!version2)||data.status!=="completed"
    ||data.automatic_retry!==false||!validFrameCount(data.frame_count)||data.frame_count!==expectedFrames||data.fps_num!==30||data.fps_den!==1
    ||typeof data.master_sha256!=="string"||!sha.test(data.master_sha256))
    throw new Error("The hybrid master result is invalid. Inspect the original operation before rendering again.");
  if(version2) parseHybridMasterStreamClock(data.stream_clock, data.frame_count);
  if(data.saved_master===null) {
    if(data.review_warning!=="The original master completed. Open saved masters to recover it; do not render it again.")
      throw new Error("The completed hybrid master has no bounded recovery status.");
  } else {
    const inventory=parseSavedMasterInventory({
      schema:"motionwright.saved-master-inventory/1",project_id:project.id,
      generation:project.generation,revision:project.revision,deliverable_id:profile,
      items:[data.saved_master],truncated:false,
    },project,profile);
    const hint=inventory.items[0];
    if(hint.master_sha256!==data.master_sha256||hint.frame_count!==data.frame_count
      ||hint.fps_num!==30||hint.fps_den!==1||data.review_warning!==null)
      throw new Error("The saved receipt does not identify the completed original hybrid master.");
  }
  return data as unknown as HybridMasterResult;
}
export class HybridNativeOperationError extends Error {
  readonly failure:HybridFailure;
  constructor(value:unknown,effectsPossible:boolean) {
    let failure:HybridFailure;
    try {
      const data=closed(value,["schema","status","stage","outcome_known","may_have_native_effects","automatic_retry","message"]);
      if(data.schema!=="motionwright.hybrid-desktop-failure/1"||data.automatic_retry!==false
        ||typeof data.outcome_known!=="boolean"||typeof data.may_have_native_effects!=="boolean"
        ||!["preflight","owner","permission","sources","timeline","master"].includes(String(data.stage))
        ||typeof data.message!=="string"||data.message.length===0||data.message.length>480
        ||data.status!==(!data.outcome_known?"unknown":data.may_have_native_effects?"stopped":"blocked"))
        throw new Error("Invalid operation state");
      failure=data as unknown as HybridFailure;
    } catch {
      failure={schema:"motionwright.hybrid-desktop-failure/1",status:"unknown",stage:"transport",
        outcome_known:false,may_have_native_effects:effectsPossible,automatic_retry:false,
        message:effectsPossible
          ?"The hybrid operation did not return a valid result. Inspect original Jobs and saved masters before another render."
          :"The read-only hybrid preflight did not return a valid result."};
    }
    super(failure.message);
    this.name="HybridNativeOperationError";
    this.failure=failure;
  }
}
