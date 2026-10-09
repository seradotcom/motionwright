import type { CanvasNode, Project } from "./types";
import { seconds } from "./types";
import { emptyProductionDesign, mergeComponentNodes, sameValue } from "./creativeProduction";

export interface CreativePatchRecord {
  id: string; scene_id: string; source_revision: number; rationale: string;
  before: CanvasNode[]; after: CanvasNode[]; reverts: string | null;
}
interface Snapshot {source_revision:number;scene_id:string;before:CanvasNode[];after:CanvasNode[];}
const encodedSize=(value:unknown)=>new TextEncoder().encode(JSON.stringify(value)).length;
const editableFields=["x","y","width","height","rotation_deg","opacity","text","style","keyframes"] as const;
export function validateCreativePatchRecord(record:CreativePatchRecord):void {
  if(!record.rationale.trim() || new TextEncoder().encode(record.rationale).length>4000 || /[\u0000-\u0009\u000b-\u001f\u007f]/u.test(record.rationale)
    || !record.before.length || record.before.length>128 || record.before.length!==record.after.length || sameValue(record.before,record.after)
    || record.reverts===record.id || new Set(record.before.map(n=>n.id)).size!==record.before.length) throw new Error("Invalid creative patch record shape or unchanged proposal.");
  record.before.forEach((before,index)=>{
    const after=record.after[index];
    if(before.id!==after.id) throw new Error("Patch record identities differ.");
    const editable=structuredClone(before);
    for(const field of editableFields) Object.assign(editable,{[field]:structuredClone(after[field])});
    if(!sameValue(editable,after)) throw new Error("Patch record changes fields outside the creative edit contract.");
  });
  if(encodedSize(record)>256*1024) throw new Error("Creative patch record exceeds its byte budget.");
}
export function appendCreativePatchRecord(project:Project,preview:Snapshot,rationale:string,reverts:string|null):void {
  if(preview.source_revision!==project.revision) throw new Error("Creative preview is stale.");
  const record:CreativePatchRecord={id:crypto.randomUUID(),scene_id:preview.scene_id,source_revision:project.revision,rationale,
    before:structuredClone(preview.before),after:structuredClone(preview.after),reverts};
  validateCreativePatchRecord(record);
  const design=structuredClone(project.production_design ?? emptyProductionDesign());
  design.patches=[...(design.patches ?? []),record].slice(-64);
  if(encodedSize(design.patches)>4*1024*1024) throw new Error("Creative undo window exceeds its byte budget.");
  project.production_design=design;
}
/** Pure reverse proposal. It never writes application history or uses a prior generation as authority. */
export function previewCreativePatchUndo(project:Project,patchId:string) {
  const records=project.production_design?.patches ?? [];
  const record=records.find(r=>r.id===patchId);
  if(!record) throw new Error("Creative patch is no longer in the reversible window.");
  validateCreativePatchRecord(record);
  if(records.some(r=>r.reverts===patchId)) throw new Error("This patch has already been reverted; undo its inverse to redo.");
  const scene=project.scenes.find(s=>s.id===record.scene_id);
  if(!scene) throw new Error("Original scene no longer exists.");
  if(project.locks.some(lock=>["project:"+project.id,"scene:"+scene.id].includes(lock.resource) && ["content","style","position","timing"].includes(lock.kind))) throw new Error("Creative scope is locked.");
  const merged=mergeComponentNodes(record.after,scene.nodes,record.before);
  const scope=new Set(record.before.map(n=>n.id));
  const before=scene.nodes.filter(n=>scope.has(n.id)),after=merged.filter(n=>scope.has(n.id));
  if(sameValue(before,after)) throw new Error("Undo would not change the current project.");
  if(after.some(n=>n.keyframes.some(k=>!Number.isFinite(seconds(k.at)) || seconds(k.at)<0 || seconds(k.at)>=seconds(scene.duration)))) throw new Error("Undo would strand a keyframe outside the current scene.");
  let num=BigInt(scene.start.num)*BigInt(scene.duration.den)+BigInt(scene.duration.num)*BigInt(scene.start.den);
  let den=BigInt(scene.start.den)*BigInt(scene.duration.den),a=num<0n?-num:num,b=den;
  while(b!==0n){const r=a%b;a=b;b=r;}num/=a||1n;den/=a||1n;
  return {source_revision:project.revision,scene_id:scene.id,before:structuredClone(before),after:structuredClone(after),
    dirty_start:scene.start,dirty_end:{num:String(num),den:String(den)},kind:"semantic_diff_full_scene_invalidation_not_pixel_verification" as const};
}
