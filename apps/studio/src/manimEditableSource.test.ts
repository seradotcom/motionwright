import { describe, expect, it, vi } from "vitest";
import { fixtureProject } from "./fixture";
import { MANIM_EDITABLE_MIME, acknowledgeManimPending, confirmManimEdit, exportManimSource, inspectAttachedManim, manimExportSubject,
  manimScope, matchingManimHistory, pendingScopeKey, previewCurrent, previewManimEdit, readManimPending,
  readManimPreview, readManimSnapshot, retainManimPending, type ManimEditRequest, type ManimPending, type ManimPreview, type ManimSnapshot } from "./manimEditableSource";

const uuid=(n:number)=>"50000000-0000-4000-8000-"+String(n).padStart(12,"0");
const digest=(n:string)=>n.repeat(64);
function fixture() {
  const project=structuredClone(fixtureProject),scene=project.scenes[0],node=scene.nodes[0];
  scene.renderer="manim-community";
  const scope=manimScope(project,scene.id,project.deliverables[0].id);
  const snapshot:ManimSnapshot={schema:"motionwright.manim-editable-source/1",source:scope,bundle_sha256:digest("a"),
    size_bytes:"4096",media_type:MANIM_EDITABLE_MIME,native_execution_performed:false,project_modified:false};
  const request:ManimEditRequest={source:scope,original_asset_id:uuid(1),original_sha256:digest("b"),
    edited_asset_id:uuid(2),edited_sha256:digest("c"),node_id:node.id,property:"transform"};
  // These synthetic metadata rows test client identity only; actual imported
  // compiler package bytes are tested independently by the Rust service.
  const seed=project.assets[0];
  project.assets=[{...seed,id:request.original_asset_id,name:"original.mwedit.zip",media_type:MANIM_EDITABLE_MIME,content_sha256:request.original_sha256},
    {...seed,id:request.edited_asset_id,name:"edited.mwedit.zip",media_type:MANIM_EDITABLE_MIME,content_sha256:request.edited_sha256}];
  const result:ManimPreview={schema:"motionwright.manim-editable-source-edit/1",request,preview:{
    expected:{resource:"project:"+project.id,generation:project.generation,revision:project.revision},
    original_source_asset_id:request.original_asset_id,edited_source_asset_id:request.edited_asset_id,
    original_source_sha256:request.original_sha256,edited_source_sha256:request.edited_sha256,
    current_plan_sha256:digest("d"),proposed_plan_sha256:digest("e"),
    change:{type:"transform_canvas_node",scene_id:scene.id,node_id:node.id,transform:{x:node.x+12,y:node.y,width:node.width,height:node.height,rotation_deg:node.rotation_deg,opacity:node.opacity}},
    native_execution_performed:false,imported_source_authority_used:false}};
  return {project,scene,node,scope,snapshot,request,result};
}
function storage() {
  const map=new Map<string,string>();
  return {get length(){return map.size},key:(i:number)=>[...map.keys()][i]??null,
    getItem:(k:string)=>map.get(k)??null,setItem:(k:string,v:string)=>{map.set(k,v)},removeItem:(k:string)=>{map.delete(k)}};
}
describe("Manim closed source exchange",()=>{
  it("binds an inert compiler snapshot and one export to exact bytes, path and grant",async()=>{
    const {scope,snapshot}=fixture(),path="E:\\Exports\\original.mwedit.zip";
    expect(readManimSnapshot(snapshot,scope)).toEqual(snapshot);
    expect(()=>readManimSnapshot({...snapshot,native_execution_performed:true},scope)).toThrow();
    const grant={token:uuid(3),effect:"deliver_local",one_time:true,project_id:scope.project_id,generation:scope.generation,revision:scope.revision};
    const exported={...snapshot,schema:"motionwright.manim-editable-source-export/1",path};
    const call=vi.fn(async(command:string,_args:Record<string,unknown>)=>command==="issue_effect_grant"?grant:exported);
    expect(await exportManimSource(snapshot,path,call)).toEqual(exported);
    expect(call.mock.calls.map(row=>row[0])).toEqual(["issue_effect_grant","export_manim_editable_source"]);
    expect(call.mock.calls[0][1]).toMatchObject({request:{subject:manimExportSubject(snapshot,path)}});
    const mismatch=vi.fn(async(command:string)=>command==="issue_effect_grant"?{...grant,revision:scope.revision+1}:exported);
    await expect(exportManimSource(snapshot,path,mismatch)).rejects.toThrow();
    expect(mismatch).toHaveBeenCalledTimes(1);
  });
  it("refuses stale, unrelated or execution-bearing edit responses before a normal change can be reviewed",async()=>{
    const {project,request,result}=fixture();
    const call=vi.fn(async()=>result);
    expect(await previewManimEdit(request,call)).toEqual(result);
    expect(previewCurrent(project,result)).toBe(true);
    expect(previewCurrent({...project,revision:project.revision+1},result)).toBe(false);
    for(const changed of [
      {...result,request:{...request,node_id:uuid(8)}},
      {...result,preview:{...result.preview,imported_source_authority_used:true}},
      {...result,preview:{...result.preview,edited_source_sha256:digest("f")}},
      {...result,preview:{...result.preview,change:{...result.preview.change,node_id:uuid(8)}}},
      {...result,preview:{...result.preview,change:{...result.preview.change,extra:"not allowed"}}},
    ])expect(()=>readManimPreview(changed,request)).toThrow();
  });
  it("checks the attached original's exact digest, controls and absence of execution",async()=>{
    const {scope,request}=fixture();
    const attached={schema:"motionwright.manim-editable-source-attached/1",source:scope,asset_id:request.original_asset_id,
      source_sha256:request.original_sha256,original_plan_sha256:digest("a"),current_plan_sha256:digest("b"),
      origin_revision:scope.revision-1,revision_only_rebound:true,node_ids:[request.node_id],
      editable_parameters:["one_node.transform","one_node.text","one_node.style"],native_execution_performed:false,imported_source_authority_used:false};
    const input={source:scope,asset_id:request.original_asset_id,expected_sha256:request.original_sha256};
    expect(await inspectAttachedManim(input,async()=>attached)).toEqual(attached);
    await expect(inspectAttachedManim(input,async()=>({...attached,node_ids:[request.node_id,request.node_id]}))).rejects.toThrow();
  });
  it("retains pending source effects before dispatch, and refuses to replay an archived export or edit",()=>{
    const {scope,snapshot,result}=fixture(),store=storage(),key=pendingScopeKey(scope);
    const pending:ManimPending={kind:"export",source:scope,path:"E:\\Exports\\original.mwedit.zip",bundle_sha256:snapshot.bundle_sha256,size_bytes:snapshot.size_bytes};
    const raw=retainManimPending(store,pending);
    expect(readManimPending(store.getItem(key)!,key)).toEqual(pending);
    expect(()=>retainManimPending(store,pending)).toThrow(/earlier/);
    expect(()=>acknowledgeManimPending(store,pending,"changed")).toThrow(/changed/);
    store.setItem(key+":archive:"+uuid(4),raw);acknowledgeManimPending(store,pending,raw);
    expect(()=>retainManimPending(store,pending)).toThrow(/uncertain export/);
    const edit:ManimPending={kind:"edit",source:scope,result};
    const armed=retainManimPending(store,edit);
    store.setItem(key+":archive:"+uuid(5),armed);acknowledgeManimPending(store,edit,armed);
    expect(()=>retainManimPending(store,edit)).toThrow(/uncertain edit/);
  });
  it("observes exact contiguous saved history, not the original response",()=>{
    const {project,scope,result}=fixture(),pending:Extract<ManimPending,{kind:"edit"}>={kind:"edit",source:scope,result};
    const saved={...project,revision:project.revision+1};
    expect(matchingManimHistory(pending,saved,[{revision:saved.revision,change:result.preview.change,created_at:"now"}])).toEqual([saved.revision]);
    expect(()=>matchingManimHistory(pending,saved,[])).toThrow(/incomplete/);
    expect(confirmManimEdit(project,result,saved)).toBe(false);
  });
});
