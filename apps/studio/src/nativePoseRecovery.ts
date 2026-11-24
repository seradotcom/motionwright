/** Local author-buffer custody, never a Project writer or execution authority. */
import type { CanvasKeyframe, CanvasNode, Change, Project, ProjectEvent, Scene } from "./types";
import { defaultNativePoseDraft, savedNativePoseDraft, nativePoseEdit, MAX_POSE_WAYPOINTS, POSE_GEOMETRY, type NativePoseDraft, type PoseCurve, type PoseGeometry } from "./nativePoseDraft";
import { sameValue } from "./creativeProduction";
import { sameProjectRevision } from "./projectMutation";

export const POSE_RECOVERY_PREFIX = "motionwright.native-pose-recovery.v1:";
export const POSE_FIELDS = ["x", "y", "width", "height", "rotation_deg", "endFrame"] as const;
export type PoseField = typeof POSE_FIELDS[number];
export type PoseWaypointRaw = Record<keyof PoseGeometry, string> & { frame: string; curve: PoseCurve };
export type PoseRaw = Record<PoseField, string> & Pick<NativePoseDraft, "curve" | "fade" | "fadeCurve"> & {
  fadeEndFrame?: string;
  fadeOutStartFrame?: string;
  fadeOutEndFrame?: string;
  fadeOutCurve?: NativePoseDraft["fadeCurve"];
  waypoints?: PoseWaypointRaw[];
};
type Profile = Project["deliverables"][number] | null;
type PoseChange = Extract<Change, { type: "set_canvas_motion_keyframes" }>;
export interface PoseAuthority {
  scope: string; revision: number; profileId: string | null; context: string;
}
export interface PoseRecovery {
  schema: "motionwright.native-pose-recovery/1";
  authority: PoseAuthority; raw: PoseRaw; dirty: boolean;
  blocked: PoseChange[];
  pending: { id: string; expectedContext: string; submitted: PoseChange } | null;
}
type StorageAccess = Pick<Storage, "length" | "key" | "getItem" | "setItem">;
const MAX_COPY = 512 * 1024, MAX_TOTAL = 4 * 1024 * 1024, MAX_COPIES = 128;
const bytes = (value: string) => new TextEncoder().encode(value).length;
const decimal = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;
const obj = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const exact = (value: Record<string, unknown>, fields: string[]) =>
  Object.keys(value).length === fields.length && fields.every(field => Object.hasOwn(value, field));
const string = (value: unknown): value is string => typeof value === "string";
const uuid = (value: unknown): value is string => string(value) && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
const properties = ["x", "y", "width", "height", "rotation_deg", "opacity"];
const validKey = (value: unknown): value is CanvasKeyframe =>
  obj(value) && exact(value, ["at", "property", "value", "interpolation"]) &&
  obj(value.at) && exact(value.at, ["num", "den"]) && string(value.at.num) && string(value.at.den) &&
  /^(0|[1-9][0-9]{0,18})$/.test(value.at.num) && /^[1-9][0-9]{0,18}$/.test(value.at.den) &&
  string(value.property) && properties.includes(value.property) &&
  typeof value.value === "number" && Number.isFinite(value.value) &&
  string(value.interpolation) && ["linear", "ease_out_cubic", "hold"].includes(value.interpolation);
const numericText = (value: unknown): value is string => string(value) && value.length <= 64;
const validWaypoint = (value: unknown): value is PoseWaypointRaw =>
  obj(value) && exact(value, [...POSE_GEOMETRY, "frame", "curve"]) &&
  [...POSE_GEOMETRY, "frame"].every(field => numericText(value[field])) &&
  string(value.curve) && ["linear", "ease_out_cubic", "hold"].includes(value.curve);
