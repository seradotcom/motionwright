import {describe,it,expect} from "vitest";
import {fixtureBootstrap} from "./fixture";
import {parseSavedMotionCaptureInventory,parseRecoveredMotionCapture} from "./savedMotionCapture";

const project=fixtureBootstrap.project,profile=project.deliverables[0].id;
const hint={receipt_id:"11111111-1111-4111-8111-111111111111",capture_sha256:"a".repeat(64),
  segment_count:1,frame_count:60,fps_num:30,fps_den:1};
function inventory(){return {schema:"motionwright.saved-motion-inventory/1",project_id:project.id,
  generation:project.generation,revision:project.revision,deliverable_id:profile,capture:{...hint}};}
function response(){
  const source={segment_id:"original-segment",scene_ids:[project.scenes[0].id],frame_count:60};
  return {receipt_id:hint.receipt_id,capture_sha256:hint.capture_sha256,project_resource:"project:"+project.id,
    generation:project.generation,revision:project.revision,deliverable_id:profile,frame_rate:{num:30,den:1},
    segments:[{...source,plan_ref:"original-plan",fingerprint:"b".repeat(64),job_ref:"original-job",
      artifact:{frame_count:60,manifest_sha256:"c".repeat(64)},verification:{}}],
    preview:[{...source,token:"22222222-2222-4222-8222-222222222222"}]};
}
describe("saved complete Motion Canvas capture",()=>{
  it("admits the current closed selector and all original preview handles",()=>{
    expect(parseSavedMotionCaptureInventory(inventory(),project,profile).capture).toEqual(hint);
    const value=response();
    const {receipt_id:_id,capture_sha256:_sha,...expected}=value;
    expect(parseRecoveredMotionCapture(value,project,profile,hint)).toEqual(expected);
    expect(parseSavedMotionCaptureInventory({...inventory(),capture:null},project,profile).capture).toBeNull();
    const equivalent=structuredClone(project);equivalent.deliverables[0].frame_rate={num:"60",den:"2"};
    expect(parseSavedMotionCaptureInventory(inventory(),equivalent,profile).capture).toEqual(hint);
  });
  it.each(["project_id","generation","revision","deliverable_id"] as const)("rejects foreign inventory %s",key=>{
    const value=inventory();Object.assign(value,{[key]:key==="revision"?project.revision+1:"other"});
    expect(()=>parseSavedMotionCaptureInventory(value,project,profile)).toThrow();
  });
  it("rejects authority, paths, malformed counts and a different exact rate in selectors",()=>{
    for(const patch of [{root_id:"forged"},{output_path:"private/file"},{frame_count:36001},
      {segment_count:65},{segment_count:0},{segment_count:61},{fps_num:60},{fps_den:0},
      {receipt_id:"00000000-0000-0000-0000-000000000000"},{capture_sha256:"a".repeat(63)}]){
      expect(()=>parseSavedMotionCaptureInventory({...inventory(),capture:{...hint,...patch}},project,profile)).toThrow();
    }
  });
  it.each(["receipt_id","capture_sha256","project_resource","generation","revision","deliverable_id"] as const)(
    "rejects a changed recovered %s",key=>{
      const value=response();Object.assign(value,{[key]:key==="revision"?project.revision+1:"other"});
      expect(()=>parseRecoveredMotionCapture(value,project,profile,hint)).toThrow();
    });
  it("rejects incomplete, substituted and duplicated source handles",()=>{
    const original=response();
    const wrongFrame=structuredClone(original);wrongFrame.preview[0].frame_count=59;
    const wrongToken=structuredClone(original);wrongToken.preview[0].token="invalid";
    const foreignScene=structuredClone(original);foreignScene.segments[0].scene_ids=["other"];
    const wrongManifest=structuredClone(original);wrongManifest.segments[0].artifact.manifest_sha256="bad";
    const missingFrame=structuredClone(original);missingFrame.segments[0].frame_count=59;
    for(const value of [wrongFrame,wrongToken,foreignScene,wrongManifest,missingFrame,
      {...original,preview:[]},{...original,segments:[]},{...original,root_id:"forged"}]){
      expect(()=>parseRecoveredMotionCapture(value,project,profile,hint)).toThrow();
    }
    const repeated={...original,segments:[...original.segments,...original.segments],
      preview:[...original.preview,...original.preview]};
    expect(()=>parseRecoveredMotionCapture(repeated,project,profile,{...hint,segment_count:2,frame_count:120})).toThrow();
  });
});
