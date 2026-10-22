import { sameValue } from "./creativeProduction";
import type { Change, Project, ProjectEvent } from "./types";

export const BRANCH_RECOVERY_PREFIX = "motionwright.branch-recovery.v1:";
export const BRANCH_POINTER_PREFIX = "motionwright.branch-writer.v1:";
export const MAX_BRANCH_COPIES = 512;
export const MAX_BRANCH_COPY_BYTES = 128 * 1024;
export const MAX_BRANCH_RECOVERY_BYTES = 2 * 1024 * 1024;
const MAX_KEYS = 4096, MAX_HISTORY = 2048;
const clone = <T,>(value: T): T => structuredClone(value);
const uuid = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const exact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const bytes = (value: string) => new TextEncoder().encode(value).length;
export interface BranchOrigin { key: string; raw: string }
export interface BranchPending {
  id: string; revision: number; sequence: number; name: string; priorIds: string[];
  outcome: "UNKNOWN" | "NOT_SENT"; diagnostic: string | null;
}
export interface BranchDraft {
  scope: string; baseRevision: number; sequence: number; name: string;
  pending: BranchPending | null; origins: BranchOrigin[];
}
export interface BranchCopy {
  schema: "motionwright.branch-recovery/1"; scope: string; writerId: string; updatedAt: number; draft: BranchDraft;
}
export interface BranchCustody { writerId: string; expectedRaw: string | null; expectedPointer: string | null }
export type BranchStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem">;
export const branchScope = (project: Project) => JSON.stringify([project.id, project.generation, project.active_branch]);
export const branchCopyKey = (scope: string, writerId: string) => BRANCH_RECOVERY_PREFIX + encodeURIComponent(scope) + ":" + writerId;
export const branchPointerKey = (scope: string) => BRANCH_POINTER_PREFIX + encodeURIComponent(scope);
function assertScope(scope: string) {
  const value: unknown = JSON.parse(scope);
  if (!Array.isArray(value) || value.length !== 3 || !value.every(uuid)) throw new Error("The branch draft owner is invalid.");
}
function assertOwner(draft: BranchDraft, project: Project) {
  if (draft.scope !== branchScope(project)) throw new Error("This name belongs to another project, generation or branch.");
}
export const branchUnknown = (draft: BranchDraft) => draft.pending?.outcome === "UNKNOWN";
export const branchHasWork = (draft: BranchDraft) => draft.name !== "" || draft.pending !== null;
export function createBranchDraft(project: Project): BranchDraft {
  const scope = branchScope(project); assertScope(scope);
  return { scope, baseRevision: project.revision, sequence: 0, name: "", pending: null, origins: [] };
}
export function editBranchDraft(draft: BranchDraft, name: string): BranchDraft {
  if (!integer(draft.sequence) || draft.sequence === Number.MAX_SAFE_INTEGER)
    throw new Error("The branch edit sequence cannot advance safely. Keep a recovery export before continuing.");
  return { ...draft, name, sequence: draft.sequence + 1 };
}
export function reconcileBranchDraft(draft: BranchDraft, project: Project): BranchDraft {
  assertOwner(draft, project);
  return branchHasWork(draft) || draft.baseRevision === project.revision ? draft : { ...draft, baseRevision: project.revision };
}
export interface BranchPointerObservation { observed: boolean; expectedPointer: string | null }
export function readBranchRecoveryPointer(storage: Pick<BranchStorage, "getItem">, scope: string,
  prior: BranchPointerObservation): BranchPointerObservation {
  assertScope(scope);
  const pointer = storage.getItem(branchPointerKey(scope));
  if (prior.observed && pointer !== prior.expectedPointer)
    throw new Error("The earlier branch recovery pointer changed.");
  return { observed: true, expectedPointer: pointer };
}
/** A failed read has no authority to discard saved input or a later raw edit. */
export function recoverBranchAfterReadFailure(recovered: BranchDraft, current: BranchDraft,
  editedWhileBlocked: boolean, project: Project): BranchDraft {
  assertOwner(recovered, project); assertOwner(current, project);
  const attempt = (pending: BranchPending) => ({
    id: pending.id, revision: pending.revision, sequence: pending.sequence,
    name: pending.name, priorIds: pending.priorIds,
  });
  if (current.pending && (!recovered.pending || !sameValue(attempt(current.pending), attempt(recovered.pending))))
    throw new Error("The current and recovered branch attempts differ.");
  if (![current.sequence, recovered.sequence].every(integer))
    throw new Error("The recovered branch edit sequence is invalid.");
  const maximum = Math.max(current.sequence, recovered.sequence);
  if (editedWhileBlocked && maximum === Number.MAX_SAFE_INTEGER)
    throw new Error("The branch edit sequence cannot advance safely.");
  const origins = new Map<string, BranchOrigin>();
  for (const origin of [...recovered.origins, ...current.origins]) {
    const previous = origins.get(origin.key);
    if (previous && previous.raw !== origin.raw) throw new Error("A retained branch origin has conflicting bytes.");
    origins.set(origin.key, clone(origin));
  }
  if (origins.size > 16) throw new Error("The branch recovery origin limit was reached.");
  return reconcileBranchDraft({
    ...recovered,
    name: editedWhileBlocked ? current.name : recovered.name,
    sequence: editedWhileBlocked ? maximum + 1 : recovered.sequence,
    // NOT_SENT is known only by the original live caller. A retained label is
    // not permission to repeat a request after reloading.
    pending: recovered.pending ? { ...clone(recovered.pending), outcome: "UNKNOWN" } : null,
    origins: [...origins.values()],
  }, project);
}
export function validateBranchName(name: string, project: Project): string {
  const value = name.trim();
  if (!value) throw new Error("Enter a name for the new branch.");
  if (bytes(value) > 120 || /[\u0000-\u001f\u007f-\u009f]/.test(value)) throw new Error("Branch names use at most 120 UTF-8 bytes and no control characters. Your original input is kept.");
  const asciiLower = (text: string) => text.replace(/[A-Z]/g, char => char.toLowerCase());
  if (project.branches.some(branch => asciiLower(branch.name) === asciiLower(value))) throw new Error("That branch name is already in use. Choose another name; your input is kept.");
  if (project.branches.length >= 256) throw new Error("This project has reached its branch limit. Your name remains editable.");
  return value;
}
export function prepareBranchSubmission(draft: BranchDraft, project: Project, id: string): { draft: BranchDraft; change: Change } {
  assertOwner(draft, project);
  if (branchUnknown(draft)) throw new Error("Check the unconfirmed branch creation before starting another. No request was repeated.");
  if (draft.baseRevision !== project.revision) throw new Error("Review the current source branch before creating from this name.");
  if (!uuid(id)) throw new Error("A new local attempt identity is required.");
  const name = validateBranchName(draft.name, project);
  return { draft: { ...draft, pending: { id, revision: project.revision, sequence: draft.sequence, name,
    priorIds: project.branches.map(branch => branch.id), outcome: "UNKNOWN", diagnostic: null } }, change: { type: "create_branch", name } };
}
export function retainBranchFailure(draft: BranchDraft, id: string, diagnostic: string, notSent = false): BranchDraft {
  return draft.pending?.id !== id ? draft : { ...draft, pending: { ...draft.pending,
    outcome: notSent ? "NOT_SENT" : "UNKNOWN", diagnostic: diagnostic.slice(0, 4000) } };
}
/** Only the original live callback supplies this Project and local identity. */
export function acknowledgeBranchDraft(draft: BranchDraft, originalId: string, project: Project): BranchDraft {
  const pending = draft.pending;
  if (!pending || pending.id !== originalId) return draft;
  assertOwner(draft, project);
  const added = project.branches.filter(branch => !pending.priorIds.includes(branch.id));
  const branch = added[0];
  if (project.revision !== pending.revision + 1 || added.length !== 1 || !uuid(branch?.id)
    || branch.name !== pending.name || branch.parent_branch !== project.active_branch
    || branch.base_revision !== pending.revision || branch.head_revision !== pending.revision || branch.protected
    || !project.branch_workspaces.some(workspace => workspace.branch_id === branch.id && workspace.base_revision === pending.revision))
    throw new Error("The original reply did not confirm this branch. The name and unconfirmed operation are kept.");
  return { ...draft, baseRevision: project.revision, name: draft.sequence === pending.sequence ? "" : draft.name, pending: null };
}
export function advanceBranchDraft(draft: BranchDraft, project: Project): BranchDraft {
  assertOwner(draft, project);
  if (branchUnknown(draft)) throw new Error("The original branch outcome must be reviewed separately.");
  return { ...draft, baseRevision: project.revision, sequence: draft.sequence + 1, pending: null };
}

