import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fixtureProject } from "./fixture";
import type { Project } from "./types";
import type { DeliveryRecipe, RecipeDraft, RecipeGroup } from "./deliveryRecipe";

let recipe: typeof import("./deliveryRecipe");
const id = (value: number) => "abcdef00-0000-4000-8000-" + value.toString(16).padStart(12, "0");
const writerA = id(1001), writerB = id(1002);

/** Storage faults are isolated unit fixtures, not service or native evidence. */
class StorageFixture implements Storage {
  readonly entries = new Map<string, string>();
  fail: "read" | "write" | "remove" | "scan" | null = null;
  get length() { if (this.fail === "scan") throw new Error("storage scan denied"); return this.entries.size; }
  clear() { this.entries.clear(); }
  key(index: number) { if (this.fail === "scan") throw new Error("storage scan denied"); return [...this.entries.keys()][index] ?? null; }
  getItem(key: string) { if (this.fail === "read") throw new Error("storage read denied"); return this.entries.get(key) ?? null; }
  setItem(key: string, value: string) { if (this.fail === "write") throw new Error("storage quota denied"); this.entries.set(key, value); }
  removeItem(key: string) { if (this.fail === "remove") throw new Error("storage remove denied"); this.entries.delete(key); }
}
let local: StorageFixture, session: StorageFixture;

function sample() {
  const project: Project = structuredClone(fixtureProject);
  project.revision = 20;
  project.locks = [];
  const profileA = project.deliverables[0].id, profileB = id(2001);
  project.deliverables.push({ ...structuredClone(project.deliverables[0]), id: profileB });
  const sceneA = project.scenes[0].id, sceneB = project.scenes[1].id;
  const music = id(3001), sfx = id(3002);
  project.assets.push(
    { id: music, name: "unit music", media_type: "audio/wav", content_sha256: "a".repeat(64), source_revision: null },
    { id: sfx, name: "unit SFX", media_type: "audio/wav", content_sha256: "b".repeat(64), source_revision: null },
  );
  const canonical: DeliveryRecipe = {
    schema: "motionwright.delivery-recipe/1",
    scene_intents: [
      { scene_id: sceneA, role: "hook", archetype: "statement" },
      { scene_id: sceneB, role: "evidence", archetype: "evidence_frame" },
    ],
    original_mix: { ...recipe.defaultOriginalMix(), music_asset_id: music, sfx_asset_id: sfx },
  };
  project.production_design = { plan: null, heroes: [], capsules: [], delivery_recipes: { [profileA]: structuredClone(canonical), [profileB]: recipe.emptyDeliveryRecipe() } };
  return { project, profileA, profileB, sceneA, sceneB, music, sfx, canonical };
}
function context(group: RecipeGroup = "film") {
  const fixture = sample();
  const scope = recipe.recipeDraftScope(fixture.project, fixture.profileA, group);
  return { ...fixture, group, scope, key: recipe.recipeDraftStorageKey(scope, writerA), current: recipe.recipeFields(fixture.canonical, group), draft: recipe.newRecipeDraft(scope, group, fixture.project.revision) };
}
function changed(draft: RecipeDraft, field: string, value: string, current: Record<string, string>) {
  return recipe.editRecipeField(draft, field, value, current);
}

