import { useEffect, useRef, useState } from "react";
import { bootstrap, projectHistory } from "./api";
import { sameValue } from "./creativeProduction";
import { formatBeatTime, parseBeatTime } from "./sceneBeatTiming";
import type { Change, Project, ProjectEvent, RationalTime, Scene } from "./types";

export type InspectorChange = Extract<Change, { type: "update_scene_objective" | "set_scene_duration" }>;
export type InspectorField = "objective" | "duration";
export interface InspectorRaw { objective: string; duration: string }
export interface InspectorPending {
  id: string; revision: number; sequence: number; versions: Record<InspectorField, number>;
  field: InspectorField; change: InspectorChange; raw: InspectorRaw;
  prior: { objective: string; duration: RationalTime };
  outcome: "UNKNOWN" | "NOT_SENT"; diagnostic?: string;
}
export interface InspectorDraft {
  scope: string; baseRevision: number; sequence: number;
  versions: Record<InspectorField, number>; dirty: Record<InspectorField, boolean>;
  raw: InspectorRaw; clean: InspectorRaw; pending: InspectorPending | null;
}
export const INSPECTOR_RECOVERY_PREFIX = "motionwright.scene-inspector-recovery.v1:";
export const INSPECTOR_POINTER_PREFIX = "motionwright.scene-inspector-writer.v1:";
export const MAX_INSPECTOR_COPY_BYTES = 128 * 1024;
export const MAX_INSPECTOR_COPIES = 256;
export const MAX_INSPECTOR_RECOVERY_BYTES = 4 * 1024 * 1024;
const MAX_STORAGE_KEYS = 4096;
const fields = ["objective", "duration"] as const;
const clone = <T,>(value: T): T => structuredClone(value);
const same = sameValue;
const bytes = (value: string) => new TextEncoder().encode(value).length;
const uuid = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const number = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const exactKeys = (value: Record<string, unknown>, keys: readonly string[]) =>
  Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
export const inspectorOwner = (project: Project) => JSON.stringify([project.id, project.generation, project.active_branch]);
export const inspectorScope = (project: Project, sceneId: string) =>
  JSON.stringify([project.id, project.generation, project.active_branch, sceneId]);
export function inspectorIdentity(scope: string): [string, string, string, string] {
  const value: unknown = JSON.parse(scope);
  if (!Array.isArray(value) || value.length !== 4 || !value.every(uuid))
    throw new Error("The Inspector recovery project, generation, branch or scene is invalid.");
  return value as [string, string, string, string];
}
const ownerOf = (draft: InspectorDraft) => JSON.stringify(inspectorIdentity(draft.scope).slice(0, 3));
const targetOf = (draft: InspectorDraft) => inspectorIdentity(draft.scope)[3];
function assertOwner(draft: InspectorDraft, project: Project): void {
  if (ownerOf(draft) !== inspectorOwner(project))
    throw new Error("This Inspector input belongs to another project, generation or branch.");
}
function assertTarget(draft: InspectorDraft, project: Project): Scene {
  assertOwner(draft, project);
  const scene = project.scenes.find(item => item.id === targetOf(draft));
  if (!scene) throw new Error("The scene is no longer present. Its raw Inspector input was kept.");
  return scene;
}
const sceneRaw = (scene: Scene): InspectorRaw => ({ objective: scene.objective, duration: formatBeatTime(scene.duration) });
export function createInspectorDraft(project: Project, sceneId: string): InspectorDraft {
  const scope = inspectorScope(project, sceneId); inspectorIdentity(scope);
  const scene = project.scenes.find(item => item.id === sceneId);
  if (!scene) throw new Error("The selected Inspector scene is unavailable.");
  const raw = sceneRaw(scene);
  return { scope, baseRevision: project.revision, sequence: 0, versions: { objective: 0, duration: 0 },
    dirty: { objective: false, duration: false }, raw, clean: clone(raw), pending: null };
}
export const inspectorHasInput = (draft: InspectorDraft) => fields.some(field => draft.dirty[field]);
export const inspectorHasWork = (draft: InspectorDraft) => inspectorHasInput(draft) || draft.pending !== null;
export const inspectorUnknown = (draft: InspectorDraft) => draft.pending?.outcome === "UNKNOWN";
export function editInspectorDraft(draft: InspectorDraft, field: InspectorField, value: string): InspectorDraft {
  const sequence = draft.sequence + 1;
  return { ...draft, sequence, raw: { ...draft.raw, [field]: value }, dirty: { ...draft.dirty, [field]: true },
    versions: { ...draft.versions, [field]: sequence } };
}
export function reconcileInspectorDraft(draft: InspectorDraft, project: Project): InspectorDraft {
  assertOwner(draft, project);
  // Incoming values are observations, never original-request acknowledgements.
  if (inspectorHasWork(draft)) return draft;
  const raw = sceneRaw(assertTarget(draft, project));
  return { ...draft, baseRevision: project.revision, raw, clean: clone(raw) };
}
export function advanceInspectorDraft(draft: InspectorDraft, project: Project): InspectorDraft {
  assertTarget(draft, project);
  if (inspectorUnknown(draft)) throw new Error("Keep the original UNKNOWN attempt separate from a new intent.");
  return { ...draft, baseRevision: project.revision, sequence: draft.sequence + 1, pending: null };
}
const equalTime = (left: RationalTime, right: RationalTime) =>
  BigInt(left.num) * BigInt(right.den) === BigInt(right.num) * BigInt(left.den);
