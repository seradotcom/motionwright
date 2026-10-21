import { useEffect, useRef, useState } from "react";
import { sameProjectRevision } from "./projectMutation";
import {
  canonicalRecipeField, decodeRecipeDraft, deliveryArchetypes, deliveryRoles, editRecipeField,
  newRecipeDraft, proposedDeliveryRecipe, recipeConflicts, recipeDraftScope, recipeDraftStorageKey,
  recipeFields, reconcileRecipeDraft, RECIPE_DRAFT_STORAGE_PREFIX, resolveRecipeField,
  savedDeliveryRecipe, submitRecipeDraft, validateDeliveryRecipe,
} from "./deliveryRecipe";
import type { DeliveryRecipe, RecipeDraft, RecipeGroup } from "./deliveryRecipe";
import type { Change, Project, ProjectEvent } from "./types";

export interface RecipePending {
  /** Local comparison identity only. Never a service request ID or a grant. */
  id: string;
  revision: number;
  fields: Record<string, string>;
  sequences: Record<string, number>;
  /** null means a legacy uncertain copy has no original full-recipe snapshot. */
  fingerprint: string | null;
  outcome: "UNKNOWN" | "NOT_SENT";
  diagnostic?: string;
}
export interface RecipeAuthorState {
  schema: "motionwright.recipe-author-input/1";
  input: RecipeDraft;
  sequence: number;
  fieldSequences: Record<string, number>;
  pending: RecipePending | null;
}
export interface RecipeCustody { writerId: string; expectedRaw: string | null; expectedPointer: string | null }
export interface RecipeRecovery {
  schema: "motionwright.recipe-recovery/2";
  scope: string;
  writerId: string;
  updatedAt: number;
  state: RecipeAuthorState;
}
export interface RecipeCopy {
  key: string; writerId: string; raw: string; state: RecipeAuthorState | null;
  revision: number | null; fields: number; readable: boolean;
}
export const RECIPE_WRITER_POINTER_PREFIX = "motionwright.delivery-recipe-writer.v2:";
export const MAX_RECIPE_COPY_BYTES = 256 * 1024;
export const MAX_RECIPE_TOTAL_BYTES = 2 * 1024 * 1024;
export const MAX_RECIPE_COPIES = 64;
export const MAX_RECIPE_HISTORY = 2048;
type StorageLike = Pick<Storage, "length" | "key" | "getItem" | "setItem" | "removeItem">;
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const uuid = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(value);
const bytes = (value: string) => new TextEncoder().encode(value).length;
const exact = (value: Record<string, unknown>, required: readonly string[], optional: readonly string[] = []) =>
  required.every(key => Object.hasOwn(value, key)) && Object.keys(value).every(key => required.includes(key) || optional.includes(key));
export const recipePointerKey = (scope: string) => RECIPE_WRITER_POINTER_PREFIX + scope;
export const recipeHasWork = (state: RecipeAuthorState) => Object.keys(state.input.edits).length > 0 || state.pending !== null;
export const recipeUnconfirmed = (state: RecipeAuthorState) => state.pending?.outcome === "UNKNOWN";
const ownerPart = (scope: string) => JSON.stringify((JSON.parse(scope) as unknown[]).slice(0, 3));
function identity(scope: string): [string, string, string, string, RecipeGroup] {
  const value: unknown = JSON.parse(scope);
  if (!Array.isArray(value) || value.length !== 5 || value.slice(0, 4).some(part => typeof part !== "string" || !part)
    || !uuid(value[3]) || !["film", "mix"].includes(String(value[4]))) throw new Error("Recipe author input has an invalid owner identity.");
  return value as [string, string, string, string, RecipeGroup];
}
function checkOwner(state: RecipeAuthorState, project: Project): string {
  const [, , , profileId, group] = identity(state.input.scope);
  if (state.input.scope !== recipeDraftScope(project, profileId, group)
    || project.deliverables.filter(profile => profile.id === profileId).length !== 1)
    throw new Error("This recipe input belongs to another project, generation, branch, or unavailable profile.");
  return profileId;
}
/** Stable semantic comparison only; this string is never used as a command. */
export function recipeFingerprint(recipe: DeliveryRecipe | null): string {
  if (!recipe || (!recipe.scene_intents.length && !recipe.original_mix)) return "null";
  return JSON.stringify({ schema: recipe.schema,
    scene_intents: recipe.scene_intents.map(intent => ({ scene_id: intent.scene_id, role: intent.role, archetype: intent.archetype }))
      .sort((a, b) => a.scene_id.localeCompare(b.scene_id)),
    original_mix: recipe.original_mix ? Object.fromEntries(Object.entries(recipe.original_mix).sort(([a], [b]) => a.localeCompare(b))) : null });
}
/** History payloads are untrusted JSON. Check the current wire shape and its
 * existing domain bounds, but not membership in today's Project: a later event
 * may have removed a scene or asset. Extend this alongside future wire fields.
 */
