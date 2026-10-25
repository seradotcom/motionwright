import type { Change, Project, ProjectEvent } from "./types";
import type { SemanticSfxCue } from "./semanticSfx";
import { SFX_CUE_SCHEMA, validateSemanticSfx } from "./semanticSfx";
import { sameValue } from "./creativeProduction";
import { sameProjectRevision } from "./projectMutation";

export type SfxCue = SemanticSfxCue & { tail_policy?: "within_scene" | "continue_across_cuts" };
export type SfxChange = Extract<Change, { type: "upsert_semantic_sfx" | "remove_semantic_sfx" }>;
export interface SfxRaw {
  sceneId: string; event: string; anchorKind: string; beatId: string;
  sourceAssetId: string; sourceSha256: string; offsetFrames: string; sourceFrames: string;
  gainMilliDb: string; attackFrames: string; releaseFrames: string; tailPolicy: string; attestation: string;
}
export interface SfxPending {
  id: string; revision: number; sequence: number; change: SfxChange; raw: SfxRaw;
  priorCues: SfxCue[]; outcome: "UNKNOWN" | "NOT_SENT"; diagnostic: string | null;
}
export interface SfxOrigin { key: string; raw: string }
export interface SfxDraft {
  scope: string; target: string; baseRevision: number; base: SfxCue | null;
  raw: SfxRaw; sequence: number; intent: boolean; pending: SfxPending | null; origins: SfxOrigin[];
}
export const SFX_COPY_PREFIX = "motionwright.semantic-sfx-recovery.v1:";
export const SFX_POINTER_PREFIX = "motionwright.semantic-sfx-writer.v1:";
export const MAX_SFX_COPY_BYTES = 256 * 1024;
export const MAX_SFX_COPIES = 256;
export const MAX_SFX_RECOVERY_BYTES = 4 * 1024 * 1024;
const MAX_KEYS = 4096, MAX_ORIGINS = 16, MAX_HISTORY = 2048;
const clone = <T,>(value: T): T => structuredClone(value);
const equal = sameValue;
const bytes = (value: string) => new TextEncoder().encode(value).length;
const uuid = (value: unknown): value is string => typeof value === "string"
  && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const exact = (value: Record<string, unknown>, required: readonly string[], optional: readonly string[] = []) =>
  required.every(key => Object.hasOwn(value, key)) && Object.keys(value).every(key => required.includes(key) || optional.includes(key));
const rawKeys = ["sceneId", "event", "anchorKind", "beatId", "sourceAssetId", "sourceSha256", "offsetFrames",
  "sourceFrames", "gainMilliDb", "attackFrames", "releaseFrames", "tailPolicy", "attestation"] as const;
const cueKeys = ["schema", "id", "scene_id", "event", "anchor", "offset_frames", "source_asset_id",
  "source_sha256", "source_frames", "gain_milli_db", "attack_frames", "release_frames", "original_source_attestation"];
const events = ["scene_reveal", "beat_emphasis", "ui_focus", "confirmation", "impact", "transition"];
const policies = ["within_scene", "continue_across_cuts"];
export function sfxIdentity(scope: string): [string, string, string, string] {
  const value: unknown = JSON.parse(scope);
  if (!Array.isArray(value) || value.length !== 4 || !value.slice(0, 3).every(uuid)
    || (value[3] !== "new" && !uuid(value[3]))) throw new Error("The sound draft owner or target is invalid.");
  return value as [string, string, string, string];
}
export const sfxScope = (project: Project, target: string) =>
  JSON.stringify([project.id, project.generation, project.active_branch, target]);
