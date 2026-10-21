import type { MotionCanvasArchetype, MotionCanvasNarrativeRole, Project } from "./types";

export interface DeliverySceneIntent {
  scene_id: string;
  role: MotionCanvasNarrativeRole | null;
  archetype: MotionCanvasArchetype | null;
}
export interface OriginalMixRecipe {
  music_asset_id: string | null;
  sfx_asset_id: string | null;
  sfx_gain_db: number;
  duck_attenuation_db: number;
  duck_attack_samples: number;
  duck_release_samples: number;
}
export interface DeliveryRecipe {
  schema: "motionwright.delivery-recipe/1";
  scene_intents: DeliverySceneIntent[];
  original_mix: OriginalMixRecipe | null;
}
export type RecipeGroup = "film" | "mix";
export const deliveryRoles: MotionCanvasNarrativeRole[] = ["hook", "problem", "mechanism", "evidence", "comparison", "reveal", "payoff", "cta"];
export const deliveryArchetypes: MotionCanvasArchetype[] = ["statement", "split_explanation", "architecture_reveal", "comparison", "metric", "timeline", "code_focus", "diagram_build", "object_spotlight", "evidence_frame", "product_proof", "endcard"];
export const emptyDeliveryRecipe = (): DeliveryRecipe => ({ schema: "motionwright.delivery-recipe/1", scene_intents: [], original_mix: null });
export const defaultOriginalMix = (): OriginalMixRecipe => ({ music_asset_id: null, sfx_asset_id: null, sfx_gain_db: -8, duck_attenuation_db: 14, duck_attack_samples: 2400, duck_release_samples: 4800 });
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const numberFields = ["sfx_gain_db", "duck_attenuation_db", "duck_attack_samples", "duck_release_samples"] as const;
const decimal = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
const mixKeys = ["enabled", "music_asset_id", "sfx_asset_id", ...numberFields] as const;
const ownKeys = (value: unknown, keys: readonly string[], label: string): Record<string, unknown> => {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid " + label + ".");
  const record = value as Record<string, unknown>;
  if (Object.keys(record).length !== keys.length || keys.some(key => !Object.hasOwn(record, key)) || Object.keys(record).some(key => !keys.includes(key))) throw new Error("Unknown or missing " + label + " fields.");
  return record;
};

/** Mirrors the typed wire for UI feedback. The Rust service remains canonical. */
export function validateDeliveryRecipe(project: Project, profileId: string, candidate: unknown): asserts candidate is DeliveryRecipe {
  if (!uuid.test(profileId) || !project.deliverables.some(profile => profile.id.toLowerCase() === profileId.toLowerCase())) throw new Error("The delivery profile no longer exists.");
  const recipe = ownKeys(candidate, ["schema", "scene_intents", "original_mix"], "delivery recipe");
  if (recipe.schema !== "motionwright.delivery-recipe/1") throw new Error("Unsupported delivery recipe schema.");
  if (!Array.isArray(recipe.scene_intents) || recipe.scene_intents.length > 4096) throw new Error("Delivery scene intent is outside the bounded collection.");
  const seen = new Set<string>();
  for (const value of recipe.scene_intents) {
    const intent = ownKeys(value, ["scene_id", "role", "archetype"], "scene intent");
    if (typeof intent.scene_id !== "string" || !uuid.test(intent.scene_id) || seen.has(intent.scene_id.toLowerCase()) || !project.scenes.some(scene => scene.id.toLowerCase() === (intent.scene_id as string).toLowerCase())) throw new Error("Recipe scene is missing or duplicated.");
    seen.add(intent.scene_id.toLowerCase());
    if (intent.role !== null && !deliveryRoles.includes(intent.role as MotionCanvasNarrativeRole)) throw new Error("Unknown narrative role.");
    if (intent.archetype !== null && !deliveryArchetypes.includes(intent.archetype as MotionCanvasArchetype)) throw new Error("Unknown visual archetype.");
  }
  if (recipe.original_mix === null) return;
  const mix = ownKeys(recipe.original_mix, ["music_asset_id", "sfx_asset_id", ...numberFields], "original mix");
  for (const key of ["music_asset_id", "sfx_asset_id"] as const) {
    const id = mix[key];
    if (id !== null && (typeof id !== "string" || !uuid.test(id) || !project.assets.some(asset => asset.id.toLowerCase() === id.toLowerCase() && asset.media_type.startsWith("audio/") && /^[a-f0-9]{64}$/.test(asset.content_sha256 ?? "")))) throw new Error("The selected " + key.replaceAll("_", " ") + " is not a current hashed audio source.");
  }
  if (typeof mix.music_asset_id === "string" && typeof mix.sfx_asset_id === "string" && mix.music_asset_id.toLowerCase() === mix.sfx_asset_id.toLowerCase()) throw new Error("Music and SFX require distinct source assets.");
  for (const key of numberFields) if (typeof mix[key] !== "number" || !Number.isFinite(mix[key])) throw new Error("Mix settings must be finite numbers.");
  if ((mix.sfx_gain_db as number) < -80 || (mix.sfx_gain_db as number) > 12) throw new Error("SFX gain must be between -80 and 12 dB.");
  if ((mix.duck_attenuation_db as number) < 0 || (mix.duck_attenuation_db as number) > 30) throw new Error("Duck attenuation must be between 0 and 30 dB.");
  for (const key of ["duck_attack_samples", "duck_release_samples"] as const) if (!Number.isInteger(mix[key]) || (mix[key] as number) < 1 || (mix[key] as number) > 48000) throw new Error("Attack and release must be whole samples between 1 and 48000.");
}