const validRaw = (value: unknown): value is PoseRaw =>
  obj(value) && exact(value, [...POSE_FIELDS, "curve", "fade", "fadeCurve",
    ...["fadeEndFrame", "fadeOutStartFrame", "fadeOutEndFrame", "fadeOutCurve"].filter(field => Object.hasOwn(value, field)),
    ...(Object.hasOwn(value, "waypoints") ? ["waypoints"] : [])]) &&
  ["fadeEndFrame", "fadeOutStartFrame", "fadeOutEndFrame"].every(field => !Object.hasOwn(value, field) || numericText(value[field])) &&
  (!Object.hasOwn(value, "fadeOutCurve") || (string(value.fadeOutCurve) && ["linear", "ease_out_cubic"].includes(value.fadeOutCurve))) &&
  (!Object.hasOwn(value, "waypoints") || (Array.isArray(value.waypoints) &&
    value.waypoints.length <= MAX_POSE_WAYPOINTS && value.waypoints.every(validWaypoint))) &&
  POSE_FIELDS.every(field => string(value[field]) && value[field].length <= 64) &&
  string(value.curve) && ["linear", "ease_out_cubic", "hold"].includes(value.curve) &&
  string(value.fade) && ["none", "in", "out", "in_out"].includes(value.fade) &&
  string(value.fadeCurve) && ["linear", "ease_out_cubic"].includes(value.fadeCurve);
export const poseScope = (project: Project, scene: Scene, node: CanvasNode) =>
  JSON.stringify([project.id, project.generation, project.active_branch, scene.id, node.id]);
export const poseRecoveryKey = (scope: string) => POSE_RECOVERY_PREFIX + encodeURIComponent(scope);
const canonical = (value: unknown): string => JSON.stringify(value, (_key, part) =>
  obj(part) ? Object.fromEntries(Object.keys(part).sort().map(key => [key, part[key]])) : part);
