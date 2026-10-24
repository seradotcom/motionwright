import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import type { CameraReframe } from "./cameraReframe";
import type { Project, Scene } from "./types";
import { emptyProductionDesign } from "./creativeProduction";
import {
  acknowledgeCameraDraft, armCameraDraft, cameraAcknowledged, cameraDraftChanged, cameraDraftValue,
  cameraPointerKey, cameraRecoveryKey, cameraSessionIssue, cameraScope, confirmCameraOutcomeAbsent,
  createCameraDraft, decodeCameraCopy, editCameraDraft, listCameraCopies,
  loadCameraCopy, rebaseCameraDraft, reconcileCameraDraft, writeCameraConfirmationCopy, writeCameraCopy,
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

 it("uses the original response to acknowledge a reframe without erasing later human edits",()=>{
  const {draft,project,scene,camera}=setup();
  const authored=editCameraDraft(draft,"end_center_x","978");
  const sent=armCameraDraft(authored,project,scene,"upsert",cameraDraftValue(authored),uuid(31));
  const later=editCameraDraft(sent,"duration_frames","112");
  const saved=source();saved.revision=project.revision+1;
  saved.production_design ??= emptyProductionDesign();
  saved.production_design.camera_reframes=[sent.awaiting!.source!];
  expect(cameraAcknowledged(later,saved,saved.scenes[0])).toBe(false);
  expect(cameraAcknowledged(later,saved,saved.scenes[0],uuid(31))).toBe(true);
  const ack=acknowledgeCameraDraft(later,uuid(31),saved,saved.scenes[0]);
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

describe("Camera original outcome authority",()=>{
 it("does not acknowledge a held original from an identical peer upsert",()=>{
  const {draft,project,scene,camera}=setup();
  const sent=armCameraDraft(draft,project,scene,"upsert",camera,uuid(301));
  const peer=structuredClone(project);peer.revision++;
  peer.production_design ??= emptyProductionDesign();
  peer.production_design.camera_reframes=[structuredClone(camera)];
  expect(cameraAcknowledged(sent,peer,peer.scenes[0])).toBe(false);
  expect(reconcileCameraDraft(sent,peer,peer.scenes[0])).toEqual(sent);
 });
 it("does not acknowledge a held detach because a peer also removed the Camera",()=>{
  const {project,scene,camera}=setup();
  project.production_design=emptyProductionDesign();
  project.production_design.camera_reframes=[camera];
  const authored=createCameraDraft(project,scene,camera);
  const sent=armCameraDraft(authored,project,scene,"detach",null,uuid(302));
  const peer=structuredClone(project);peer.revision++;
  peer.production_design!.camera_reframes=[];
  expect(cameraAcknowledged(sent,peer,peer.scenes[0])).toBe(false);
  expect(reconcileCameraDraft(sent,peer,peer.scenes[0])).toEqual(sent);
 });
 it("cannot release a held original merely by reading its unchanged base",()=>{
  const {draft,project,scene,camera}=setup();
  const sent=armCameraDraft(draft,project,scene,"upsert",camera,uuid(303));
  expect(()=>confirmCameraOutcomeAbsent(sent,project,scene)).toThrow(/original|terminal|unconfirmed/i);
  expect(sent.awaiting?.id).toBe(uuid(303));
 });
});

{
const uuid = (value: number) =>
  "018f0000-0000-7000-8000-" + String(value).padStart(12, "0");

function setup(existing = false) {
  const project = structuredClone(fixtureProject);
  const scene = project.scenes[0];
  const camera: CameraReframe = {
    id: uuid(7), scene_id: scene.id, component_version: 1,
    start_center_x: 960, start_center_y: 540,
    end_center_x: 972, end_center_y: 544, duration_frames: 90,
  };
  project.production_design = emptyProductionDesign();
  project.production_design.camera_reframes = existing ? [structuredClone(camera)] : [];
  return { project, scene, camera, draft: createCameraDraft(project, scene, camera) };
}

function resultOfUpsert(project: Project, camera: CameraReframe) {
  const committed = structuredClone(project);
  committed.revision = project.revision + 1;
  committed.production_design ??= emptyProductionDesign();
  committed.production_design.camera_reframes = [structuredClone(camera)];
  const scene = committed.scenes.find(value => value.id === camera.scene_id)!;
  scene.camera.center_x = camera.end_center_x;
  scene.camera.center_y = camera.end_center_y;
  return { project: committed, scene };
}

function resultOfDetach(project: Project, sceneId: string) {
  const committed = structuredClone(project);
  committed.revision = project.revision + 1;
  committed.production_design ??= emptyProductionDesign();
  committed.production_design.camera_reframes = [];
  return { project: committed, scene: committed.scenes.find(value => value.id === sceneId)! };
}

describe("Camera recovery retains original outcome identity",()=>{
  it("a recovered v1 pending copy remains unconfirmed when a later read matches its values", () => {
    const { project, scene, camera, draft } = setup();
    const sent = armCameraDraft(draft, project, scene, "upsert", camera, uuid(36));
    const later = editCameraDraft(sent, "duration_frames", "1e");
    const writerId = uuid(37);
    const bytes = JSON.stringify({
      schema: "motionwright.camera-recovery/1",
      writerId, scope: later.scope, draft: later,
    });
    const recovered = decodeCameraCopy(bytes, cameraScope(project, scene), writerId);
    const peer = resultOfUpsert(project, camera);
    const next = reconcileCameraDraft(recovered, peer.project, peer.scene);
    expect(next).toEqual(recovered);
    expect(next.awaiting?.id).toBe(uuid(36));
    expect(next.fields.duration_frames).toBe("1e");
    expect(JSON.stringify({
      schema: "motionwright.camera-recovery/1",
      writerId, scope: later.scope, draft: later,
    })).toBe(bytes);
  });
});
describe("Camera acknowledgement uses the original mutation result", () => {
  it("commits one original upsert and preserves raw edits made after submission", () => {
    const { project, scene, draft } = setup();
    const authored = editCameraDraft(draft, "end_center_x", "978");
    const sent = armCameraDraft(authored, project, scene,
      "upsert", cameraDraftValue(authored), uuid(41));
    const later = editCameraDraft(sent, "duration_frames", "1e");
    const before = structuredClone(later);
    const committed = resultOfUpsert(project, sent.awaiting!.source!);
    const acknowledged = acknowledgeCameraDraft(later, uuid(41), committed.project, committed.scene);
    expect(later).toEqual(before);
    expect(acknowledged.awaiting).toBeNull();
    expect(acknowledged.baseRevision).toBe(committed.project.revision);
    expect(acknowledged.base).toEqual(sent.awaiting!.source);
    expect(acknowledged.initial.duration_frames).toBe("90");
    expect(acknowledged.fields.end_center_x).toBe("978");
    expect(acknowledged.fields.duration_frames).toBe("1e");
    expect(acknowledged.sequence).toBe(later.sequence);
  });

  it("acknowledges an original detach without inferring its identity from absence", () => {
    const { project, scene, draft } = setup(true);
    const sent = armCameraDraft(draft, project, scene, "detach", null, uuid(42));
    const committed = resultOfDetach(project, scene.id);
    expect(reconcileCameraDraft(sent, committed.project, committed.scene)).toEqual(sent);
    const acknowledged = acknowledgeCameraDraft(sent, uuid(42), committed.project, committed.scene);
    expect(acknowledged.awaiting).toBeNull();
    expect(acknowledged.baseRevision).toBe(committed.project.revision);
    expect(acknowledged.base).toBeNull();
    expect(acknowledged.fields).toEqual(sent.fields);
  });

  it("compares Camera values independently of serialized property order in the original result", () => {
    const { project, scene, camera, draft } = setup();
    const sent = armCameraDraft(draft, project, scene, "upsert", camera, uuid(50));
    const committed = resultOfUpsert(project, camera);
    const reordered = Object.fromEntries(Object.entries(camera).reverse()) as unknown as CameraReframe;
    committed.project.production_design!.camera_reframes = [reordered];
    expect(acknowledgeCameraDraft(sent, uuid(50), committed.project, committed.scene).awaiting)
      .toBeNull();
  });

  it("does not let a response for another attempt change the current draft", () => {
    const { project, scene, camera, draft } = setup();
    const current = armCameraDraft(draft, project, scene, "upsert", camera, uuid(43));
    const committed = resultOfUpsert(project, camera);
    expect(acknowledgeCameraDraft(current, uuid(999), committed.project, committed.scene)).toBe(current);
    expect(current.awaiting?.id).toBe(uuid(43));
  });

  it("a delayed acknowledgement cannot clear a newer pending operation", () => {
    const { project, scene, camera, draft } = setup();
    const first = armCameraDraft(draft, project, scene, "upsert", camera, uuid(44));
    const committed = resultOfUpsert(project, camera);
    const acknowledged = acknowledgeCameraDraft(first, uuid(44), committed.project, committed.scene);
    const edited = editCameraDraft(acknowledged, "end_center_x", "982");
    const second = armCameraDraft(edited, committed.project, committed.scene,
      "upsert", cameraDraftValue(edited), uuid(45));
    expect(acknowledgeCameraDraft(second, uuid(44), committed.project, committed.scene)).toBe(second);
    expect(second.awaiting?.id).toBe(uuid(45));
    expect(second.fields.end_center_x).toBe("982");
  });

  it("does not rebase the acknowledged draft onto a later peer revision", () => {
    const { project, scene, camera, draft } = setup();
    const sent = armCameraDraft(draft, project, scene, "upsert", camera, uuid(46));
    const committed = resultOfUpsert(project, camera);
    const acknowledged = acknowledgeCameraDraft(sent, uuid(46), committed.project, committed.scene);
    const peer = structuredClone(committed.project);
    peer.revision++;
    peer.scenes[0].name = "A later unrelated peer revision";
    const peerScene = peer.scenes.find(value => value.id === scene.id)!;
    expect(reconcileCameraDraft(acknowledged, peer, peerScene)).toBe(acknowledged);
    expect(acknowledged.baseRevision).toBe(committed.project.revision);
    expect(cameraSessionIssue(acknowledged, peer, peerScene, camera)).toMatch(/revision changed/i);
  });

  it("rejects a mismatched result instead of confirming the pending upsert", () => {
    const { project, scene, camera, draft } = setup();
    const sent = armCameraDraft(draft, project, scene, "upsert", camera, uuid(47));
    for (const mutate of [
      (value: CameraReframe) => { value.id = uuid(77); },
      (value: CameraReframe) => { value.end_center_x += 1; },
      (value: CameraReframe) => { value.duration_frames += 1; },
    ]) {
      const changed = structuredClone(camera);
      mutate(changed);
      const wrong = resultOfUpsert(project, changed);
      expect(() => acknowledgeCameraDraft(sent, uuid(47), wrong.project, wrong.scene)).toThrow();
      expect(sent.awaiting?.id).toBe(uuid(47));
    }
  });

  it("rejects an original detach result that still contains the camera", () => {
    const { project, scene, camera, draft } = setup(true);
    const sent = armCameraDraft(draft, project, scene, "detach", null, uuid(48));
    const wrong = resultOfUpsert(project, camera);
    expect(() => acknowledgeCameraDraft(sent, uuid(48), wrong.project, wrong.scene)).toThrow();
    expect(sent.awaiting?.id).toBe(uuid(48));
  });

  it("does not turn a completed draft back into an acknowledged operation", () => {
    const { project, scene, camera, draft } = setup();
    const sent = armCameraDraft(draft, project, scene, "upsert", camera, uuid(49));
    const committed = resultOfUpsert(project, camera);
    const acknowledged = acknowledgeCameraDraft(sent, uuid(49), committed.project, committed.scene);
    const edited = editCameraDraft(acknowledged, "end_center_y", "1e");
    expect(acknowledgeCameraDraft(edited, uuid(49), committed.project, committed.scene)).toBe(edited);
    expect(edited.fields.end_center_y).toBe("1e");
  });
});

describe("Camera original-result scope validation", () => {
  it("rejects a matching attempt result from a different Project identity, generation or branch", () => {
    const { project, scene, camera, draft } = setup();
    const sent = armCameraDraft(draft, project, scene, "upsert", camera, uuid(61));
    for (const mutate of [
      (value: Project) => { value.id = uuid(62); },
      (value: Project) => { value.generation += 1; },
      (value: Project) => { value.active_branch = uuid(63); },
    ]) {
      const wrong = resultOfUpsert(project, camera);
      mutate(wrong.project);
      expect(() => acknowledgeCameraDraft(sent, uuid(61), wrong.project, wrong.scene)).toThrow();
      expect(sent.awaiting?.id).toBe(uuid(61));
    }
  });

  it("rejects an out-of-band scene when the committed Project does not contain it", () => {
    const { project, scene, camera, draft } = setup();
    const sent = armCameraDraft(draft, project, scene, "upsert", camera, uuid(64));
    const wrong = resultOfUpsert(project, camera);
    const missing: Scene = structuredClone(wrong.scene);
    wrong.project.scenes = wrong.project.scenes.filter(value => value.id !== scene.id);
    expect(() => acknowledgeCameraDraft(sent, uuid(64), wrong.project, missing)).toThrow();
    expect(sent.awaiting?.id).toBe(uuid(64));
  });
});

}

describe("Camera original mutation revision contract", () => {
  it("requires a safe integer revision strictly newer than the submitted base", () => {
    const project = structuredClone(fixtureProject);
    const scene = project.scenes[0];
    project.production_design = emptyProductionDesign();
    const camera: CameraReframe = {
      id: "018f0000-0000-7000-8000-000000000071",
      scene_id: scene.id, component_version: 1,
      start_center_x: 960, start_center_y: 540,
      end_center_x: 972, end_center_y: 544, duration_frames: 90,
    };
    const id = "018f0000-0000-7000-8000-000000000072";
    const sent = armCameraDraft(createCameraDraft(project, scene, camera),
      project, scene, "upsert", camera, id);
    for (const revision of [project.revision - 1, project.revision,
      Number.NaN, Number.POSITIVE_INFINITY, 1.5]) {
      const wrong = structuredClone(project);
      wrong.revision = revision;
      wrong.production_design!.camera_reframes = [structuredClone(camera)];
      const wrongScene = wrong.scenes.find(value => value.id === scene.id)!;
      expect(() => acknowledgeCameraDraft(sent, id, wrong, wrongScene)).toThrow();
      expect(sent.awaiting?.id).toBe(id);
    }
  });
});

describe("Camera rebase model scope", () => {
  it("does not rewrite a draft base from another Project", () => {
    const project = structuredClone(fixtureProject);
    project.production_design = emptyProductionDesign();
    const scene = project.scenes[0];
    const camera: CameraReframe = {
      id: "018f0000-0000-7000-8000-000000000081",
      scene_id: scene.id, component_version: 1,
      start_center_x: 960, start_center_y: 540,
      end_center_x: 972, end_center_y: 544, duration_frames: 90,
    };
    const draft = createCameraDraft(project, scene, camera);
    const before = structuredClone(draft);
    const foreign = structuredClone(project);
    foreign.id = "018f0000-0000-7000-8000-000000000082";
    foreign.revision++;
    expect(() => rebaseCameraDraft(draft, foreign, foreign.scenes[0])).toThrow();
    expect(draft).toEqual(before);
  });
});

describe("Camera confirmed checkpoint publication",()=>{
 const armed=()=>{
  const {draft,project,scene,camera}=setup();
  const sent=armCameraDraft(draft,project,scene,"upsert",camera,uuid(401));
  const committed=structuredClone(project);committed.revision++;
  committed.production_design ??= emptyProductionDesign();
  committed.production_design.camera_reframes=[camera];
  const next=acknowledgeCameraDraft(sent,uuid(401),committed,committed.scenes[0]);
  const local=new MemoryStorage(),session=new MemoryStorage(),writer=uuid(402),nextWriter=uuid(403);
  const raw=writeCameraCopy(local,session,sent,writer,null);
  return {sent,next,local,session,writer,nextWriter,raw};
 };
 it("retains the armed original when a confirmed checkpoint becomes the active writer",()=>{
  const {sent,next,local,session,writer,nextWriter,raw}=armed();
  const originalKey=cameraRecoveryKey(sent.scope,writer);
  const nextRaw=writeCameraConfirmationCopy(local,session,next,writer,raw,nextWriter);
  expect(local.getItem(originalKey)).toBe(raw);
  expect(decodeCameraCopy(raw,sent.scope,writer).awaiting?.id).toBe(uuid(401));
  expect(session.getItem(cameraPointerKey(sent.scope))).toBe(nextWriter);
  expect(loadCameraCopy(local,session,sent.scope)?.draft).toEqual(next);
  expect(local.getItem(cameraRecoveryKey(sent.scope,nextWriter))).toBe(nextRaw);
 });
 it("preserves the original through silent data writes and failed or silent pointer updates",()=>{
  for(const phase of ["local-silent","pointer-throw","pointer-silent"]){
   const {sent,next,local,session,writer,nextWriter,raw}=armed();
   if(phase==="local-silent")local.silent=true;
   if(phase==="pointer-throw")session.fail=true;
   if(phase==="pointer-silent")session.silent=true;
   expect(()=>writeCameraConfirmationCopy(local,session,next,writer,raw,nextWriter)).toThrow();
   expect(local.getItem(cameraRecoveryKey(sent.scope,writer))).toBe(raw);
   expect(session.getItem(cameraPointerKey(sent.scope))).toBe(writer);
   expect(loadCameraCopy(local,session,sent.scope)?.draft.awaiting?.id).toBe(uuid(401));
   const staged=local.getItem(cameraRecoveryKey(sent.scope,nextWriter));
   if(phase!=="local-silent")expect(decodeCameraCopy(staged!,sent.scope,nextWriter).awaiting).toBeNull();
  }
 });
 it("can recover either verified copy when the pointer was written but its readback failed",()=>{
  const {sent,next,local,session,writer,nextWriter,raw}=armed();
  const get=session.getItem.bind(session),set=session.setItem.bind(session);
  let failRead=false;
  session.setItem=(key,value)=>{set(key,value);if(value===nextWriter)failRead=true;};
  session.getItem=key=>{
   if(failRead){failRead=false;throw new Error("Pointer readback interrupted");}
   return get(key);
  };
  expect(()=>writeCameraConfirmationCopy(local,session,next,writer,raw,nextWriter)).toThrow(/readback/);
  expect(local.getItem(cameraRecoveryKey(sent.scope,writer))).toBe(raw);
  expect(loadCameraCopy(local,session,sent.scope)).toEqual({writerId:nextWriter,draft:next});
 });
 it("does not switch the writer over an author copy changed during confirmation staging",()=>{
  const {sent,next,local,session,writer,nextWriter,raw}=armed();
  const newer=editCameraDraft(sent,"duration_frames","1e");
  const newerRaw=JSON.stringify({schema:"motionwright.camera-recovery/1",
   writerId:writer,scope:sent.scope,draft:newer});
  const set=local.setItem.bind(local);
  local.setItem=(key,value)=>{
   set(key,value);
   if(key===cameraRecoveryKey(sent.scope,nextWriter))set(cameraRecoveryKey(sent.scope,writer),newerRaw);
  };
  expect(()=>writeCameraConfirmationCopy(local,session,next,writer,raw,nextWriter)).toThrow(/changed/);
  expect(session.getItem(cameraPointerKey(sent.scope))).toBe(writer);
  expect(local.getItem(cameraRecoveryKey(sent.scope,writer))).toBe(newerRaw);
  expect(loadCameraCopy(local,session,sent.scope)?.draft.fields.duration_frames).toBe("1e");
 });
});


describe("Camera v2 recovered source identity",()=>{
  function savedV2(){
    const {project,scene,camera}=setup();scene.renderer="manim-community";
    camera.component_version=2;camera.end_center_x=1000;camera.end_center_y=544;camera.duration_frames=24;
    project.production_design=emptyProductionDesign();
    project.production_design.camera_reframes=[structuredClone(camera)];
    return {project,scene,camera,draft:createCameraDraft(project,scene,camera)};
  }
  it("retains saved v2 after recovery and editing instead of downgrading its pair",()=>{
    const {project,scene,camera,draft}=savedV2(),writer=uuid(501);
    const original=JSON.stringify({schema:"motionwright.camera-recovery/1",
      writerId:writer,scope:draft.scope,draft});
    const recovered=decodeCameraCopy(original,cameraScope(project,scene),writer);
    const edited=editCameraDraft(recovered,"end_center_x","1012");
    expect(cameraDraftValue(edited)).toEqual({...camera,end_center_x:1012});
    expect(recovered.base).toEqual(camera);
    expect(JSON.stringify({schema:"motionwright.camera-recovery/1",
      writerId:writer,scope:draft.scope,draft})).toBe(original);
  });
  it("keeps legacy new drafts v1 rather than inferring a component version from the scene",()=>{
    const {project,scene,camera}=savedV2();
    project.production_design!.camera_reframes=[];
    expect(cameraDraftValue(createCameraDraft(project,scene,{...camera,component_version:1}))
      .component_version).toBe(1);
  });
  it("rejects malformed or foreign base versions instead of adopting them into author values",()=>{
    const {draft}=savedV2(),writer=uuid(502);
    for(const mutate of [
      (value:CameraReframe)=>{value.id=uuid(503);},
      (value:CameraReframe)=>{value.scene_id=uuid(504);},
      (value:CameraReframe)=>{value.component_version=3;},
      (value:CameraReframe)=>{value.duration_frames=1.5;},
      (value:CameraReframe)=>{delete (value as Partial<CameraReframe>).start_center_x;},
      (value:CameraReframe)=>{(value as unknown as Record<string,unknown>).renderer="manim-community";},
    ]){
      const changed=structuredClone(draft);mutate(changed.base!);
      expect(()=>cameraDraftValue(changed)).toThrow();
      const raw=JSON.stringify({schema:"motionwright.camera-recovery/1",writerId:writer,scope:draft.scope,draft:changed});
      expect(()=>decodeCameraCopy(raw,draft.scope,writer)).toThrow();
      expect(changed.base).not.toEqual(draft.base);
    }
  });
  it("preserves v2 and later incomplete input across the original response",()=>{
    const {draft,project,scene}=savedV2();
    const edited=editCameraDraft(draft,"end_center_x","1012");
    const sent=armCameraDraft(edited,project,scene,"upsert",cameraDraftValue(edited),uuid(505));
    const later=editCameraDraft(sent,"duration_frames","1e");
    const committed=structuredClone(project);committed.revision++;
    committed.production_design!.camera_reframes=[structuredClone(sent.awaiting!.source!)];
    const ack=acknowledgeCameraDraft(later,uuid(505),committed,committed.scenes[0]);
    expect(ack.base?.component_version).toBe(2);expect(ack.fields.duration_frames).toBe("1e");
    expect(()=>cameraDraftValue(ack)).toThrow(/complete finite/);
    expect(cameraDraftValue(editCameraDraft(ack,"duration_frames","30")).component_version).toBe(2);
  });
});