export function decodeBranchCopy(raw: string, scope: string, writerId: string): BranchCopy {
  if (bytes(raw) > MAX_BRANCH_COPY_BYTES) throw new Error("The branch recovery copy exceeds its size limit.");
  assertScope(scope);
  const copy: unknown = JSON.parse(raw);
  if (!object(copy) || !exact(copy, ["schema", "scope", "writerId", "updatedAt", "draft"])
    || copy.schema !== "motionwright.branch-recovery/1" || copy.scope !== scope || copy.writerId !== writerId
    || !uuid(writerId) || !integer(copy.updatedAt) || !object(copy.draft)) throw new Error("The branch recovery envelope is invalid.");
  const draft = copy.draft;
  if (!exact(draft, ["scope", "baseRevision", "sequence", "name", "pending", "origins"])
    || draft.scope !== scope || !integer(draft.baseRevision) || !integer(draft.sequence) || typeof draft.name !== "string"
    || !Array.isArray(draft.origins) || draft.origins.length > 16) throw new Error("The branch recovery input is invalid.");
  for (const origin of draft.origins) {
    if (!object(origin) || !exact(origin, ["key", "raw"]) || typeof origin.key !== "string" || typeof origin.raw !== "string"
      || !origin.key.startsWith(BRANCH_RECOVERY_PREFIX + encodeURIComponent(scope) + ":") || bytes(origin.raw) > MAX_BRANCH_COPY_BYTES)
      throw new Error("The branch recovery origin is invalid.");
    const writer = origin.key.slice(origin.key.lastIndexOf(":") + 1), value: unknown = JSON.parse(origin.raw);
    if (!uuid(writer) || !object(value) || value.schema !== "motionwright.branch-recovery/1" || value.scope !== scope || value.writerId !== writer
      || !object(value.draft) || !object(value.draft.pending) || value.draft.pending.outcome !== "UNKNOWN")
      throw new Error("The retained branch origin does not identify its original unconfirmed copy.");
  }
  if (draft.pending !== null) {
    const pending = draft.pending;
    if (!object(pending) || !exact(pending, ["id", "revision", "sequence", "name", "priorIds", "outcome", "diagnostic"])
      || !uuid(pending.id) || !integer(pending.revision) || pending.revision !== draft.baseRevision
      || !integer(pending.sequence) || pending.sequence > draft.sequence || typeof pending.name !== "string" || !pending.name.trim()
      || !Array.isArray(pending.priorIds) || pending.priorIds.length > 256 || !pending.priorIds.every(uuid)
      || !["UNKNOWN", "NOT_SENT"].includes(String(pending.outcome))
      || (pending.diagnostic !== null && (typeof pending.diagnostic !== "string" || pending.diagnostic.length > 4000)))
      throw new Error("The original branch attempt in this copy is invalid.");
  }
  return copy as unknown as BranchCopy;
}
export function assertBranchOrigins(storage: Pick<Storage, "getItem">, draft: BranchDraft) {
  for (const origin of draft.origins) if (storage.getItem(origin.key) !== origin.raw)
    throw new Error("An original unconfirmed branch copy changed. This later draft cannot authorize another creation; the recovery inputs remain retained.");
}
export function retainBranchOrigin(draft: BranchDraft, origin: BranchOrigin): BranchDraft {
  if (!branchUnknown(draft)) return draft;
  const writer = origin.key.slice(origin.key.lastIndexOf(":") + 1);
  if (origin.key !== branchCopyKey(draft.scope, writer) || !sameValue(decodeBranchCopy(origin.raw, draft.scope, writer).draft, draft))
    throw new Error("The recovered branch attempt does not match its exact original copy.");
  // Reopening one attempt retains its first exact origin. Nesting each reload's
  // envelope into another envelope would exhaust the bounded recovery budget
  // without any new author action or operation.
  const pending = { ...draft.pending!, diagnostic: null };
  if (draft.origins.some(value => {
    const retained = (JSON.parse(value.raw) as BranchCopy).draft.pending;
    return retained !== null && sameValue({ ...retained, diagnostic: null }, pending);
  })) return draft;
  return { ...draft, origins: [...draft.origins.filter(value => value.key !== origin.key), clone(origin)] };
}
export function readBranchCopies(storage: BranchStorage, scope: string) {
  if (storage.length > MAX_KEYS) throw new Error("The local recovery catalogue is too large to inspect safely.");
  const found: { key: string; raw: string; writerId: string; value: BranchCopy | null; error: string | null }[] = [];
  const prefix = BRANCH_RECOVERY_PREFIX + encodeURIComponent(scope) + ":";
  for (let index = 0; index < storage.length; index++) {
    const key = storage.key(index); if (!key?.startsWith(prefix)) continue;
    const raw = storage.getItem(key); if (raw === null) continue;
    const writerId = key.slice(prefix.length);
    try { found.push({ key, raw, writerId, value: decodeBranchCopy(raw, scope, writerId), error: null }); }
    catch (reason) { found.push({ key, raw, writerId, value: null, error: String(reason) }); }
  }
  return found;
}
export function writeBranchCopy(storage: BranchStorage, tab: Pick<Storage, "getItem" | "setItem">, draft: BranchDraft, custody: BranchCustody): string {
  const key = branchCopyKey(draft.scope, custody.writerId), pointer = branchPointerKey(draft.scope);
  assertBranchOrigins(storage, draft);
  if (tab.getItem(pointer) !== custody.expectedPointer || storage.getItem(key) !== custody.expectedRaw)
    throw new Error("This branch copy or writer pointer changed elsewhere. Earlier bytes were not replaced.");
  const value: BranchCopy = { schema: "motionwright.branch-recovery/1", scope: draft.scope, writerId: custody.writerId, updatedAt: Date.now(), draft };
  const raw = JSON.stringify(value); decodeBranchCopy(raw, draft.scope, custody.writerId);
  if (storage.length > MAX_KEYS) throw new Error("The recovery catalogue is too large to inspect safely.");
  let total = bytes(raw), count = 1;
  for (let index = 0; index < storage.length; index++) {
    const other = storage.key(index); if (!other?.startsWith(BRANCH_RECOVERY_PREFIX) || other === key) continue;
    count++; total += bytes(storage.getItem(other) ?? "");
  }
  if (count > MAX_BRANCH_COPIES || total > MAX_BRANCH_RECOVERY_BYTES) throw new Error("Branch recovery storage is full. Earlier copies are kept; export your input before closing Studio.");
  if (custody.expectedRaw !== null && custody.expectedPointer === custody.writerId
    && sameValue(decodeBranchCopy(custody.expectedRaw, draft.scope, custody.writerId).draft, draft)) {
    assertBranchOrigins(storage, draft); return custody.expectedRaw;
  }
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The branch recovery bytes were not confirmed. Creation is blocked.");
  custody.expectedRaw = raw;
  if (tab.getItem(pointer) !== custody.expectedPointer)
    throw new Error("The branch writer pointer changed during recovery publication. The other writer was kept.");
  tab.setItem(pointer, custody.writerId);
  if (tab.getItem(pointer) !== custody.writerId) throw new Error("The branch recovery pointer was not confirmed. Creation is blocked.");
  custody.expectedPointer = custody.writerId;
  if (storage.getItem(key) !== raw) throw new Error("The branch recovery bytes changed while its pointer was published.");
  assertBranchOrigins(storage, draft);
  return raw;
}

