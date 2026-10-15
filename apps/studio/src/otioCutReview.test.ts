import {describe,expect,it} from "vitest";
import {inspectOtioCutPreview,otioHasAbsoluteAnchors} from "./otioCutReview";
import {reviewableOtioProject,sampleOtioPreview} from "./otioCutReviewTestFixtures";

describe("source-bound OTIO cut review is a non-executable proposal",()=>{
  it("admits only an exact reviewed reordering and end trim, never marks imported",()=>{
    const project=reviewableOtioProject();
    const original=structuredClone(project);
    const data=sampleOtioPreview(project);
    const parsed=inspectOtioCutPreview(project,JSON.stringify(data));
    expect(parsed).toEqual(data);
    expect(parsed.edit_imported).toBe(false);
    expect(parsed.rendering_performed).toBe(false);
    expect(otioHasAbsoluteAnchors(project)).toBe(true);
    expect(project).toEqual(original);
  });
  it.each(["project_id","generation","expected_project_revision","source_project_sha256"])(
    "rejects a stale, unrelated or malformed %s",key=>{
      const project=reviewableOtioProject(),data=sampleOtioPreview(project);
      const forged={...data,[key]:key==="expected_project_revision"?project.revision+1:"different"};
      expect(()=>inspectOtioCutPreview(project,JSON.stringify(forged))).toThrow();
    }
  );
  it.each([
    "edit_imported","source_media_relinked","rendering_performed","publication_approved",
  ])("never trusts a claim that %s is completed",key=>{
    const project=reviewableOtioProject(),data=sampleOtioPreview(project);
    expect(()=>inspectOtioCutPreview(project,JSON.stringify({...data,[key]:true}))).toThrow();
  });
  it("rejects media cardinality mismatch and forged unknown fields",()=>{
    const project=reviewableOtioProject(),data=sampleOtioPreview(project);
    expect(()=>inspectOtioCutPreview(project,JSON.stringify({...data,media_refs_verified:1}))).toThrow();
    expect(()=>inspectOtioCutPreview(project,JSON.stringify({...data,launch_new_provider:true}))).toThrow();
    const stripped={...data};delete (stripped as Partial<typeof data>).property_fidelity;
    expect(()=>inspectOtioCutPreview(project,JSON.stringify(stripped))).toThrow();
  });
  it("rejects a changed fidelity status or missing acknowledged property",()=>{
    const project=reviewableOtioProject(),data=sampleOtioPreview(project);
    data.property_fidelity[0].status="authentic_media";
    expect(()=>inspectOtioCutPreview(project,JSON.stringify(data))).toThrow();
    const noLoss=sampleOtioPreview(project);noLoss.loss_report=[];
    expect(()=>inspectOtioCutPreview(project,JSON.stringify(noLoss))).toThrow();
  });
  it("cannot add, delete, duplicate or invent original scenes",()=>{
    const project=reviewableOtioProject(),data=sampleOtioPreview(project);
    const forged=[...data.proposed_scene_order];forged[2]=forged[0];
    expect(()=>inspectOtioCutPreview(project,JSON.stringify({...data,proposed_scene_order:forged}))).toThrow();
    expect(()=>inspectOtioCutPreview(project,JSON.stringify({...data,proposed_scene_order:[...data.proposed_scene_order,"new"]}))).toThrow();
  });
  it("blocks nonminimal, duplicated, reordered or unsafe native edit claims",()=>{
    const project=reviewableOtioProject(),data=sampleOtioPreview(project);
    expect(()=>inspectOtioCutPreview(project,JSON.stringify({
      ...data,native_change_proposals:[...data.native_change_proposals].reverse(),
    }))).toThrow();
    expect(()=>inspectOtioCutPreview(project,JSON.stringify({
      ...data,native_change_proposals:[...data.native_change_proposals,...data.native_change_proposals],
    }))).toThrow();
    expect(()=>inspectOtioCutPreview(project,JSON.stringify({
      ...data,native_change_proposals:[],
    }))).toThrow();
    expect(()=>inspectOtioCutPreview(project,JSON.stringify({
      ...data,native_change_proposals:[{type:"download_source",uri:"http://invalid.example"}],
    }))).toThrow();
  });
  it("cannot convert external NLE clip extension into a Motionwright end trim",()=>{
    const project=reviewableOtioProject(),data=sampleOtioPreview(project);
    const duration={num:"13",den:"1"};
    data.native_change_proposals[1]={type:"set_scene_duration",scene_id:project.scenes[2].id,duration};
    expect(()=>inspectOtioCutPreview(project,JSON.stringify(data))).toThrow(/cannot extend/i);
  });
  it("honors existing project and scene content locks plus human approval",()=>{
    const project=reviewableOtioProject(),data=sampleOtioPreview(project);
    project.scenes[0].status="approved";
    expect(()=>inspectOtioCutPreview(project,JSON.stringify(data))).toThrow(/review/i);
    project.scenes[0].status="draft";
    project.locks.push({id:"test-lock",resource:"scene:"+project.scenes[2].id,kind:"timing",note:"source lock"});
    expect(()=>inspectOtioCutPreview(project,JSON.stringify(data))).toThrow(/locked/i);
  });
  it("refuses invalid pasted JSON and over-budget input",()=>{
    const project=reviewableOtioProject();
    expect(()=>inspectOtioCutPreview(project,"not json")).toThrow();
    expect(()=>inspectOtioCutPreview(project," ".repeat(130000))).toThrow();
  });
});
