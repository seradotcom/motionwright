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
    expect(parseHybridMaster(complete(),project,profile,180)).toEqual(complete());
  });
  it("admits a two-source cut and refuses unbounded source counts",()=>{
    expect(parseHybridPreflight({...source(),source_count:2},project,profile).source_count).toBe(2);
    for(const source_count of [1,4,2.5,"2"]){
      expect(()=>parseHybridPreflight({...source(),source_count},project,profile)).toThrow();
    }
  });
  it("uses the original preflight clock for a shorter cut and its saved master",()=>{
    const frame_count=120;
    const preflight=parseHybridPreflight({...source(),frame_count},project,profile);
    const result={...complete(),frame_count,saved_master:{...hint(),frame_count}};
    expect(parseHybridMaster(result,project,profile,preflight.frame_count)).toEqual(result);
    expect(()=>parseHybridMaster(result,project,profile,180)).toThrow();
    expect(()=>parseHybridPreflight({...source(),frame_count:29},project,profile)).toThrow();
  });
  it("retains an observed audio mismatch without inventing authority or weakening the authored clock",()=>{
    const frame_count=31;
    const stream_clock={exact_video_clock:true,audio_start_sample:0,presented_audio_samples:49_584,
      authored_audio_samples:49_600};
    const result={...complete(),schema:"motionwright.hybrid-desktop-master/2",frame_count,
      saved_master:{...hint(),frame_count},stream_clock};
    expect(parseHybridMaster(result,project,profile,frame_count)).toEqual(result);
    expect(parseHybridMaster({...result,stream_clock:{...stream_clock,presented_audio_samples:null}},project,profile,frame_count).stream_clock?.presented_audio_samples).toBeNull();
    for(const patch of [{authored_audio_samples:49_584},{exact_video_clock:"true"},
      {presented_audio_samples:-1},{audio_start_sample:1.5},{delivery_authorized:true}]){
      expect(()=>parseHybridMaster({...result,stream_clock:{...stream_clock,...patch}},project,profile,frame_count)).toThrow();
    }
    const {stream_clock:_,...withoutMeasurement}=result;
    expect(()=>parseHybridMaster(withoutMeasurement,project,profile,frame_count)).toThrow();
    expect(()=>parseHybridMaster({...result,schema:"motionwright.hybrid-desktop-master/1"},project,profile,frame_count)).toThrow();
  });
  it("preserves known completion after the review owner closes",()=>{
    const value={...complete(),saved_master:null,
      review_warning:"The original master completed. Open saved masters to recover it; do not render it again."};
    expect(parseHybridMaster(value,project,profile,180).status).toBe("completed");
    expect(parseHybridMaster(value,project,profile,180).automatic_retry).toBe(false);
    expect(()=>parseHybridMaster({...value,review_warning:null},project,profile,180)).toThrow();
  });
  it("rejects foreign generations, revisions and profiles",()=>{
    for(const patch of [{project_id:"other"},{generation:"other"},{revision:project.revision+1},{deliverable_id:"other"}]){
      expect(()=>parseHybridPreflight({...source(),...patch},project,profile)).toThrow();
      expect(()=>parseHybridMaster({...complete(),...patch},project,profile,180)).toThrow();
    }
  });
  it("refuses changed clocks, digests and caller authority in source and master responses",()=>{
    for(const patch of [{frame_count:181},{fps_num:60},{fps_den:0},{path:"private/output"},{grant:"forged"}]){
      expect(()=>parseHybridPreflight({...source(),...patch},project,profile)).toThrow();
      expect(()=>parseHybridMaster({...complete(),...patch},project,profile,180)).toThrow();
    }
    expect(()=>parseHybridPreflight({...source(),project_sha256:"bad"},project,profile)).toThrow();
    expect(()=>parseHybridMaster({...complete(),automatic_retry:true},project,profile,180)).toThrow();
  });
  it("does not adopt another master or a changed saved clock",()=>{
    for(const patch of [{master_sha256:"d".repeat(64)},{frame_count:181},{fps_num:60},{path:"private/file"}])
      expect(()=>parseHybridMaster({...complete(),saved_master:{...hint(),...patch}},project,profile,180)).toThrow();
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
