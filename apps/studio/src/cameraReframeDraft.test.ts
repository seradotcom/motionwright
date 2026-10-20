import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import type { CameraReframe } from "./cameraReframe";
import { emptyProductionDesign } from "./creativeProduction";
import {
  armCameraDraft, cameraAcknowledged, cameraDraftChanged, cameraDraftValue,
  cameraRecoveryKey, cameraSessionIssue, cameraScope, confirmCameraOutcomeAbsent,
  createCameraDraft, decodeCameraCopy, editCameraDraft, listCameraCopies,
  loadCameraCopy, rebaseCameraDraft, reconcileCameraDraft, writeCameraCopy,
} from "./cameraReframeDraft";

const source=()=>structuredClone(fixtureProject);
const uuid=(number:number)=>"018f0000-0000-7000-8000-"+String(number).padStart(12,"0");
const setup=()=>{
 const project=source(),scene=project.scenes[0];
 const camera:CameraReframe={id:uuid(7),scene_id:scene.id,component_version:1,
  start_center_x:960,start_center_y:540,end_center_x:972,end_center_y:544,duration_frames:90};
 return {project,scene,camera,draft:createCameraDraft(project,scene,camera)};
};
class MemoryStorage {
  data=new Map<string,string>();silent=false;fail=false;
  get length(){return this.data.size;}
  key(n:number){return [...this.data.keys()][n]??null;}
  getItem(key:string){return this.data.get(key)??null;}
  setItem(key:string,value:string){if(this.fail)throw new Error("QuotaExceededError");
    if(!this.silent)this.data.set(key,value);}
  removeItem(key:string){this.data.delete(key);}
}
describe("source-bound Camera reframe author drafts",()=>{
 it("recovers exact incomplete raw through ten unrelated Project revisions without writing the Project",()=>{
  const {project,scene,draft}=setup(),local=new MemoryStorage(),session=new MemoryStorage(),writer=uuid(22);
  let edited=editCameraDraft(draft,"end_center_x","1e");
  edited=editCameraDraft(edited,"duration_frames","");
  writeCameraCopy(local,session,edited,writer);
  for(let n=0;n<10;n++){project.revision++;project.scenes[0].name="Peer change "+n;
   const recovered=loadCameraCopy(local,session,cameraScope(project,scene))!;
   expect(recovered.draft.fields.end_center_x).toBe("1e");
   expect(recovered.draft.fields.duration_frames).toBe("");
   expect(cameraSessionIssue(recovered.draft,project,scene,undefined)).toContain("revision changed");
  }
  expect(()=>cameraDraftValue(edited)).toThrow(/complete finite/);
  const selected=loadCameraCopy(local,session,cameraScope(project,scene))!.draft;
  const rebased=rebaseCameraDraft(selected,project,scene);
  expect(rebased.baseRevision).toBe(project.revision);
  expect(rebased.fields.end_center_x).toBe("1e");
  expect(cameraDraftChanged(rebased)).toBe(true);
 });

 it("disallows blank, incomplete, overflow and fractional duration without normalizing raw",()=>{
  const {draft}=setup();
  for(const raw of ["","1e","Infinity","NaN","1.5","-1e999"]){
   const next=editCameraDraft(draft,"duration_frames",raw);
   expect(()=>cameraDraftValue(next)).toThrow();
   expect(next.fields.duration_frames).toBe(raw);
  }
  expect(()=>editCameraDraft(draft,"end_center_x","X".repeat(65))).toThrow(/64/);
 });

 it("never considers a silently ignored local or session writer pointer a durable recovery",()=>{
  const {draft}=setup(),local=new MemoryStorage(),session=new MemoryStorage();
  local.silent=true;
  expect(()=>writeCameraCopy(local,session,draft,uuid(101))).toThrow(/not be verified/);
  expect(local.data.size).toBe(0);
  local.silent=false;session.silent=true;
  expect(()=>writeCameraCopy(local,session,draft,uuid(101))).toThrow(/pointer was not confirmed/);
  expect(loadCameraCopy(local,session,draft.scope)).toBeNull();
  expect(listCameraCopies(local,draft.scope)).toEqual([uuid(101)]);
  expect(decodeCameraCopy(local.getItem(cameraRecoveryKey(draft.scope,uuid(101)))!,draft.scope,uuid(101)).fields)
   .toEqual(draft.fields);
 });

 it("cannot use another recovery writer or Project scope implicitly",()=>{
  const {draft,project,scene}=setup(),local=new MemoryStorage(),session=new MemoryStorage();
  writeCameraCopy(local,session,draft,uuid(1));
  expect(()=>writeCameraCopy(local,session,draft,uuid(2))).toThrow(/Another Camera/);
  const different={...project,id:uuid(9)};
  expect(loadCameraCopy(local,session,cameraScope(different,scene))).toBeNull();
  const bytes=local.getItem(cameraRecoveryKey(draft.scope,uuid(1)))!;
  expect(()=>decodeCameraCopy(bytes,cameraScope(different,scene),uuid(1))).toThrow();
  expect(()=>decodeCameraCopy(bytes,draft.scope,uuid(2))).toThrow();
 });

 it("keeps originals and unconfirmed command immutable when another revision arrives",()=>{
  const {draft,project,scene,camera}=setup();
  const v=armCameraDraft(draft,project,scene,"upsert",camera,uuid(17));
  expect(()=>armCameraDraft(v,project,scene,"upsert",camera,uuid(18))).toThrow(/unconfirmed/);
  project.revision+=1;
  project.scenes[0].name="Unrelated peer revision";
  expect(cameraAcknowledged(v,project,scene)).toBe(false);
  expect(reconcileCameraDraft(v,project,scene).awaiting).toEqual(v.awaiting);
  expect(()=>confirmCameraOutcomeAbsent(v,project,scene)).toThrow();
 });

 it("acknowledges exactly the saved reframe but never erases later human edits",()=>{
  const {draft,project,scene,camera}=setup();
  const authored=editCameraDraft(draft,"end_center_x","978");
  const sent=armCameraDraft(authored,project,scene,"upsert",cameraDraftValue(authored),uuid(31));
  const later=editCameraDraft(sent,"duration_frames","112");
  const saved=source();saved.revision=project.revision+1;
  saved.production_design ??= emptyProductionDesign();
  saved.production_design.camera_reframes=[sent.awaiting!.source!];
  expect(cameraAcknowledged(later,saved,saved.scenes[0])).toBe(true);
  const ack=reconcileCameraDraft(later,saved,saved.scenes[0]);
  expect(ack.awaiting).toBeNull();
  expect(ack.baseRevision).toBe(saved.revision);
  expect(ack.fields.end_center_x).toBe("978");
  expect(ack.fields.duration_frames).toBe("112");
  expect(cameraDraftValue(ack).duration_frames).toBe(112);
 });

 it("blocks a stale saved camera until explicit source review without losing input",()=>{
  const {draft,project,scene,camera}=setup();
  const changed=editCameraDraft(draft,"end_center_y","556");
  project.production_design ??= emptyProductionDesign();
  project.production_design.camera_reframes=[camera];
  expect(cameraSessionIssue(changed,project,scene,camera)).toContain("saved Camera changed");
  const rebased=rebaseCameraDraft(changed,project,scene);
  expect(rebased.fields.end_center_y).toBe("556");
  expect(cameraSessionIssue(rebased,project,scene,camera)).toBeNull();
 });

 it("requires explicit no-change confirmation before unlocking an uncertain reply",()=>{
  const {draft,project,scene,camera}=setup();
  const sent=armCameraDraft(draft,project,scene,"upsert",camera,uuid(89));
  const recovered=confirmCameraOutcomeAbsent(sent,project,scene);
  expect(recovered.awaiting).toBeNull();
  expect(recovered.fields.end_center_y).toBe(draft.fields.end_center_y);
  const after=armCameraDraft(recovered,project,scene,"upsert",camera,uuid(90));
  expect(after.awaiting?.id).toBe(uuid(90));
 });

 it("rejects corrupt recovery objects instead of silently becoming a clean draft",()=>{
  const {draft}=setup(),local=new MemoryStorage(),session=new MemoryStorage(),id=uuid(98);
  writeCameraCopy(local,session,draft,id);
  const key=cameraRecoveryKey(draft.scope,id),good=local.getItem(key)!;
  for(const modify of [
   (obj:any)=>{obj.draft.fields.duration_frames={value:"90"};},
   (obj:any)=>{obj.draft.awaiting={id:"bad"};},
   (obj:any)=>{obj.scope="wrong";},
   (obj:any)=>{obj.draft.cameraId="invalid";},
  ]){
   const v=JSON.parse(good);modify(v);
   expect(()=>decodeCameraCopy(JSON.stringify(v),draft.scope,id)).toThrow();
  }
  expect(local.getItem(key)).toBe(good);
 });

 it("rejects duplicate/scoped full copies rather than pruning existing author bytes",()=>{
  const {draft}=setup(),local=new MemoryStorage(),session=new MemoryStorage();
  writeCameraCopy(local,session,draft,uuid(10));
  const preserved=local.getItem(cameraRecoveryKey(draft.scope,uuid(10)))!;
  session.fail=true;
  expect(()=>writeCameraCopy(local,session,editCameraDraft(draft,"end_center_y","551"),uuid(10))).toThrow(/QuotaExceeded/);
  expect(preserved).not.toBeNull();
  // The failed session pointer never authorizes a Project mutation.
 });
});

