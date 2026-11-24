import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { applyChange } from "./api";
import { isStaticNativeGroup, nativeGroupOrderIssue, nativeGroupTreeRows, staticNativeGroupAncestors, staticNativeGroupParent } from "./nativeGroupHierarchy";
import { nativePoseEdit, defaultNativePoseDraft } from "./nativePoseDraft";
import { previewTransform } from "./canvasMotion";

function fixture(){
  const project=structuredClone(fixtureProject),scene=project.scenes[0];
  const child=structuredClone(scene.nodes[0]); child.kind="rectangle";child.name="Child";
  child.x=650;child.y=400;child.width=240;child.height=140;child.opacity=1;child.rotation_deg=0;
  child.parent_id=null;child.keyframes=[];child.relations=[];child.property_locks=[];
  child.style.blend_mode="normal";child.coordinate_space="project_pixels";child.z_index=3;
  const group={...structuredClone(child),id:crypto.randomUUID(),kind:"group",name:"Group",x:500,y:300,width:500,height:350,z_index:2};
  const root={...structuredClone(child),id:crypto.randomUUID(),name:"Root",z_index:0,x:1150};
  child.parent_id=group.id; scene.nodes=[root,group,child];scene.beats=[];scene.renderer="motion-canvas";
  project.locks=[];
  if(project.production_design) project.production_design.camera_reframes=[];
  return {project,scene,group,root,child,profile:project.deliverables[0]};
}
describe("static native root group",()=>{
  it("authors exact child motion without changing the root or world-space terminal pose",async()=>{
    const {project,scene,group,root,child,profile}=fixture();
    expect(isStaticNativeGroup(group)).toBe(true);
    expect(staticNativeGroupParent(child,scene)).toEqual(group);
    expect(nativeGroupOrderIssue(scene)).toBeNull();
    const saved=await applyChange(project,{type:"set_canvas_out_cubic_position_motion",scene_id:scene.id,node_id:child.id,deliverable_id:profile.id,start_x:child.x-60,start_y:child.y,end_frame:30});
    const savedChild=saved.scenes[0].nodes.find(node=>node.id===child.id)!;
    expect(savedChild.keyframes).toHaveLength(4);
    expect(previewTransform(savedChild,0).x).toBe(child.x-60);
    expect(previewTransform(savedChild,1).x).toBe(child.x);
    expect(saved.scenes[0].nodes.find(node=>node.id===group.id)).toEqual(group);
    expect(saved.scenes[0].nodes.find(node=>node.id===root.id)).toEqual(root);
    const draft=defaultNativePoseDraft(child);
    draft.waypoints=[{frame:15,curve:"linear",pose:{...draft.start,x:child.x+30}}];
    expect(nativePoseEdit(project,scene,child,profile,draft).keys).toHaveLength(15);
  });
  it("rejects incompatible painter order, drawable parents and transformed containers without mutating them",()=>{
    const {scene,group,root,child}=fixture();
    group.z_index=-1;
    expect(nativeGroupOrderIssue(scene)).toMatch(/interleave/);
    expect(child.z_index).toBe(3);
    group.z_index=2;
    group.parent_id=root.id;
    expect(staticNativeGroupParent(child,scene)).toBeUndefined();
    expect(nativeGroupOrderIssue(scene)).toMatch(/ancestor/);
    group.parent_id=null;group.rotation_deg=12;
    expect(nativeGroupOrderIssue(scene)).toMatch(/transformed|ancestor/);
  });
});

describe("nested static composition", () => {
  it("keeps exact ancestry, child motion and visible tree depth through Group and Clip containers", async () => {
    const {project, scene, group, child, profile} = fixture();
    const clip = {...structuredClone(group), id:crypto.randomUUID(), kind:"clip_group", name:"Inner Clip", parent_id:group.id, z_index:3};
    const inner = {...structuredClone(group), id:crypto.randomUUID(), name:"Inner Group", parent_id:clip.id, z_index:4};
    child.parent_id = inner.id; child.z_index = 5; scene.nodes.push(clip, inner);
    const original = structuredClone(scene);
    expect(staticNativeGroupAncestors(child, scene)?.map(node => node.id)).toEqual([inner.id, clip.id, group.id]);
    expect(staticNativeGroupParent(child, scene)?.id).toBe(inner.id);
    expect(nativeGroupOrderIssue(scene)).toBeNull();
    expect(nativeGroupTreeRows(scene).map(({node,depth}) => [node.name, depth]))
      .toEqual([["Group",0],["Inner Clip",1],["Inner Group",2],["Child",3],["Root",0]]);
    const saved = await applyChange(project, {type:"set_canvas_out_cubic_position_motion", scene_id:scene.id,
      node_id:child.id, deliverable_id:profile.id, start_x:child.x-60, start_y:child.y, end_frame:30});
    const actual = saved.scenes[0].nodes.find(node=>node.id===child.id)!;
    expect(actual.parent_id).toBe(inner.id);
    expect(previewTransform(actual,0).x).toBe(child.x-60);
    expect(previewTransform(actual,1).x).toBe(child.x);
    expect(nativePoseEdit(project,scene,child,profile,defaultNativePoseDraft(child)).issue).toBeNull();
    expect(scene).toEqual(original);
  });
  it("checks painter order at inner scopes and rejects a broken ancestor beyond a valid immediate parent", () => {
    const {scene,group,child} = fixture();
    const inner={...structuredClone(group),id:crypto.randomUUID(),parent_id:group.id,z_index:2};
    const peer={...structuredClone(child),id:crypto.randomUUID(),parent_id:group.id,z_index:4};
    child.parent_id=inner.id;child.z_index=3;scene.nodes.push(inner,peer);
    expect(nativeGroupOrderIssue(scene)).toBeNull();
    peer.z_index=2; expect(nativeGroupOrderIssue(scene)).toMatch(/interleave/);
    peer.z_index=4; group.parent_id=inner.id;
    expect(staticNativeGroupAncestors(child,scene)).toBeUndefined();
    expect(nativeGroupOrderIssue(scene)).toMatch(/ancestor/);
    group.parent_id=crypto.randomUUID();
    expect(staticNativeGroupParent(child,scene)).toBeUndefined();
    group.parent_id=null;group.opacity=0.5;
    expect(staticNativeGroupParent(child,scene)).toBeUndefined();
    expect(nativeGroupTreeRows(scene)).toHaveLength(scene.nodes.length);
  });
});