export function inspectorDuration(raw: string): RationalTime {
  const value = parseBeatTime(raw), n = BigInt(value.num), d = BigInt(value.den);
  if (n <= 0n || n > 86400n * d) throw new Error("Scene duration must be positive and no longer than 86400 seconds.");
  return value;
}
export function inspectorChange(draft: InspectorDraft, project: Project, field: InspectorField): InspectorChange {
  const scene = assertTarget(draft, project);
  if (!draft.dirty[field]) throw new Error("Edit this field before saving.");
  const locks = project.locks.filter(lock => lock.resource === "scene:" + scene.id);
  if (locks.some(lock => lock.kind === (field === "objective" ? "content" : "timing")))
    throw new Error("The scene's " + (field === "objective" ? "content" : "timing") + " is locked.");
  if (field === "objective") {
    const objective = draft.raw.objective.trim();
    if (!objective || bytes(objective) > 4000) throw new Error("A scene objective must contain 1–4000 UTF-8 bytes.");
    if (objective === scene.objective) throw new Error("The objective already matches the saved scene.");
    return { type: "update_scene_objective", scene_id: scene.id, objective };
  }
  const duration = inspectorDuration(draft.raw.duration);
  if (equalTime(duration, scene.duration)) throw new Error("The duration already matches the saved scene.");
  return { type: "set_scene_duration", scene_id: scene.id, duration };
}
export function prepareInspectorSubmission(draft: InspectorDraft, project: Project, change: InspectorChange, id: string): InspectorDraft {
  const scene = assertTarget(draft, project);
  if (!uuid(id)) throw new Error("A local original-attempt identity is required.");
  if (inspectorUnknown(draft)) throw new Error("The original Inspector outcome is UNKNOWN. No request is replayed.");
  if (draft.baseRevision !== project.revision) throw new Error("Review this input against the current revision before saving.");
  const field = change.type === "update_scene_objective" ? "objective" : "duration";
  if (!same(inspectorChange(draft, project, field), change))
    throw new Error("The Inspector request does not match its exact author input and scene.");
  return { ...draft, pending: { id, revision: project.revision, sequence: draft.sequence, versions: clone(draft.versions),
    field, change: clone(change), raw: clone(draft.raw), prior: { objective: scene.objective, duration: clone(scene.duration) }, outcome: "UNKNOWN" } };
}
export function retainInspectorFailure(draft: InspectorDraft, id: string, diagnostic: string, notSent = false): InspectorDraft {
  if (draft.pending?.id !== id) return draft;
  return { ...draft, pending: { ...draft.pending, outcome: notSent ? "NOT_SENT" : "UNKNOWN", diagnostic: diagnostic.slice(0, 4000) } };
}
function confirmInspector(pending: InspectorPending, project: Project): boolean {
  const scene = project.scenes.find(item => item.id === pending.change.scene_id);
  if (!scene) return false;
  return pending.change.type === "update_scene_objective"
    ? scene.objective === pending.change.objective && same(scene.duration, pending.prior.duration)
    : equalTime(scene.duration, pending.change.duration) && scene.objective === pending.prior.objective;
}
/** Only the live original callback supplies its attempt ID and returned Project. */
export function acknowledgeInspectorDraft(draft: InspectorDraft, originalId: string, receipt: Project): InspectorDraft {
  const pending = draft.pending;
  if (!pending || pending.id !== originalId) return draft;
  assertOwner(draft, receipt);
  if (receipt.revision !== pending.revision + 1 || !confirmInspector(pending, receipt))
    throw new Error("The original reply did not confirm this exact Inspector operation. Input and UNKNOWN were kept.");
  const clean = sceneRaw(assertTarget(draft, receipt)), raw = clone(draft.raw), dirty = { ...draft.dirty };
  if (draft.versions[pending.field] === pending.versions[pending.field]) dirty[pending.field] = false;
  for (const field of fields) if (!dirty[field]) raw[field] = clean[field];
  return { ...draft, baseRevision: receipt.revision, raw, clean, dirty, pending: null };
}
export const MAX_INSPECTOR_HISTORY = 2048;
export interface InspectorOutcomeTransport {
  readProject(projectId: string): Promise<Project>;
  readHistory(project: Project, afterRevision: number, limit: number): Promise<ProjectEvent[]>;
}
export interface InspectorOutcome {
  scope: string; pending: InspectorPending; project: Project; events: ProjectEvent[]; matchingValueRevisions: number[];
}
function sameObserved(left: Project, right: Project, draft: InspectorDraft): boolean {
  return ownerOf(draft) === inspectorOwner(left) && ownerOf(draft) === inspectorOwner(right)
    && left.revision === right.revision && same(left, right);
}
export function inspectorOutcomeMatches(draft: InspectorDraft, project: Project, outcome: InspectorOutcome | null): boolean {
  return Boolean(outcome && inspectorUnknown(draft) && outcome.scope === draft.scope
    && same(draft.pending, outcome.pending) && sameObserved(project, outcome.project, draft));
}
/** Read the selected saved source twice around a contiguous bounded journal.
 * Matching authored values are observations, never an original-request receipt.
 */
