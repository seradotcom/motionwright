/** MW05-E08-03 — original, source-bound semantic SFX placement.
 *
 * This is a closed arrangement and provenance contract at EXACT 30 fps.
 * It never invents audio, reads a path or certifies WAV/rights/loudness.
 * The owner-local storage and native PCM mixer verify real imported bytes.
 */
import type { Project } from "./types";

export const SFX_CUE_SCHEMA="motionwright.semantic-sfx/1";
export type SfxEvent="scene_reveal"|"beat_emphasis"|"ui_focus"|"confirmation"|"impact"|"transition";
export type SfxAnchor={kind:"scene_start"}|{kind:"beat_start";beat_id:string};
export interface SemanticSfxCue {
  schema:string;
  id:string;
  scene_id:string;
  event:SfxEvent;
  anchor:SfxAnchor;
  offset_frames:number;
  source_asset_id:string;
  source_sha256:string;
  source_frames:number;
  gain_milli_db:number;
  attack_frames:number;
  release_frames:number;
  original_source_attestation:string;
}
export interface ScheduledSfx {
  cue_id:string;
  absolute_start_frame:number;
  absolute_end_frame:number;
  first_sample:number;
  sample_count:number;
}
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const sha=/^[0-9a-f]{64}$/;
const EVENT_KINDS=["scene_reveal","beat_emphasis","ui_focus","confirmation","impact","transition"];
const CUE_KEYS=["schema","id","scene_id","event","anchor","offset_frames","source_asset_id",
  "source_sha256","source_frames","gain_milli_db","attack_frames","release_frames","original_source_attestation"];
function invalid(message:string):never {throw new Error(message);}
function exactly(source:object, keys:string[]):void{
  const a=Object.keys(source).sort(),b=[...keys].sort();
  if(a.length!==b.length || a.some((x,i)=>x!==b[i]))invalid("Unknown or missing semantic SFX schema fields.");
}
function frame(time:{num:string;den:string}):number{
  if(!/^-?\d+$/.test(time.num)||!/^-?\d+$/.test(time.den))
    invalid("Invalid rational SFX time.");
  const n=BigInt(time.num),d=BigInt(time.den);
  if(d<=0n||n<0n || (n*30n)%d!==0n)
    invalid("SFX timeline anchors must be exact at 30 fps; no implicit snap.");
  const q=n*30n/d;
  if(q>36000n)invalid("SFX source timeline is too long.");
  return Number(q);
}
export function validateSemanticSfx(project:Project,cue:SemanticSfxCue):ScheduledSfx{
  if(!cue || typeof cue!=="object" || Array.isArray(cue))invalid("Invalid SFX cue schema.");
  exactly(cue,CUE_KEYS);
  if(cue.schema!==SFX_CUE_SCHEMA || !uuid.test(cue.id) || !uuid.test(cue.scene_id) ||
    !EVENT_KINDS.includes(cue.event) ||
    !Number.isInteger(cue.offset_frames) || cue.offset_frames< -15 || cue.offset_frames>30 ||
    !Number.isInteger(cue.source_frames) || cue.source_frames<1 || cue.source_frames>180 ||
    !Number.isInteger(cue.gain_milli_db) || cue.gain_milli_db< -36000 || cue.gain_milli_db>0 ||
    !Number.isInteger(cue.attack_frames) || cue.attack_frames<0 || cue.attack_frames>30 ||
    !Number.isInteger(cue.release_frames) || cue.release_frames<0 || cue.release_frames>90 ||
    cue.attack_frames+cue.release_frames>=cue.source_frames ||
    !cue.original_source_attestation.trim() ||
    [...cue.original_source_attestation].length>160 ||
    /\p{Cc}/u.test(cue.original_source_attestation) || !uuid.test(cue.source_asset_id) ||
    !sha.test(cue.source_sha256))
    invalid("SFX requires bounded original WAV source, timing, energy envelope and author declaration.");
  if(!project.assets.some(a=>a.id===cue.source_asset_id && a.content_sha256===cue.source_sha256 &&
    ["audio/wav","audio/x-wav","audio/wave"].includes(a.media_type)))
    invalid("SFX references a missing or mismatched imported stereo WAV Asset.");
  const scene=project.scenes.find(s=>s.id===cue.scene_id);
  if(!scene)invalid("SFX source scene is missing.");
  let relative=0;
  if(cue.anchor.kind==="scene_start"){
    exactly(cue.anchor,["kind"]);
  }else if(cue.anchor.kind==="beat_start"){
    exactly(cue.anchor,["kind","beat_id"]);
    const id=cue.anchor.beat_id;
    const target=scene.beats.find(b=>b.id===id);
    if(!target)invalid("SFX beat is no longer in the source scene.");
    relative=frame(target.start);
  }else{
    invalid("Unsupported SFX semantic anchor.");
  }
  const at=relative+cue.offset_frames;
  if(at<0 || at+cue.source_frames>frame(scene.duration))
    invalid("SFX source tail leaves its authored scene interval.");
  const begin=frame(scene.start)+at,end=begin+cue.source_frames;
  if(end>36000)invalid("SFX placement exceeds bounded native film.");
  return {cue_id:cue.id,absolute_start_frame:begin,absolute_end_frame:end,
    first_sample:begin*1600,sample_count:cue.source_frames*1600};
}
export function validateSfxProject(project:Project):ScheduledSfx[]{
  const cues=project.audio.sfx_cues??[];
  if(cues.length>64)invalid("SFX cue collection exceeds budget.");
  const seen=new Set<string>();
  const scheduled=cues.map(cue=>{
    if(seen.has(cue.id))invalid("Duplicate semantic SFX identity.");
    seen.add(cue.id);
    return validateSemanticSfx(project,cue);
  }).sort((a,b)=>a.absolute_start_frame-b.absolute_start_frame||a.cue_id.localeCompare(b.cue_id));
  const ending:number[]=[];
  for(const item of scheduled){
    for(let i=ending.length-1;i>=0;i--)if(ending[i]<=item.absolute_start_frame)ending.splice(i,1);
    if(ending.length>=4)invalid("SFX simultaneous mix exceeds four imported sources.");
    ending.push(item.absolute_end_frame);
  }
  return scheduled;
}
/** Replacing an imported original WAV never copies its former author/rights
 * declaration into the new source. The new declaration must be entered by
 * the user after EACH source switch (new draft or existing cue);
 * this remains an attestation, not a
 * copyright-cleared/independently-licensed evidence claim.
 */