export function poseAuthority(project: Project, scene: Scene, node: CanvasNode, profile: Profile): PoseAuthority {
  const parent = node.parent_id === null ? null : scene.nodes.find(item => item.id === node.parent_id) ?? null;
  return { scope: poseScope(project, scene, node), revision: project.revision, profileId: profile?.id ?? null,
    context: canonical({ node, parent, renderer: scene.renderer, start: scene.start, duration: scene.duration,
      beats: scene.beats, camera: scene.camera, profile,
      children: scene.nodes.filter(item => item.parent_id === node.id).map(item => item.id),
      locks: project.locks.filter(lock => lock.resource === "scene:" + scene.id || lock.resource === "project:" + project.id),
      cameraReframe: project.production_design?.camera_reframes?.find(item => item.scene_id === scene.id) ?? null }) };
}
export function poseRaw(draft: NativePoseDraft): PoseRaw {
  return { x: String(draft.start.x), y: String(draft.start.y), width: String(draft.start.width),
    height: String(draft.start.height), rotation_deg: String(draft.start.rotation_deg),
    endFrame: String(draft.endFrame), curve: draft.curve, fade: draft.fade, fadeCurve: draft.fadeCurve,
    ...(draft.fadeEndFrame === undefined ? {} : { fadeEndFrame: String(draft.fadeEndFrame) }),
    ...(draft.fadeOutStartFrame === undefined ? {} : { fadeOutStartFrame: String(draft.fadeOutStartFrame) }),
    ...(draft.fadeOutEndFrame === undefined ? {} : { fadeOutEndFrame: String(draft.fadeOutEndFrame) }),
    ...(draft.fadeOutCurve === undefined ? {} : { fadeOutCurve: draft.fadeOutCurve }),
    ...(draft.waypoints === undefined ? {} : { waypoints: draft.waypoints.map(waypoint => ({
      frame: String(waypoint.frame), curve: waypoint.curve, x: String(waypoint.pose.x), y: String(waypoint.pose.y),
      width: String(waypoint.pose.width), height: String(waypoint.pose.height), rotation_deg: String(waypoint.pose.rotation_deg),
    })) }) };
}
export function parsePoseRaw(raw: PoseRaw): NativePoseDraft {
  if (!validRaw(raw)) throw new Error("Pose input exceeds its supported fields or 64-character limit.");
  const number = (field: string, text: string) => {
    const value = text.trim();
    if (!decimal.test(value) || !Number.isFinite(Number(value))) throw new Error("Enter a complete finite number for " + field + ". Your exact input is retained.");
    return Number(value);
  };
  const pose = (value: Pick<PoseRaw, keyof PoseGeometry>): PoseGeometry => ({
    x: number("x", value.x), y: number("y", value.y), width: number("width", value.width),
    height: number("height", value.height), rotation_deg: number("rotation_deg", value.rotation_deg),
  });
  return { start: pose(raw), endFrame: number("endFrame", raw.endFrame),
    curve: raw.curve, fade: raw.fade, fadeCurve: raw.fadeCurve,
    ...(raw.fade === "none" || raw.fadeEndFrame === undefined || raw.fadeEndFrame === "" ? {} :
      { fadeEndFrame: number("fade end frame", raw.fadeEndFrame) }),
    ...(raw.fade !== "in_out" ? {} : {
      fadeOutStartFrame: number("fade-out start frame", raw.fadeOutStartFrame ?? ""),
      fadeOutEndFrame: number("fade-out end frame", raw.fadeOutEndFrame ?? ""),
      fadeOutCurve: raw.fadeOutCurve ?? raw.fadeCurve,
    }),
    ...(raw.waypoints === undefined || raw.waypoints.length === 0 ? {} : {
      waypoints: raw.waypoints.map(waypoint => ({
        frame: number("intermediate frame", waypoint.frame), curve: waypoint.curve, pose: pose(waypoint),
      })),
    }),
  };
}
export function freshPoseRecovery(project: Project, scene: Scene, node: CanvasNode, profile: Profile, blocked: PoseChange[] = []): PoseRecovery {
  return { schema: "motionwright.native-pose-recovery/1", authority: poseAuthority(project, scene, node, profile),
    raw: poseRaw(savedNativePoseDraft(node, profile) ?? defaultNativePoseDraft(node)), dirty: false, blocked, pending: null };
}
export function poseConflict(draft: PoseRecovery, project: Project, scene: Scene, node: CanvasNode, profile: Profile): boolean {
  return !sameValue(draft.authority, poseAuthority(project, scene, node, profile));
}
export function rebasePose(draft: PoseRecovery, project: Project, scene: Scene, node: CanvasNode, profile: Profile): PoseRecovery {
  if (draft.pending || draft.authority.scope !== poseScope(project, scene, node)) throw new Error("Review the original attempt and object before rebasing.");
  return { ...draft, authority: poseAuthority(project, scene, node, profile), dirty: true };
}
function orderedKeys(keys: CanvasKeyframe[]): CanvasKeyframe[] {
  return [...keys].sort((a, b) => {
    const difference = BigInt(a.at.num) * BigInt(b.at.den) - BigInt(b.at.num) * BigInt(a.at.den);
    return difference < 0n ? -1 : difference > 0n ? 1 : properties.indexOf(a.property) - properties.indexOf(b.property);
  });
}
export function preparePose(draft: PoseRecovery, project: Project, scene: Scene, node: CanvasNode, profile: Profile,
  id: string): { draft: PoseRecovery; change: PoseChange } {
  if (!uuid(id) || draft.pending || poseConflict(draft, project, scene, node, profile))
    throw new Error("Review the saved source and any unconfirmed attempt before saving a new pose.");
  const edit = nativePoseEdit(project, scene, node, profile, parsePoseRaw(draft.raw));
  if (!edit.keys) throw new Error(edit.issue);
  const change: PoseChange = { type: "set_canvas_motion_keyframes", scene_id: scene.id, node_id: node.id, keyframes: edit.keys };
  if (draft.blocked.some(prior => sameValue(orderedKeys(prior.keyframes), orderedKeys(change.keyframes)))) throw new Error("An earlier unconfirmed pose cannot be resubmitted. Make a distinct edit.");
  if (sameValue(orderedKeys(node.keyframes), orderedKeys(change.keyframes))) throw new Error("This pose is already saved; no change is needed.");
  const expectedContext = poseAuthority(project, scene, { ...node, keyframes: orderedKeys(edit.keys) }, profile).context;
  return { draft: { ...draft, dirty: true, pending: { id, expectedContext, submitted: structuredClone(change) } }, change };
}
export function acknowledgePose(draft: PoseRecovery, id: string, receipt: Project): PoseRecovery {
  if (!draft.pending || draft.pending.id !== id) throw new Error("Only the original pose submission can acknowledge this attempt.");
  const [_project, _generation, _branch, sceneId, nodeId] = JSON.parse(draft.authority.scope) as string[];
  const scene = receipt.scenes.find(item => item.id === sceneId);
  const node = scene?.nodes.find(item => item.id === nodeId);
  const profile = receipt.deliverables.find(item => item.id === draft.authority.profileId);
  if (!scene || !node || !profile || poseScope(receipt, scene, node) !== draft.authority.scope)
    throw new Error("The original response belongs to another pose source.");
  const authority = poseAuthority(receipt, scene, { ...node, keyframes: orderedKeys(node.keyframes) }, profile);
  if (receipt.revision !== draft.authority.revision + 1 || authority.context !== draft.pending.expectedContext)
    throw new Error("The original response did not confirm the exact pose, parent and output context.");
  return { ...draft, authority: poseAuthority(receipt, scene, node, profile), dirty: false, pending: null };
}
function validChange(value: unknown, scope: string): value is PoseChange {
  if (!obj(value) || !exact(value, ["type", "scene_id", "node_id", "keyframes"]) || value.type !== "set_canvas_motion_keyframes") return false;
  const ids = JSON.parse(scope) as string[];
  if (value.scene_id !== ids[3] || value.node_id !== ids[4] || !Array.isArray(value.keyframes) ||
      !value.keyframes.every(validKey)) return false;
  const alpha = value.keyframes.filter(key => key.property === "opacity").length;
  const geometry = value.keyframes.length - alpha;
  return geometry >= 10 && geometry <= (MAX_POSE_WAYPOINTS + 2) * POSE_GEOMETRY.length &&
    geometry % POSE_GEOMETRY.length === 0 && [0, 2, 3, 4].includes(alpha);
}
export function decodePoseRecovery(raw: string, scope: string): PoseRecovery {
  if (bytes(raw) > MAX_COPY) throw new Error("The retained pose copy exceeds its bounded size.");
  const value: unknown = JSON.parse(raw);
  if (!obj(value) || !exact(value, ["schema", "authority", "raw", "dirty", "pending", "blocked"]) ||
    value.schema !== "motionwright.native-pose-recovery/1" || !obj(value.authority) ||
    !exact(value.authority, ["scope", "revision", "profileId", "context"]) || value.authority.scope !== scope ||
    typeof value.authority.revision !== "number" || !Number.isSafeInteger(value.authority.revision) || value.authority.revision < 0 ||
    !(value.authority.profileId === null || string(value.authority.profileId)) ||
    !string(value.authority.context) || !validRaw(value.raw) || typeof value.dirty !== "boolean" ||
    !Array.isArray(value.blocked) || value.blocked.length > 16 || !value.blocked.every(change => validChange(change, scope)) ||
    !(value.pending === null || (value.dirty && obj(value.pending) &&
      exact(value.pending, ["id", "expectedContext", "submitted"]) && uuid(value.pending.id) &&
      string(value.pending.expectedContext) && validChange(value.pending.submitted, scope))))
    throw new Error("The retained pose copy is invalid or belongs to another source. Its original bytes were not replaced.");
  return value as unknown as PoseRecovery;
}
export function writePoseRecovery(storage: StorageAccess, draft: PoseRecovery, expected: string | null): string {
  const key = poseRecoveryKey(draft.authority.scope), raw = JSON.stringify(draft);
  decodePoseRecovery(raw, draft.authority.scope);
  if (storage.getItem(key) !== expected) throw new Error("Another window changed the retained pose. Your current input was not sent.");
  let size = bytes(key) + bytes(raw), count = 1;
  for (let index = 0; index < storage.length; index++) {
    const other = storage.key(index);
    if (!other?.startsWith(POSE_RECOVERY_PREFIX) || other === key) continue;
    size += bytes(other) + bytes(storage.getItem(other) ?? ""); count++;
  }
  if (size > MAX_TOTAL || count > MAX_COPIES) throw new Error("Local pose recovery is full (128 copies / 4 MiB).");
  if (storage.getItem(key) !== expected) throw new Error("The pose copy changed during backup.");
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The pose backup was not confirmed.");
  return raw;
}