export async function inspectInspectorOutcome(draft: InspectorDraft, selected: Project, transport: InspectorOutcomeTransport): Promise<InspectorOutcome> {
  assertOwner(draft, selected);
  if (!inspectorUnknown(draft)) throw new Error("There is no unconfirmed inspector attempt to inspect.");
  const pending = clone(draft.pending!);
  const observed = clone(await transport.readProject(selected.id));
  if (!sameObserved(observed, selected, draft)) throw new Error("The saved project changed. Reload it before reviewing the original outcome.");
  if (!number(observed.revision) || observed.revision < pending.revision || observed.revision - pending.revision > MAX_INSPECTOR_HISTORY)
    throw new Error("The inspector history is outside its bounded inspection window.");
  const events: ProjectEvent[] = [];
  let cursor = pending.revision;
  do {
    const page = await transport.readHistory(observed, cursor, 256);
    if (!Array.isArray(page) || page.length > 256 || (!page.length && cursor < observed.revision))
      throw new Error("The saved inspector history is incomplete.");
    for (const event of page) {
      if (!event || !number(event.revision) || event.revision !== cursor + 1 || event.revision > observed.revision
        || !event.change || typeof event.change.type !== "string" || typeof event.created_at !== "string" || !event.created_at)
        throw new Error("The saved inspector history is not a contiguous revision sequence.");
      events.push(clone(event)); cursor = event.revision;
    }
  } while (cursor < observed.revision);
  if (!sameObserved(await transport.readProject(selected.id), observed, draft))
    throw new Error("The saved project changed during inspector. Read it and the history again.");
  return { scope: draft.scope, pending, project: observed, events,
    matchingValueRevisions: events.filter(event => same(event.change, pending.change)).map(event => event.revision) };
}
/** Inspect first, then retain a separate author intent. The original UNKNOWN
 * copy stays immutable; unchanged submitted values become clean observations.
 * Later edits, including edits back to an earlier spelling, remain authored.
 */
