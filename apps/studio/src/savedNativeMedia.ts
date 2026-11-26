import type {Project} from "./types";

export interface SavedMasterHint {
  mux_receipt_id:string;
  master_sha256:string;
  frame_count:number;
  fps_num:number;
  fps_den:number;
}
export interface SavedMasterInventory {
  schema:"motionwright.saved-master-inventory/1";
  project_id:string;
  generation:string;
  revision:number;
  deliverable_id:string;
  items:SavedMasterHint[];
  truncated:boolean;
}
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const sha=/^[0-9a-f]{64}$/;
function closed(value:unknown,keys:readonly string[]):Record<string,unknown>{
  if(!value||typeof value!=="object"||Array.isArray(value)
    ||Object.keys(value).length!==keys.length||!keys.every(key=>Object.hasOwn(value,key)))
    throw new Error("Saved original media response is invalid.");
  return value as Record<string,unknown>;
}
function positive(value:unknown,max:number):value is number{
  return typeof value==="number"&&Number.isSafeInteger(value)&&value>0&&value<=max;
}
export function parseSavedMasterInventory(value:unknown,project:Project,deliverableId:string):SavedMasterInventory{
  const profile=project.deliverables.find(item=>item.id===deliverableId);
  const num=profile?.frame_rate?.num,den=profile?.frame_rate?.den;
  const integer=/^[1-9][0-9]{0,19}$/;
  const rate=typeof num==="string"&&typeof den==="string"&&integer.test(num)&&integer.test(den)
    ?{num:BigInt(num),den:BigInt(den)}:null;
  const data=closed(value,["schema","project_id","generation","revision","deliverable_id","items","truncated"]);
  if(data.schema!=="motionwright.saved-master-inventory/1"
    ||data.project_id!==project.id||data.generation!==project.generation
    ||data.revision!==project.revision||!Number.isSafeInteger(data.revision)||Number(data.revision)<0
    ||data.deliverable_id!==deliverableId
    ||!profile||!rate||rate.num>0xffffffffffffffffn||rate.den>0xffffffffffffffffn
    ||typeof data.truncated!=="boolean"||!Array.isArray(data.items)||data.items.length>32)
    throw new Error("Saved original media source differs from the current project.");
  const ids=new Set<string>();
  const items=data.items.map(value=>{
    const item=closed(value,["mux_receipt_id","master_sha256","frame_count","fps_num","fps_den"]);
    if(typeof item.mux_receipt_id!=="string"||!uuid.test(item.mux_receipt_id)||ids.has(item.mux_receipt_id)
      ||typeof item.master_sha256!=="string"||!sha.test(item.master_sha256)
      ||!positive(item.frame_count,36000)||!positive(item.fps_num,0xffffffff)
      ||!positive(item.fps_den,0xffffffff)||item.fps_num/item.fps_den>120
      ||BigInt(item.fps_num)*rate.den!==BigInt(item.fps_den)*rate.num)
      throw new Error("Saved original media reference is invalid.");
    ids.add(item.mux_receipt_id);
    return item as unknown as SavedMasterHint;
  });
  return {...data,items} as unknown as SavedMasterInventory;
}


/** Admit only the returned copy of the selected original; never mint authority. */
export function parseSavedMasterExportReceipt(
  value:unknown,project:Project,deliverableId:string,hint:SavedMasterHint,destination:string,integrity:boolean,
):import("./types").MasterExportReceipt{
  const data=closed(value,["destination","size_bytes","sha256","revision","deliverable_id","source_current","integrity_manifest_path"]);
  if(data.destination!==destination||data.sha256!==hint.master_sha256
    ||data.revision!==project.revision||data.deliverable_id!==deliverableId
    ||typeof data.source_current!=="boolean"||!positive(data.size_bytes,1024*1024*1024)
    ||data.integrity_manifest_path!==(integrity?destination+".motionwright-integrity.json":null))
    throw new Error("Saved MP4 delivery returned an unexpected result. Inspect the destination before making another copy.");
  return data as unknown as import("./types").MasterExportReceipt;
}