export interface BranchOutcome { scope: string; pending: BranchPending; project: Project; events: ProjectEvent[]; matchingRevisions: number[] }
export const branchOutcomeMatches = (draft: BranchDraft, project: Project, outcome: BranchOutcome | null) => Boolean(outcome
  && branchUnknown(draft) && outcome.scope === draft.scope && sameValue(outcome.pending, draft.pending)
  && project.revision === outcome.project.revision && branchScope(project) === draft.scope && sameValue(project, outcome.project));
export async function inspectBranchOutcome(draft: BranchDraft, selected: Project, transport: {
  readProject(id: string): Promise<Project>; readHistory(project: Project, afterRevision: number, limit: number): Promise<ProjectEvent[]>;
}): Promise<BranchOutcome> {
  assertOwner(draft, selected);
  if (!branchUnknown(draft)) throw new Error("There is no unconfirmed branch creation to inspect.");
  const pending = clone(draft.pending!), project = clone(await transport.readProject(selected.id));
  if (!sameValue(project, selected)) throw new Error("The saved project changed. Reload it before reading the branch history.");
  if (project.revision < pending.revision || project.revision - pending.revision > MAX_HISTORY) throw new Error("The branch history exceeds its bounded inspection window.");
  const events: ProjectEvent[] = []; let cursor = pending.revision;
  do {
    const page = await transport.readHistory(project, cursor, 256);
    if (!Array.isArray(page) || page.length > 256 || (!page.length && cursor < project.revision)) throw new Error("The saved branch history is incomplete.");
    for (const event of page) {
      if (!event || event.revision !== cursor + 1 || event.revision > project.revision || !event.change
        || typeof event.change.type !== "string" || typeof event.created_at !== "string" || !event.created_at)
        throw new Error("The saved branch history is not contiguous.");
      events.push(clone(event)); cursor = event.revision;
    }
  } while (cursor < project.revision);
  if (!sameValue(await transport.readProject(selected.id), project)) throw new Error("The saved branch changed during inspection. Read it again.");
  return { scope: draft.scope, pending, project, events,
    matchingRevisions: events.filter(event => event.change.type === "create_branch" && event.change.name === pending.name).map(event => event.revision) };
}
export function separateBranchIntent(draft: BranchDraft, project: Project, outcome: BranchOutcome, origin: BranchOrigin): BranchDraft {
  if (!branchOutcomeMatches(draft, project, outcome)) throw new Error("Read the exact saved branch and its history before starting a separate intent.");
  const writer = origin.key.slice(origin.key.lastIndexOf(":") + 1);
  if (origin.key !== branchCopyKey(draft.scope, writer) || !sameValue(decodeBranchCopy(origin.raw, draft.scope, writer).draft, draft))
    throw new Error("The original branch input must remain in its exact separate recovery copy.");
  return { ...draft, baseRevision: project.revision, sequence: draft.sequence + 1,
    name: draft.sequence === draft.pending!.sequence ? "" : draft.name, pending: null,
    origins: [...draft.origins.filter(value => value.key !== origin.key), clone(origin)] };
}