export function separateInspectedIntent(draft: InspectorDraft, project: Project, outcome: InspectorOutcome): InspectorDraft {
  if (!inspectorOutcomeMatches(draft, project, outcome))
    throw new Error("Read the current saved scene and its complete history before starting a separate intent.");
  const scene = assertTarget(draft, project), pending = draft.pending!;
  const clean = sceneRaw(scene), raw = clone(draft.raw), dirty = { ...draft.dirty };
  if (draft.versions[pending.field] === pending.versions[pending.field]) dirty[pending.field] = false;
  for (const field of fields) if (!dirty[field]) raw[field] = clean[field];
  return { ...draft, baseRevision: project.revision, raw, clean, dirty, sequence: draft.sequence + 1, pending: null };
}

export type InspectorStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem">;
export interface InspectorCustody { writerId: string; expectedRaw: string | null; expectedPointer: string | null }
export interface InspectorCopy { schema: "motionwright.scene-inspector-recovery/1"; scope: string; writerId: string; updatedAt: number; draft: InspectorDraft }
export const inspectorCopyKey = (scope: string, writerId: string) => INSPECTOR_RECOVERY_PREFIX + encodeURIComponent(scope) + ":" + writerId;
export const inspectorPointerKey = (scope: string) => INSPECTOR_POINTER_PREFIX + encodeURIComponent(scope);
function validRaw(raw: unknown): raw is InspectorRaw {
  return object(raw) && exactKeys(raw, fields) && typeof raw.objective === "string" && raw.objective.length <= 16000
    && typeof raw.duration === "string" && raw.duration.length <= 256;
}
function validTime(value: unknown): value is RationalTime {
  if (!object(value) || !exactKeys(value, ["num", "den"]) || typeof value.num !== "string" || typeof value.den !== "string") return false;
  try {
    parseBeatTime(value.num + "/" + value.den);
    const n = BigInt(value.num), d = BigInt(value.den), limit = (1n << 63n) - 1n;
    return n >= -limit - 1n && n <= limit && d > 0n && d <= limit;
  } catch { return false; }
}
function validVersions(value: unknown, limit: number): value is Record<InspectorField, number> {
  return object(value) && exactKeys(value, fields) && fields.every(field => number(value[field]) && Number(value[field]) <= limit);
}
function validChange(value: unknown, sceneId: string, raw: InspectorRaw): value is InspectorChange {
  if (!object(value) || value.scene_id !== sceneId) return false;
  if (value.type === "update_scene_objective") return exactKeys(value, ["type", "scene_id", "objective"])
    && typeof value.objective === "string" && value.objective === raw.objective.trim() && !!value.objective
    && bytes(value.objective) <= 4000;
  if (value.type !== "set_scene_duration" || !exactKeys(value, ["type", "scene_id", "duration"]) || !validTime(value.duration)) return false;
  try { return same(inspectorDuration(raw.duration), value.duration); } catch { return false; }
}
export function decodeInspectorCopy(raw: string, scope: string, writerId: string): InspectorCopy {
  if (bytes(raw) > MAX_INSPECTOR_COPY_BYTES) throw new Error("The Inspector recovery exceeds its 128 KiB copy budget.");
  const identity = inspectorIdentity(scope), value: unknown = JSON.parse(raw);
  if (!object(value) || !exactKeys(value, ["schema", "scope", "writerId", "updatedAt", "draft"])
    || value.schema !== "motionwright.scene-inspector-recovery/1" || value.scope !== scope || value.writerId !== writerId
    || !uuid(writerId) || !number(value.updatedAt) || !object(value.draft))
    throw new Error("The Inspector recovery envelope is invalid.");
  const draft = value.draft;
  if (!exactKeys(draft, ["scope", "baseRevision", "sequence", "versions", "dirty", "raw", "clean", "pending"])
    || draft.scope !== scope || !number(draft.baseRevision) || !number(draft.sequence)
    || !validVersions(draft.versions, draft.sequence) || !object(draft.dirty) || !exactKeys(draft.dirty, fields)
    || !fields.every(field => typeof (draft.dirty as Record<string, unknown>)[field] === "boolean")
    || !validRaw(draft.raw) || !validRaw(draft.clean)) throw new Error("The Inspector recovery draft is invalid.");
  if (draft.pending !== null) {
    const pending = draft.pending;
    if (!object(pending) || !exactKeys(pending, ["id", "revision", "sequence", "versions", "field", "change", "raw", "prior", "outcome",
        ...(pending.diagnostic === undefined ? [] : ["diagnostic"])])
      || !uuid(pending.id) || !number(pending.revision) || pending.revision !== draft.baseRevision
      || !number(pending.sequence) || pending.sequence > draft.sequence || !validVersions(pending.versions, pending.sequence)
      || !["objective", "duration"].includes(String(pending.field)) || !validRaw(pending.raw)
      || !validChange(pending.change, identity[3], pending.raw)
      || (pending.field === "objective") !== (pending.change.type === "update_scene_objective")
      || !object(pending.prior) || !exactKeys(pending.prior, ["objective", "duration"])
      || typeof pending.prior.objective !== "string" || !validTime(pending.prior.duration)
      || !["UNKNOWN", "NOT_SENT"].includes(String(pending.outcome))
      || (pending.diagnostic !== undefined && (typeof pending.diagnostic !== "string" || pending.diagnostic.length > 4000)))
      throw new Error("The Inspector recovery attempt is invalid.");
  }
  return clone(value as unknown as InspectorCopy);
}
export function encodeInspectorCopy(copy: InspectorCopy): string {
  const raw = JSON.stringify(copy); decodeInspectorCopy(raw, copy.scope, copy.writerId); return raw;
}
export function writeInspectorCopy(storage: InspectorStorage, tab: Pick<Storage, "getItem" | "setItem">,
  draft: InspectorDraft, custody: InspectorCustody): void {
  const key = inspectorCopyKey(draft.scope, custody.writerId), pointerKey = inspectorPointerKey(draft.scope);
  const pointer = tab.getItem(pointerKey);
  if (pointer !== custody.expectedPointer && pointer !== custody.writerId) throw new Error("Another writer changed this tab's inspector pointer. Earlier copies were kept.");
  if (storage.getItem(key) !== custody.expectedRaw) throw new Error("Another window changed the inspector copy. Its bytes were kept.");
  const raw = encodeInspectorCopy({ schema: "motionwright.scene-inspector-recovery/1", scope: draft.scope,
    writerId: custody.writerId, updatedAt: Date.now(), draft });
  if (custody.expectedRaw !== null) {
    const earlier = decodeInspectorCopy(custody.expectedRaw, draft.scope, custody.writerId).draft;
    if (!inspectorHasWork(earlier) && !same(earlier, draft)) throw new Error("A clean inspector checkpoint is immutable; use a new writer.");
    if (same(earlier, draft) && pointer === custody.writerId) {
      if (storage.getItem(key) !== custody.expectedRaw || tab.getItem(pointerKey) !== custody.writerId)
        throw new Error("The inspector recovery changed while its existing bytes were being verified.");
      custody.expectedPointer = custody.writerId; return;
    }
  }
  if (storage.length > MAX_STORAGE_KEYS) throw new Error("The recovery key budget is full. Current input remains in the editor.");
  let total = bytes(key) + bytes(raw), count = 1;
  for (let index = 0; index < storage.length; index++) {
    const existing = storage.key(index);
    if (!existing?.startsWith(INSPECTOR_RECOVERY_PREFIX) || existing === key) continue;
    total += bytes(existing) + bytes(storage.getItem(existing) ?? ""); count++;
  }
  if (count > MAX_INSPECTOR_COPIES || total > MAX_INSPECTOR_RECOVERY_BYTES)
    throw new Error("Inspector recovery is full (256 copies / 4 MiB). Export current input before removing only copies you no longer need.");
  if (storage.getItem(key) !== custody.expectedRaw || tab.getItem(pointerKey) !== pointer)
    throw new Error("Inspector recovery changed before writing. Earlier copies were kept.");
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The inspector recovery write was not confirmed.");
  custody.expectedRaw = raw;
  tab.setItem(pointerKey, custody.writerId);
  if (tab.getItem(pointerKey) !== custody.writerId || storage.getItem(key) !== raw)
    throw new Error("The inspector recovery pointer or bytes were not confirmed.");
  custody.expectedPointer = custody.writerId;
}