beforeEach(async () => {
  vi.resetModules();
  local = new StorageFixture();
  session = new StorageFixture();
  vi.stubGlobal("localStorage", local);
  vi.stubGlobal("sessionStorage", session);
  vi.stubGlobal("window", { opener: null });
  vi.spyOn(performance, "getEntriesByType").mockReturnValue([{ type: "navigate" } as PerformanceNavigationTiming]);
  recipe = await import("./deliveryRecipe");
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("recipe input review and observed canonical revisions", () => {
  it("merges a film edit with the latest saved mix and untouched scene fields without changing the project or another profile", () => {
    const { project, profileA, profileB, canonical, current, draft, sceneA, sceneB } = context();
    const before = structuredClone(project);
    const pending = changed(draft, sceneA + ":role", "reveal", current);
    const peer = structuredClone(canonical);
    peer.original_mix!.sfx_gain_db = -13;
    peer.scene_intents[1].archetype = "metric";
    const reconciled = recipe.reconcileRecipeDraft(pending, recipe.recipeFields(peer, "film"), project.revision + 1);
    expect(recipe.recipeConflicts(reconciled, recipe.recipeFields(peer, "film"))).toEqual([]);
    const proposed = recipe.proposedDeliveryRecipe(peer, reconciled);
    recipe.validateDeliveryRecipe(project, profileA, proposed);
    expect(proposed.scene_intents.find(intent => intent.scene_id === sceneA)?.role).toBe("reveal");
    expect(proposed.scene_intents.find(intent => intent.scene_id === sceneB)?.archetype).toBe("metric");
    expect(proposed.original_mix).toEqual(peer.original_mix);
    expect(recipe.savedDeliveryRecipe(project, profileB)).toEqual(recipe.emptyDeliveryRecipe());
    expect(project).toEqual(before);
  });

  it("preserves scene intent when editing the mix and leaves global voice/music gains outside the recipe", () => {
    const { project, profileA, canonical, current, draft } = context("mix");
    const globalMix = structuredClone(project.audio.mix);
    const pending = changed(draft, "sfx_gain_db", "-11.5", current);
    const peer = structuredClone(canonical);
    peer.scene_intents[0].role = "payoff";
    const proposed = recipe.proposedDeliveryRecipe(peer, pending);
    recipe.validateDeliveryRecipe(project, profileA, proposed);
    expect(proposed.scene_intents).toEqual(peer.scene_intents);
    expect(proposed.original_mix?.sfx_gain_db).toBe(-11.5);
    expect(project.audio.mix).toEqual(globalMix);
    expect(Object.keys(proposed.original_mix!).sort()).toEqual(["duck_attack_samples", "duck_attenuation_db", "duck_release_samples", "music_asset_id", "sfx_asset_id", "sfx_gain_db"]);
  });

  it("reports only overlapping fields and resolves each field explicitly against the latest saved base", () => {
    const { draft, current, sceneA, sceneB } = context();
    let pending = changed(draft, sceneA + ":role", "reveal", current);
    pending = changed(pending, sceneB + ":role", "comparison", current);
    const peer = { ...current, [sceneA + ":role"]: "problem", [sceneB + ":archetype"]: "metric" };
    pending = recipe.reconcileRecipeDraft(pending, peer, 21);
    expect(recipe.recipeConflicts(pending, peer)).toEqual([sceneA + ":role"]);
    const keep = recipe.resolveRecipeField(pending, sceneA + ":role", peer, "draft");
    expect(keep.edits[sceneA + ":role"]).toEqual({ base: "problem", value: "reveal" });
    expect(recipe.recipeConflicts(keep, peer)).toEqual([]);
    const saved = recipe.resolveRecipeField(pending, sceneA + ":role", peer, "saved");
    expect(saved.edits[sceneA + ":role"]).toBeUndefined();
    expect(saved.edits[sceneB + ":role"].value).toBe("comparison");
    expect(pending.edits[sceneA + ":role"].base).toBe("hook");
  });

  it("does not use a returned callback or same-revision value as proof that authored input was saved", () => {
    const { draft, current, sceneA } = context();
    const field = sceneA + ":role";
    const sent = recipe.submitRecipeDraft(changed(draft, field, "reveal", current), 20);
    const unseen = recipe.reconcileRecipeDraft(sent, current, 20);
    expect(unseen.edits[field]).toEqual({ base: "hook", value: "reveal", intent: true });
    expect(unseen.awaiting).toEqual({ revision: 20, fields: { [field]: "reveal" } });
    const sameRevision = recipe.reconcileRecipeDraft(sent, { ...current, [field]: "reveal" }, 20);
    expect(sameRevision.edits[field]?.value).toBe("reveal");
    expect(sameRevision.awaiting).toBeDefined();
    const observed = recipe.reconcileRecipeDraft(sent, { ...current, [field]: "reveal" }, 21);
    expect(observed.edits).toEqual({});
    expect(observed.awaiting).toBeUndefined();
  });

  it("acknowledges only submitted fields when an unrelated scene or the other group changes", () => {
    const { draft, current, sceneA, sceneB } = context();
    const field = sceneA + ":role";
    const sent = recipe.submitRecipeDraft(changed(draft, field, "reveal", current), 20);
    expect(Object.keys(sent.awaiting!.fields)).toEqual([field]);
    const peer = { ...current, [field]: "reveal", [sceneB + ":archetype"]: "metric" };
    const observed = recipe.reconcileRecipeDraft(sent, peer, 22);
    expect(observed.edits).toEqual({});
    expect(observed.awaiting).toBeUndefined();
  });

  it("keeps later keystrokes across an observed save, requiring explicit conflict review before another save", () => {
    const { draft, current, sceneA } = context();
    const field = sceneA + ":role";
    const sent = recipe.submitRecipeDraft(changed(draft, field, "reveal", current), 20);
    const later = changed(sent, field, "payoff", current);
    const observed = recipe.reconcileRecipeDraft(later, { ...current, [field]: "reveal" }, 21);
    expect(observed.edits[field]).toEqual({ base: "hook", value: "payoff", intent: true });
    expect(observed.awaiting).toBeUndefined();
    expect(recipe.recipeConflicts(observed, { ...current, [field]: "reveal" })).toEqual([field]);
  });

  it("preserves Old → Sent → Old through remount, an unrelated revision and eventual observed Sent", async () => {
    const { draft, current, sceneA, key, scope } = context();
    const field = sceneA + ":role";
    recipe.loadRecipeDraft(key, scope, "film", 20);
    const sent = recipe.submitRecipeDraft(changed(draft, field, "reveal", current), 20);
    recipe.persistRecipeDraft(key, sent);
    const remounted = recipe.loadRecipeDraft(key, scope, "film", 20).draft;
    const backToOld = changed(remounted, field, "hook", current);
    recipe.persistRecipeDraft(key, backToOld);
    const raw = local.getItem(key)!;
    expect(JSON.parse(raw).edits[field]).toEqual({ base: "hook", value: "hook", intent: true });
    vi.resetModules();
    recipe = await import("./deliveryRecipe");
    const reloaded = recipe.loadRecipeDraft(key, scope, "film", 21).draft;
    const unrelated = recipe.reconcileRecipeDraft(reloaded, current, 21);
    expect(unrelated.edits[field]?.value).toBe("hook");
    expect(unrelated.awaiting?.fields[field]).toBe("reveal");
    const savedSent = { ...current, [field]: "reveal" };
    const observed = recipe.reconcileRecipeDraft(unrelated, savedSent, 22);
    expect(observed.edits[field]?.value).toBe("hook");
    expect(recipe.recipeConflicts(observed, savedSent)).toEqual([field]);
    const resolved = recipe.resolveRecipeField(observed, field, savedSent, "draft");
    expect(resolved.edits[field]).toEqual({ base: "reveal", value: "hook" });
    const resubmitted = recipe.submitRecipeDraft(resolved, 22);
    const savedOld = recipe.reconcileRecipeDraft(resubmitted, current, 23);
    expect(savedOld.edits).toEqual({});
    expect(savedOld.awaiting).toBeUndefined();
    expect(local.getItem(key)).toBe(raw); // Pure readback never auto-submits or writes.
  });

  it("preserves a raw number through recovery and clears it only when its submitted canonical number is observed", () => {
    const { draft, current, scope } = context("mix");
    const raw = "  -04.500e0  ";
    const sent = recipe.submitRecipeDraft(changed(draft, "sfx_gain_db", raw, current), 20);
    const recovered = recipe.decodeRecipeDraft(JSON.stringify(sent), scope, "mix");
    expect(recovered.edits.sfx_gain_db.value).toBe(raw);
    const observed = recipe.reconcileRecipeDraft(recovered, { ...current, sfx_gain_db: "-4.5" }, 21);
    expect(observed.edits).toEqual({});
    expect(observed.awaiting).toBeUndefined();
    const later = changed(recovered, "sfx_gain_db", "-6", current);
    expect(recipe.reconcileRecipeDraft(later, { ...current, sfx_gain_db: "-4.5" }, 21).edits.sfx_gain_db.value).toBe("-6");
    expect(recipe.recipeConflicts(recovered, { ...current, sfx_gain_db: "-4.5" })).toEqual([]);
  });

  it("never reconciles backwards or submits against a project older than the observed draft", () => {
    const { draft, current, sceneA } = context();
    const field = sceneA + ":role";
    const pending = recipe.submitRecipeDraft(changed(draft, field, "reveal", current), 20);
    const advanced = recipe.reconcileRecipeDraft(pending, current, 24);
    const lower = recipe.reconcileRecipeDraft(advanced, { ...current, [field]: "reveal" }, 21);
    expect(lower).toEqual(advanced);
    expect(() => recipe.submitRecipeDraft(lower, 21)).toThrow("behind this draft");
    expect(() => recipe.submitRecipeDraft(draft, -1)).toThrow();
    expect(() => recipe.submitRecipeDraft(draft, 20)).toThrow("no edited recipe input");
  });

  it("lets explicit abandonment end pending review one field at a time after an unconfirmed request", () => {
    const { draft, current, sceneA, sceneB } = context();
    let pending = changed(draft, sceneA + ":role", "reveal", current);
    pending = changed(pending, sceneB + ":role", "comparison", current);
    const sent = recipe.submitRecipeDraft(pending, 20);
    const one = recipe.resolveRecipeField(sent, sceneA + ":role", current, "saved");
    expect(one.awaiting?.fields).toEqual({ [sceneB + ":role"]: "comparison" });
    const all = recipe.resolveRecipeField(one, sceneB + ":role", current, "saved");
    expect(all.edits).toEqual({});
    expect(all.awaiting).toBeUndefined();
  });

  it("keeps orphan scene input visible for explicit discard instead of silently removing it", () => {
    const { project, profileA, canonical, draft, current, sceneA } = context();
    const pending = changed(draft, sceneA + ":role", "reveal", current);
    project.scenes = project.scenes.filter(scene => scene.id !== sceneA);
    canonical.scene_intents = canonical.scene_intents.filter(intent => intent.scene_id !== sceneA);
    const now = recipe.recipeFields(canonical, "film");
    const orphan = recipe.reconcileRecipeDraft(pending, now, 21);
    expect(orphan.edits[sceneA + ":role"].value).toBe("reveal");
    expect(() => recipe.validateDeliveryRecipe(project, profileA, recipe.proposedDeliveryRecipe(canonical, orphan))).toThrow("missing or duplicated");
    const discarded = recipe.resolveRecipeField(orphan, sceneA + ":role", now, "saved");
    recipe.validateDeliveryRecipe(project, profileA, recipe.proposedDeliveryRecipe(canonical, discarded));
    expect(discarded.edits).toEqual({});
  });
});

describe("bounded authored recipe values and wire projection", () => {
  it("accepts every supported narrative role and archetype without generating or approving media", () => {
    const { project, profileA, canonical } = context();
    for (const role of recipe.deliveryRoles) for (const archetype of recipe.deliveryArchetypes) {
      const candidate = structuredClone(canonical);
      candidate.scene_intents[0] = { ...candidate.scene_intents[0], role, archetype };
      expect(() => recipe.validateDeliveryRecipe(project, profileA, candidate)).not.toThrow();
    }
  });

  it.each(["", " ", "0x10", "NaN", "Infinity", "1_000", "--2"])("retains invalid numeric raw %j for correction and rejects its projection", raw => {
    const { draft, current, canonical, scope } = context("mix");
    const pending = changed(draft, "sfx_gain_db", raw, current);
    expect(recipe.decodeRecipeDraft(JSON.stringify(pending), scope, "mix").edits.sfx_gain_db.value).toBe(raw);
    expect(() => recipe.proposedDeliveryRecipe(canonical, pending)).toThrow("decimal value");
    expect(pending.edits.sfx_gain_db.value).toBe(raw);
  });

  it.each([
    ["sfx_gain_db", -80.1], ["sfx_gain_db", 12.1], ["sfx_gain_db", Infinity],
    ["duck_attenuation_db", -0.1], ["duck_attenuation_db", 30.1],
    ["duck_attack_samples", 0], ["duck_attack_samples", 1.5],
    ["duck_release_samples", 48001], ["duck_release_samples", NaN],
  ])("rejects out-of-range %s=%s without changing the saved recipe", (field, value) => {
    const { project, profileA, canonical } = context();
    const candidate = structuredClone(canonical);
    Object.assign(candidate.original_mix!, { [field]: value });
    expect(() => recipe.validateDeliveryRecipe(project, profileA, candidate)).toThrow();
    expect(recipe.savedDeliveryRecipe(project, profileA)).toEqual(canonical);
  });

  it("accepts exact bounds and nullable stems, but not arbitrary enablement", () => {
    const { project, profileA, canonical, draft, current } = context("mix");
    const candidate = structuredClone(canonical);
    candidate.original_mix = { music_asset_id: null, sfx_asset_id: null, sfx_gain_db: -80, duck_attenuation_db: 30, duck_attack_samples: 1, duck_release_samples: 48000 };
    recipe.validateDeliveryRecipe(project, profileA, candidate);
    expect(() => recipe.proposedDeliveryRecipe(canonical, changed(draft, "enabled", "maybe", current))).toThrow("whether the original mix");
    const disabled = recipe.proposedDeliveryRecipe(canonical, changed(draft, "enabled", "false", current));
    expect(disabled.original_mix).toBeNull();
    expect(disabled.scene_intents).toEqual(canonical.scene_intents);
  });

  it("rejects missing profiles/scenes, duplicate UUID spellings and unknown source or authority fields", () => {
    const { project, profileA, canonical, sceneA } = context();
    expect(() => recipe.validateDeliveryRecipe(project, id(9999), canonical)).toThrow("profile no longer exists");
    const duplicate = structuredClone(canonical);
    duplicate.scene_intents.push({ ...duplicate.scene_intents[0], scene_id: sceneA.toUpperCase() });
    expect(() => recipe.validateDeliveryRecipe(project, profileA, duplicate)).toThrow("duplicated");
    const missing = structuredClone(canonical);
    missing.scene_intents[0].scene_id = id(9999);
    expect(() => recipe.validateDeliveryRecipe(project, profileA, missing)).toThrow("missing");
    for (const addition of [{ grant: "unit grant" }, { token: "unit token" }, { plan: {} }, { approved: true }]) {
      expect(() => recipe.validateDeliveryRecipe(project, profileA, { ...canonical, ...addition })).toThrow("Unknown or missing");
    }
    expect(() => recipe.validateDeliveryRecipe(project, profileA, { ...canonical, schema: "motionwright.delivery-recipe/2" })).toThrow("schema");
  });

  it("compares UUID identity for source existence and distinct stems, matching the typed wire", () => {
    const { project, profileA, canonical, music } = context();
    const candidate = structuredClone(canonical);
    candidate.original_mix!.music_asset_id = music.toUpperCase();
    recipe.validateDeliveryRecipe(project, profileA.toUpperCase(), candidate);
    candidate.original_mix!.sfx_asset_id = music;
    expect(() => recipe.validateDeliveryRecipe(project, profileA, candidate)).toThrow("distinct source assets");
  });

  it.each(["missing", "image", "unhashed", "malformed hash"])("rejects a %s audio source without losing the authored ID", fault => {
    const { project, profileA, canonical, music } = context();
    const source = project.assets.find(asset => asset.id === music)!;
    if (fault === "missing") project.assets = project.assets.filter(asset => asset.id !== music);
    if (fault === "image") source.media_type = "image/png";
    if (fault === "unhashed") source.content_sha256 = null;
    if (fault === "malformed hash") source.content_sha256 = "not-a-sha256";
    expect(() => recipe.validateDeliveryRecipe(project, profileA, canonical)).toThrow("current hashed audio source");
    expect(canonical.original_mix!.music_asset_id).toBe(music);
  });

  it("rejects field keys and overlong input before mutating an existing draft", () => {
    const { draft, current } = context("mix");
    const pending = changed(draft, "sfx_gain_db", "-4", current);
    const before = structuredClone(pending);
    expect(() => changed(pending, "grant", "token", current)).toThrow("bounded draft");
    expect(() => changed(pending, "sfx_gain_db", "x".repeat(129), current)).toThrow("bounded draft");
    expect(pending).toEqual(before);
  });
});

describe("strict recovery decoding", () => {
  it("keeps bounded invalid raw and submission intent without admitting execution authority", () => {
    const { draft, current, scope } = context("mix");
    const invalid = changed(draft, "sfx_gain_db", "n".repeat(128), current);
    const decoded = recipe.decodeRecipeDraft(JSON.stringify(invalid), scope, "mix");
    expect(decoded).toEqual(invalid);
    const sent = recipe.submitRecipeDraft(changed(draft, "sfx_gain_db", "-4", current), 20);
    expect(recipe.decodeRecipeDraft(JSON.stringify(sent), scope, "mix")).toEqual(sent);
  });

  it.each([null, false, 0, "", [], {}, { revision: 20, fields: {} }, { revision: 21, fields: { sfx_gain_db: "-4" } }, { revision: -1, fields: { sfx_gain_db: "-4" } }, { revision: 20, fields: { token: "a" } }, { revision: 20, fields: { sfx_gain_db: "-4" }, approved: true }])("rejects malformed awaiting %j", awaiting => {
    const { draft, scope } = context("mix");
    expect(() => recipe.decodeRecipeDraft(JSON.stringify({ ...draft, awaiting }), scope, "mix")).toThrow("pending intent");
  });

  it.each([
    { intent: false }, { intent: null }, { base: 1 }, { value: null },
    { value: "x".repeat(129) }, { base: "x".repeat(129) }, { grant: "unit" },
  ])("rejects malformed edits %j without rewriting the original bytes", patch => {
    const { draft, scope } = context("mix");
    const raw = JSON.stringify({ ...draft, edits: { sfx_gain_db: { base: "-8", value: "-4", ...patch } } });
    expect(() => recipe.decodeRecipeDraft(raw, scope, "mix")).toThrow();
    expect(JSON.parse(raw).edits.sfx_gain_db).toEqual({ base: "-8", value: "-4", ...patch });
  });

  it("rejects identity changes, future formats, unknown fields and non-object edits", () => {
    const { draft, scope } = context("mix");
    for (const patch of [{ scope: "another project" }, { group: "film" }, { version: 2 }, { revision: 1.5 }, { revision: Number.MAX_SAFE_INTEGER + 1 }, { edits: [] }, { edits: null }, { token: "unit" }, { grant: { approved: true } }]) {
      expect(() => recipe.decodeRecipeDraft(JSON.stringify({ ...draft, ...patch }), scope, "mix")).toThrow();
    }
    const missing = { ...draft } as Partial<RecipeDraft>;
    delete missing.edits;
    expect(() => recipe.decodeRecipeDraft(JSON.stringify(missing), scope, "mix")).toThrow();
  });

  it("rejects noncanonical or aliased scene keys and enforces UTF-8 byte and field budgets", () => {
    const { draft, scope } = context();
    const field = id(41) + ":role";
    expect(() => recipe.decodeRecipeDraft(JSON.stringify({ ...draft, edits: { [field]: { base: "", value: "hook" }, [field.toUpperCase().replace(":ROLE", ":role")]: { base: "", value: "payoff" } } }), scope, "film")).toThrow();
    const oversized = JSON.stringify({ ...draft, edits: Object.fromEntries(Array.from({ length: 8193 }, (_, index) => [id(index) + ":role", { base: "", value: "hook" }])) });
    expect(() => recipe.decodeRecipeDraft(oversized, scope, "film")).toThrow();
    const multibyte = JSON.stringify({ ...draft, edits: Object.fromEntries(Array.from({ length: 750 }, (_, index) => [id(index) + ":role", { base: "", value: "界".repeat(100) }])) });
    expect(multibyte.length).toBeLessThan(256 * 1024);
    expect(new TextEncoder().encode(multibyte).length).toBeGreaterThan(256 * 1024);
    expect(() => recipe.decodeRecipeDraft(multibyte, scope, "film")).toThrow("256 KiB");
  });
});

describe("recovery storage and editing-window ownership", () => {
  it("warns on inaccessible storage without blocking edits or valid service-bound recipe projection", () => {
    const { key, scope, current, canonical, profileA, project } = context("mix");
    local.fail = "read";
    const loaded = recipe.loadRecipeDraft(key, scope, "mix", 20);
    expect(loaded.blocked).toBeNull();
    expect(loaded.warning).toContain("could not be accessed");
    const pending = changed(loaded.draft, "sfx_gain_db", "-5", current);
    const persisted = recipe.persistRecipeDraft(key, pending);
    expect(persisted.blocked).toBeNull();
    expect(persisted.warning).toContain("kept in this window");
    expect(recipe.loadRecipeDraft(key, scope, "mix", 20).draft.edits.sfx_gain_db.value).toBe("-5");
    recipe.validateDeliveryRecipe(project, profileA, recipe.proposedDeliveryRecipe(canonical, pending));
  });

  it("preserves a previously inaccessible original if access returns after new typing", () => {
    const { key, scope, current, draft } = context("mix");
    const original = JSON.stringify(changed(draft, "sfx_gain_db", "-12", current));
    local.setItem(key, original);
    local.fail = "read";
    const loaded = recipe.loadRecipeDraft(key, scope, "mix", 20);
    const later = changed(loaded.draft, "sfx_gain_db", "-4", current);
    local.fail = null;
    const stored = recipe.persistRecipeDraft(key, later);
    expect(stored.blocked).toBeNull();
    expect(stored.warning).toContain("earlier inaccessible copy is retained");
    expect(local.getItem(key)).toBe(original);
    expect(recipe.loadRecipeDraft(key, scope, "mix", 20).draft.edits.sfx_gain_db.value).toBe("-4");
  });

  it("keeps the backup warning through submission and clears it only after a successful backup", () => {
    const { key, scope, current } = context("mix");
    const loaded = recipe.loadRecipeDraft(key, scope, "mix", 20);
    local.fail = "write";
    const pending = changed(loaded.draft, "sfx_gain_db", "-5", current);
    const failed = recipe.persistRecipeDraft(key, pending);
    const sent = recipe.persistRecipeDraft(key, recipe.submitRecipeDraft(failed.draft, 20));
    expect(sent.warning).toContain("storage quota denied");
    expect(sent.blocked).toBeNull();
    expect(recipe.loadRecipeDraft(key, scope, "mix", 20).warning).toBe(sent.warning);
    local.fail = null;
    const restored = recipe.persistRecipeDraft(key, sent.draft);
    expect(restored.warning).toBeNull();
    expect(recipe.decodeRecipeDraft(local.getItem(key)!, scope, "mix")).toEqual(sent.draft);
  });

  it.each(["{broken JSON", ""])("retains unreadable own bytes %j and new memory input, including blocked state on remount", raw => {
    const { key, scope, current } = context("mix");
    local.setItem(key, raw);
    const loaded = recipe.loadRecipeDraft(key, scope, "mix", 20);
    expect(loaded.blocked).toContain("original copy is retained");
    const later = changed(loaded.draft, "sfx_gain_db", "-4", current);
    recipe.persistRecipeDraft(key, later);
    const remounted = recipe.loadRecipeDraft(key, scope, "mix", 22);
    expect(remounted.blocked).toBe(loaded.blocked);
    expect(remounted.draft.edits.sfx_gain_db.value).toBe("-4");
    expect(local.getItem(key)).toBe(raw);
    const discarded = recipe.discardUnreadableRecipeDraft(key, scope, "mix", 22);
    expect(discarded.blocked).toBeNull();
    expect(discarded.draft.edits.sfx_gain_db.value).toBe("-4");
    expect(recipe.decodeRecipeDraft(local.getItem(key)!, scope, "mix").edits.sfx_gain_db.value).toBe("-4");
  });

  it("does not unblock or lose typed input when discarding the unreadable original fails", () => {
    const { key, scope, current } = context("mix");
    local.setItem(key, "{broken JSON");
    const loaded = recipe.loadRecipeDraft(key, scope, "mix", 20);
    recipe.persistRecipeDraft(key, changed(loaded.draft, "sfx_gain_db", "-4", current));
    local.fail = "remove";
    expect(() => recipe.discardUnreadableRecipeDraft(key, scope, "mix", 20)).toThrow("remove denied");
    expect(recipe.loadRecipeDraft(key, scope, "mix", 20).blocked).toBe(loaded.blocked);
    expect(recipe.loadRecipeDraft(key, scope, "mix", 20).draft.edits.sfx_gain_db.value).toBe("-4");
    expect(local.getItem(key)).toBe("{broken JSON");
  });

  it("isolates memory and pending intent by project, generation, branch, profile and group", () => {
    const { project, profileA, profileB, draft, current, key, scope } = context("mix");
    recipe.loadRecipeDraft(key, scope, "mix", 20);
    const sent = recipe.submitRecipeDraft(changed(draft, "sfx_gain_db", "-4", current), 20);
    recipe.persistRecipeDraft(key, sent);
    const alternatives: Array<[Project, string, RecipeGroup]> = [
      [{ ...project, id: id(501) }, profileA, "mix"],
      [{ ...project, generation: id(502) }, profileA, "mix"],
      [{ ...project, active_branch: id(503) }, profileA, "mix"],
      [project, profileB, "mix"], [project, profileA, "film"],
    ];
    for (const [otherProject, profileId, group] of alternatives) {
      const otherScope = recipe.recipeDraftScope(otherProject, profileId, group);
      const other = recipe.loadRecipeDraft(recipe.recipeDraftStorageKey(otherScope, writerA), otherScope, group, 20);
      expect(other.draft.edits).toEqual({});
      expect(other.draft.awaiting).toBeUndefined();
    }
    const scopeB = recipe.recipeDraftScope(project, profileB, "mix");
    const keyB = recipe.recipeDraftStorageKey(scopeB, writerA);
    const draftB = changed(recipe.loadRecipeDraft(keyB, scopeB, "mix", 20).draft, "sfx_gain_db", "-11", current);
    recipe.persistRecipeDraft(keyB, draftB);
    recipe.persistRecipeDraft(key, recipe.reconcileRecipeDraft(sent, { ...current, sfx_gain_db: "-4" }, 21));
    expect(recipe.loadRecipeDraft(keyB, scopeB, "mix", 21).draft).toEqual(draftB);
    expect(recipe.loadRecipeDraft(key, scope, "mix", 21).draft.edits).toEqual({});
  });

  it("uses the same pointer on an own reload and restores the raw pending copy", async () => {
    const { scope, draft, current } = context("mix");
    const firstWriter = recipe.recipeDraftWriterId();
    const key = recipe.recipeDraftStorageKey(scope, firstWriter);
    recipe.loadRecipeDraft(key, scope, "mix", 20);
    recipe.persistRecipeDraft(key, recipe.submitRecipeDraft(changed(draft, "sfx_gain_db", " -04.0 ", current), 20));
    vi.resetModules();
    vi.mocked(performance.getEntriesByType).mockReturnValue([{ type: "reload" } as PerformanceNavigationTiming]);
    recipe = await import("./deliveryRecipe");
    expect(recipe.recipeDraftWriterId()).toBe(firstWriter);
    const own = recipe.loadRecipeDraft(key, scope, "mix", 21);
    expect(own.draft.edits.sfx_gain_db.value).toBe(" -04.0 ");
    expect(own.draft.awaiting).toBeDefined();
  });

  it.each(["navigate", "back_forward", "reload with opener"])("does not autoapply a cloned sessionStorage copy on %s", async navigation => {
    const { scope, draft, current } = context("mix");
    const originalKey = recipe.recipeDraftStorageKey(scope, writerA);
    const original = JSON.stringify(changed(draft, "sfx_gain_db", "-13", current));
    local.setItem(originalKey, original);
    session.setItem(recipe.RECIPE_DRAFT_STORAGE_PREFIX + "writer", writerA);
    vi.resetModules();
    vi.mocked(performance.getEntriesByType).mockReturnValue([{ type: navigation === "reload with opener" ? "reload" : navigation } as PerformanceNavigationTiming]);
    vi.stubGlobal("window", { opener: navigation === "reload with opener" ? {} : null });
    recipe = await import("./deliveryRecipe");
    const newWriter = recipe.recipeDraftWriterId();
    expect(newWriter).not.toBe(writerA);
    const ownKey = recipe.recipeDraftStorageKey(scope, newWriter);
    expect(recipe.loadRecipeDraft(ownKey, scope, "mix", 20).draft.edits).toEqual({});
    const available = recipe.readRecipeRecoveryCopies(scope, "mix", ownKey);
    expect(available.copies).toEqual([{ key: originalKey, revision: 20, fields: 1, readable: true }]);
    const recovered = recipe.recoverRecipeDraftCopy(ownKey, originalKey, scope, "mix", { ...current, sfx_gain_db: "-9" }, 21);
    expect(recovered.draft.edits.sfx_gain_db.value).toBe("-13");
    expect(recipe.recipeConflicts(recovered.draft, { ...current, sfx_gain_db: "-9" })).toEqual(["sfx_gain_db"]);
    expect(local.getItem(originalKey)).toBe(original);
  });

  it("retains a recovered future revision instead of rebasing it onto an older project", () => {
    const { scope, key, draft, current } = context("mix");
    const foreign = recipe.recipeDraftStorageKey(scope, writerB);
    const future = { ...changed(draft, "sfx_gain_db", "-4", current), revision: 30 };
    local.setItem(foreign, JSON.stringify(future));
    const recovered = recipe.recoverRecipeDraftCopy(key, foreign, scope, "mix", current, 20);
    expect(recovered.draft).toEqual(future);
    expect(() => recipe.submitRecipeDraft(recovered.draft, 20)).toThrow("behind this draft");
  });

  it("requires resolving dirty, pending or unreadable own input before recovering another copy", () => {
    const { key, scope, draft, current } = context("mix");
    const foreign = recipe.recipeDraftStorageKey(scope, writerB);
    local.setItem(foreign, JSON.stringify(changed(draft, "sfx_gain_db", "-13", current)));
    recipe.loadRecipeDraft(key, scope, "mix", 20);
    const pending = changed(draft, "sfx_gain_db", "-4", current);
    recipe.persistRecipeDraft(key, pending);
    expect(() => recipe.recoverRecipeDraftCopy(key, foreign, scope, "mix", current, 20)).toThrow("Resolve this window");
    const awaitingOnly = { ...recipe.submitRecipeDraft(pending, 20), edits: {} };
    recipe.persistRecipeDraft(key, awaitingOnly);
    expect(() => recipe.recoverRecipeDraftCopy(key, foreign, scope, "mix", current, 20)).toThrow("Resolve this window");
    const corruptKey = recipe.recipeDraftStorageKey(scope, id(1003));
    local.setItem(corruptKey, "{broken JSON");
    expect(() => recipe.recoverRecipeDraftCopy(corruptKey, foreign, scope, "mix", current, 20)).toThrow("Resolve this window");
    expect(local.getItem(corruptKey)).toBe("{broken JSON");
  });

  it("distinguishes corrupt copies from inaccessible enumeration and protects foreign scopes during discard", () => {
    const { key, scope, draft, current, project, profileB } = context("mix");
    const foreign = recipe.recipeDraftStorageKey(scope, writerB);
    const otherScope = recipe.recipeDraftScope(project, profileB, "mix");
    const outside = recipe.recipeDraftStorageKey(otherScope, writerB);
    local.setItem(foreign, "{broken JSON");
    local.setItem(outside, JSON.stringify(changed({ ...draft, scope: otherScope }, "sfx_gain_db", "-13", current)));
    expect(recipe.readRecipeRecoveryCopies(scope, "mix", key)).toEqual({ copies: [{ key: foreign, revision: null, fields: 0, readable: false }], warning: null });
    local.fail = "read";
    expect(recipe.readRecipeRecoveryCopies(scope, "mix", key).warning).toContain("could not be read");
    expect(recipe.readRecipeRecoveryCopies(scope, "mix", key).copies).toEqual([]);
    local.fail = null;
    expect(() => recipe.discardRecipeDraftCopy(scope, key, outside)).toThrow("separate recipe copy");
    expect(() => recipe.discardRecipeDraftCopy(scope, key, key)).toThrow("separate recipe copy");
    expect(local.getItem(outside)).not.toBeNull();
    recipe.discardRecipeDraftCopy(scope, key, foreign);
    expect(local.getItem(foreign)).toBeNull();
    expect(local.getItem(outside)).not.toBeNull();
  });

  it("keeps input and a warning when other scopes or unlisted keys exhaust the global budget", () => {
    const { key, scope, current, canonical, project, profileA } = context("mix");
    for (let index = 0; index < 64; index++) local.setItem(recipe.RECIPE_DRAFT_STORAGE_PREFIX + "retained-scope:" + index, "retained original " + index);
    const originals = [...local.entries];
    const loaded = recipe.loadRecipeDraft(key, scope, "mix", 20);
    const pending = changed(loaded.draft, "sfx_gain_db", "-4", current);
    const stored = recipe.persistRecipeDraft(key, pending);
    expect(stored.blocked).toBeNull();
    expect(stored.warning).toContain("64-copy / 2 MiB budget");
    expect(stored.draft.edits.sfx_gain_db.value).toBe("-4");
    expect(recipe.readRecipeRecoveryCopies(scope, "mix", key).copies).toEqual([]);
    expect([...local.entries]).toEqual(originals);
    recipe.validateDeliveryRecipe(project, profileA, recipe.proposedDeliveryRecipe(canonical, pending));
    // This is an explicit limit: no global copy manager and no reload guarantee
    // when backup failed. Successful project saving remains available.
  });

  it("enforces the total byte budget without deleting originals or preventing later backup after space is freed", () => {
    const { key, scope, draft, current } = context("mix");
    const outside = recipe.RECIPE_DRAFT_STORAGE_PREFIX + "retained-large-original";
    local.setItem(outside, "界".repeat(700000));
    recipe.loadRecipeDraft(key, scope, "mix", 20);
    const pending = changed(draft, "sfx_gain_db", "-4", current);
    expect(recipe.persistRecipeDraft(key, pending).warning).toContain("2 MiB budget");
    expect(local.getItem(outside)).toHaveLength(700000);
    local.removeItem(outside); // Explicit external fixture action, never automatic eviction.
    expect(recipe.persistRecipeDraft(key, pending).warning).toBeNull();
    expect(recipe.decodeRecipeDraft(local.getItem(key)!, scope, "mix")).toEqual(pending);
  });

  it("returns clones so consumers cannot silently mutate cached or recovered input", () => {
    const { key, scope, current } = context("mix");
    const loaded = recipe.loadRecipeDraft(key, scope, "mix", 20);
    const pending = changed(loaded.draft, "sfx_gain_db", "-4", current);
    const stored = recipe.persistRecipeDraft(key, pending);
    stored.draft.edits.sfx_gain_db.value = "-40";
    pending.edits.sfx_gain_db.value = "-50";
    expect(recipe.loadRecipeDraft(key, scope, "mix", 20).draft.edits.sfx_gain_db.value).toBe("-4");
  });
});