export const sfxCopyKey = (scope: string, writer: string) => SFX_COPY_PREFIX + scope + ":" + writer;
export const sfxPointerKey = (scope: string) => SFX_POINTER_PREFIX + scope;
function owner(draft: SfxDraft, project: Project) {
  if (sfxIdentity(draft.scope)[3] !== draft.target || draft.scope !== sfxScope(project, draft.target))
    throw new Error("This sound draft belongs to another project, generation or branch.");
}
export function canonicalSfxCue(cue: SfxCue): SfxCue {
  const value = clone(cue); if (value.tail_policy === "within_scene") delete value.tail_policy; return value;
}
const sameCue = (a: SfxCue | null, b: SfxCue | null) => a === null || b === null ? a === b : equal(canonicalSfxCue(a), canonicalSfxCue(b));
const saved = (project: Project, target: string): SfxCue | null =>
  target === "new" ? null : project.audio.sfx_cues?.find(cue => cue.id === target) ?? null;
export function sfxRaw(cue: SfxCue | null, project: Project): SfxRaw {
  return { sceneId: cue?.scene_id ?? project.scenes[0]?.id ?? "", event: cue?.event ?? "scene_reveal",
    anchorKind: cue?.anchor.kind ?? "scene_start", beatId: cue?.anchor.kind === "beat_start" ? cue.anchor.beat_id : "",
    sourceAssetId: cue?.source_asset_id ?? "", sourceSha256: cue?.source_sha256 ?? "",
    offsetFrames: String(cue?.offset_frames ?? 0), sourceFrames: String(cue?.source_frames ?? 30),
    gainMilliDb: String(cue?.gain_milli_db ?? -12000), attackFrames: String(cue?.attack_frames ?? 3),
    releaseFrames: String(cue?.release_frames ?? 12), tailPolicy: cue?.tail_policy ?? "within_scene",
    attestation: cue?.original_source_attestation ?? "" };
}
export function createSfxDraft(project: Project, target: string): SfxDraft {
  const scope = sfxScope(project, target); sfxIdentity(scope);
  const base = saved(project, target);
  if (target !== "new" && !base) throw new Error("This sound event is absent. Open a retained draft to recover its original input.");
  return { scope, target, baseRevision: project.revision, base: clone(base), raw: sfxRaw(base, project),
    sequence: 0, intent: false, pending: null, origins: [] };
}
export const sfxHasWork = (draft: SfxDraft) => draft.intent || draft.pending !== null;
export const sfxUnknown = (draft: SfxDraft) => draft.pending?.outcome === "UNKNOWN";
function nextSequence(value: number) {
  if (!integer(value) || value === Number.MAX_SAFE_INTEGER) throw new Error("The sound edit sequence cannot advance safely. Export the retained input.");
  return value + 1;
}
export function editSfxDraft(draft: SfxDraft, patch: Partial<SfxRaw>): SfxDraft {
  if (Object.keys(patch).some(key => !rawKeys.includes(key as keyof SfxRaw) || typeof patch[key as keyof SfxRaw] !== "string"))
    throw new Error("Only raw sound editor fields can be changed.");
  return { ...draft, raw: { ...draft.raw, ...patch }, sequence: nextSequence(draft.sequence), intent: true };
}
export function reconcileSfxDraft(draft: SfxDraft, project: Project): SfxDraft {
  owner(draft, project);
  if (sfxHasWork(draft) || (draft.target !== "new" && !saved(project, draft.target))) return draft;
  const next = createSfxDraft(project, draft.target);
  return equal(next, draft) ? draft : { ...next, origins: draft.origins };
}
export function sfxConflict(draft: SfxDraft, project: Project) {
  try { owner(draft, project); return draft.baseRevision !== project.revision || !sameCue(draft.base, saved(project, draft.target))
    || (draft.target !== "new" && !saved(project, draft.target)); } catch { return true; }
}
export function sfxLocked(project: Project, target: string, raw: SfxRaw) {
  const scene = saved(project, target)?.scene_id ?? raw.sceneId;
  return project.locks.some(lock => ["content", "timing"].includes(lock.kind)
    && ["project:" + project.id, "scene:" + scene].includes(lock.resource));
}
function parseInteger(raw: string, minimum: number, maximum: number, label: string): number {
  if (!/^-?(0|[1-9][0-9]*)$/.test(raw)) throw new Error(label + " must be a complete integer. Your raw input is kept.");
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum)
    throw new Error(label + " must be between " + minimum + " and " + maximum + ".");
  return value;
}
export function parseSfxRaw(raw: SfxRaw, project: Project, id: string): SfxCue {
  if (!uuid(id) || !events.includes(raw.event) || !policies.includes(raw.tailPolicy))
    throw new Error("Choose a supported sound event and tail behavior.");
  const source = project.assets.find(asset => asset.id === raw.sourceAssetId);
  if (!source || !["audio/wav", "audio/x-wav", "audio/wave"].includes(source.media_type)
    || !/^[0-9a-f]{64}$/.test(raw.sourceSha256) || source.content_sha256 !== raw.sourceSha256)
    throw new Error("The imported WAV and its original digest must still match this draft.");
  if (!["scene_start", "beat_start"].includes(raw.anchorKind)) throw new Error("Choose a supported sound anchor.");
  const cue: SfxCue = {
    schema: SFX_CUE_SCHEMA, id, scene_id: raw.sceneId, event: raw.event as SfxCue["event"],
    anchor: raw.anchorKind === "beat_start" ? { kind: "beat_start", beat_id: raw.beatId } : { kind: "scene_start" },
    offset_frames: parseInteger(raw.offsetFrames, -15, 30, "Offset frames"),
    source_asset_id: raw.sourceAssetId, source_sha256: raw.sourceSha256,
    source_frames: parseInteger(raw.sourceFrames, 1, 180, "Source frames"),
    gain_milli_db: parseInteger(raw.gainMilliDb, -36000, 0, "Gain milli-dB"),
    attack_frames: parseInteger(raw.attackFrames, 0, 30, "Attack frames"),
    release_frames: parseInteger(raw.releaseFrames, 0, 90, "Release frames"),
    ...(raw.tailPolicy === "continue_across_cuts" ? { tail_policy: "continue_across_cuts" as const } : {}),
    original_source_attestation: raw.attestation,
  };
  // Reuse the existing source-placement validator. The actual domain service
  // remains authoritative for selected-cut schedules, overlaps and PCM sources.
  validateSemanticSfx(project, cue);
  return cue;
}
function assertEditable(draft: SfxDraft, project: Project) {
  owner(draft, project);
  if (draft.pending) throw new Error("Keep the original sound attempt separate before another save.");
  if (draft.target !== "new" && !saved(project, draft.target))
    throw new Error("This sound event was removed. Its retained draft cannot silently recreate it.");
  if (sfxConflict(draft, project)) throw new Error("Compare the sound draft with the current saved event before choosing its new base.");
  if (sfxLocked(project, draft.target, draft.raw)) throw new Error("The project or source scene is locked. Your sound input is retained.");
}
export function sfxChange(draft: SfxDraft, project: Project, createdId?: string): SfxChange {
  assertEditable(draft, project);
  const id = draft.target === "new" ? createdId ?? "00000000-0000-4000-8000-000000000005" : draft.target;
  if (draft.target === "new" && (project.audio.sfx_cues ?? []).length >= 64) throw new Error("This project has reached its 64-sound-event limit.");
  const cue = parseSfxRaw(draft.raw, project, id);
  if (draft.target === "new" && project.audio.sfx_cues?.some(item => item.id === id)) throw new Error("A new sound event needs a distinct identity.");
  if (draft.base && cue.scene_id !== draft.base.scene_id) throw new Error("An existing sound identity cannot move to another scene.");
  if (draft.base && sameCue(draft.base, cue)) throw new Error("There are no changes to this saved sound event.");
  return { type: "upsert_semantic_sfx", cue };
}
export function prepareSfxSubmission(draft: SfxDraft, project: Project, id: string,
  operation: "save" | "remove" = "save", createdId?: string): { draft: SfxDraft; change: SfxChange } {
  if (!uuid(id)) throw new Error("A local original-attempt identity is required.");
  assertEditable(draft, project);
  if (operation === "remove" && draft.target === "new") throw new Error("Only a saved sound event can be removed.");
  const change: SfxChange = operation === "remove" ? { type: "remove_semantic_sfx", cue_id: draft.target }
    : sfxChange(draft, project, draft.target === "new" ? createdId ?? crypto.randomUUID() : undefined);
  const pending: SfxPending = { id, revision: project.revision, sequence: draft.sequence, change: clone(change),
    raw: clone(draft.raw), priorCues: clone(project.audio.sfx_cues ?? []), outcome: "UNKNOWN", diagnostic: null };
  return { change, draft: { ...draft, pending } };
}
export function retainSfxFailure(draft: SfxDraft, id: string, diagnostic: string, notSent = false): SfxDraft {
  return draft.pending?.id !== id ? draft : { ...draft, pending: { ...draft.pending,
    outcome: notSent ? "NOT_SENT" : "UNKNOWN", diagnostic: diagnostic.slice(0, 4000) } };
}
export function acknowledgeSfxDraft(draft: SfxDraft, id: string, receipt: Project, submitted: Project): SfxDraft {
  owner(draft, receipt); owner(draft, submitted);
  const p = draft.pending;
  if (!p || p.id !== id || p.outcome !== "UNKNOWN" || receipt.revision !== p.revision + 1
    || submitted.revision !== p.revision || !equal(submitted.audio.sfx_cues ?? [], p.priorCues))
    throw new Error("The original sound response does not match its source, owner and revision.");
  let expected = clone(p.priorCues);
  if (p.change.type === "remove_semantic_sfx") {
    const removedId = p.change.cue_id;
    expected = expected.filter(cue => cue.id !== removedId);
  }
  else {
    const cue = p.change.cue, index = expected.findIndex(item => item.id === cue.id);
    if (index < 0) expected.push(cue); else expected[index] = cue;
  }
  if (!equal((receipt.audio.sfx_cues ?? []).map(canonicalSfxCue), expected.map(canonicalSfxCue)))
    throw new Error("The original sound response did not preserve the exact events and their order.");
  const { sfx_cues: _old, ...oldAudio } = submitted.audio, { sfx_cues: _new, ...newAudio } = receipt.audio;
  if (!equal(oldAudio, newAudio) || !equal(submitted.assets, receipt.assets))
    throw new Error("The original sound response changed unrelated audio or source assets.");
  const later = draft.sequence !== p.sequence;
  const base = draft.target === "new" ? null : saved(receipt, draft.target) ?? draft.base;
  return { ...draft, baseRevision: receipt.revision, base: clone(base),
    raw: later || p.change.type === "remove_semantic_sfx" ? draft.raw : sfxRaw(base, receipt),
    intent: later, pending: null };
}
export function resolveSfxDraft(draft: SfxDraft, project: Project, choice: "saved" | "draft"): SfxDraft {
  owner(draft, project);
  if (sfxUnknown(draft)) throw new Error("Read the original uncertain sound outcome before a separate edit.");
  const base = saved(project, draft.target);
  if (draft.target !== "new" && !base) throw new Error("The sound target is absent. Export its retained input or create a distinct new event.");
  return { ...draft, baseRevision: project.revision, base: clone(base), raw: choice === "saved" ? sfxRaw(base, project) : draft.raw,
    sequence: nextSequence(draft.sequence), intent: choice === "draft", pending: null };
}
function mergeOrigins(a: SfxOrigin[], b: SfxOrigin[]) {
  const values = new Map<string, SfxOrigin>();
  for (const value of [...a, ...b]) {
    if (values.has(value.key) && values.get(value.key)!.raw !== value.raw) throw new Error("A retained sound origin has conflicting bytes.");
    values.set(value.key, clone(value));
  }
  if (values.size > MAX_ORIGINS) throw new Error("The sound recovery origin limit was reached. Export the retained inputs.");
  return [...values.values()];
}
export function recoverSfxRead(current: SfxDraft, recovered: SfxDraft, edited: boolean): SfxDraft {
  sfxIdentity(current.scope); sfxIdentity(recovered.scope);
  if (current.scope !== recovered.scope || current.target !== recovered.target)
    throw new Error("The recovered sound input belongs to another owner or target.");
  const attempt = (p: SfxPending) => ({ ...p, diagnostic: null, outcome: "UNKNOWN" });
  if (current.pending && (!recovered.pending || !equal(attempt(current.pending), attempt(recovered.pending))))
    throw new Error("The current and recovered sound attempts differ.");
  return { ...recovered, raw: edited ? clone(current.raw) : recovered.raw, intent: edited || recovered.intent,
    sequence: edited ? nextSequence(Math.max(current.sequence, recovered.sequence)) : recovered.sequence,
    pending: recovered.pending ? { ...clone(recovered.pending), outcome: "UNKNOWN" } : null,
    origins: mergeOrigins(recovered.origins, current.origins) };
}
export interface SfxOutcome {
  scope: string; sequence: number; pending: SfxPending; project: Project; events: ProjectEvent[]; matchingRevisions: number[];
}
export const sfxOutcomeMatches = (draft: SfxDraft, project: Project, value: SfxOutcome | null) => !!value
  && sfxUnknown(draft) && value.scope === draft.scope && value.sequence === draft.sequence
  && sfxScope(project, draft.target) === draft.scope && equal(value.pending, draft.pending)
  && sameProjectRevision(project, value.project) && equal(project, value.project);