/** Verify a separate UNKNOWN checkpoint before publishing the new intent's
 * pointer. A partial failure leaves an UNKNOWN copy recoverable, never a
 * silently released request. The original writer's bytes remain immutable.
 */
export function retainSeparateInspectorIntent(storage: InspectorStorage, tab: Pick<Storage, "getItem" | "setItem">,
  before: InspectorDraft, next: InspectorDraft, original: InspectorCustody, writerId: string): InspectorCustody {
  if (!inspectorUnknown(before) || next.pending || before.scope !== next.scope || !uuid(writerId)
    || writerId === original.writerId || original.expectedRaw === null)
    throw new Error("The separate inspector recovery transition is invalid.");
  const key = inspectorCopyKey(before.scope, original.writerId), originalRaw = storage.getItem(key);
  if (originalRaw === null || originalRaw !== original.expectedRaw
    || !same(decodeInspectorCopy(originalRaw, before.scope, original.writerId).draft, before))
    throw new Error("The original UNKNOWN inspector input was not retained exactly.");
  const custody: InspectorCustody = { writerId, expectedRaw: null, expectedPointer: original.expectedPointer };
  writeInspectorCopy(storage, tab, before, custody);
  if (storage.getItem(key) !== originalRaw) throw new Error("The original inspector copy changed while inspection was staged.");
  writeInspectorCopy(storage, tab, next, custody);
  if (storage.getItem(key) !== originalRaw) throw new Error("The original UNKNOWN copy changed while the separate intent was saved.");
  return custody;
}

