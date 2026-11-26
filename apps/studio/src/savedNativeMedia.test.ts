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
