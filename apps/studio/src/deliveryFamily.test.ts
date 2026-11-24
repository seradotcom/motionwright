import { describe,expect,it } from "vitest";
import { fixtureProject } from "./fixture";
import { applyDeliverySettingPropagation, buildDeliverySettingPropagation, deliveryFamilyRows,
  readDeliverySetting, SHARED_DELIVERY_FIELDS } from "./deliveryFamily";
import type { DeliverableProfile, Project } from "./types";

function family(){
  const project:Project=structuredClone(fixtureProject);
  const source={...project.deliverables[0],brand_profile:"Campaign 2",parent_profile_id:null,source_revision:null};
  const child:DeliverableProfile={...structuredClone(source),id:"10000000-0000-4000-8000-000000000001",name:"Vertical",
    width:1080,height:1920,parent_profile_id:source.id,source_revision:0,brand_profile:"Campaign 1",adaptation_notes:["Vertical composition"]};
  const grand:DeliverableProfile={...structuredClone(child),id:"10000000-0000-4000-8000-000000000002",name:"Spanish vertical",
    parent_profile_id:child.id,language:"es",brand_profile:"Locale override",adaptation_notes:["Locale-specific treatment"]};
  const keep:DeliverableProfile={...structuredClone(child),id:"10000000-0000-4000-8000-000000000003",name:"Separate override",brand_profile:"Special brand"};
  const unrelated:DeliverableProfile={...structuredClone(source),id:"10000000-0000-4000-8000-000000000004",name:"Unrelated",
    parent_profile_id:null,source_revision:null,brand_profile:"Independent"};
  project.deliverables=[source,child,grand,keep,unrelated];
  project.reviews=[];project.locks=[];
  return {project,source,child,grand,keep,unrelated};
}
describe("explicit source-bound family propagation",()=>{
  it("updates only selected descendants and the exact chosen field, retaining locale, cut, aspect and history",()=>{
    const {project,source,child,grand,keep,unrelated}=family();
    const before=structuredClone(project);
    expect(deliveryFamilyRows(project,source.id,"brand_profile").map(row=>row.profile.id)).toEqual([child.id,grand.id,keep.id]);
    const p=buildDeliverySettingPropagation(project,source.id,"brand_profile",[child.id,grand.id]);
    expect(p.targets.map(t=>t.expected.value)).toEqual(["Campaign 1","Locale override"]);
    expect(applyDeliverySettingPropagation(project,p)).toEqual([source,{...child,brand_profile:"Campaign 2"},{...grand,brand_profile:"Campaign 2"},keep,unrelated]);
    expect(project).toEqual(before);
  });
  it("rejects stale revision, generation, branch, project, tampered source, target and mixed field",()=>{
    const {project,source,child}=family(),p=buildDeliverySettingPropagation(project,source.id,"brand_profile",[child.id]);
    for(const patch of [{expected_revision:project.revision+1},{generation:"wrong"},{branch_id:"wrong"},{project_id:"wrong"},
      {source:{field:"brand_profile" as const,value:"not saved"}},
      {targets:[{profile_id:child.id,expected:{field:"brand_profile" as const,value:"not saved"}}]},
      {targets:[{profile_id:child.id,expected:{field:"video_codec" as const,value:child.video_codec}}]}]){
      expect(()=>applyDeliverySettingPropagation(project,{...p,...patch})).toThrow();
    }
  });
  it("rejects source, unrelated, duplicate, no-op, locked and reviewed rows without a partial update",()=>{
    const {project,source,child,grand,unrelated}=family();
    for(const ids of [[],[source.id],[unrelated.id],[child.id,child.id]])expect(()=>buildDeliverySettingPropagation(project,source.id,"brand_profile",ids)).toThrow();
    const p=buildDeliverySettingPropagation(project,source.id,"brand_profile",[child.id,grand.id]);
    project.locks=[{id:crypto.randomUUID(),resource:"deliverable:"+grand.id,kind:"renderer",note:"Protect"}];
    expect(()=>applyDeliverySettingPropagation(project,p)).toThrow();
    expect(project.deliverables[1]).toEqual(child);
    project.locks=[];project.reviews=[{id:crypto.randomUUID(),kind:"creative",status:"resolved",body:"Protected",resolution:"Reviewed",
      created_at:"2026-11-24T00:00:00Z",resolved_at:"2026-11-24T00:00:00Z",anchor:{resource:"deliverable:"+grand.id,branch_id:project.active_branch,
        revision:project.revision,start:null,end:null,locale:"es",profile_id:grand.id}}];
    expect(deliveryFamilyRows(project,source.id,"brand_profile").find(r=>r.profile.id===grand.id)?.reason).toContain("Reviewed");
    expect(()=>applyDeliverySettingPropagation(project,p)).toThrow();
    project.reviews=[];project.deliverables[1]={...child,brand_profile:source.brand_profile};
    expect(()=>buildDeliverySettingPropagation(project,source.id,"brand_profile",[child.id])).toThrow();
  });
  it("preserves exact rational clock, does not transfer unrelated settings, and keeps null brand explicit",()=>{
    const {project,source,child}=family();
    const root={...source,frame_rate:{num:"24000",den:"1001"},brand_profile:null,video_codec:"hevc" as const};
    project.deliverables[0]=root;
    project.deliverables[1]={...child,frame_rate:{num:"30",den:"1"}};
    const p=buildDeliverySettingPropagation(project,root.id,"frame_rate",[child.id]);
    const result=applyDeliverySettingPropagation(project,p)[1];
    expect(result).toEqual({...project.deliverables[1],frame_rate:root.frame_rate});
    expect(readDeliverySetting(root,"brand_profile")).toEqual({field:"brand_profile",value:null});
    for(const field of SHARED_DELIVERY_FIELDS)expect(readDeliverySetting(root,field.field).field).toBe(field.field);
  });
});
