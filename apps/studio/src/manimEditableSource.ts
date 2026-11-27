import { invoke } from "@tauri-apps/api/core";
import type { Asset, CanvasNode, CanvasTransform, Change, NodeStyle, Project, ProjectEvent } from "./types";
import { sameValue } from "./creativeProduction";

export const MANIM_EDITABLE_MIME = "application/vnd.motionwright.manim-source+zip";
export type ManimProperty = "transform" | "text" | "style" | "controls";
export const MANIM_CONTROLS_RATIONALE = "Reintegrate reviewed Manim object controls";
export type ManimChange = Extract<Change, {type: "transform_canvas_node" | "update_canvas_text" | "update_canvas_style" | "apply_creative_patch"}>;
export type ManimScope = {project_id: string; generation: string; revision: number; active_branch: string; scene_id: string; deliverable_id: string};
export type ManimSnapshot = {schema: "motionwright.manim-editable-source/1"; source: ManimScope; bundle_sha256: string; size_bytes: string; media_type: string; native_execution_performed: false; project_modified: false};
export type ManimExport = Omit<ManimSnapshot, "schema"> & {schema: "motionwright.manim-editable-source-export/1"; path: string};
export type ManimAttachedRequest = {source: ManimScope; asset_id: string; expected_sha256: string};
export type ManimAttached = {schema: "motionwright.manim-editable-source-attached/1"; source: ManimScope; asset_id: string; source_sha256: string; original_plan_sha256: string; current_plan_sha256: string; origin_revision: number; revision_only_rebound: boolean; node_ids: string[]; editable_parameters: string[]; native_execution_performed: false; imported_source_authority_used: false};
export type ManimEditRequest = {source: ManimScope; original_asset_id: string; original_sha256: string; edited_asset_id: string; edited_sha256: string; node_id: string; property: ManimProperty};
export type ManimPreview = {schema: "motionwright.manim-editable-source-edit/1"; request: ManimEditRequest; preview: {
  expected: {resource: string; generation: string; revision: number};
  original_source_asset_id: string; edited_source_asset_id: string; original_source_sha256: string; edited_source_sha256: string;
  current_plan_sha256: string; proposed_plan_sha256: string; change: ManimChange;
  native_execution_performed: false; imported_source_authority_used: false;
}};
type Transport = (command: string, args: Record<string, unknown>) => Promise<unknown>;
const desktop = () => typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
const transport: Transport = (command, args) => {
  if (!desktop()) throw new Error("Manim editable source requires Motionwright desktop.");
  return invoke(command, args);
};
const sha = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const fail = (): never => { throw new Error("The Manim response does not match the exact selected saved source."); };
const exact = (value: Record<string, unknown>, keys: readonly string[]) => Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value,key));
const boundedSize = (value: unknown) => typeof value === "string" && /^[1-9][0-9]{0,8}$/.test(value) && Number(value) <= 34 * 1024 * 1024;
const scopeFields = ["project_id","generation","revision","active_branch","scene_id","deliverable_id"] as const;
const uuid = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value) && value !== "00000000-0000-0000-0000-000000000000";
const scopeShape = (value: unknown): value is ManimScope => record(value) && exact(value,scopeFields) &&
  scopeFields.filter(field=>field!=="revision").every(field=>uuid(value[field])) &&
  typeof value.revision === "number" && Number.isSafeInteger(value.revision) && value.revision >= 0;

export function manimScope(project: Project, sceneId: string, deliverableId: string): ManimScope {
  if (!project.scenes.some(scene => scene.id === sceneId && scene.renderer === "manim-community") ||
      !project.deliverables.some(profile => profile.id === deliverableId))
    throw new Error("Choose a saved Manim Community scene and output profile.");
  return {project_id: project.id, generation: project.generation, revision: project.revision,
    active_branch: project.active_branch, scene_id: sceneId, deliverable_id: deliverableId};
}
export function scopeCurrent(project: Project, scope: ManimScope): boolean {
  try { return sameValue(manimScope(project,scope.scene_id,scope.deliverable_id),scope); } catch { return false; }
}
export function manimAsset(project: Project, id: string) {
  const asset = project.assets.find(item => item.id === id && item.media_type === MANIM_EDITABLE_MIME && sha(item.content_sha256));
  if (!asset) throw new Error("Choose an attached Manim editable ZIP with its saved digest.");
  return asset;
}

