import {describe,it,expect} from "vitest";
import {fixtureBootstrap} from "./fixture";
import {parseHybridPreflight,parseHybridMaster,HybridNativeOperationError} from "./hybridNative";
const project=fixtureBootstrap.project,profile=project.deliverables[0].id;
const source=()=>({schema:"motionwright.hybrid-source-preflight/1",project_id:project.id,
  generation:project.generation,revision:project.revision,deliverable_id:profile,
  project_sha256:"a".repeat(64),source_count:3,frame_count:180,width:1920,height:1080,
  fps_num:30,fps_den:1,source_audio_sha256:"b".repeat(64)});
const hint=()=>({mux_receipt_id:"11111111-1111-4111-8111-111111111111",
  master_sha256:"c".repeat(64),frame_count:180,fps_num:30,fps_den:1});
const complete=()=>({schema:"motionwright.hybrid-desktop-master/1",status:"completed",project_id:project.id,
  generation:project.generation,revision:project.revision,deliverable_id:profile,
  master_sha256:"c".repeat(64),frame_count:180,fps_num:30,fps_den:1,saved_master:hint(),
  review_warning:null,automatic_retry:false});
describe("original hybrid Desktop result",()=>{
  it("admits only the current finite source and the completed original receipt",()=>{
    expect(parseHybridPreflight(source(),project,profile)).toEqual(source());
    expect(parseHybridMaster(complete(),project,profile)).toEqual(complete());
  });
  it("preserves known completion after the review owner closes",()=>{
    const value={...complete(),saved_master:null,
      review_warning:"The original master completed. Open saved masters to recover it; do not render it again."};
    expect(parseHybridMaster(value,project,profile).status).toBe("completed");
    expect(parseHybridMaster(value,project,profile).automatic_retry).toBe(false);
    expect(()=>parseHybridMaster({...value,review_warning:null},project,profile)).toThrow();
  });
  it("rejects foreign generations, revisions and profiles",()=>{
    for(const patch of [{project_id:"other"},{generation:"other"},{revision:project.revision+1},{deliverable_id:"other"}]){
      expect(()=>parseHybridPreflight({...source(),...patch},project,profile)).toThrow();
      expect(()=>parseHybridMaster({...complete(),...patch},project,profile)).toThrow();
    }
  });
  it("refuses changed clocks, digests and caller authority in source and master responses",()=>{
    for(const patch of [{frame_count:181},{fps_num:60},{fps_den:0},{path:"private/output"},{grant:"forged"}]){
      expect(()=>parseHybridPreflight({...source(),...patch},project,profile)).toThrow();
      expect(()=>parseHybridMaster({...complete(),...patch},project,profile)).toThrow();
    }
    expect(()=>parseHybridPreflight({...source(),project_sha256:"bad"},project,profile)).toThrow();
    expect(()=>parseHybridMaster({...complete(),automatic_retry:true},project,profile)).toThrow();
  });
  it("does not adopt another master or a changed saved clock",()=>{
    for(const patch of [{master_sha256:"d".repeat(64)},{frame_count:181},{fps_num:60},{path:"private/file"}])
      expect(()=>parseHybridMaster({...complete(),saved_master:{...hint(),...patch}},project,profile)).toThrow();
  });
  it("keeps UNKNOWN and partial effects explicit without creating a retry",()=>{
    const value={schema:"motionwright.hybrid-desktop-failure/1",status:"unknown",stage:"master",
      outcome_known:false,may_have_native_effects:true,automatic_retry:false,message:"Original mux is unknown."};
    expect(new HybridNativeOperationError(value,true).failure).toEqual(value);
    const stopped={...value,status:"stopped",stage:"timeline",outcome_known:true,message:"Owner closed."};
    expect(new HybridNativeOperationError(stopped,true).failure).toEqual(stopped);
    for(const bad of ["transport lost",{...value,automatic_retry:true},{...value,status:"completed"}]){
      const error=new HybridNativeOperationError(bad,true);
      expect(error.failure.outcome_known).toBe(false);
      expect(error.failure.may_have_native_effects).toBe(true);
      expect(error.failure.automatic_retry).toBe(false);
    }
  });
});