export function assertRecipeHistoryPayload(value: unknown): asserts value is DeliveryRecipe | null {
  if (value === null) return;
  if (!record(value) || !exact(value, ["schema", "scene_intents", "original_mix"])
    || value.schema !== "motionwright.delivery-recipe/1" || !Array.isArray(value.scene_intents)
    || value.scene_intents.length > 4096) throw new Error("The saved recipe history contains an invalid recipe payload.");
  const scenes = new Set<string>();
  for (const intent of value.scene_intents) {
    if (!record(intent) || !exact(intent, ["scene_id", "role", "archetype"])) throw new Error("The saved recipe history contains an invalid scene intent.");
    const sceneId = intent.scene_id;
    if (!uuid(sceneId) || scenes.has(sceneId.toLowerCase())
      || (intent.role !== null && !deliveryRoles.some(role => role === intent.role))
      || (intent.archetype !== null && !deliveryArchetypes.some(archetype => archetype === intent.archetype)))
      throw new Error("The saved recipe history contains an invalid scene intent.");
    scenes.add(sceneId.toLowerCase());
  }
  if (value.original_mix === null) return;
  const mix = value.original_mix;
  // Keep persisted history aligned with DeliveryRecipe and its validator.
  // Unknown fields require a shared wire contract before they can be accepted.
  if (!record(mix) || !exact(mix, ["music_asset_id", "sfx_asset_id", "sfx_gain_db", "duck_attenuation_db", "duck_attack_samples", "duck_release_samples"]))
    throw new Error("The saved recipe history contains an invalid original mix.");
  for (const key of ["music_asset_id", "sfx_asset_id"]) if (mix[key] !== null && !uuid(mix[key]))
    throw new Error("The saved recipe history contains an invalid audio source identity.");
  if (typeof mix.music_asset_id === "string" && typeof mix.sfx_asset_id === "string"
    && mix.music_asset_id.toLowerCase() === mix.sfx_asset_id.toLowerCase())
    throw new Error("The saved recipe history contains duplicate music and SFX sources.");
  const gain = mix.sfx_gain_db, attenuation = mix.duck_attenuation_db;
  if (typeof gain !== "number" || !Number.isFinite(gain) || gain < -80 || gain > 12
    || typeof attenuation !== "number" || !Number.isFinite(attenuation) || attenuation < 0 || attenuation > 30)
    throw new Error("The saved recipe history contains invalid mix levels.");
  for (const key of ["duck_attack_samples", "duck_release_samples"]) {
    const samples = mix[key];
    if (typeof samples !== "number" || !Number.isInteger(samples) || samples < 1 || samples > 48000)
      throw new Error("The saved recipe history contains invalid mix sample counts.");
  }
}
export function createRecipeAuthorState(project: Project, profileId: string, group: RecipeGroup): RecipeAuthorState {
  return { schema: "motionwright.recipe-author-input/1", input: newRecipeDraft(recipeDraftScope(project, profileId, group), group, project.revision),
    sequence: 0, fieldSequences: {}, pending: null };
}
export function editRecipeAuthorState(state: RecipeAuthorState, field: string, raw: string, current: Record<string, string>, retainDecision = false): RecipeAuthorState {
  const input = editRecipeField(state.input, field, raw, current), sequence = state.sequence + 1;
  // During an own-copy read failure even a return to the saved value is a later
  // decision. Keep its raw value before another Project observation can change it.
  if (retainDecision) input.edits[field] = { base: input.edits[field]?.base ?? current[field] ?? "", value: raw, intent: true };
  if (!Number.isSafeInteger(sequence)) throw new Error("The recipe edit sequence is exhausted. Keep an author copy before continuing.");
  return { ...state, input, sequence, fieldSequences: { ...state.fieldSequences, [field]: sequence } };
}
/** Project observations preserve every uncertain snapshot, even matching values. */
export function observeRecipeAuthorState(state: RecipeAuthorState, project: Project): RecipeAuthorState {
  const profileId = checkOwner(state, project);
  const input = reconcileRecipeDraft(state.input, recipeFields(savedDeliveryRecipe(project, profileId), state.input.group), project.revision);
  return JSON.stringify(input) === JSON.stringify(state.input) ? state : { ...state, input };
}
/** Recover author decisions without treating coincident saved values as an ACK.
 * Only the fresh buffer created after a failed own-copy read may be overlaid.
 * Its per-field sequences retain typing that returned to the saved value.
 */
