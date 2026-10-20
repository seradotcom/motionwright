import type { Project, Scene } from "./types";
import type { CameraReframe } from "./cameraReframe";

// Local author intent and its exact transport snapshot. Neither a recovery
// record nor a pending ACK authorizes another service call.
export const CAMERA_FIELDS = ["start_center_x","start_center_y","end_center_x","end_center_y","duration_frames"] as const;
export type CameraField = typeof CAMERA_FIELDS[number];
export type CameraRaw = Record<CameraField, string>;
export type CameraOperation = "upsert" | "detach";
export interface CameraPending {
  id: string; revision: number; operation: CameraOperation;
  source: CameraReframe | null; sequence: number;
}
export interface CameraDraft {
  schema: "motionwright.camera-draft/1"; scope: string;
  cameraId: string; sceneId: string; baseRevision: number;
  base: CameraReframe | null; initial: CameraRaw; fields: CameraRaw;
  sequence: number; awaiting: CameraPending | null;
}
export const CAMERA_RECOVERY_PREFIX = "motionwright.camera-recovery.v1:";
export const CAMERA_POINTER_PREFIX = "motionwright.camera-writer.v1:";
export const cameraScope = (project: Project, scene: Scene) =>
  JSON.stringify([project.id,project.generation,project.active_branch,scene.id]);
const encoded = (scope: string) => encodeURIComponent(scope);
export const cameraRecoveryKey = (scope: string, writerId: string) =>
  CAMERA_RECOVERY_PREFIX + encoded(scope) + ":" + writerId;
export const cameraPointerKey = (scope: string) => CAMERA_POINTER_PREFIX + encoded(scope);
const record = (value: unknown): value is Record<string,unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const uuid = (value: unknown): value is string =>
  typeof value === "string" && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
export const cameraFields = (camera: CameraReframe): CameraRaw =>
  Object.fromEntries(CAMERA_FIELDS.map(field => [field,String(camera[field])])) as CameraRaw;
export function createCameraDraft(project: Project, scene: Scene, source: CameraReframe): CameraDraft {
  return { schema:"motionwright.camera-draft/1",scope:cameraScope(project,scene),
    cameraId:source.id,sceneId:scene.id,baseRevision:project.revision,
    base:structuredClone(project.production_design?.camera_reframes?.find(item=>item.scene_id===scene.id) ?? null),
    initial:cameraFields(source),fields:cameraFields(source),sequence:0,awaiting:null };
}
export function cameraDraftChanged(draft: CameraDraft): boolean {
  return CAMERA_FIELDS.some(field => draft.fields[field] !== draft.initial[field]);
}
export function editCameraDraft(draft: CameraDraft, field: CameraField, raw: string): CameraDraft {
  if (raw.length > 64) throw new Error("A Camera parameter exceeds 64 characters.");
  return {...draft,fields:{...draft.fields,[field]:raw},sequence:draft.sequence+1};
}
const decimal = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;
export function cameraDraftValue(draft: CameraDraft): CameraReframe {
  const camera: CameraReframe={id:draft.cameraId,scene_id:draft.sceneId,component_version:1,
    start_center_x:0,start_center_y:0,end_center_x:0,end_center_y:0,duration_frames:0};
  for (const field of CAMERA_FIELDS) {
    const raw=draft.fields[field].trim();
    if (!decimal.test(raw)) throw new Error("Enter a complete finite Camera number for "+field+".");
    const value=Number(raw);
    if (!Number.isFinite(value) || (field === "duration_frames" && !Number.isSafeInteger(value)))
      throw new Error("Enter a finite Camera number; duration must be whole frames.");
    camera[field]=value;
  }
  return camera;
}
export function cameraSessionIssue(draft: CameraDraft, project: Project, scene: Scene,
  stored: CameraReframe | undefined): string | null {
  if (draft.scope!==cameraScope(project,scene)) return "A different Project or scene owns this Camera draft.";
  if (draft.awaiting) return "The original Camera operation has an unconfirmed result; only its original response can resolve this pending attempt.";
  if (draft.baseRevision !== project.revision) return "Project revision changed; compare and explicitly rebase this Camera draft.";
  if (JSON.stringify(draft.base)!==JSON.stringify(stored??null))
    return "The saved Camera changed in another editor; choose a resolution before applying.";
  return null;
}
export function rebaseCameraDraft(draft: CameraDraft,project:Project,scene:Scene): CameraDraft {
  if(draft.awaiting) throw new Error("The original Camera operation is still unconfirmed; it cannot be rebased.");
  const ownedScene=project.scenes.find(item=>item.id===draft.sceneId);
  if(!ownedScene || scene.id!==ownedScene.id || draft.scope!==cameraScope(project,ownedScene))
    throw new Error("A different Project or scene owns this Camera draft.");
  const saved=project.production_design?.camera_reframes?.find(item=>item.scene_id===ownedScene.id)??null;
  if(saved&&saved.id!==draft.cameraId)
    throw new Error("Saved Camera identity changed. Restore the saved Camera explicitly.");
  return {...draft,baseRevision:project.revision,base:structuredClone(saved)};
}
export function armCameraDraft(draft:CameraDraft, project:Project,scene:Scene,
  operation:CameraOperation, source:CameraReframe|null,id:string):CameraDraft {
  const issue=cameraSessionIssue(draft,project,scene,
    project.production_design?.camera_reframes?.find(item=>item.scene_id===scene.id));
  if(issue) throw new Error(issue);
  if (!uuid(id)) throw new Error("A real Camera request identity is required.");
  return {...draft,awaiting:{id,revision:project.revision,operation,
    source:source?structuredClone(source):null,sequence:draft.sequence}};
}
// The attempt ID must come from the invocation that returned this Project.
// Reading a Project, restoring a copy or matching values cannot produce it.
export function cameraAcknowledged(draft:CameraDraft,project:Project,scene:Scene,
  originalAttemptId?:string):boolean {
  const sent=draft.awaiting;
  if(!sent || originalAttemptId!==sent.id || !Number.isSafeInteger(project.revision) ||
    project.revision<=sent.revision) return false;
  const ownedScenes=project.scenes.filter(item=>item.id===draft.sceneId);
  if(ownedScenes.length!==1 || scene.id!==draft.sceneId ||
    draft.scope!==cameraScope(project,ownedScenes[0])) return false;
  // Only the scene contained in the returned Project selects the result.
  // An out-of-band Scene cannot supply missing receipt membership.
  const saved=project.production_design?.camera_reframes?.filter(item=>
    item.scene_id===ownedScenes[0].id)??[];
  if(sent.operation==="detach") return sent.source===null && saved.length===0;
  const expected=sent.source;
  if(sent.operation!=="upsert" || !expected || saved.length!==1 ||
    expected.id!==draft.cameraId || expected.scene_id!==draft.sceneId) return false;
  const actual=saved[0];
  return actual.id===expected.id && actual.scene_id===expected.scene_id &&
    actual.component_version===expected.component_version &&
    CAMERA_FIELDS.every(field=>Number.isFinite(actual[field]) &&
      Number.isFinite(expected[field]) && actual[field]===expected[field]);
}