export function isManimEditedAsset(asset: Asset) {
  return (asset.media_type === MANIM_EDITABLE_MIME || asset.media_type === "application/json")
    && sha(asset.content_sha256);
}

export function manimEditedAsset(project: Project, id: string) {
  const asset = project.assets.find(item => item.id === id && isManimEditedAsset(item));
  if (!asset) throw new Error("Choose an attached Manim ZIP or edited plan JSON with its saved digest.");
  return asset;
}

export function readManimSnapshot(value: unknown, scope: ManimScope): ManimSnapshot {
  if (!record(value) || value.schema !== "motionwright.manim-editable-source/1" ||
      !sameValue(value.source, scope) || !sha(value.bundle_sha256) || !boundedSize(value.size_bytes) ||
      value.media_type !== MANIM_EDITABLE_MIME || value.project_modified !== false || value.native_execution_performed !== false) return fail();
  return value as unknown as ManimSnapshot;
}
export async function inspectManimSource(scope: ManimScope, call: Transport = transport): Promise<ManimSnapshot> {
  return readManimSnapshot(await call("inspect_manim_editable_source", {request: scope}), scope);
}
export function manimExportSubject(snapshot: ManimSnapshot, path: string) {
  if (!path || path !== path.trim() || /[\u0000-\u001f\u007f]/.test(path) ||
      new TextEncoder().encode(path).length > 1600 || !path.endsWith(".mwedit.zip"))
    throw new Error("Choose one absolute .mwedit.zip destination.");
  const source = snapshot.source;
  return ["manim-editable-source",source.active_branch,source.scene_id,source.deliverable_id,snapshot.bundle_sha256,path].join("|");
}
export async function exportManimSource(snapshot: ManimSnapshot, path: string, call: Transport = transport): Promise<ManimExport> {
  const subject = manimExportSubject(snapshot,path), source = snapshot.source;
  const grant = await call("issue_effect_grant", {request: {effect:"deliver_local",project_id:source.project_id,
    generation:source.generation,revision:source.revision,subject}});
  if (!record(grant) || !uuid(grant.token) || grant.effect !== "deliver_local" || grant.one_time !== true ||
      grant.project_id !== source.project_id || grant.generation !== source.generation || grant.revision !== source.revision) return fail();
  const value = await call("export_manim_editable_source", {request:{source,
    expected_bundle_sha256:snapshot.bundle_sha256,path,effect_grant:grant.token}});
  if (!record(value) || value.schema !== "motionwright.manim-editable-source-export/1" ||
      !sameValue(value.source,source) || value.bundle_sha256 !== snapshot.bundle_sha256 ||
      value.size_bytes !== snapshot.size_bytes || value.path !== path || value.media_type !== MANIM_EDITABLE_MIME ||
      value.native_execution_performed !== false || value.project_modified !== false) return fail();
  return value as unknown as ManimExport;
}
export async function inspectAttachedManim(request: ManimAttachedRequest, call: Transport = transport): Promise<ManimAttached> {
  const value = await call("inspect_attached_manim_editable_source",{request});
  if (!record(value) || value.schema !== "motionwright.manim-editable-source-attached/1" ||
      !sameValue(value.source,request.source) || value.asset_id !== request.asset_id ||
      value.source_sha256 !== request.expected_sha256 || !sha(value.original_plan_sha256) ||
      !sha(value.current_plan_sha256) || typeof value.origin_revision !== "number" ||
      !Number.isSafeInteger(value.origin_revision) || value.origin_revision < 0 || value.origin_revision > request.source.revision ||
      typeof value.revision_only_rebound !== "boolean" || !Array.isArray(value.node_ids) ||
      value.node_ids.length < 1 || value.node_ids.length > 128 || !value.node_ids.every(uuid) ||
      new Set(value.node_ids).size !== value.node_ids.length ||
      !(sameValue(value.editable_parameters,["one_node.transform","one_node.text","one_node.style"]) ||
      sameValue(value.editable_parameters,["one_node.transform","one_node.text","one_node.style","one_node.controls"])) ||
      value.native_execution_performed !== false || value.imported_source_authority_used !== false) return fail();
  return value as unknown as ManimAttached;
}
const transformKeys = ["x","y","width","height","rotation_deg","opacity"] as const;
const styleKeys = ["fill","stroke","stroke_width","font_family","font_size","font_weight","line_height","blend_mode"] as const;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const optionalText = (value: unknown) => value === null || typeof value === "string";
const optionalNumber = (value: unknown) => value === null || finite(value);
function validTransform(value: unknown): value is CanvasTransform {
  return record(value) && exact(value,transformKeys) && transformKeys.every(field=>finite(value[field])) &&
    (value.width as number)>0 && (value.height as number)>0 &&
    (value.opacity as number)>=0 && (value.opacity as number)<=1;
}
function validStyle(value: unknown): value is NodeStyle {
  return record(value) && exact(value,styleKeys) &&
    ["fill","stroke","font_family"].every(field=>optionalText(value[field])) &&
    ["font_size","font_weight","line_height"].every(field=>optionalNumber(value[field])) &&
    finite(value.stroke_width) && ["normal","multiply","screen","add"].includes(String(value.blend_mode));
}
function validControlsPatch(value: unknown, request: ManimEditRequest): boolean {
  if (!record(value) || !exact(value,["type","patch"]) || value.type !== "apply_creative_patch" || !record(value.patch)) return false;
  const patch=value.patch;
  if (!exact(patch,["scene_id","base_revision","rationale","edits"]) || patch.scene_id !== request.source.scene_id ||
      patch.base_revision !== request.source.revision || patch.rationale !== MANIM_CONTROLS_RATIONALE ||
      !Array.isArray(patch.edits) || patch.edits.length < 1 || patch.edits.length > 3) return false;
  let previous=-1;
  for (const edit of patch.edits) {
    if (!record(edit) || edit.node_id !== request.node_id) return false;
    const index=["transform","text","style"].indexOf(String(edit.kind));
    if (index <= previous) return false;
    previous=index;
    if (edit.kind === "transform" && exact(edit,["kind","node_id","transform"]) && validTransform(edit.transform)) continue;
    if (edit.kind === "text" && exact(edit,["kind","node_id","text"]) && typeof edit.text === "string") continue;
    if (edit.kind === "style" && exact(edit,["kind","node_id","style"]) && validStyle(edit.style)) continue;
    return false;
  }
  return true;
}
function validChange(value: unknown, request: ManimEditRequest): value is ManimChange {
  if (request.property === "controls") return validControlsPatch(value,request);
  if (!record(value) || value.scene_id !== request.source.scene_id || value.node_id !== request.node_id) return false;
  if (request.property === "transform") return value.type === "transform_canvas_node" && exact(value,["type","scene_id","node_id","transform"]) && validTransform(value.transform);
  if (request.property === "text") return value.type === "update_canvas_text" && exact(value,["type","scene_id","node_id","text"]) && optionalText(value.text);
  return request.property === "style" && value.type === "update_canvas_style" && exact(value,["type","scene_id","node_id","style"]) && validStyle(value.style);
}
function editRequestShape(value: unknown): value is ManimEditRequest {
  return record(value) && exact(value,["source","original_asset_id","original_sha256","edited_asset_id","edited_sha256","node_id","property"]) &&
    scopeShape(value.source) && uuid(value.original_asset_id) && uuid(value.edited_asset_id) && value.original_asset_id !== value.edited_asset_id &&
    uuid(value.node_id) && sha(value.original_sha256) && sha(value.edited_sha256) && ["transform","text","style","controls"].includes(String(value.property));
}
export function readManimPreview(value: unknown, request: ManimEditRequest): ManimPreview {
  if (!record(value) || value.schema !== "motionwright.manim-editable-source-edit/1" ||
      !sameValue(value.request,request) || !record(value.preview)) return fail();
  const p = value.preview, source = request.source;
  if (!sameValue(p.expected,{resource:"project:"+source.project_id,generation:source.generation,revision:source.revision}) ||
      p.original_source_asset_id !== request.original_asset_id || p.edited_source_asset_id !== request.edited_asset_id ||
      p.original_source_sha256 !== request.original_sha256 || p.edited_source_sha256 !== request.edited_sha256 ||
      !sha(p.current_plan_sha256) || !sha(p.proposed_plan_sha256) || p.current_plan_sha256 === p.proposed_plan_sha256 ||
      p.native_execution_performed !== false || p.imported_source_authority_used !== false || !validChange(p.change,request)) return fail();
  return value as unknown as ManimPreview;
}
export async function previewManimEdit(request: ManimEditRequest, call: Transport = transport): Promise<ManimPreview> {
  return readManimPreview(await call("preview_manim_editable_source_edit",{request}),request);
}
export function proposedNode(before: CanvasNode, change: ManimChange): CanvasNode {
  if (change.type === "apply_creative_patch") {
    let result={...before};
    for (const edit of change.patch.edits) {
      if (edit.node_id !== before.id) return fail();
      switch (edit.kind) {
        case "transform": result={...result,...edit.transform}; break;
        case "text": result={...result,text:edit.text}; break;
        case "style": result={...result,style:edit.style}; break;
        default: return fail();
      }
    }
    return result;
  }
  if (before.id !== change.node_id) return fail();
  switch (change.type) {
    case "transform_canvas_node": return {...before,...change.transform};
    case "update_canvas_text": return {...before,text:change.text};
    case "update_canvas_style": return {...before,style:change.style};
  }
}
export function previewCurrent(project: Project, result: ManimPreview): boolean {
  if (!scopeCurrent(project,result.request.source)) return false;
  try {
    return manimAsset(project,result.request.original_asset_id).content_sha256 === result.request.original_sha256 &&
      manimEditedAsset(project,result.request.edited_asset_id).content_sha256 === result.request.edited_sha256 &&
      project.scenes.find(scene=>scene.id===result.request.source.scene_id)?.nodes.some(node=>node.id===result.request.node_id) === true;
  } catch { return false; }
}