export function recoverRecipeAuthorState(recovered: RecipeAuthorState, project: Project, later?: RecipeAuthorState): RecipeAuthorState {
  const profileId = checkOwner(recovered, project);
  if (later && (checkOwner(later, project) !== profileId || later.input.scope !== recovered.input.scope
    || later.pending || later.input.awaiting)) throw new Error("A separate uncertain recipe attempt cannot be merged into a recovered copy.");
  let next = structuredClone(recovered);
  if (next.pending) next.pending.outcome = "UNKNOWN";
  for (const edit of Object.values(next.input.edits)) edit.intent = true;
  if (later) {
    const current = recipeFields(savedDeliveryRecipe(project, profileId), next.input.group);
    const touched = new Set([...Object.keys(later.fieldSequences), ...Object.keys(later.input.edits)]);
    for (const field of touched) {
      const raw = later.input.edits[field]?.value ?? current[field] ?? "";
      next = editRecipeAuthorState(next, field, raw, current, true);
    }
  }
  return observeRecipeAuthorState(next, project);
}
export function prepareRecipeSubmission(state: RecipeAuthorState, project: Project, id: string): {
  state: RecipeAuthorState; change: Extract<Change, { type: "set_delivery_recipe" }>;
} {
  const profileId = checkOwner(state, project), canonical = savedDeliveryRecipe(project, profileId);
  if (!uuid(id)) throw new Error("A local recipe comparison identity is required.");
  if (recipeUnconfirmed(state)) throw new Error("Read the saved recipe and history before starting a separate intent.");
  if (state.input.revision > project.revision) throw new Error("The saved project is behind this recipe input.");
  if (recipeConflicts(state.input, recipeFields(canonical, state.input.group)).length)
    throw new Error("Resolve overlapping recipe values before saving.");
  if (project.locks.some(lock => lock.kind === "content" && lock.resource === "project:" + project.id))
    throw new Error("The project content lock blocks recipe changes.");
  const proposed = proposedDeliveryRecipe(canonical, state.input);
  validateDeliveryRecipe(project, profileId, proposed);
  const fingerprint = recipeFingerprint(proposed);
  if (fingerprint === recipeFingerprint(canonical)) throw new Error("These recipe values already match the saved project.");
  const unsent = structuredClone(state.input);
  // Only the live caller can retain NOT_SENT. Recovered copies become UNKNOWN.
  if (state.pending?.outcome === "NOT_SENT") delete unsent.awaiting;
  const input = submitRecipeDraft(unsent, project.revision), fields = structuredClone(input.awaiting!.fields);
  const pending: RecipePending = { id, revision: project.revision, fields,
    sequences: Object.fromEntries(Object.keys(fields).map(field => [field, state.fieldSequences[field] ?? 0])),
    fingerprint, outcome: "UNKNOWN" };
  return { state: { ...state, input, pending }, change: {
    type: "set_delivery_recipe", deliverable_profile_id: profileId, recipe: fingerprint === "null" ? null : proposed,
  } };
}
export function retainRecipeFailure(state: RecipeAuthorState, id: string, diagnostic: string, notSent = false): RecipeAuthorState {
  return state.pending?.id === id ? { ...state, pending: { ...state.pending, diagnostic, outcome: notSent ? "NOT_SENT" : "UNKNOWN" } } : state;
}
/** Call only with the Project returned by the original throwing coordinator. */
export function acknowledgeRecipeOriginal(state: RecipeAuthorState, id: string, receipt: Project): RecipeAuthorState {
  const pending = state.pending;
  if (!pending || pending.id !== id) return state;
  const profileId = checkOwner(state, receipt), saved = savedDeliveryRecipe(receipt, profileId);
  if (pending.fingerprint === null || receipt.revision !== pending.revision + 1 || recipeFingerprint(saved) !== pending.fingerprint)
    throw new Error("The original recipe reply does not confirm the exact submitted recipe and next revision.");
  if (saved) validateDeliveryRecipe(receipt, profileId, saved);
  const current = recipeFields(saved, state.input.group), input = structuredClone(state.input);
  delete input.awaiting; input.revision = receipt.revision;
  for (const field of Object.keys(pending.fields)) {
    if ((state.fieldSequences[field] ?? 0) === pending.sequences[field]) delete input.edits[field];
  }
  // This exact original receipt establishes the new base without consuming later typing.
  for (const [field, edit] of Object.entries(input.edits)) input.edits[field] = { ...edit, base: current[field] ?? "", intent: true };
  return { ...state, input, pending: null };
}
export function resolveRecipeAuthorField(state: RecipeAuthorState, project: Project, field: string, choice: "saved" | "draft"): RecipeAuthorState {
  const profileId = checkOwner(state, project);
  if (recipeUnconfirmed(state)) throw new Error("Review the unconfirmed recipe result before resolving its input.");
  const source = structuredClone(state.input);
  if (state.pending?.outcome === "NOT_SENT") delete source.awaiting;
  const input = resolveRecipeField(source, field, recipeFields(savedDeliveryRecipe(project, profileId), state.input.group), choice);
  input.revision = project.revision;
  return { ...state, input, pending: null };
}

export interface RecipeOutcomeTransport {
  readProject(projectId: string): Promise<Project>;
  readHistory(project: Project, afterRevision: number, limit: number): Promise<ProjectEvent[]>;
}
export interface RecipeOutcomeReview { scope: string; pending: RecipePending; project: Project; events: ProjectEvent[]; matchingValueRevisions: number[] }
const pendingEqual = (a: RecipePending | null, b: RecipePending) => a !== null && JSON.stringify(a) === JSON.stringify(b);
function sameObservedRecipe(a: Project, b: Project, profileId: string): boolean {
  return sameProjectRevision(a, b) && a.deliverables.filter(profile => profile.id === profileId).length === 1
    && b.deliverables.filter(profile => profile.id === profileId).length === 1
    && recipeFingerprint(savedDeliveryRecipe(a, profileId)) === recipeFingerprint(savedDeliveryRecipe(b, profileId));
}
export function recipeOutcomeMatches(state: RecipeAuthorState, project: Project, review: RecipeOutcomeReview | null): boolean {
  if (!review || !recipeUnconfirmed(state) || review.scope !== state.input.scope || !pendingEqual(state.pending, review.pending)) return false;
  try { return sameObservedRecipe(project, review.project, checkOwner(state, project)); } catch { return false; }
}
export async function inspectRecipeOutcome(state: RecipeAuthorState, selected: Project, transport: RecipeOutcomeTransport): Promise<RecipeOutcomeReview> {
  const profileId = checkOwner(state, selected);
  if (!recipeUnconfirmed(state)) throw new Error("There is no unconfirmed recipe attempt to inspect.");
  const pending = structuredClone(state.pending!), observed = structuredClone(await transport.readProject(selected.id));
  if (!sameObservedRecipe(observed, selected, profileId)) throw new Error("The saved project changed. Reload it before reviewing the recipe outcome.");
  checkOwner(state, observed);
  if (!integer(observed.revision) || observed.revision < pending.revision || observed.revision - pending.revision > MAX_RECIPE_HISTORY)
    throw new Error("The recipe history range is unavailable or exceeds the bounded review window.");
  const events: ProjectEvent[] = [];
  let cursor = pending.revision;
  do {
    const page = await transport.readHistory(observed, cursor, 256);
    if (!Array.isArray(page) || page.length > 256 || (!page.length && cursor < observed.revision)) throw new Error("The saved recipe history is incomplete.");
    for (const event of page) {
      if (!event || !integer(event.revision) || event.revision !== cursor + 1 || event.revision > observed.revision
        || !event.change || typeof event.change.type !== "string" || typeof event.created_at !== "string" || !event.created_at)
        throw new Error("The saved recipe history is not a contiguous revision sequence.");
      events.push(structuredClone(event)); cursor = event.revision;
    }
  } while (cursor < observed.revision);
  if (!sameObservedRecipe(await transport.readProject(selected.id), observed, profileId))
    throw new Error("The saved project changed while recipe history was read. Inspect it again.");
  return { scope: state.input.scope, pending, project: observed, events,
    matchingValueRevisions: events.filter(event => {
      if (event.change.type !== "set_delivery_recipe" || event.change.deliverable_profile_id !== profileId) return false;
      const recipe = event.change.recipe;
      assertRecipeHistoryPayload(recipe);
      if (pending.fingerprint !== null) return recipeFingerprint(recipe) === pending.fingerprint;
      const values = recipeFields(recipe, state.input.group);
      return Object.entries(pending.fields).every(([field, raw]) => (values[field] ?? "") === canonicalRecipeField(state.input.group, field, raw));
    }).map(event => event.revision) };
}
export function releaseRecipeOutcome(state: RecipeAuthorState, project: Project, review: RecipeOutcomeReview): RecipeAuthorState {
  if (!recipeOutcomeMatches(state, project, review)) throw new Error("Read the current saved recipe and complete history before a separate intent.");
  const input = structuredClone(state.input); delete input.awaiting;
  const current = recipeFields(savedDeliveryRecipe(project, checkOwner(state, project)), input.group);
  // This explicit local decision establishes a new base only for values the
  // author just compared and found equal. Differing overlaps still need a choice.
  for (const [field, edit] of Object.entries(input.edits)) {
    if (canonicalRecipeField(input.group, field, edit.value) === (current[field] ?? ""))
      input.edits[field] = { ...edit, base: current[field] ?? "", intent: true };
  }
  return observeRecipeAuthorState({ ...state, input, pending: null }, project);
}