export function acknowledgeCameraDraft(draft:CameraDraft,originalAttemptId:string,
  committedProject:Project,committedScene:Scene):CameraDraft {
  const sent=draft.awaiting;
  // A late result cannot replace another attempt or a later completed draft.
  if(!sent || sent.id!==originalAttemptId) return draft;
  if(!cameraAcknowledged(draft,committedProject,committedScene,originalAttemptId))
    throw new Error("The original Camera result does not confirm this exact pending operation.");
  const saved=committedProject.production_design?.camera_reframes?.find(item=>
    item.scene_id===draft.sceneId)??null;
  const keepLater=draft.sequence!==sent.sequence;
  const latest=saved?cameraFields(saved):draft.initial;
  return {...draft,awaiting:null,baseRevision:committedProject.revision,
    base:structuredClone(saved),initial:latest,fields:keepLater?draft.fields:latest};
}

export function reconcileCameraDraft(draft:CameraDraft,project:Project,scene:Scene,
  originalAttemptId?:string):CameraDraft {
  // Snapshot and recovery reads preserve UNKNOWN. The explicit original
  // callback can also use this entry point with its correlated attempt ID.
  return originalAttemptId===undefined?draft:
    acknowledgeCameraDraft(draft,originalAttemptId,project,scene);
}