describe("Camera recovery window concurrency",()=>{
 it("rejects stale copies from a cloned window and preserves the original bytes",()=>{
  const {draft}=setup(),local=new MemoryStorage(),session=new MemoryStorage(),writer=uuid(114);
  const original=writeCameraCopy(local,session,draft,writer,null);
  const newer=editCameraDraft(draft,"end_center_x","982");
  const latest=writeCameraCopy(local,session,newer,writer,original);
  const bytes=local.getItem(cameraRecoveryKey(draft.scope,writer));
  expect(bytes).toBe(latest);
  expect(()=>writeCameraCopy(local,session,editCameraDraft(draft,"end_center_y","549"),
    writer,original)).toThrow(/changed in another window/);
  expect(local.getItem(cameraRecoveryKey(draft.scope,writer))).toBe(bytes);
 });

 it("requires explicit identity review if a peer replaced the saved Camera component",()=>{
  const {draft,project,scene,camera}=setup();
  const typed=editCameraDraft(draft,"end_center_y","550");
  const replacement={...camera,id:uuid(219)};
  project.production_design=emptyProductionDesign();
  project.production_design.camera_reframes=[replacement];
  expect(()=>rebaseCameraDraft(typed,project,scene)).toThrow(/identity changed/);
  expect(typed.fields.end_center_y).toBe("550");
 });
});