export async function inspectSfxOutcome(draft: SfxDraft, selected: Project, transport: {
  readProject(id: string): Promise<Project>; readHistory(project: Project, after: number, limit: number): Promise<ProjectEvent[]>;
}): Promise<SfxOutcome> {
  owner(draft, selected);
  if (!sfxUnknown(draft)) throw new Error("There is no unconfirmed sound attempt to inspect.");
  const pending = clone(draft.pending!), project = clone(await transport.readProject(selected.id));
  if (!sameProjectRevision(project, selected) || !equal(project, selected)) throw new Error("Reload the saved project before comparing this sound outcome.");
  if (!integer(project.revision) || project.revision < pending.revision || project.revision - pending.revision > MAX_HISTORY)
    throw new Error("Sound history exceeds the bounded inspection window.");
  const entries: ProjectEvent[] = []; let cursor = pending.revision;
  do {
    const page = await transport.readHistory(project, cursor, 256);
    if (!Array.isArray(page) || page.length > 256 || (!page.length && cursor < project.revision)) throw new Error("Saved sound history is incomplete.");
    for (const event of page) {
      if (!event || event.revision !== cursor + 1 || event.revision > project.revision || typeof event.change?.type !== "string"
        || typeof event.created_at !== "string" || !event.created_at) throw new Error("Saved sound history is not contiguous.");
      entries.push(clone(event)); cursor = event.revision;
    }
  } while (cursor < project.revision);
  if (!equal(await transport.readProject(selected.id), project)) throw new Error("The saved project changed during sound inspection.");
  return { scope: draft.scope, sequence: draft.sequence, pending, project, events: entries,
    matchingRevisions: entries.filter(event => equal(event.change, pending.change)).map(event => event.revision) };
}
export function separateSfxIntent(draft: SfxDraft, project: Project, review: SfxOutcome): SfxDraft {
  if (!sfxOutcomeMatches(draft, project, review)) throw new Error("Read the current project and complete sound history before a separate edit.");
  if (project.revision <= draft.pending!.revision) throw new Error("The original request could still commit on its base. Keep it UNKNOWN.");
  const base = saved(project, draft.target);
  if (draft.target !== "new" && !base) throw new Error("The original target was removed. Keep this recovery copy; create a distinct event explicitly.");
  const raw = draft.target === "new" && draft.sequence === draft.pending!.sequence ? sfxRaw(null, project) : draft.raw;
  return { ...draft, baseRevision: project.revision, base: clone(base), raw, pending: null, intent: true, sequence: nextSequence(draft.sequence) };
}
export type SfxStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem">;
export interface SfxCustody { writerId: string; expectedRaw: string | null; expectedPointer: string | null }
export interface SfxCopy { schema: "motionwright.semantic-sfx-recovery/1"; scope: string; writerId: string; updatedAt: number; draft: SfxDraft }
export interface SfxRecoveryEntry { key: string; writerId: string; raw: string; value: SfxCopy | null; error: string | null }
function rawShape(value: unknown): value is SfxRaw { return record(value) && exact(value, rawKeys) && rawKeys.every(key => typeof value[key] === "string"); }
function cueShape(value: unknown): value is SfxCue {
  if (!record(value) || !exact(value, cueKeys, ["tail_policy"]) || value.schema !== SFX_CUE_SCHEMA
    || ![value.id, value.scene_id, value.source_asset_id].every(uuid) || !events.includes(String(value.event))
    || typeof value.source_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(value.source_sha256)
    || typeof value.original_source_attestation !== "string" || !record(value.anchor)
    || (Object.hasOwn(value, "tail_policy") && !policies.includes(String(value.tail_policy)))) return false;
  if (value.anchor.kind === "scene_start" ? !exact(value.anchor, ["kind"])
    : value.anchor.kind !== "beat_start" || !exact(value.anchor, ["kind", "beat_id"]) || !uuid(value.anchor.beat_id)) return false;
  return ["offset_frames", "source_frames", "gain_milli_db", "attack_frames", "release_frames"].every(key => Number.isSafeInteger(value[key]));
}
function changeShape(value: unknown): value is SfxChange {
  return record(value) && (value.type === "upsert_semantic_sfx" ? exact(value, ["type", "cue"]) && cueShape(value.cue)
    : value.type === "remove_semantic_sfx" && exact(value, ["type", "cue_id"]) && uuid(value.cue_id));
}
function originShape(value: unknown, scope: string): value is SfxOrigin {
  if (!record(value) || !exact(value, ["key", "raw"]) || typeof value.key !== "string" || typeof value.raw !== "string"
    || bytes(value.raw) > MAX_SFX_COPY_BYTES) return false;
  const writer = value.key.slice(value.key.lastIndexOf(":") + 1);
  if (!uuid(writer) || value.key !== sfxCopyKey(scope, writer)) return false;
  const original: unknown = JSON.parse(value.raw);
  return record(original) && original.schema === "motionwright.semantic-sfx-recovery/1" && original.scope === scope
    && original.writerId === writer && record(original.draft) && record(original.draft.pending)
    && ["UNKNOWN", "NOT_SENT"].includes(String(original.draft.pending.outcome));
}
function draftShape(value: unknown): value is SfxDraft {
  if (!record(value) || !exact(value, ["scope", "target", "baseRevision", "base", "raw", "sequence", "intent", "pending", "origins"])
    || typeof value.scope !== "string" || typeof value.target !== "string" || sfxIdentity(value.scope)[3] !== value.target
    || !integer(value.baseRevision) || !integer(value.sequence) || typeof value.intent !== "boolean" || !rawShape(value.raw)
    || (value.target === "new" ? value.base !== null : !cueShape(value.base) || value.base.id !== value.target)
    || !Array.isArray(value.origins) || value.origins.length > MAX_ORIGINS || !value.origins.every(origin => originShape(origin, value.scope as string))
    || new Set(value.origins.map(origin => (origin as SfxOrigin).key)).size !== value.origins.length) return false;
  const p = value.pending; if (p === null) return true;
  if (!record(p) || !exact(p, ["id", "revision", "sequence", "change", "raw", "priorCues", "outcome", "diagnostic"])
    || !uuid(p.id) || p.revision !== value.baseRevision || !integer(p.sequence) || p.sequence > value.sequence
    || !changeShape(p.change) || !rawShape(p.raw) || !Array.isArray(p.priorCues) || p.priorCues.length > 64
    || !p.priorCues.every(cueShape) || new Set(p.priorCues.map(cue => cue.id)).size !== p.priorCues.length
    || !["UNKNOWN", "NOT_SENT"].includes(String(p.outcome)) || (p.diagnostic !== null && typeof p.diagnostic !== "string")) return false;
  const id = p.change.type === "upsert_semantic_sfx" ? p.change.cue.id : p.change.cue_id;
  return value.target === "new" ? p.change.type === "upsert_semantic_sfx" && !p.priorCues.some(cue => cue.id === id)
    : id === value.target && p.priorCues.some(cue => cue.id === value.target && sameCue(cue, value.base as SfxCue));
}
export function encodeSfxCopy(draft: SfxDraft, writerId: string, updatedAt = Date.now()): string {
  if (!uuid(writerId) || !integer(updatedAt) || !draftShape(draft)) throw new Error("The sound recovery record is invalid.");
  const raw = JSON.stringify({ schema: "motionwright.semantic-sfx-recovery/1", scope: draft.scope, writerId, updatedAt, draft } satisfies SfxCopy);
  if (bytes(raw) > MAX_SFX_COPY_BYTES) throw new Error("This sound recovery copy exceeds 256 KiB. Export and shorten the retained input.");
  return raw;
}
export function decodeSfxCopy(raw: string, scope: string, writerId?: string): SfxCopy {
  if (bytes(raw) > MAX_SFX_COPY_BYTES) throw new Error("This sound recovery copy exceeds its size limit.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || JSON.stringify(value) !== raw || !exact(value, ["schema", "scope", "writerId", "updatedAt", "draft"])
    || value.schema !== "motionwright.semantic-sfx-recovery/1" || value.scope !== scope || !uuid(value.writerId)
    || (writerId !== undefined && value.writerId !== writerId) || !integer(value.updatedAt) || !draftShape(value.draft)
    || value.draft.scope !== scope) throw new Error("This sound recovery copy has an incompatible owner or shape. Its original bytes were kept.");
  const copy = clone(value as unknown as SfxCopy);
  if (copy.draft.pending) copy.draft.pending.outcome = "UNKNOWN";
  return copy;
}
export function readSfxPointer(storage: Pick<SfxStorage, "getItem">, scope: string, prior: { observed: boolean; value: string | null }) {
  sfxIdentity(scope); const value = storage.getItem(sfxPointerKey(scope));
  if (prior.observed && value !== prior.value) throw new Error("The earlier sound recovery pointer changed.");
  return { observed: true, value };
}
export function assertSfxOrigins(storage: Pick<SfxStorage, "getItem">, draft: SfxDraft) {
  for (const origin of draft.origins) if (storage.getItem(origin.key) !== origin.raw)
    throw new Error("An original uncertain sound copy changed. Current input is retained; another save cannot be authorized from it.");
}
export function retainSfxOrigin(draft: SfxDraft, origin: SfxOrigin): SfxDraft {
  if (!draft.pending) return draft;
  const writer = origin.key.slice(origin.key.lastIndexOf(":") + 1), source = decodeSfxCopy(origin.raw, draft.scope, writer).draft;
  if (origin.key !== sfxCopyKey(draft.scope, writer) || !equal(source, { ...draft, pending: { ...draft.pending, outcome: "UNKNOWN" } }))
    throw new Error("The sound attempt does not match its exact original copy.");
  const attempt = (p: SfxPending) => ({ ...p, diagnostic: null, outcome: "UNKNOWN" });
  if (draft.origins.some(value => {
    const p = (JSON.parse(value.raw) as SfxCopy).draft.pending;
    return p !== null && equal(attempt(p), attempt(draft.pending!));
  })) return draft;
  return { ...draft, origins: mergeOrigins(draft.origins, [origin]) };
}
export function writeSfxCopy(storage: SfxStorage, pointers: SfxStorage, draft: SfxDraft, custody: SfxCustody): string {
  const key = sfxCopyKey(draft.scope, custody.writerId), pointer = sfxPointerKey(draft.scope);
  const check = () => {
    assertSfxOrigins(storage, draft);
    if (storage.getItem(key) !== custody.expectedRaw || pointers.getItem(pointer) !== custody.expectedPointer)
      throw new Error("Another writer changed the sound copy or pointer. Earlier bytes were kept.");
  };
  check();
  const sameCopy = custody.expectedRaw !== null
    && equal(decodeSfxCopy(custody.expectedRaw, draft.scope, custody.writerId).draft, draft);
  // A confirmed data copy alone is not a confirmed recovery pointer. Retry a
  // partial publication with its original bytes before authorizing dispatch.
  if (sameCopy && custody.expectedPointer === custody.writerId) return custody.expectedRaw!;
  const raw = sameCopy ? custody.expectedRaw! : encodeSfxCopy(draft, custody.writerId);
  if (!sameCopy) {
    if (storage.length > MAX_KEYS) throw new Error("Sound recovery enumeration exceeds its safe limit.");
    let total = bytes(raw), count = 1;
    for (let i = 0; i < storage.length; i++) {
      const other = storage.key(i); if (!other?.startsWith(SFX_COPY_PREFIX) || other === key) continue;
      count++; total += bytes(storage.getItem(other) ?? "");
    }
    if (count > MAX_SFX_COPIES || total > MAX_SFX_RECOVERY_BYTES) throw new Error("Sound recovery storage is full. Export retained inputs before closing.");
    check(); storage.setItem(key, raw);
    if (storage.getItem(key) !== raw) throw new Error("The sound recovery bytes were not confirmed. No project change may be sent.");
    custody.expectedRaw = raw;
  }
  if (pointers.getItem(pointer) !== custody.expectedPointer) throw new Error("The sound pointer changed before publication.");
  pointers.setItem(pointer, custody.writerId);
  if (pointers.getItem(pointer) !== custody.writerId) throw new Error("The sound recovery pointer was not confirmed.");
  custody.expectedPointer = custody.writerId;
  if (storage.getItem(key) !== raw) throw new Error("The sound recovery bytes changed during pointer publication.");
  assertSfxOrigins(storage, draft); return raw;
}
function catalogue(storage: SfxStorage, accepts: (scope: string) => boolean): { entries: SfxRecoveryEntry[]; limited: boolean } {
  const entries: SfxRecoveryEntry[] = [], limit = Math.min(storage.length, MAX_KEYS);
  for (let i = 0; i < limit; i++) {
    const key = storage.key(i); if (!key?.startsWith(SFX_COPY_PREFIX)) continue;
    const suffix = key.slice(SFX_COPY_PREFIX.length), end = suffix.lastIndexOf(":"), scope = suffix.slice(0, end), writerId = suffix.slice(end + 1);
    try { sfxIdentity(scope); if (!accepts(scope)) continue; } catch { continue; }
    const raw = storage.getItem(key); if (raw === null) continue;
    try { entries.push({ key, writerId, raw, value: decodeSfxCopy(raw, scope, writerId), error: null }); }
    catch (reason) { entries.push({ key, writerId, raw, value: null, error: reason instanceof Error ? reason.message : String(reason) }); }
  }
  return { entries, limited: storage.length > MAX_KEYS };
}
export const readSfxCopies = (storage: SfxStorage, scope: string) => catalogue(storage, candidate => candidate === scope);
export const readSfxCatalogue = (storage: SfxStorage, project: Project) => {
  const current = JSON.stringify([project.id, project.generation, project.active_branch]);
  return catalogue(storage, scope => JSON.stringify(sfxIdentity(scope).slice(0, 3)) === current);
};