export function confirmCameraOutcomeAbsent(draft:CameraDraft,_project:Project,_scene:Scene):CameraDraft {
  if(draft.awaiting)
    throw new Error("The original Camera operation is still unconfirmed. A Project read cannot establish its terminal result.");
  return draft;
}
export function validCameraDraft(value:unknown,scope:string):value is CameraDraft {
  if(!record(value)||value.schema!=="motionwright.camera-draft/1"||value.scope!==scope||
    !uuid(value.cameraId)||!uuid(value.sceneId)||typeof value.baseRevision!=="number"||
    typeof value.sequence!=="number"||!Number.isSafeInteger(value.baseRevision)||
    !Number.isSafeInteger(value.sequence)||value.baseRevision<0||value.sequence<0||
    !record(value.initial)||!record(value.fields))return false;
  for(const field of CAMERA_FIELDS) {
    if(typeof value.initial[field]!=="string"||typeof value.fields[field]!=="string"||
      (value.initial[field] as string).length>64||(value.fields[field] as string).length>64)return false;
  }
  const extra=Object.keys(value.fields).filter(field=>!CAMERA_FIELDS.includes(field as CameraField));
  if(extra.length)return false;
  if(value.awaiting!==null) {
    const sent=value.awaiting;
    if(!record(sent)||!uuid(sent.id)||typeof sent.revision!=="number"||
      typeof sent.sequence!=="number"||!Number.isSafeInteger(sent.revision)||
      !Number.isSafeInteger(sent.sequence)||sent.revision<0||sent.sequence<0||
      (sent.operation!=="upsert"&&sent.operation!=="detach")||
      (sent.operation==="upsert"&&!record(sent.source))||
      (sent.operation==="detach"&&sent.source!==null))return false;
  }
  if(value.base!==null&&!record(value.base))return false;
  return true;
}
export function decodeCameraCopy(raw:string,scope:string,writerId:string):CameraDraft {
  const value:unknown=JSON.parse(raw);
  if(!record(value)||value.schema!=="motionwright.camera-recovery/1"||
    value.writerId!==writerId||value.scope!==scope||!validCameraDraft(value.draft,scope))
    throw new Error("The Camera recovery copy is unreadable or belongs to a different editor.");
  return value.draft;
}
export function writeCameraCopy(local:Pick<Storage,"getItem"|"setItem">,
  session:Pick<Storage,"getItem"|"setItem">,draft:CameraDraft,writerId:string,
  expectedRaw?:string|null):string {
  if(!uuid(writerId)||!validCameraDraft(draft,draft.scope)) throw new Error("Invalid Camera author copy.");
  const key=cameraRecoveryKey(draft.scope,writerId), pointer=cameraPointerKey(draft.scope);
  const prev=session.getItem(pointer);
  if(expectedRaw!==undefined&&local.getItem(key)!==expectedRaw)
    throw new Error("Camera recovery changed in another window. Original copies were retained.");
  if(prev&&prev!==writerId) throw new Error("Another Camera recovery writer owns this session.");
  const record={schema:"motionwright.camera-recovery/1",writerId,scope:draft.scope,draft};
  const raw=JSON.stringify(record);
  if(raw.length>16384) throw new Error("Camera recovery exceeds the bounded 16 KiB copy.");
  local.setItem(key,raw);
  if(local.getItem(key)!==raw) throw new Error("Camera recovery write could not be verified.");
  session.setItem(pointer,writerId);
  if(session.getItem(pointer)!==writerId) throw new Error("Camera session pointer was not confirmed.");
  return raw;
}

// Publish a confirmed checkpoint under a new writer. The original armed copy
// remains intact even if storage fails after writing data or switching pointer.
export function writeCameraConfirmationCopy(local:Pick<Storage,"getItem"|"setItem">,
  session:Pick<Storage,"getItem"|"setItem">,draft:CameraDraft,writerId:string,
  expectedRaw:string,nextWriterId:string):string {
  if(!uuid(writerId)||!uuid(nextWriterId)||writerId===nextWriterId||
    !validCameraDraft(draft,draft.scope)||draft.awaiting)
    throw new Error("Invalid confirmed Camera copy.");
  const original=decodeCameraCopy(expectedRaw,draft.scope,writerId);
  if(!original.awaiting||draft.cameraId!==original.cameraId||draft.sceneId!==original.sceneId||
    draft.baseRevision<=original.awaiting.revision)
    throw new Error("The Camera confirmation does not belong to the retained original.");
  const key=cameraRecoveryKey(draft.scope,writerId),pointer=cameraPointerKey(draft.scope);
  const nextKey=cameraRecoveryKey(draft.scope,nextWriterId);
  const unchanged=()=>session.getItem(pointer)===writerId&&local.getItem(key)===expectedRaw;
  if(!unchanged())throw new Error("Camera recovery changed; original copies were retained.");
  if(local.getItem(nextKey)!==null)throw new Error("The Camera confirmation writer already exists.");
  const raw=JSON.stringify({schema:"motionwright.camera-recovery/1",writerId:nextWriterId,
    scope:draft.scope,draft});
  if(raw.length>16384)throw new Error("Camera recovery exceeds the bounded 16 KiB copy.");
  local.setItem(nextKey,raw);
  if(local.getItem(nextKey)!==raw)throw new Error("Camera confirmation copy could not be verified.");
  if(!unchanged())throw new Error("Camera recovery changed while confirmation was staged; copies were retained.");
  session.setItem(pointer,nextWriterId);
  if(session.getItem(pointer)!==nextWriterId)throw new Error("Camera confirmation pointer was not verified.");
  return raw;
}

export function loadCameraCopy(local:Pick<Storage,"getItem">,session:Pick<Storage,"getItem">,
  scope:string):{writerId:string;draft:CameraDraft}|null {
  const writerId=session.getItem(cameraPointerKey(scope));
  if(!writerId)return null;
  const raw=local.getItem(cameraRecoveryKey(scope,writerId));
  if(!raw)throw new Error("The Camera session references a missing recovery copy.");
  return {writerId,draft:decodeCameraCopy(raw,scope,writerId)};
}
export function listCameraCopies(storage:Pick<Storage,"length"|"key"|"getItem">,scope:string):string[]{
  const prefix=CAMERA_RECOVERY_PREFIX+encoded(scope)+":";
  return Array.from({length:storage.length},(_,i)=>storage.key(i))
    .filter((key):key is string=>Boolean(key?.startsWith(prefix)))
    .map(key=>key.slice(prefix.length));
}