export function savedDeliveryRecipe(project: Project, profileId: string): DeliveryRecipe | null {
  return project.production_design?.delivery_recipes?.[profileId] ?? null;
}
export function recipeFields(recipe: DeliveryRecipe | null, group: RecipeGroup): Record<string, string> {
  if (group === "film") return Object.fromEntries((recipe?.scene_intents ?? []).flatMap(intent => [
    [intent.scene_id + ":role", intent.role ?? ""], [intent.scene_id + ":archetype", intent.archetype ?? ""],
  ]));
  const mix = recipe?.original_mix ?? defaultOriginalMix();
  return { enabled: recipe?.original_mix ? "true" : "false", music_asset_id: mix.music_asset_id ?? "", sfx_asset_id: mix.sfx_asset_id ?? "", ...Object.fromEntries(numberFields.map(key => [key, String(mix[key])])) };
}
export interface RecipeEdit { base: string; value: string; intent?: true }
export interface RecipeDraft {
  version: 1;
  scope: string;
  group: RecipeGroup;
  revision: number;
  edits: Record<string, RecipeEdit>;
  awaiting?: { revision: number; fields: Record<string, string> };
}
export const newRecipeDraft = (scope: string, group: RecipeGroup, revision: number): RecipeDraft => ({ version: 1, scope, group, revision, edits: {} });
export function editRecipeField(draft: RecipeDraft, key: string, value: string, current: Record<string, string>): RecipeDraft {
  if (!validField(draft.group, key) || value.length > 128) throw new Error("Recipe input is outside the bounded draft.");
  const next = structuredClone(draft);
  const old = next.edits[key];
  if (value === (current[key] ?? "") && !old?.intent) delete next.edits[key];
  else next.edits[key] = { base: old?.base ?? current[key] ?? "", value, ...(old?.intent ? { intent: true as const } : {}) };
  return next;
}
/** Record authored input, never a grant or proof that the request committed. */
export function submitRecipeDraft(draft: RecipeDraft, revision: number): RecipeDraft {
  if (!Number.isSafeInteger(revision) || revision < draft.revision) throw new Error("The saved project is behind this draft.");
  if (draft.awaiting) throw new Error("Review the unconfirmed recipe outcome before a separate intent.");
  if (!Object.keys(draft.edits).length) throw new Error("There is no edited recipe input to save.");
  const next = structuredClone(draft);
  next.revision = revision;
  for (const edit of Object.values(next.edits)) edit.intent = true;
  next.awaiting = { revision, fields: Object.fromEntries(Object.entries(next.edits).map(([key, edit]) => [key, edit.value])) };
  return next;
}
export function canonicalRecipeField(group: RecipeGroup, key: string, raw: string): string {
  if (group === "mix" && (numberFields as readonly string[]).includes(key) && decimal.test(raw.trim()) && Number.isFinite(Number(raw))) return String(Number(raw));
  return raw;
}
/** A current value can agree with a draft without proving which request wrote it.
 * This legacy input projection never acknowledges a submission. The original
 * Project receipt is handled by the authority-aware coordinator, not by props.
 */