interface Session {
  draft: InspectorDraft; custody: InspectorCustody; inFlight: boolean;
  storageError: string | null; readError: string | null; error: string | null; notice: string | null;
  outcome: InspectorOutcome | null; reviewing: boolean;
}
// Author buffers only. Canonical Project mutation remains owned by App's gate.
// These sessions preserve input during navigation and never replay an operation.
const sessions = new Map<string, Session>(), listeners = new Map<string, Set<() => void>>();
let unloadProtected = false;
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const notify = (session: Session) => listeners.get(ownerOf(session.draft))?.forEach(listener => listener());
const busy = (project: Project) => [...sessions.values()].some(session => session.inFlight && ownerOf(session.draft) === inspectorOwner(project));
function backup(session: Session): boolean {
  try {
    if (session.readError) throw new Error(session.readError);
    if (session.custody.expectedRaw !== null) {
      const earlier = decodeInspectorCopy(session.custody.expectedRaw, session.draft.scope, session.custody.writerId).draft;
      if (!inspectorHasWork(earlier) && !same(earlier, session.draft))
        session.custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: session.custody.expectedPointer };
    }
    writeInspectorCopy(window.localStorage, window.sessionStorage, session.draft, session.custody);
    session.storageError = null; return true;
  } catch (reason) {
    session.storageError = message(reason) + " Saving is blocked until this input has a verified recovery copy.";
    return false;
  }
}
function sessionFor(project: Project, target: string): Session {
  if (!unloadProtected) {
    unloadProtected = true;
    window.addEventListener("beforeunload", event => {
      if ([...sessions.values()].some(session => session.storageError && inspectorHasWork(session.draft))) {
        event.preventDefault(); event.returnValue = "";
      }
    });
  }
  const scope = inspectorScope(project, target), cached = sessions.get(scope);
  if (cached) {
    cached.draft = reconcileInspectorDraft(cached.draft, project); return cached;
  }
  let pointer: string | null = null, recovered: InspectorDraft | null = null, readError: string | null = null;
  try {
    pointer = window.sessionStorage.getItem(inspectorPointerKey(scope));
    if (pointer !== null) {
      const raw = window.localStorage.getItem(inspectorCopyKey(scope, pointer));
      if (raw === null) throw new Error("This tab's inspector recovery copy is missing.");
      recovered = decodeInspectorCopy(raw, scope, pointer).draft;
    }
  } catch (reason) { readError = message(reason) + " Earlier recovery bytes were kept."; }
  const session: Session = { draft: reconcileInspectorDraft(recovered ?? createInspectorDraft(project, target), project),
    custody: { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: pointer },
    inFlight: false, readError, storageError: readError, error: null, notice: null, outcome: null, reviewing: false };
  sessions.set(scope, session);
  if (recovered && inspectorHasWork(recovered)) backup(session);
  return session;
}
export function useInspectorDraft(project: Project, target: string, commit: (change: Change) => Promise<Project>) {
  const session = sessionFor(project, target), owner = inspectorOwner(project);
  const [, refresh] = useState(0);
  const projectRef = useRef(project), visibleScope = useRef(session.draft.scope), mounted = useRef(true);
  projectRef.current = project; visibleScope.current = session.draft.scope;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const listener = () => refresh(value => value + 1), set = listeners.get(owner) ?? new Set<() => void>();
    set.add(listener); listeners.set(owner, set);
    return () => { set.delete(listener); if (!set.size) listeners.delete(owner); };
  }, [owner]);
  const edit = <K extends keyof InspectorRaw>(field: K, value: InspectorRaw[K]) => {
    session.draft = editInspectorDraft(session.draft, field, value); session.error = null; session.notice = null;
    backup(session); notify(session);
  };
  const submit = async (change: InspectorChange): Promise<boolean> => {
    if (busy(project) || inspectorUnknown(session.draft)) return false;
    const id = crypto.randomUUID();
    try {
      session.draft = prepareInspectorSubmission(session.draft, project, change, id);
    } catch (reason) { session.error = message(reason); notify(session); return false; }
    session.inFlight = true; session.error = null; session.notice = null;
    if (!backup(session)) {
      session.draft = retainInspectorFailure(session.draft, id, "No request was sent because recovery was not confirmed.", true);
      session.inFlight = false; backup(session); notify(session); return false;
    }
    notify(session);
    try {
      const receipt = await commit(change);
      session.draft = acknowledgeInspectorDraft(session.draft, id, receipt);
      session.notice = "The original reply confirmed this scene change. Any later author input was retained.";
      backup(session);
      return true;
    } catch (reason) {
      session.draft = retainInspectorFailure(session.draft, id, message(reason));
      session.error = message(reason); backup(session); return false;
    } finally { session.inFlight = false; notify(session); }
  };
  const advance = () => {
    try {
      if (session.inFlight || inspectorUnknown(session.draft)) throw new Error("Keep the original attempt until its outcome is known.");
      if (!backup(session)) { notify(session); return; }
      const before = session.custody;
      const next = advanceInspectorDraft(session.draft, project);
      const candidate: InspectorCustody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: before.expectedPointer };
      writeInspectorCopy(window.localStorage, window.sessionStorage, next, candidate);
      session.custody = candidate; session.draft = next; session.error = null; session.storageError = null;
      session.notice = "The author explicitly retained this draft against the current revision; the earlier copy was kept.";
    } catch (reason) { session.error = message(reason); }
    notify(session);
  };

  const readOutcome = async () => {
    if (session.inFlight || session.reviewing || !inspectorUnknown(session.draft)) return;
    session.reviewing = true; session.outcome = null; session.error = null; notify(session);
    try {
      const observed = await inspectInspectorOutcome(session.draft, projectRef.current, {
        readProject: async id => {
          const boot = await bootstrap(id);
          if (!boot.project || boot.project_selection.status !== "selected") throw new Error("The saved inspector project is unavailable.");
          return boot.project;
        }, readHistory: projectHistory,
      });
      if (!mounted.current || visibleScope.current !== session.draft.scope) return;
      if (!inspectorOutcomeMatches(session.draft, projectRef.current, observed))
        throw new Error("The inspector owner, target or saved source changed. Inspect it again.");
      session.outcome = observed;
    } catch (reason) { session.error = message(reason); }
    finally { session.reviewing = false; notify(session); }
  };
  const separateIntent = () => {
    if (session.inFlight || session.reviewing || !session.outcome) return;
    try {
      const next = separateInspectedIntent(session.draft, projectRef.current, session.outcome);
      if (!backup(session)) { notify(session); return; }
      const retained = retainSeparateInspectorIntent(window.localStorage, window.sessionStorage,
        session.draft, next, session.custody, crypto.randomUUID());
      session.custody = retained; session.draft = next; session.outcome = null;
      session.storageError = null; session.error = null;
      session.notice = "The original UNKNOWN copy is unchanged. This is a separate author intent; no original request was acknowledged or replayed.";
    } catch (reason) { session.storageError = message(reason); session.error = "The original attempt and current author input were kept."; }
    notify(session);
  };

  const retryBackup = () => { backup(session); notify(session); };
  const download = () => {
    // Export is author-input custody only; it grants no Project or reply authority.
    const raw = JSON.stringify({ schema: "motionwright.scene-inspector-recovery/1", scope: session.draft.scope,
      writerId: session.custody.writerId, updatedAt: Date.now(), draft: session.draft }, null, 2);
    const url = URL.createObjectURL(new Blob([raw], { type: "application/json" })), anchor = document.createElement("a");
    anchor.href = url; anchor.download = "motionwright-inspector-draft-" + session.custody.writerId + ".json";
    anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  return { draft: session.draft, edit, submit, advance, retryBackup, download, readOutcome, separateIntent,
    outcome: inspectorOutcomeMatches(session.draft, project, session.outcome) ? session.outcome : null, reviewing: session.reviewing,
    saving: session.inFlight, busy: busy(project), unknown: inspectorUnknown(session.draft),
    stale: inspectorHasInput(session.draft) && session.draft.baseRevision !== project.revision,
    storageError: session.storageError, error: session.error, notice: session.notice,
    blocked: busy(project) || inspectorUnknown(session.draft) || session.storageError !== null
      || (inspectorHasInput(session.draft) && session.draft.baseRevision !== project.revision) };
}