function checkedSequenceMap(value: unknown, input: RecipeDraft, maximum: number): Record<string, number> {
  if (!record(value) || Object.keys(value).length > 8192) throw new Error("Recipe author sequences are invalid or excessive.");
  const edits = Object.fromEntries(Object.keys(value).map(field => [field, { base: "", value: "" }]));
  decodeRecipeDraft(JSON.stringify({ ...newRecipeDraft(input.scope, input.group, input.revision), edits }), input.scope, input.group);
  if (Object.values(value).some(sequence => !integer(sequence) || sequence > maximum)) throw new Error("Recipe author sequences are outside their retained range.");
  return { ...value } as Record<string, number>;
}
function checkedState(value: unknown, scope: string, group: RecipeGroup): RecipeAuthorState {
  if (!record(value) || !exact(value, ["schema", "input", "sequence", "fieldSequences", "pending"])
    || value.schema !== "motionwright.recipe-author-input/1" || !integer(value.sequence)) throw new Error("Recipe recovery has an unsupported author-input shape.");
  identity(scope);
  const input = decodeRecipeDraft(JSON.stringify(value.input), scope, group);
  const fieldSequences = checkedSequenceMap(value.fieldSequences, input, value.sequence);
  let pending: RecipePending | null = null;
  if (value.pending !== null) {
    const source = value.pending;
    if (!record(source) || !exact(source, ["id", "revision", "fields", "sequences", "fingerprint", "outcome"], ["diagnostic"])
      || !uuid(source.id) || !integer(source.revision) || !input.awaiting || source.revision !== input.awaiting.revision
      || JSON.stringify(source.fields) !== JSON.stringify(input.awaiting.fields)
      || (source.fingerprint !== null && typeof source.fingerprint !== "string")
      || !["UNKNOWN", "NOT_SENT"].includes(String(source.outcome)) || (source.diagnostic !== undefined && typeof source.diagnostic !== "string"))
      throw new Error("Recipe recovery has an invalid pending comparison snapshot.");
    const sequences = checkedSequenceMap(source.sequences, input, value.sequence);
    if (JSON.stringify(Object.keys(sequences).sort()) !== JSON.stringify(Object.keys(input.awaiting.fields).sort()))
      throw new Error("Recipe pending author sequences do not match its submitted fields.");
    pending = { id: source.id, revision: source.revision, fields: structuredClone(input.awaiting.fields), sequences,
      fingerprint: source.fingerprint as string | null, outcome: "UNKNOWN",
      ...(typeof source.diagnostic === "string" ? { diagnostic: source.diagnostic } : {}) };
  } else if (input.awaiting) throw new Error("Recipe recovery omitted its pending comparison state.");
  return { schema: "motionwright.recipe-author-input/1", input, sequence: value.sequence, fieldSequences, pending };
}
export function decodeRecipeRecovery(raw: string, scope: string, group: RecipeGroup, writerId: string): RecipeAuthorState {
  if (bytes(raw) > MAX_RECIPE_COPY_BYTES) throw new Error("Recipe recovery copy exceeds 256 KiB.");
  if (!uuid(writerId)) throw new Error("Recipe recovery has an invalid writer identity.");
  const value: unknown = JSON.parse(raw);
  if (record(value) && value.version === 1) {
    // Preserve the existing format as comparison-only input, including UNKNOWN.
    const input = decodeRecipeDraft(raw, scope, group); identity(scope);
    const fields = input.awaiting?.fields;
    return { schema: "motionwright.recipe-author-input/1", input, sequence: 0, fieldSequences: {},
      pending: fields ? { id: writerId, revision: input.awaiting!.revision, fields: structuredClone(fields),
        sequences: Object.fromEntries(Object.keys(fields).map(field => [field, 0])), fingerprint: null, outcome: "UNKNOWN" } : null };
  }
  if (!record(value) || !exact(value, ["schema", "scope", "writerId", "updatedAt", "state"])
    || value.schema !== "motionwright.recipe-recovery/2" || value.scope !== scope || value.writerId !== writerId || !integer(value.updatedAt))
    throw new Error("Recipe recovery belongs to another writer or has an unsupported format.");
  return checkedState(value.state, scope, group);
}
export function readRecipeCopies(storage: StorageLike, scope: string, group: RecipeGroup): { copies: RecipeCopy[]; limited: boolean } {
  const prefix = RECIPE_DRAFT_STORAGE_PREFIX + scope + ":", copies: RecipeCopy[] = [];
  let limited = false;
  for (let index = 0; index < storage.length; index++) {
    const key = storage.key(index); if (!key?.startsWith(prefix)) continue;
    if (copies.length >= MAX_RECIPE_COPIES) { limited = true; continue; }
    const raw = storage.getItem(key); if (raw === null) continue;
    const writerId = key.slice(prefix.length);
    let state: RecipeAuthorState | null = null;
    try { state = decodeRecipeRecovery(raw, scope, group, writerId); } catch { /* Original unreadable bytes remain available. */ }
    copies.push({ key, writerId, raw, state, revision: state?.input.revision ?? null,
      fields: state ? Object.keys(state.input.edits).length : 0, readable: state !== null });
  }
  return { copies, limited };
}
export function writeRecipeRecovery(storage: StorageLike, session: Pick<Storage, "getItem" | "setItem">,
  state: RecipeAuthorState, custody: RecipeCustody): RecipeCustody {
  const scope = state.input.scope, key = recipeDraftStorageKey(scope, custody.writerId), pointerKey = recipePointerKey(scope);
  const pointer = session.getItem(pointerKey);
  if (pointer !== custody.expectedPointer && pointer !== custody.writerId) throw new Error("Another writer changed this tab's recipe pointer. Earlier copies were kept.");
  if (storage.getItem(key) !== custody.expectedRaw) throw new Error("This recipe recovery changed in another window. Earlier copies were kept.");
  let raw = JSON.stringify({ schema: "motionwright.recipe-recovery/2", scope, writerId: custody.writerId, updatedAt: Date.now(), state } satisfies RecipeRecovery);
  if (custody.expectedRaw !== null) {
    const before = decodeRecipeRecovery(custody.expectedRaw, scope, state.input.group, custody.writerId);
    const same = JSON.stringify(before) === JSON.stringify(state);
    if (!recipeHasWork(before) && !same) throw new Error("A clean recipe checkpoint is immutable. Start a separate writer.");
    // Reconfirming identical author input does not rewrite its timestamp or bytes.
    if (same) raw = custody.expectedRaw;
  }
  decodeRecipeRecovery(raw, scope, state.input.group, custody.writerId);
  let count = 1, total = bytes(key) + bytes(raw);
  for (let index = 0; index < storage.length; index++) {
    const other = storage.key(index); if (!other?.startsWith(RECIPE_DRAFT_STORAGE_PREFIX) || other === key) continue;
    count++; total += bytes(other) + bytes(storage.getItem(other) ?? "");
  }
  if (count > MAX_RECIPE_COPIES || total > MAX_RECIPE_TOTAL_BYTES) throw new Error("Recipe recovery reached its 64-copy / 2 MiB budget. No original input was removed.");
  // Enumeration may run application or storage hooks. Recheck both owners before
  // replacing bytes; the earlier check alone cannot guard that interval.
  if (storage.getItem(key) !== custody.expectedRaw) throw new Error("This recipe recovery changed while its budget was inspected. Earlier copies were kept.");
  if (session.getItem(pointerKey) !== pointer) throw new Error("Another writer changed this tab's recipe pointer while its budget was inspected. Earlier copies were kept.");
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The recipe recovery write was not confirmed.");
  custody.expectedRaw = raw;
  session.setItem(pointerKey, custody.writerId);
  if (session.getItem(pointerKey) !== custody.writerId) throw new Error("The recipe writer pointer was not confirmed.");
  if (storage.getItem(key) !== raw) throw new Error("The recipe recovery changed while its writer pointer was published. Its retained bytes were not confirmed.");
  custody.expectedPointer = custody.writerId;
  return custody;
}
export function retainReviewedRecipeIntent(storage: StorageLike, session: Pick<Storage, "getItem" | "setItem">,
  before: RecipeAuthorState, next: RecipeAuthorState, original: RecipeCustody, writerId: string): RecipeCustody {
  if (!recipeUnconfirmed(before) || next.pending || before.input.scope !== next.input.scope || !uuid(writerId)
    || writerId === original.writerId || original.expectedRaw === null) throw new Error("The reviewed recipe fork is invalid.");
  const key = recipeDraftStorageKey(before.input.scope, original.writerId), raw = storage.getItem(key);
  if (raw === null || raw !== original.expectedRaw || JSON.stringify(decodeRecipeRecovery(raw, before.input.scope, before.input.group, original.writerId)) !== JSON.stringify(before))
    throw new Error("The earlier recipe attempt was not retained exactly.");
  const custody: RecipeCustody = { writerId, expectedRaw: null, expectedPointer: original.expectedPointer };
  writeRecipeRecovery(storage, session, before, custody);
  if (storage.getItem(key) !== raw) throw new Error("The earlier recipe copy changed during review.");
  writeRecipeRecovery(storage, session, next, custody);
  if (storage.getItem(key) !== raw) throw new Error("The earlier recipe copy changed during review.");
  return custody;
}

