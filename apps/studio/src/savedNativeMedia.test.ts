import {describe,it,expect} from "vitest";
import {fixtureBootstrap} from "./fixture";
import {parseSavedMasterInventory} from "./savedNativeMedia";

const project=fixtureBootstrap.project,profile=project.deliverables[0].id;
function inventory(){return {schema:"motionwright.saved-master-inventory/1",project_id:project.id,
  generation:project.generation,revision:project.revision,deliverable_id:profile,truncated:false,
  items:[{mux_receipt_id:"11111111-1111-4111-8111-111111111111",master_sha256:"a".repeat(64),
    frame_count:120,fps_num:30,fps_den:1}]};}
describe("saved original media discovery",()=>{
  it("keeps only exact current source references, without paths or authority",()=>{
    expect(parseSavedMasterInventory(inventory(),project,profile)).toEqual(inventory());
    const equivalent=structuredClone(project);equivalent.deliverables[0].frame_rate={num:"60",den:"2"};
    expect(parseSavedMasterInventory(inventory(),equivalent,profile)).toEqual(inventory());
    expect(parseSavedMasterInventory({...inventory(),items:[]},project,profile).items).toEqual([]);
  });
  it.each(["project_id","generation","revision","deliverable_id"] as const)("rejects changed %s",(key)=>{
    const value=inventory();Object.assign(value,{[key]:key==="revision"?project.revision+1:"22222222-2222-4222-8222-222222222222"});
    expect(()=>parseSavedMasterInventory(value,project,profile)).toThrow();
  });
  it("refuses extra fields, duplicate identities, unbounded pages and absent profiles",()=>{
    const value=inventory();
    for(const invalid of [{...value,export_token:"forged"},
      {...value,items:[{...value.items[0],path:"private/original.mp4"}]},
      {...value,items:[value.items[0],value.items[0]]},
      {...value,items:Array.from({length:33},()=>value.items[0])},
      {...value,items:null}]){
      expect(()=>parseSavedMasterInventory(invalid,project,profile)).toThrow();
    }
    expect(()=>parseSavedMasterInventory(value,{...project,deliverables:[]},profile)).toThrow();
  });
  it.each([
    {master_sha256:"a".repeat(63)},{mux_receipt_id:"00000000-0000-0000-0000-000000000000"},
    {frame_count:36001},{frame_count:0},{fps_num:60},{fps_num:121,fps_den:1},{fps_den:0},{fps_num:0x100000000},
  ])("refuses invalid original identity or clock %j",(fields)=>{
    const value=inventory();Object.assign(value.items[0],fields);
    expect(()=>parseSavedMasterInventory(value,project,profile)).toThrow();
  });
});


import {parseSavedMasterExportReceipt} from "./savedNativeMedia";
describe("saved original copy receipt",()=>{
  const hint=inventory().items[0],destination="E:\\Exports\\original.mp4";
  function result(){return {destination,size_bytes:104557,sha256:hint.master_sha256,
    revision:project.revision,deliverable_id:profile,source_current:true,integrity_manifest_path:null};}
  it("admits an exact known original and preserves historical status",()=>{
    expect(parseSavedMasterExportReceipt(result(),project,profile,hint,destination,false)).toEqual(result());
    expect(parseSavedMasterExportReceipt({...result(),source_current:false},project,profile,hint,destination,false).source_current).toBe(false);
    const manifest=destination+".motionwright-integrity.json";
    expect(parseSavedMasterExportReceipt({...result(),integrity_manifest_path:manifest},project,profile,hint,destination,true).integrity_manifest_path).toBe(manifest);
  });
  it.each([
    {sha256:"b".repeat(64)},{destination:"E:\\Elsewhere\\different.mp4"},{revision:project.revision+1},
    {deliverable_id:"other"},{size_bytes:1024*1024*1024+1},{source_current:"true"},
    {integrity_manifest_path:"E:\\Exports\\another.json"},{export_token:"forged"},
  ])("rejects a substituted or malformed copy result %j",fields=>{
    expect(()=>parseSavedMasterExportReceipt({...result(),...fields},project,profile,hint,destination,false)).toThrow();
  });
});
