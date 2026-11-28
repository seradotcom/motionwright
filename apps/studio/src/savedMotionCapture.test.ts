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
  it("admits covered audio cue frames only with the original renderer observation",()=>{
    const authored=structuredClone(project),cueId="33333333-3333-4333-8333-333333333333";
    authored.audio.cues.push({id:cueId,at:{num:"0",den:"1"},label:"Cue",source_segment_id:null,evidence:"manual"});
    authored.production_design ??= {plan:null,heroes:[],capsules:[]};
    authored.production_design.delivery_recipes = {[profile]:{schema:"motionwright.delivery-recipe/1",
      scene_intents:[],original_mix:null,motion_canvas_audio_cues:"preserve_authored_time"}};
    const film="d".repeat(64), manifest="c".repeat(64), observation="e".repeat(64);
    const value=response();
    const coverage={film_sha256:film,manifest_sha256:manifest,observation_sha256:observation,frames:[{cue_id:cueId,frame:0}]};
    Object.assign(value.segments[0],{audio_cue_frame_coverage:coverage,verification:{measurement:{
      intent_digest:film,coverage:{first_frame:0,end_frame_exclusive:60,observed_frames:60,exhaustive:true,
        fps:{num:30,den:1},artifact_digest:manifest,observation_digest:observation}
    }}});
    expect(parseRecoveredMotionCapture(value,authored,profile,hint).segments[0].audio_cue_frame_coverage).toEqual(coverage);
    for(const patch of [
      {film_sha256:"f".repeat(64)}, {manifest_sha256:"f".repeat(64)}, {observation_sha256:"f".repeat(64)},
      {frames:[{cue_id:cueId,frame:60}]}, {frames:[{cue_id:cueId,frame:-1}]},
      {frames:[{cue_id:hint.receipt_id,frame:0}]}, {frames:[{cue_id:cueId,frame:0},{cue_id:cueId,frame:1}]},
      {frames:[]}, {local_path:"untrusted"},
    ]){
      const changed=structuredClone(value);
      Object.assign(changed.segments[0],{audio_cue_frame_coverage:{...coverage,...patch}});
      expect(()=>parseRecoveredMotionCapture(changed,authored,profile,hint)).toThrow();
    }
    const unknown=structuredClone(authored);unknown.audio.cues.at(-1)!.evidence="unknown";
    expect(()=>parseRecoveredMotionCapture(value,unknown,profile,hint)).toThrow(/audio cue/);
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
