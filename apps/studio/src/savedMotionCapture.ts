import {savedDeliveryRecipe} from "./deliveryRecipe";
import type {MotionCanvasRenderEvidence,Project} from "./types";

export interface SavedMotionCaptureHint {
  receipt_id:string;
  capture_sha256:string;
  segment_count:number;
  frame_count:number;
  fps_num:number;
  fps_den:number;
}
export interface SavedMotionCaptureInventory {
  schema:"motionwright.saved-motion-inventory/1";
  project_id:string;
  generation:string;
  revision:number;
  deliverable_id:string;
  capture:SavedMotionCaptureHint|null;
}
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const sha=/^[0-9a-f]{64}$/;
function object(value:unknown):Record<string,unknown>{
  if(!value||typeof value!=="object"||Array.isArray(value))throw new Error("Saved capture response is invalid.");
  return value as Record<string,unknown>;
}
function closed(value:unknown,keys:readonly string[],optional:readonly string[]=[]):Record<string,unknown>{
  const result=object(value);
  if(!keys.every(key=>Object.hasOwn(result,key))||Object.keys(result).some(key=>!keys.includes(key)&&!optional.includes(key)))
    throw new Error("Saved capture response fields are invalid.");
  return result;
}
function positive(value:unknown,max:number):value is number {
  return typeof value==="number"&&Number.isSafeInteger(value)&&value>0&&value<=max;
}
function rateMatches(project:Project,deliverableId:string,num:unknown,den:unknown):boolean{
  const profile=project.deliverables.find(item=>item.id===deliverableId);
  const integer=/^[1-9][0-9]{0,19}$/;
  return Boolean(profile&&typeof profile.frame_rate.num==="string"&&typeof profile.frame_rate.den==="string"
    &&integer.test(profile.frame_rate.num)&&integer.test(profile.frame_rate.den)
    &&positive(num,0xffffffff)&&positive(den,0xffffffff)
    &&BigInt(num)*BigInt(profile.frame_rate.den)===BigInt(den)*BigInt(profile.frame_rate.num));
}
export function parseSavedMotionCaptureHint(value:unknown,project:Project,deliverableId:string):SavedMotionCaptureHint{
  const item=closed(value,["receipt_id","capture_sha256","segment_count","frame_count","fps_num","fps_den"]);
  if(typeof item.receipt_id!=="string"||!uuid.test(item.receipt_id)
    ||typeof item.capture_sha256!=="string"||!sha.test(item.capture_sha256)
    ||!positive(item.segment_count,64)||!positive(item.frame_count,36000)
    ||item.segment_count>item.frame_count||!rateMatches(project,deliverableId,item.fps_num,item.fps_den))
    throw new Error("Saved capture reference differs from the current profile.");
  return item as unknown as SavedMotionCaptureHint;
}
export function parseSavedMotionCaptureInventory(value:unknown,project:Project,deliverableId:string):SavedMotionCaptureInventory{
  const data=closed(value,["schema","project_id","generation","revision","deliverable_id","capture"]);
  if(data.schema!=="motionwright.saved-motion-inventory/1"||data.project_id!==project.id
    ||data.generation!==project.generation||data.revision!==project.revision
    ||data.deliverable_id!==deliverableId||!project.deliverables.some(profile=>profile.id===deliverableId))
    throw new Error("Saved capture inventory belongs to a different project or revision.");
  return {...data,capture:data.capture===null?null:parseSavedMotionCaptureHint(data.capture,project,deliverableId)} as unknown as SavedMotionCaptureInventory;
}
export function parseRecoveredMotionCapture(value:unknown,project:Project,deliverableId:string,hint:SavedMotionCaptureHint):MotionCanvasRenderEvidence{
  parseSavedMotionCaptureHint(hint,project,deliverableId);
  const data=closed(value,["receipt_id","capture_sha256","project_resource","generation","revision",
    "deliverable_id","frame_rate","segments","preview"]);
  const rate=closed(data.frame_rate,["num","den"]);
  if(data.receipt_id!==hint.receipt_id||data.capture_sha256!==hint.capture_sha256
    ||data.project_resource!=="project:"+project.id||data.generation!==project.generation
    ||data.revision!==project.revision||data.deliverable_id!==deliverableId
    ||rate.num!==hint.fps_num||rate.den!==hint.fps_den
    ||!Array.isArray(data.segments)||data.segments.length!==hint.segment_count
    ||!Array.isArray(data.preview)||data.preview.length!==hint.segment_count)
    throw new Error("Recovered capture does not match the selected saved original.");
  const ids=new Set<string>(),tokens=new Set<string>(),scenes=new Set(project.scenes.map(scene=>scene.id));
  let frames=0;
  data.segments.forEach((value,index)=>{
    const segment=closed(value,["segment_id","scene_ids","frame_count","plan_ref","fingerprint","job_ref","artifact","verification"],
      ["audio_cue_frame_coverage"]);
    if(typeof segment.segment_id!=="string"||segment.segment_id.length>256||!segment.segment_id.length||ids.has(segment.segment_id)
      ||!positive(segment.frame_count,36000)||!Array.isArray(segment.scene_ids)||!segment.scene_ids.length
      ||segment.scene_ids.length>1024||!segment.scene_ids.every(id=>typeof id==="string"&&scenes.has(id))
      ||new Set(segment.scene_ids).size!==segment.scene_ids.length
      ||![segment.plan_ref,segment.fingerprint,segment.job_ref].every(value=>typeof value==="string"&&value.length>0&&value.length<=256))
      throw new Error("Recovered capture segment is invalid.");
    ids.add(segment.segment_id);frames+=segment.frame_count;
    const artifact=object(segment.artifact),verification=object(segment.verification);
    if(artifact.frame_count!==segment.frame_count||typeof artifact.manifest_sha256!=="string"||!sha.test(artifact.manifest_sha256))
      throw new Error("Recovered capture original manifest is invalid.");
    if(Object.hasOwn(segment,"audio_cue_frame_coverage")){
      const coverage=closed(segment.audio_cue_frame_coverage,["film_sha256","manifest_sha256","observation_sha256","frames"]);
      const measurement=object(verification.measurement),observed=object(measurement.coverage),fps=object(observed.fps);
      if(savedDeliveryRecipe(project,deliverableId)?.motion_canvas_audio_cues!=="preserve_authored_time"
        ||typeof coverage.film_sha256!=="string"||!sha.test(coverage.film_sha256)||measurement.intent_digest!==coverage.film_sha256
        ||coverage.manifest_sha256!==artifact.manifest_sha256||observed.artifact_digest!==coverage.manifest_sha256
        ||typeof coverage.observation_sha256!=="string"||!sha.test(coverage.observation_sha256)
        ||observed.observation_digest!==coverage.observation_sha256||observed.first_frame!==0
        ||observed.end_frame_exclusive!==segment.frame_count||observed.observed_frames!==segment.frame_count
        ||observed.exhaustive!==true||fps.num!==hint.fps_num||fps.den!==hint.fps_den
        ||!Array.isArray(coverage.frames)||!coverage.frames.length||coverage.frames.length>512)
        throw new Error("Saved audio cue frame coverage differs from the original observation.");
      const known=new Set(project.audio.cues.filter(cue=>cue.evidence!=="unknown").map(cue=>cue.id));
      for(const value of coverage.frames){
        const frame=closed(value,["cue_id","frame"]);
        if(typeof frame.cue_id!=="string"||!known.delete(frame.cue_id)
          ||(frame.frame!==0&&!positive(frame.frame,segment.frame_count-1)))
          throw new Error("Saved audio cue frame is absent, duplicated or outside coverage.");
      }
    }
    const preview=closed((data.preview as unknown[])[index],["token","segment_id","scene_ids","frame_count"]);
    if(typeof preview.token!=="string"||!uuid.test(preview.token)||tokens.has(preview.token)
      ||preview.segment_id!==segment.segment_id||preview.frame_count!==segment.frame_count
      ||JSON.stringify(preview.scene_ids)!==JSON.stringify(segment.scene_ids))
      throw new Error("Recovered capture preview is not bound to its original segment.");
    tokens.add(preview.token);
  });
  if(frames!==hint.frame_count)throw new Error("Recovered capture frame range is incomplete.");
  const {receipt_id: _receipt,capture_sha256: _digest,...evidence}=data;
  return evidence as unknown as MotionCanvasRenderEvidence;
}
