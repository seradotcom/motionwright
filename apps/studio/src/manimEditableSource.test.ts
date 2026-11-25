import { describe, expect, it, vi } from "vitest";
import { fixtureProject } from "./fixture";
import { applyChange } from "./api";
import { MANIM_CONTROLS_RATIONALE, manimControlRows, proposedNode, MANIM_EDITABLE_MIME, acknowledgeManimPending, confirmManimEdit, exportManimSource, inspectAttachedManim, manimExportSubject,
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

function controlsFixture() {
  const f=fixture();
  f.project.locks=[]; // This positive source is explicitly writable; lock negatives remain separate.
  f.request.property="controls";
  f.result.preview.change={type:"apply_creative_patch",patch:{
    scene_id:f.scene.id,base_revision:f.project.revision,rationale:MANIM_CONTROLS_RATIONALE,edits:[
      {kind:"transform",node_id:f.node.id,transform:{x:f.node.x+12,y:f.node.y+20,width:f.node.width,height:f.node.height,rotation_deg:7,opacity:.8}},
      {kind:"text",node_id:f.node.id,text:"Returned complete object"},
      {kind:"style",node_id:f.node.id,style:{...f.node.style,fill:"#86C8D1",font_size:40}},
    ],
  }};
  return f;
}
describe("Manim complete object controls",()=>{
  it("reviews and commits all changed controls in one revision with retained sources and existing undo",async()=>{
    const {project,node,request,result}=controlsFixture(),before=structuredClone(project);
    expect(readManimPreview(result,request)).toEqual(result);
    const proposed=proposedNode(node,result.preview.change),rows=manimControlRows(node,proposed);
    expect(rows.map(row=>row.field)).toEqual(expect.arrayContaining(["x","y","rotation_deg","opacity","text","style.fill","style.font_size"]));
    const saved=await applyChange(project,result.preview.change);
    expect(saved.revision).toBe(project.revision+1);
    expect(confirmManimEdit(project,result,saved)).toBe(true);
    expect(saved.assets).toEqual(before.assets);expect(project).toEqual(before);
    const reopened=JSON.parse(JSON.stringify(saved));
    expect(confirmManimEdit(project,result,reopened)).toBe(true);
    const patch=saved.production_design!.patches!.at(-1)!;
    const undone=await applyChange(saved,{type:"undo_creative_patch",patch_id:patch.id});
    expect(undone.scenes[0].nodes).toEqual(project.scenes[0].nodes);
    expect(undone.assets).toEqual(project.assets);
  });
  it("rejects outside objects, duplicate groups, motion, stale bases and extra returned controls",()=>{
    const {request,result}=controlsFixture();
    if(result.preview.change.type!=="apply_creative_patch")throw new Error("fixture");
    const patch=result.preview.change.patch,transform=patch.edits[0];
    for(const candidate of [
      {...patch,base_revision:patch.base_revision+1},
      {...patch,edits:[]},
      {...patch,edits:[transform,transform]},
      {...patch,edits:[{...transform,node_id:uuid(22)}]},
      {...patch,edits:[{kind:"motion",node_id:request.node_id,keyframes:[]}]},
      {...patch,edits:[{...transform,hidden:"outside"}]},
      {...patch,rationale:"Unreviewed imported command"},
      {...patch,scene_id:uuid(23)},
    ]) expect(()=>readManimPreview({...result,preview:{...result.preview,change:{type:"apply_creative_patch",patch:candidate}}},request)).toThrow();
  });
  it("requires the actual saved patch record and refuses replay when only the base revision changes",async()=>{
    const {project,scope,result}=controlsFixture(),store=storage(),key=pendingScopeKey(scope);
    const pending:ManimPending={kind:"edit",source:scope,result};
    const raw=retainManimPending(store,pending);
    store.setItem(key+":archive:"+uuid(11),raw);acknowledgeManimPending(store,pending,raw);
    const next=structuredClone(pending);
    next.source.revision++;next.result.request.source.revision=next.source.revision;
    next.result.preview.expected.revision=next.source.revision;
    if(next.result.preview.change.type!=="apply_creative_patch")throw new Error("fixture");
    next.result.preview.change.patch.base_revision=next.source.revision;
    expect(()=>retainManimPending(store,next)).toThrow(/uncertain edit/);
    const saved=await applyChange(project,result.preview.change);
    saved.production_design!.patches=[];
    expect(confirmManimEdit(project,result,saved)).toBe(false);
  });
  it("recognizes the new capability while preserving earlier source inspectors",async()=>{
    const {scope,request}=controlsFixture();
    const response={schema:"motionwright.manim-editable-source-attached/1",source:scope,asset_id:request.original_asset_id,
      source_sha256:request.original_sha256,original_plan_sha256:digest("a"),current_plan_sha256:digest("b"),
      origin_revision:scope.revision,revision_only_rebound:false,node_ids:[request.node_id],
      editable_parameters:["one_node.transform","one_node.text","one_node.style","one_node.controls"],native_execution_performed:false,imported_source_authority_used:false};
    expect(await inspectAttachedManim({source:scope,asset_id:request.original_asset_id,expected_sha256:request.original_sha256},async()=>response)).toEqual(response);
  });
});