type RecipeSession = {
  state: RecipeAuthorState;
  custody: RecipeCustody;
  inFlight: boolean;
  backupError: string | null;
  copyWarning: string | null;
  error: string | null;
  message: string | null;
  copies: RecipeCopy[];
  limited: boolean;
  /** undefined raw means storage could not be read; deletion must wait for a fresh read. */
  unreadableOwn: { key: string; raw: string | null | undefined } | null;
  listeners: Set<() => void>;
  review: RecipeOutcomeReview | null;
  reviewing: boolean;
  reviewToken: number;
};
// A window retains buffers across workspace navigation even during a storage outage.
// Other windows have independent Maps and always fork their writable recovery key.
const sessions = new Map<string, RecipeSession>();
const explain = (failure: unknown) => failure instanceof Error ? failure.message : String(failure);
function notify(session: RecipeSession) {
  const owner = ownerPart(session.state.input.scope);
  for (const value of sessions.values()) if (ownerPart(value.state.input.scope) === owner)
    for (const listener of value.listeners) listener();
}
function projectBusy(project: Project): boolean {
  const owner = JSON.stringify([project.id, project.generation, project.active_branch]);
  return [...sessions.values()].some(session => session.inFlight && ownerPart(session.state.input.scope) === owner);
}
function refreshCopies(session: RecipeSession) {
  try {
    const result = readRecipeCopies(window.localStorage, session.state.input.scope, session.state.input.group);
    const own = recipeDraftStorageKey(session.state.input.scope, session.custody.writerId);
    session.copies = result.copies.filter(copy => copy.key !== own);
    session.limited = result.limited;
    session.copyWarning = result.limited ? "Only the first 64 scoped copies are listed. Original bytes were kept; explicitly discard an unwanted copy and refresh to see the remainder." : null;
  } catch (failure) { session.copyWarning = "Recipe copies could not be inspected. " + explain(failure); }
}
function getSession(project: Project, profileId: string, group: RecipeGroup): RecipeSession {
  const scope = recipeDraftScope(project, profileId, group), existing = sessions.get(scope);
  if (existing) return existing;
  let pointer: string | null = null, recovered: RecipeAuthorState | null = null;
  let backupError: string | null = null, unreadableOwn: RecipeSession["unreadableOwn"] = null;
  try {
    pointer = window.sessionStorage.getItem(recipePointerKey(scope));
    const navigation = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
    const ownReload = navigation?.type === "reload" && !window.opener;
    const legacy = ownReload && pointer === null ? window.sessionStorage.getItem(RECIPE_DRAFT_STORAGE_PREFIX + "writer") : null;
    const recoveredWriter = ownReload ? pointer ?? legacy : null;
    if (recoveredWriter) {
      const key = recipeDraftStorageKey(scope, recoveredWriter);
      let raw: string | null | undefined;
      try {
        raw = window.localStorage.getItem(key);
        if (raw === null) {
          // A legacy global pointer often has no draft for a clean scope.
          if (pointer !== null) throw new Error("This tab's recipe pointer references a missing copy.");
        } else recovered = decodeRecipeRecovery(raw, scope, group, recoveredWriter);
      } catch (failure) { unreadableOwn = { key, raw }; throw failure; }
    }
  } catch (failure) { backupError = explain(failure); }
  const session: RecipeSession = {
    state: recovered ? recoverRecipeAuthorState(recovered, project) : createRecipeAuthorState(project, profileId, group),
    custody: { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: pointer },
    inFlight: false, backupError, copyWarning: null, error: null,
    message: recovered ? (recipeHasWork(recovered)
      ? "Recovered this tab's recipe input. An uncertain save still requires a current saved-project and history review."
      : "Recovered this tab's clean checkpoint; it contains no pending author input.") : null,
    copies: [], limited: false, unreadableOwn, listeners: new Set(), review: null, reviewing: false, reviewToken: 0,
  };
  sessions.set(scope, session); refreshCopies(session);
  return session;
}
function forkWriter(session: RecipeSession) {
  session.custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: session.custody.expectedPointer };
}
function backup(session: RecipeSession): boolean {
  try {
    if (session.unreadableOwn) throw new Error("The earlier unreadable recipe copy must be reviewed or explicitly discarded before writing a new checkpoint.");
    if (session.custody.expectedRaw !== null) {
      const before = decodeRecipeRecovery(session.custody.expectedRaw, session.state.input.scope, session.state.input.group, session.custody.writerId);
      if (!recipeHasWork(before) && JSON.stringify(before) !== JSON.stringify(session.state)) forkWriter(session);
    }
    writeRecipeRecovery(window.localStorage, window.sessionStorage, session.state, session.custody);
    session.backupError = null; refreshCopies(session);
    return true;
  } catch (failure) {
    session.backupError = explain(failure) + " Current input remains in this window. Its recovery copy or writer pointer is unconfirmed; earlier copies were kept.";
    refreshCopies(session); return false;
  }
}
function replace(session: RecipeSession, state: RecipeAuthorState, persist = true): boolean {
  session.state = state;
  if (!recipeUnconfirmed(state)) session.review = null;
  const confirmed = persist ? backup(session) : true;
  notify(session); return confirmed;
}
function observedState(session: RecipeSession, project: Project): RecipeAuthorState {
  try { return observeRecipeAuthorState(session.state, project); }
  catch { return session.state; } // A removed profile cannot grant authority to another scope.
}