export function sourceChangeRightsDeclaration(
  selectedAssetId:string,
  nextAssetId:string,
  currentDeclaration:string,
):string {
  return selectedAssetId!==nextAssetId ? "" : currentDeclaration;
}

/** Read-only original-source SFX editor preview: adding a new cue and
 * replacing a persisted cue MUST share the same full schedule validator.
 * In-place editing never moves a stable cue ID between scenes.
 */
export function previewSemanticSfxEdit(
  project:Project,candidate:SemanticSfxCue,editingId:string|null,
):ScheduledSfx {
  if(editingId!==null){
    const prior=(project.audio.sfx_cues??[]).find(c=>c.id===editingId);
    if(!prior || prior.scene_id!==candidate.scene_id || candidate.id!==editingId)
      invalid("Original SFX edit identity or source scene changed.");
  }else if((project.audio.sfx_cues??[]).some(c=>c.id===candidate.id)){
    invalid("New SFX editor draft duplicates an existing cue ID.");
  }
  const scheduled=validateSemanticSfx(project,candidate);
  const proposed=structuredClone(project);
  const old=proposed.audio.sfx_cues??[];
  proposed.audio.sfx_cues=editingId===null
    ? [...old,structuredClone(candidate)]
    : old.map(c=>c.id===editingId?structuredClone(candidate):c);
  validateSfxProject(proposed);
  return scheduled;
}

export function upsertSfxCue(project:Project,cue:SemanticSfxCue):void{
  const old=project.audio.sfx_cues??[];
  const idx=old.findIndex(c=>c.id===cue.id);
  if(idx>=0 && old[idx].scene_id!==cue.scene_id)invalid("SFX identity cannot move scenes.");
  const next=[...old];
  if(idx>=0)next[idx]=structuredClone(cue);
  else next.push(structuredClone(cue));
  const copy=structuredClone(project);
  copy.audio.sfx_cues=next;
  validateSfxProject(copy);
  project.audio.sfx_cues=next;
}