/** Local recovery data is never an execution grant. A retained UNKNOWN cannot
 * be dispatched from hydration, journal inspection, or an automatic retry. */
export type ManimPending =
  | {kind:"export"; source:ManimScope; path:string; bundle_sha256:string; size_bytes:string}
  | {kind:"edit"; source:ManimScope; result:ManimPreview};
export function pendingScopeKey(source: Pick<ManimScope,"project_id"|"generation"|"active_branch"|"scene_id">): string {
  return "motionwright.manim-source-pending.v1:"+JSON.stringify([source.project_id,source.generation,source.active_branch,source.scene_id]);
}
export function readManimPending(raw: string, key: string): ManimPending {
  if (new TextEncoder().encode(raw).length > 256 * 1024) throw new Error("Retained Manim intent exceeds its recovery budget.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || !scopeShape(value.source) || pendingScopeKey(value.source) !== key) return fail();
  if (value.kind === "export" && exact(value,["kind","source","path","bundle_sha256","size_bytes"]) &&
      typeof value.path === "string" && value.path.endsWith(".mwedit.zip") && sha(value.bundle_sha256) && boundedSize(value.size_bytes))
    return value as unknown as ManimPending;
  if (value.kind === "edit" && exact(value,["kind","source","result"]) && record(value.result) &&
      editRequestShape(value.result.request) && sameValue(value.result.request.source,value.source)) {
    readManimPreview(value.result,value.result.request);
    return value as unknown as ManimPending;
  }
  return fail();
}
type LocalStore = Pick<Storage,"length"|"key"|"getItem"|"setItem"|"removeItem">;
function replayShape(change: ManimChange): ManimChange {
  return change.type === "apply_creative_patch" ? {...change,patch:{...change.patch,base_revision:0}} : change;
}
function rejectArchivedReplay(store: LocalStore, pending: ManimPending) {
  const key=pendingScopeKey(pending.source), prefix=key+":archive:";
  let count=0;
  for(let index=0;index<store.length;index++) {
    const name=store.key(index);
    if(!name?.startsWith(prefix)) continue;
    if(++count>16) throw new Error("The bounded Manim review archive is invalid.");
    const raw=store.getItem(name);
    if(raw===null) throw new Error("The retained archive changed during review.");
    const old=readManimPending(raw,key);
    if(old.kind==="export" && pending.kind==="export" && old.path===pending.path)
      throw new Error("An earlier uncertain export used this destination. Choose a distinct path.");
    if(old.kind==="edit" && pending.kind==="edit" &&
        old.result.request.original_sha256===pending.result.request.original_sha256 &&
        old.result.request.edited_sha256===pending.result.request.edited_sha256 &&
        sameValue(replayShape(old.result.preview.change),replayShape(pending.result.preview.change)))
      throw new Error("An earlier uncertain edit used this exact proposal. Review a distinct edit.");
  }
}
export function retainManimPending(store: LocalStore, pending: ManimPending): string {
  const key = pendingScopeKey(pending.source), raw = JSON.stringify(pending);
  readManimPending(raw,key);
  rejectArchivedReplay(store,pending);
  if (store.getItem(key) !== null) throw new Error("An earlier Manim source operation requires outcome review.");
  store.setItem(key,raw);
  if (store.getItem(key)!==raw) throw new Error("The original Manim intent could not be retained.");
  return raw;
}
export function acknowledgeManimPending(store: LocalStore, pending: ManimPending, original: string) {
  const key = pendingScopeKey(pending.source);
  if (store.getItem(key)!==original) throw new Error("The retained original Manim intent changed.");
  store.removeItem(key);
  if (store.getItem(key)!==null) throw new Error("The original Manim intent could not be released.");
}
export function confirmManimEdit(before: Project, result: ManimPreview, after: Project): boolean {
  const scope = result.request.source, scene = before.scenes.find(s=>s.id===scope.scene_id);
  const node = scene?.nodes.find(n=>n.id===result.request.node_id);
  if (!node || !previewCurrent(before,result) || after.id !== before.id || after.generation !== before.generation ||
      after.active_branch !== before.active_branch || after.revision !== before.revision+1) return false;
  const saved = after.scenes.find(s=>s.id===scope.scene_id)?.nodes.find(n=>n.id===node.id);
  if (!sameValue(saved,proposedNode(node,result.preview.change)) || !sameValue(before.assets,after.assets)) return false;
  if (result.preview.change.type === "apply_creative_patch") {
    const patch=result.preview.change.patch;
    return (after.production_design?.patches ?? []).some(record=>record.scene_id===scope.scene_id &&
      record.source_revision===scope.revision && record.rationale===patch.rationale &&
      sameValue(record.before,[node]) && sameValue(record.after,[saved]));
  }
  return true;
}
/** Observation only: matching journal values do not acknowledge a lost reply. */
export function matchingManimHistory(pending: Extract<ManimPending,{kind:"edit"}>, project: Project, events: ProjectEvent[]): number[] {
  const source=pending.source;
  if (project.id!==source.project_id || project.generation!==source.generation ||
      project.active_branch!==source.active_branch || project.revision<source.revision ||
      project.revision-source.revision>2048 || events.length!==project.revision-source.revision ||
      events.some((event,index)=>event.revision!==source.revision+index+1)) throw new Error("The exact saved journal is unavailable or incomplete.");
  return events.filter(event=>sameValue(event.change,pending.result.preview.change)).map(event=>event.revision);
}

/** Literal changed controls, for a reviewable diff before the existing Change. */
export function manimControlRows(before: CanvasNode, after: CanvasNode): {field:string;before:string;after:string}[] {
  const display=(value:unknown)=>value===null?"Unspecified":String(value);
  const values: [string,unknown,unknown][]=[
    ...transformKeys.map(key=>[key,before[key],after[key]] as [string,unknown,unknown]),
    ["text",before.text,after.text],
    ...styleKeys.map(key=>["style."+key,before.style[key],after.style[key]] as [string,unknown,unknown]),
  ];
  return values.filter(([,a,b])=>!sameValue(a,b)).map(([field,a,b])=>({field,before:display(a),after:display(b)}));
}