/** Both surfaces require the Project returned by their original throwing coordinator. */
export function useDeliveryRecipeDraft(project: Project, profileId: string, group: RecipeGroup,
  commit: (change: Change) => Promise<Project>) {
  const scope = recipeDraftScope(project, profileId, group), session = getSession(project, profileId, group);
  const [, redraw] = useState(0);
  const mounted = useRef(true);
  const live = useRef({ session, project, epoch: 0 });
  if (live.current.session !== session) {
    live.current.session.reviewToken++;
    live.current.session.reviewing = false;
    live.current.session.review = null;
    live.current = { session, project, epoch: live.current.epoch + 1 };
  } else live.current.project = project;
  const visible = observedState(session, project), canonical = savedDeliveryRecipe(project, profileId);
  const current = recipeFields(canonical, group);
  const values = { ...current, ...Object.fromEntries(Object.entries(visible.input.edits).map(([field, edit]) => [field, edit.value])) };
  const conflicts = recipeConflicts(visible.input, current), sourceBehind = visible.input.revision > project.revision;
  const locked = project.locks.some(lock => lock.kind === "content" && lock.resource === "project:" + project.id);
  const busy = projectBusy(project), unconfirmed = recipeUnconfirmed(visible);
  const dirty = Object.keys(visible.input.edits).length > 0;
  let validationError: string | null = null, different = false;
  try {
    const proposed = proposedDeliveryRecipe(canonical, visible.input);
    validateDeliveryRecipe(project, profileId, proposed);
    different = recipeFingerprint(proposed) !== recipeFingerprint(canonical);
  } catch (failure) { validationError = explain(failure); }
  const canSave = dirty && different && !busy && !locked && !sourceBehind && !unconfirmed
    && !session.backupError && !session.unreadableOwn && !validationError && conflicts.length === 0;
  const currentSession = () => mounted.current && live.current.session === session;

  useEffect(() => {
    mounted.current = true;
    const update = () => redraw(value => value + 1);
    session.listeners.add(update);
    const storageChanged = (event: StorageEvent) => {
      if (event.key === null || event.key.startsWith(RECIPE_DRAFT_STORAGE_PREFIX)) { refreshCopies(session); update(); }
    };
    window.addEventListener("storage", storageChanged);
    return () => {
      mounted.current = false;
      session.listeners.delete(update); session.reviewToken++; session.reviewing = false; session.review = null;
      window.removeEventListener("storage", storageChanged);
    };
  }, [session]);
  useEffect(() => {
    const next = observedState(session, project);
    if (next !== session.state) replace(session, next, recipeHasWork(next));
  }, [project, session]);

  const set = (field: string, raw: string) => {
    if (!currentSession() || sourceBehind || locked) return;
    try {
      const saved = recipeFields(savedDeliveryRecipe(live.current.project, profileId), group);
      const retainDecision = session.unreadableOwn !== null;
      let next = editRecipeAuthorState(observedState(session, live.current.project), field, raw, saved, retainDecision);
      if (group === "mix" && field !== "enabled") next = editRecipeAuthorState(next, "enabled", "true", saved, retainDecision);
      session.error = null; session.message = null; replace(session, next);
    } catch (failure) { session.error = explain(failure); notify(session); }
  };
  const resolve = (field: string, keep: "saved" | "draft") => {
    if (!currentSession() || projectBusy(live.current.project) || sourceBehind || recipeUnconfirmed(session.state)) return;
    try {
      const next = resolveRecipeAuthorField(session.state, live.current.project, field, keep);
      if (!backup(session)) { notify(session); return; }
      forkWriter(session); session.error = null; session.message = "Reviewed this field locally. Save a changed recipe explicitly.";
      replace(session, next);
    } catch (failure) { session.error = explain(failure); notify(session); }
  };
  const save = async () => {
    if (!currentSession() || projectBusy(live.current.project) || locked || session.backupError || session.unreadableOwn || recipeUnconfirmed(session.state)) return;
    const id = crypto.randomUUID();
    let change: Extract<Change, { type: "set_delivery_recipe" }>;
    try {
      const prepared = prepareRecipeSubmission(session.state, live.current.project, id);
      change = prepared.change; session.error = null; session.message = null; session.review = null;
      if (!replace(session, prepared.state)) {
        replace(session, retainRecipeFailure(session.state, id, session.backupError ?? "Local recipe recovery was not confirmed.", true), false);
        session.error = "NOT_SENT: confirm the recovery copy and its writer pointer before saving.";
        notify(session); return;
      }
    } catch (failure) { session.error = explain(failure); notify(session); return; }
    session.inFlight = true; notify(session);
    try {
      const receipt = await commit(change);
      if (session.state.pending?.id !== id) return;
      const acknowledged = acknowledgeRecipeOriginal(session.state, id, receipt);
      // An original ACK remains authoritative if a later local backup fails.
      // Keep the armed copy intact, so a reload can only recover UNKNOWN.
      forkWriter(session); session.error = null;
      session.message = "Saved recipe at revision r" + receipt.revision + "."
        + (recipeHasWork(acknowledged) ? " Your later typing is still retained." : "");
      replace(session, acknowledged);
    } catch (failure) {
      const diagnostic = explain(failure);
      session.error = diagnostic; replace(session, retainRecipeFailure(session.state, id, diagnostic));
    } finally { session.inFlight = false; notify(session); }
  };
  const readOutcome = async () => {
    if (!currentSession() || projectBusy(live.current.project) || session.reviewing || !recipeUnconfirmed(session.state)) return;
    const epoch = live.current.epoch, token = ++session.reviewToken;
    session.reviewing = true; session.review = null; session.error = null; notify(session);
    const stillCurrent = () => currentSession() && live.current.epoch === epoch && session.reviewToken === token;
    try {
      const { bootstrap, projectHistory } = await import("./api");
      const review = await inspectRecipeOutcome(session.state, live.current.project, {
        readProject: async id => {
          const result = await bootstrap(id);
          if (!result.project || result.project_selection.status !== "selected") throw new Error("The saved recipe project is unavailable.");
          return result.project;
        },
        readHistory: projectHistory,
      });
      if (!stillCurrent()) return;
      if (!recipeOutcomeMatches(session.state, live.current.project, review)) throw new Error("The selected recipe state changed during inspection. Read its current history again.");
      session.review = review;
    } catch (failure) { if (stillCurrent()) session.error = explain(failure); }
    finally { if (stillCurrent()) { session.reviewing = false; notify(session); } }
  };
  const continueAfterReview = () => {
    if (!currentSession() || projectBusy(live.current.project) || !session.review) return;
    try {
      const next = releaseRecipeOutcome(session.state, live.current.project, session.review);
      if (!backup(session)) { notify(session); return; }
      session.custody = retainReviewedRecipeIntent(window.localStorage, window.sessionStorage, session.state, next, session.custody, crypto.randomUUID());
      session.error = null; session.message = "Kept the earlier unconfirmed attempt as a separate copy. Review these inputs before a new explicit save.";
      replace(session, next, false); refreshCopies(session); notify(session);
    } catch (failure) { session.error = explain(failure); notify(session); }
  };
  const recoverCopy = (key: string) => {
    if (!currentSession() || projectBusy(live.current.project) || sourceBehind || session.unreadableOwn || recipeHasWork(session.state)) return;
    try {
      const copy = session.copies.find(copy => copy.key === key);
      if (!copy?.state || window.localStorage.getItem(key) !== copy.raw) throw new Error("That recovery copy changed. Refresh it before choosing Recover.");
      const recovered = decodeRecipeRecovery(copy.raw, scope, group, copy.writerId);
      if (!recipeHasWork(recovered)) throw new Error("This clean checkpoint has no pending author input to recover.");
      forkWriter(session); session.error = null;
      session.message = "Recovered a separate author copy. Its original bytes are retained; this operation did not change the project.";
      replace(session, recoverRecipeAuthorState(recovered, live.current.project));
    } catch (failure) { session.error = explain(failure); notify(session); }
  };
  const discardCopy = (key: string) => {
    if (!currentSession() || projectBusy(live.current.project)) return;
    try {
      const copy = session.copies.find(copy => copy.key === key);
      if (!copy || key === recipeDraftStorageKey(scope, session.custody.writerId) || window.localStorage.getItem(key) !== copy.raw)
        throw new Error("That separate copy changed. Refresh it before explicitly discarding it.");
      if (session.custody.expectedPointer === copy.writerId && !backup(session)) { notify(session); return; }
      if (window.localStorage.getItem(key) !== copy.raw) throw new Error("That copy changed while the current input was retained. It was kept.");
      window.localStorage.removeItem(key);
      if (window.localStorage.getItem(key) !== null) throw new Error("Removal of the selected recovery copy was not confirmed.");
      session.error = null; refreshCopies(session); notify(session);
    } catch (failure) { session.error = explain(failure); notify(session); }
  };
  const discardUnreadableCopy = () => {
    if (!currentSession() || projectBusy(live.current.project) || !session.unreadableOwn) return;
    try {
      const copy = session.unreadableOwn;
      if (copy.raw === undefined) throw new Error("Read this copy with Retry before deciding whether to discard it.");
      if (window.localStorage.getItem(copy.key) !== copy.raw) throw new Error("The unreadable copy changed. Retry the recovery read before discarding it.");
      window.localStorage.removeItem(copy.key);
      if (window.localStorage.getItem(copy.key) !== null) throw new Error("Removal of the unreadable copy was not confirmed.");
      session.unreadableOwn = null; session.error = null;
      backup(session); notify(session);
    } catch (failure) { session.error = explain(failure); notify(session); }
  };
  const retryBackup = () => {
    if (!currentSession() || projectBusy(live.current.project)) return;
    if (session.unreadableOwn) {
      try {
        const copy = session.unreadableOwn, raw = window.localStorage.getItem(copy.key);
        session.unreadableOwn = { ...copy, raw };
        if (raw === null) throw new Error("This tab's recipe pointer still references a missing copy.");
        const writerId = copy.key.slice((RECIPE_DRAFT_STORAGE_PREFIX + scope + ":").length);
        const recovered = decodeRecipeRecovery(raw, scope, group, writerId);
        const next = recoverRecipeAuthorState(recovered, live.current.project, session.state);
        if (window.localStorage.getItem(copy.key) !== raw) throw new Error("The earlier recipe copy changed during Retry. Its bytes were kept.");
        session.unreadableOwn = null; forkWriter(session); session.error = null;
        session.message = "Recovered the earlier recipe copy and retained typing from the read failure. Its original bytes remain separate; any uncertain save still requires review.";
        replace(session, next); return;
      } catch (failure) { session.backupError = explain(failure); notify(session); return; }
    }
    backup(session); notify(session);
  };
  const clearGroup = () => {
    if (!currentSession() || projectBusy(live.current.project) || locked || sourceBehind || session.unreadableOwn || recipeHasWork(session.state)) return;
    try {
      let next = createRecipeAuthorState(live.current.project, profileId, group);
      const saved = recipeFields(savedDeliveryRecipe(live.current.project, profileId), group), empty = recipeFields(null, group);
      for (const field of new Set([...Object.keys(saved), ...Object.keys(empty)])) next = editRecipeAuthorState(next, field, empty[field] ?? "", saved);
      session.error = null; session.message = "Clearing is pending. Save this recipe change explicitly.";
      replace(session, next);
    } catch (failure) { session.error = explain(failure); notify(session); }
  };
  const download = () => {
    if (!currentSession()) return;
    try {
      // Author input only: no effect grant, execution ID, rights attestation, or replay.
      const state = structuredClone(session.state);
      if (state.pending) state.pending.outcome = "UNKNOWN";
      const blob = new Blob([JSON.stringify({ schema: "motionwright.recipe-author-file/1", scope, exportedAt: new Date().toISOString(), state }, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob), anchor = document.createElement("a");
      anchor.href = url; anchor.download = "motionwright-recipe-" + group + "-" + Date.now() + ".json";
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
      session.message = "Started downloading this recipe's author input. Verify the file was saved before leaving this window.";
      notify(session);
    } catch (failure) { session.error = explain(failure); notify(session); }
  };
  const error = [...new Set([session.error, session.backupError, session.copyWarning, dirty ? validationError : null]
    .filter((value): value is string => Boolean(value)))].join(" ") || null;
  return {
    values, savedValues: current, edits: visible.input.edits, conflicts, dirty, awaiting: visible.pending !== null,
    pending: busy, locked, sourceBehind, error, message: session.message, canSave,
    recoveryBlocked: session.backupError !== null || session.unreadableOwn !== null,
    unreadableCopy: session.unreadableOwn !== null,
    unconfirmed, notSent: visible.pending?.outcome === "NOT_SENT", reviewing: session.reviewing,
    review: recipeOutcomeMatches(visible, project, session.review) ? session.review : null,
    set, resolve, save, readOutcome, continueAfterReview, retryBackup, download,
    copies: session.copies, limited: session.limited,
    refreshCopies: () => { refreshCopies(session); notify(session); },
    recoverCopy, discardCopy, clearGroup, discardUnreadableCopy,
  };
}