export interface PoseOutcomeReview {
  scope: string; pending: NonNullable<PoseRecovery["pending"]>; project: Project; matchingValueRevisions: number[];
}
function locate(draft: PoseRecovery, project: Project) {
  const [, , , sceneId, nodeId] = JSON.parse(draft.authority.scope) as string[];
  const scene = project.scenes.find(item => item.id === sceneId);
  const node = scene?.nodes.find(item => item.id === nodeId);
  const profile = project.deliverables.find(item => item.id === draft.authority.profileId);
  if (!scene || !node || !profile || poseScope(project, scene, node) !== draft.authority.scope)
    throw new Error("The exact original pose object or output profile is unavailable.");
  return { scene, node, profile };
}
export async function inspectPoseOutcome(draft: PoseRecovery, selected: Project, transport: {
  readProject(id: string): Promise<Project>; readHistory(project: Project, after: number, limit: number): Promise<ProjectEvent[]>;
}): Promise<PoseOutcomeReview> {
  if (!draft.pending) throw new Error("There is no retained uncertain pose attempt.");
  const pending = structuredClone(draft.pending);
  const project = await transport.readProject(selected.id);
  locate(draft, project);
  if (!sameProjectRevision(project, selected)) throw new Error("Reload the saved project before inspecting this outcome.");
  let cursor = draft.authority.revision;
  if (!Number.isSafeInteger(project.revision) || project.revision < cursor || project.revision - cursor > 2048)
    throw new Error("The saved history is outside the bounded pose review window.");
  const matchingValueRevisions: number[] = [];
  while (cursor < project.revision) {
    const page = await transport.readHistory(project, cursor, 256);
    if (!Array.isArray(page) || !page.length || page.length > 256) throw new Error("The saved history is incomplete.");
    for (const event of page) {
      if (!event || event.revision !== cursor + 1 || event.revision > project.revision ||
          !obj(event.change) || !string(event.change.type) || !string(event.created_at))
        throw new Error("The saved history is not a contiguous revision sequence.");
      if (sameValue(event.change, pending.submitted)) matchingValueRevisions.push(event.revision);
      cursor = event.revision;
    }
  }
  const after = await transport.readProject(selected.id);
  const beforeContext = locate(draft, project), afterContext = locate(draft, after);
  if (!sameProjectRevision(after, project) || !sameValue(poseAuthority(project, beforeContext.scene, beforeContext.node, beforeContext.profile),
      poseAuthority(after, afterContext.scene, afterContext.node, afterContext.profile)))
    throw new Error("The saved source changed during inspection. Read it again.");
  return { scope: draft.authority.scope, pending, project, matchingValueRevisions };
}
export function poseReviewMatches(draft: PoseRecovery, project: Project, review: PoseOutcomeReview | null): boolean {
  if (!review || !draft.pending || draft.authority.scope !== review.scope || !sameValue(draft.pending, review.pending) || !sameProjectRevision(project, review.project)) return false;
  try {
    const current = locate(draft, project), read = locate(draft, review.project);
    return sameValue(poseAuthority(project, current.scene, current.node, current.profile),
      poseAuthority(review.project, read.scene, read.node, read.profile));
  } catch { return false; }
}
/** Explicitly begin a distinct edit. Preserve exact UNKNOWN bytes and never
 * equate matching history values with the original transport acknowledgement. */
