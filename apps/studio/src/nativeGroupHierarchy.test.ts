import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { applyChange } from "./api";
import { isStaticNativeGroup, nativeGroupOrderIssue, staticNativeGroupParent } from "./nativeGroupHierarchy";
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
  it("rejects incompatible painter order, nested and animated source parents without mutating them",()=>{
    const {scene,group,root,child}=fixture();
    group.z_index=-1;
    expect(nativeGroupOrderIssue(scene)).toMatch(/interleave/);
    expect(child.z_index).toBe(3);
    group.z_index=2;
    group.parent_id=root.id;
    expect(staticNativeGroupParent(child,scene)).toBeUndefined();
    expect(nativeGroupOrderIssue(scene)).toMatch(/static root/);
    group.parent_id=null;group.rotation_deg=12;
    expect(nativeGroupOrderIssue(scene)).toMatch(/transformed|static root/);
  });
});