export function reconcileRecipeDraft(draft: RecipeDraft, current: Record<string, string>, revision: number): RecipeDraft {
  const next = structuredClone(draft);
  if (revision < draft.revision) return next;
  if (!next.awaiting) for (const [key, edit] of Object.entries(next.edits)) {
    if (edit.value === (current[key] ?? "") && !edit.intent) delete next.edits[key];
  }
  next.revision = revision;
  return next;
}
export function recipeConflicts(draft: RecipeDraft, current: Record<string, string>): string[] {
  return Object.entries(draft.edits).filter(([key, edit]) => (current[key] ?? "") !== edit.base && (current[key] ?? "") !== canonicalRecipeField(draft.group, key, edit.value)).map(([key]) => key);
}
export function resolveRecipeField(draft: RecipeDraft, key: string, current: Record<string, string>, keep: "saved" | "draft"): RecipeDraft {
  if (draft.awaiting) throw new Error("Review the unconfirmed recipe outcome before resolving its input.");
  const next = structuredClone(draft);
  if (keep === "saved") delete next.edits[key];
  else if (next.edits[key]) next.edits[key] = { base: current[key] ?? "", value: next.edits[key].value };
  return next;
}
export function proposedDeliveryRecipe(current: DeliveryRecipe | null, draft: RecipeDraft): DeliveryRecipe {
  const fields = { ...recipeFields(current, draft.group), ...Object.fromEntries(Object.entries(draft.edits).map(([key, value]) => [key, value.value])) };
  const recipe = structuredClone(current ?? emptyDeliveryRecipe());
  if (draft.group === "film") {
    const ids = new Set(Object.keys(fields).map(key => key.split(":")[0]));
    recipe.scene_intents = [...ids].sort().flatMap(scene_id => {
      const role = fields[scene_id + ":role"] || null;
      const archetype = fields[scene_id + ":archetype"] || null;
      return role || archetype ? [{ scene_id, role: role as MotionCanvasNarrativeRole | null, archetype: archetype as MotionCanvasArchetype | null }] : [];
    });
  } else if (fields.enabled !== "true" && fields.enabled !== "false") throw new Error("Choose whether the original mix recipe is enabled.");
  else if (fields.enabled === "false") recipe.original_mix = null;
  else {
    for (const key of numberFields) if (!decimal.test(fields[key]?.trim() ?? "")) throw new Error("Enter a decimal value for " + key.replaceAll("_", " ") + ".");
    recipe.original_mix = { music_asset_id: fields.music_asset_id || null, sfx_asset_id: fields.sfx_asset_id || null, sfx_gain_db: Number(fields.sfx_gain_db), duck_attenuation_db: Number(fields.duck_attenuation_db), duck_attack_samples: Number(fields.duck_attack_samples), duck_release_samples: Number(fields.duck_release_samples) };
  }
  return recipe;
}
function validField(group: RecipeGroup, key: string): boolean {
  return group === "mix" ? (mixKeys as readonly string[]).includes(key) : /^[a-f0-9-]{36}:(role|archetype)$/.test(key) && uuid.test(key.split(":")[0]);
}
export const RECIPE_DRAFT_STORAGE_PREFIX = "motionwright.delivery-recipe-draft/1:";
const prefix = RECIPE_DRAFT_STORAGE_PREFIX;
export const recipeDraftScope = (project: Project, profileId: string, group: RecipeGroup): string => JSON.stringify([project.id, project.generation, project.active_branch, profileId, group]);
export const recipeDraftStorageKey = (scope: string, writerId: string): string => prefix + scope + ":" + writerId;
export interface RecipeDraftRecovery {
  draft: RecipeDraft;
  blocked: string | null;
  warning: string | null;
  /** An inaccessible original is never overwritten on a later retry. */
  unverified?: true;
}
const memory = new Map<string, RecipeDraftRecovery>();
let writer: string | undefined;
export function recipeDraftWriterId(): string {
  if (writer) return writer;
  try {
    const stored = sessionStorage.getItem(prefix + "writer");
    const navigation = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
    // A new window can inherit sessionStorage. History traversal alone does not
    // establish continuity; only a reload without an opener reuses this pointer.
    const ownReload = navigation?.type === "reload" && (typeof window === "undefined" || !window.opener);
    writer = ownReload && stored && uuid.test(stored) ? stored : crypto.randomUUID();
    sessionStorage.setItem(prefix + "writer", writer);
  } catch { writer = crypto.randomUUID(); }
  return writer;
}
function record(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}
function boundedFieldMap(value: unknown, group: RecipeGroup, edits: boolean): boolean {
  if (!record(value) || Object.keys(value).length > 8192) return false;
  const seen = new Set<string>();
  return Object.entries(value).every(([key, field]) => {
    if (!validField(group, key) || seen.has(key.toLowerCase())) return false;
    seen.add(key.toLowerCase());
    if (!edits) return typeof field === "string" && field.length <= 128;
    return record(field) && Object.hasOwn(field, "base") && Object.hasOwn(field, "value") && typeof field.base === "string" && typeof field.value === "string" && field.base.length <= 128 && field.value.length <= 128 && Object.keys(field).every(name => ["base", "value", "intent"].includes(name)) && (!Object.hasOwn(field, "intent") || field.intent === true);
  });
}
export function decodeRecipeDraft(raw: string, scope: string, group: RecipeGroup): RecipeDraft {
  if (new TextEncoder().encode(raw).length > 256 * 1024) throw new Error("Recipe recovery copy exceeds 256 KiB.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || (group !== "film" && group !== "mix") || value.version !== 1 || value.scope !== scope || value.group !== group || !Number.isSafeInteger(value.revision) || (value.revision as number) < 0 || !boundedFieldMap(value.edits, group, true)) throw new Error("Recipe recovery copy has an unsupported identity or format.");
  if (Object.keys(value).some(key => !["version", "scope", "group", "revision", "edits", "awaiting"].includes(key))) throw new Error("Recipe recovery copy contains unknown fields.");
  if (Object.hasOwn(value, "awaiting")) {
    const awaiting = value.awaiting;
    if (!record(awaiting) || Object.keys(awaiting).length !== 2 || !Number.isSafeInteger(awaiting.revision) || (awaiting.revision as number) < 0 || (awaiting.revision as number) > (value.revision as number) || !boundedFieldMap(awaiting.fields, group, false) || !Object.keys(awaiting.fields as object).length || Object.keys(awaiting).some(key => !["revision", "fields"].includes(key))) throw new Error("Recipe recovery copy has an invalid pending intent.");
  }
  return value as unknown as RecipeDraft;
}
const backupWarning = (failure: unknown): string => "The draft is kept in this window, but its recovery copy could not be saved: " + String(failure);
export function loadRecipeDraft(key: string, scope: string, group: RecipeGroup, revision: number): RecipeDraftRecovery {
  const live = memory.get(key);
  if (live) return structuredClone(live);
  let raw: string | null;
  let entry: RecipeDraftRecovery = { draft: newRecipeDraft(scope, group, revision), blocked: null, warning: null };
  try { raw = localStorage.getItem(key); }
  catch (failure) {
    entry = { ...entry, warning: "Recipe recovery storage could not be accessed. Input can still be edited in this window; project dispatch requires a verified recovery checkpoint. " + String(failure), unverified: true };
    memory.set(key, structuredClone(entry));
    return entry;
  }
  if (raw !== null) {
    try { entry.draft = decodeRecipeDraft(raw, scope, group); }
    catch (failure) { entry.blocked = "The stored recipe draft could not be read. Its original copy is retained. " + String(failure); }
  }
  memory.set(key, structuredClone(entry));
  return entry;
}
export function persistRecipeDraft(key: string, draft: RecipeDraft): RecipeDraftRecovery {
  const entry: RecipeDraftRecovery = { ...(memory.get(key) ?? { blocked: null, warning: null }), draft: structuredClone(draft) };
  // Always retain new input, including while the original copy is unreadable.
  memory.set(key, structuredClone(entry));
  if (entry.blocked) return entry;
  try {
    if (entry.unverified) {
      if (localStorage.getItem(key) !== null) throw new Error("The earlier inaccessible copy is retained. Keep a separate verified copy of current input before reloading this window.");
      delete entry.unverified;
    }
    if (!Object.keys(draft.edits).length && !draft.awaiting) {
      localStorage.removeItem(key);
      if (localStorage.getItem(key) !== null) throw new Error("The recipe recovery removal was not confirmed.");
    }
    else {
      const raw = JSON.stringify(draft);
      decodeRecipeDraft(raw, draft.scope, draft.group);
      let total = 0, count = 0;
      for (let index = 0; index < localStorage.length; index++) {
        const other = localStorage.key(index);
        if (other?.startsWith(prefix) && other !== key) { total += new TextEncoder().encode(localStorage.getItem(other) ?? "").length; count++; }
      }
      if (total + new TextEncoder().encode(raw).length > 2 * 1024 * 1024 || count >= 64) throw new Error("Recipe recovery copies reached their 64-copy / 2 MiB budget.");
      localStorage.setItem(key, raw);
      if (localStorage.getItem(key) !== raw) throw new Error("The recipe recovery write was not confirmed.");
    }
    entry.warning = null;
  } catch (failure) { entry.warning = backupWarning(failure); }
  memory.set(key, structuredClone(entry));
  return structuredClone(entry);
}
export function discardUnreadableRecipeDraft(key: string, scope: string, group: RecipeGroup, revision: number): RecipeDraftRecovery {
  const entry = loadRecipeDraft(key, scope, group, revision);
  if (!entry.blocked) return entry;
  // Removing the explicitly rejected copy must not discard later in-memory input.
  localStorage.removeItem(key);
  entry.blocked = null;
  entry.warning = null;
  delete entry.unverified;
  memory.set(key, entry);
  return persistRecipeDraft(key, entry.draft);
}
export interface RecipeRecoveryCopy { key: string; revision: number | null; fields: number; readable: boolean }
const scopedCopyKey = (scope: string, key: string): boolean => key.startsWith(prefix + scope + ":") && uuid.test(key.slice((prefix + scope + ":").length));
export function readRecipeRecoveryCopies(scope: string, group: RecipeGroup, ownKey: string): { copies: RecipeRecoveryCopy[]; warning: string | null } {
  const copies: RecipeRecoveryCopy[] = [];
  try {
    for (let index = 0; index < localStorage.length; index++) {
      const key = localStorage.key(index);
      if (!key || !scopedCopyKey(scope, key) || key === ownKey) continue;
      const raw = localStorage.getItem(key);
      if (raw === null) continue;
      try { const draft = decodeRecipeDraft(raw, scope, group); copies.push({ key, revision: draft.revision, fields: Object.keys(draft.edits).length, readable: true }); }
      catch { copies.push({ key, revision: null, fields: 0, readable: false }); }
    }
    return { copies: copies.sort((a, b) => (b.revision ?? -1) - (a.revision ?? -1)), warning: null };
  } catch (failure) { return { copies, warning: "Available recipe recovery copies could not be read. Refresh to retry. " + String(failure) }; }
}
export function recoverRecipeDraftCopy(key: string, copyKey: string, scope: string, group: RecipeGroup, current: Record<string, string>, revision: number): RecipeDraftRecovery {
  const own = loadRecipeDraft(key, scope, group, revision);
  if (own.blocked || own.draft.awaiting || Object.keys(own.draft.edits).length) throw new Error("Resolve this window's recipe input before recovering another copy.");
  if (!scopedCopyKey(scope, copyKey) || copyKey === key) throw new Error("Choose a separate recipe copy in this project, branch and profile.");
  const recovered = decodeRecipeDraft(localStorage.getItem(copyKey) ?? "", scope, group);
  // The source stays byte-for-byte intact; the recovered draft has no authority
  // over the current project and must pass conflict review and a new save.
  return persistRecipeDraft(key, reconcileRecipeDraft(recovered, current, revision));
}
export function discardRecipeDraftCopy(scope: string, ownKey: string, copyKey: string): void {
  if (!scopedCopyKey(scope, copyKey) || ownKey === copyKey) throw new Error("Choose a separate recipe copy in this project, branch and profile.");
  localStorage.removeItem(copyKey);
  memory.delete(copyKey);
}

// The authority-aware hook consumes only the original Project receipt.
export { useDeliveryRecipeDraft } from "./deliveryRecipeDraft";