export function retainSeparatePose(storage: StorageAccess, draft: PoseRecovery, expected: string | null,
  project: Project, scene: Scene, node: CanvasNode, profile: Profile, review: PoseOutcomeReview): { draft: PoseRecovery; raw: string } {
  if (!poseReviewMatches(draft, project, review) || draft.authority.scope !== poseScope(project, scene, node) ||
      profile?.id !== draft.authority.profileId || expected === null ||
      storage.getItem(poseRecoveryKey(draft.authority.scope)) !== expected ||
      !sameValue(decodePoseRecovery(expected, draft.authority.scope), draft))
    throw new Error("The original uncertain pose or current saved source changed. Inspect again.");
  if (draft.blocked.length >= 16) throw new Error("The bounded unconfirmed-pose comparison list is full.");
  const archive = poseRecoveryKey(draft.authority.scope) + ":unknown:" + draft.pending!.id;
  if (storage.getItem(archive) !== null && storage.getItem(archive) !== expected)
    throw new Error("The earlier uncertain pose archive changed.");
  let size = bytes(archive) + bytes(expected), count = 1;
  for (let index = 0; index < storage.length; index++) {
    const key = storage.key(index);
    if (!key?.startsWith(POSE_RECOVERY_PREFIX) || key === archive) continue;
    size += bytes(key) + bytes(storage.getItem(key) ?? ""); count++;
  }
  if (size > MAX_TOTAL || count > MAX_COPIES) throw new Error("Local pose recovery is full; the original attempt remains locked.");
  storage.setItem(archive, expected);
  if (storage.getItem(archive) !== expected) throw new Error("The earlier uncertain pose archive was not confirmed.");
  const next = freshPoseRecovery(project, scene, node, profile, [...draft.blocked, draft.pending!.submitted]);
  return { draft: next, raw: writePoseRecovery(storage, next, expected) };
}
