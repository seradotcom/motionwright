import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { defaultOriginalMix, recipeConflicts, recipeDraftStorageKey, recipeFields, RECIPE_DRAFT_STORAGE_PREFIX } from "./deliveryRecipe";
import {
  assertRecipeHistoryPayload, createRecipeAuthorState, decodeRecipeRecovery, editRecipeAuthorState, observeRecipeAuthorState,
  prepareRecipeSubmission, recipeHasWork, recipePointerKey, recoverRecipeAuthorState, writeRecipeRecovery,
} from "./deliveryRecipeDraft";
import type { RecipeAuthorState, RecipeCustody } from "./deliveryRecipeDraft";
import type { DeliveryRecipe, RecipeGroup } from "./deliveryRecipe";

// Pure storage / author-state adversaries. These are not service or native proof.
const id = (n: number) => "abcdef00-0000-4000-8000-" + n.toString(16).padStart(12, "0");
class MemoryStorage implements Storage {
  readonly entries = new Map<string, string>();
  onRead: ((key: string) => void) | null = null;
  onWrite: ((key: string, value: string) => void) | null = null;
  get length() { return this.entries.size; }
  clear() { this.entries.clear(); }
  key(index: number) { return [...this.entries.keys()][index] ?? null; }
  getItem(key: string) { this.onRead?.(key); return this.entries.get(key) ?? null; }
  setItem(key: string, value: string) { this.entries.set(key, value); this.onWrite?.(key, value); }
  removeItem(key: string) { this.entries.delete(key); }
}
function sample(group: RecipeGroup = "mix") {
  const project = structuredClone(fixtureProject); project.revision = 20; project.locks = [];
  const profileId = project.deliverables[0].id, sceneId = project.scenes[0].id;
  const canonical: DeliveryRecipe = { schema: "motionwright.delivery-recipe/1",
    scene_intents: [{ scene_id: sceneId, role: "hook", archetype: "statement" }], original_mix: defaultOriginalMix() };
  project.production_design = { plan: null, heroes: [], capsules: [], delivery_recipes: { [profileId]: canonical } };
  const state = createRecipeAuthorState(project, profileId, group), field = group === "mix" ? "sfx_gain_db" : sceneId + ":role";
  const current = recipeFields(canonical, group);
  const edit = (value: string, source = state) => editRecipeAuthorState(source, field, value, current);
  const submitted = prepareRecipeSubmission(edit(group === "mix" ? "-4.5" : "reveal"), project, id(100));
  const receipt = structuredClone(project); receipt.revision++;
  receipt.production_design!.delivery_recipes![profileId] = submitted.change.recipe!;
  const custody: RecipeCustody = { writerId: id(201), expectedRaw: null, expectedPointer: null };
  return { project, profileId, field, current, state, edit, submitted, receipt, custody };
}
function stored(state: RecipeAuthorState, custody: RecipeCustody) {
  const local = new MemoryStorage(), session = new MemoryStorage();
  writeRecipeRecovery(local, session, state, custody);
  return { local, session, key: recipeDraftStorageKey(state.input.scope, custody.writerId) };
}

describe("recipe recovery custody after budget and pointer operations", () => {
  it("does not overwrite own bytes changed while enumerating the recovery budget", () => {
    const { state, edit, custody } = sample(), original = edit("-4.5");
    const { local, session, key } = stored(original, custody), before = custody.expectedRaw;
    const other = RECIPE_DRAFT_STORAGE_PREFIX + "other-copy";
    local.entries.set(other, "budget fixture");
    local.onRead = candidate => { if (candidate === other) local.entries.set(key, "foreign bytes written during budget read"); };
    expect(() => writeRecipeRecovery(local, session, edit("-7", original), custody)).toThrow("budget");
    expect(local.getItem(key)).toBe("foreign bytes written during budget read");
    expect(custody.expectedRaw).toBe(before);
    expect(session.getItem(recipePointerKey(state.input.scope))).toBe(custody.writerId);
  });
  it("does not overwrite a tab pointer changed while enumerating the recovery budget", () => {
    const { state, edit, custody } = sample(), original = edit("-4.5");
    const { local, session, key } = stored(original, custody), before = local.getItem(key);
    const other = RECIPE_DRAFT_STORAGE_PREFIX + "other-copy", pointer = recipePointerKey(state.input.scope);
    local.entries.set(other, "budget fixture");
    local.onRead = candidate => { if (candidate === other) session.entries.set(pointer, id(999)); };
    expect(() => writeRecipeRecovery(local, session, edit("-7", original), custody)).toThrow("pointer");
    expect(session.getItem(pointer)).toBe(id(999)); expect(local.getItem(key)).toBe(before);
  });
  it("does not admit UNKNOWN when the just-published pointer names replaced data", () => {
    const { submitted, custody } = sample(), local = new MemoryStorage(), session = new MemoryStorage();
    const key = recipeDraftStorageKey(submitted.state.input.scope, custody.writerId);
    session.onWrite = () => local.entries.set(key, "replacement after first readback");
    expect(() => writeRecipeRecovery(local, session, submitted.state, custody)).toThrow("not confirmed");
    expect(local.getItem(key)).toBe("replacement after first readback");
    expect(submitted.state.pending?.outcome).toBe("UNKNOWN");
  });
  it("still confirms exact data and pointer when both remain unchanged", () => {
    const { submitted, custody } = sample(), { local, session, key } = stored(submitted.state, custody);
    expect(session.getItem(recipePointerKey(submitted.state.input.scope))).toBe(custody.writerId);
    expect(decodeRecipeRecovery(local.getItem(key)!, submitted.state.input.scope, "mix", custody.writerId)).toEqual(submitted.state);
  });
});

describe("recipe recovery is an author decision, not saved-value acknowledgement", () => {
  for (const group of ["film", "mix"] as const) {
    it(`${group}: an explicitly recovered equal value remains local when another author changes it again`, () => {
      const { edit, receipt, profileId, field } = sample(group), original = edit(group === "film" ? "reveal" : "-4.5");
      const before = structuredClone(original), recovered = recoverRecipeAuthorState(original, receipt);
      expect(recovered.input.edits[field].value).toBe(original.input.edits[field].value);
      const peer = structuredClone(receipt); peer.revision++;
      const recipe = peer.production_design!.delivery_recipes![profileId];
      if (group === "film") recipe.scene_intents[0].role = "payoff"; else recipe.original_mix!.sfx_gain_db = -7;
      const observed = observeRecipeAuthorState(recovered, peer);
      expect(observed.input.edits[field].value).toBe(original.input.edits[field].value);
      expect(recipeConflicts(observed.input, recipeFields(recipe, group))).toContain(field);
      expect(original).toEqual(before); expect(recovered.pending).toBeNull();
    });
  }
  it("restores NOT_SENT as UNKNOWN without changing the original submitted fields or revision", () => {
    const { submitted, receipt } = sample(), original = structuredClone(submitted.state);
    original.pending!.outcome = "NOT_SENT";
    const recovered = recoverRecipeAuthorState(original, receipt);
    expect(recovered.pending?.outcome).toBe("UNKNOWN");
    expect(recovered.pending?.fields).toEqual(submitted.state.pending!.fields);
    expect(recovered.pending?.revision).toBe(20); expect(original.pending?.outcome).toBe("NOT_SENT");
    expect(() => prepareRecipeSubmission(recovered, receipt, id(101))).toThrow("Read the saved recipe and history");
  });
  it("a clean checkpoint has no historical recipe to restore and UNKNOWN still has work", () => {
    const { state, submitted, project } = sample();
    expect(recipeHasWork(state)).toBe(false);
    expect(recipeHasWork(recoverRecipeAuthorState(state, project))).toBe(false);
    expect(recipeHasWork(submitted.state)).toBe(true);
  });
});

describe("successful reread preserves both earlier UNKNOWN and typing during an outage", () => {
  it("keeps newer incomplete numeric raw while retaining the original UNKNOWN snapshot", () => {
    const { state, project, submitted, field, current } = sample();
    const later = editRecipeAuthorState(state, field, "  1e  ", current), before = structuredClone(submitted.state);
    const recovered = recoverRecipeAuthorState(submitted.state, project, later);
    expect(recovered.input.edits[field].value).toBe("  1e  ");
    expect(recovered.pending).toEqual(before.pending); expect(recovered.input.awaiting).toEqual(before.input.awaiting);
    expect(recovered.fieldSequences[field]).toBeGreaterThan(recovered.pending!.sequences[field]);
    expect(submitted.state).toEqual(before);
    expect(() => prepareRecipeSubmission(recovered, project, id(101))).toThrow("Read the saved recipe and history");
  });
  for (const group of ["film", "mix"] as const) {
    it(`${group}: later typing back to the saved base overrides recovered input without releasing UNKNOWN`, () => {
      const { state, project, submitted, field, current } = sample(group);
      let later = editRecipeAuthorState(state, field, group === "film" ? "payoff" : "-11", current);
      later = editRecipeAuthorState(later, field, current[field], current);
      expect(later.input.edits[field]).toBeUndefined();
      const recovered = recoverRecipeAuthorState(submitted.state, project, later);
      expect(recovered.input.edits[field].value).toBe(current[field]);
      expect(recovered.input.edits[field].intent).toBe(true);
      expect(recovered.pending).toEqual(submitted.state.pending);
      expect(recovered.fieldSequences[field]).toBeGreaterThan(recovered.pending!.sequences[field]);
    });
  }
  it("keeps an outage decision returning to the old base when a peer changes the saved field before Retry", () => {
    const { state, project, submitted, field, current, receipt, profileId } = sample("film");
    let later = editRecipeAuthorState(state, field, "payoff", current, true);
    later = editRecipeAuthorState(later, field, "hook", current, true);
    const peer = structuredClone(receipt); peer.production_design!.delivery_recipes![profileId].scene_intents[0].role = "payoff";
    later = observeRecipeAuthorState(later, peer);
    const recovered = recoverRecipeAuthorState(submitted.state, peer, later);
    expect(recovered.input.edits[field].value).toBe("hook");
    expect(recovered.pending).toEqual(submitted.state.pending);
    expect(recipeConflicts(recovered.input, recipeFields(peer.production_design!.delivery_recipes![profileId], "film"))).toContain(field);
    expect(project.revision).toBe(20);
  });
  it("merges only touched later fields and retains untouched original input", () => {
    const { state, project, edit, field, current } = sample();
    const original = editRecipeAuthorState(edit("-4.5"), "duck_attenuation_db", "18", current);
    const later = editRecipeAuthorState(state, "duck_attenuation_db", "1e", current);
    const recovered = recoverRecipeAuthorState(original, project, later);
    expect(recovered.input.edits[field].value).toBe("-4.5");
    expect(recovered.input.edits.duck_attenuation_db.value).toBe("1e");
    expect(recovered.pending).toBeNull();
  });
  it("refuses a different owner or a second uncertain attempt instead of blending authorities", () => {
    const { state, project, submitted } = sample();
    const foreign = { ...project, generation: project.generation + "-other" };
    expect(() => recoverRecipeAuthorState(submitted.state, project,
      createRecipeAuthorState(foreign, foreign.deliverables[0].id, "mix"))).toThrow("belongs to another");
    expect(() => recoverRecipeAuthorState(submitted.state, project, submitted.state)).toThrow("separate uncertain");
    expect(state.pending).toBeNull(); expect(submitted.state.pending?.outcome).toBe("UNKNOWN");
  });
  it("refuses exhausted sequences without modifying either retained input", () => {
    const { state, project, submitted, edit } = sample(), original = structuredClone(submitted.state);
    original.sequence = Number.MAX_SAFE_INTEGER;
    const later = edit("-7", state), before = structuredClone(original);
    expect(() => recoverRecipeAuthorState(original, project, later)).toThrow("sequence is exhausted");
    expect(original).toEqual(before); expect(later.input.edits.sfx_gain_db.value).toBe("-7");
  });
});


describe("historical recipe JSON narrows without current-project membership", () => {
  it("accepts null and the exact current wire shape even when referenced sources are no longer in the current Project", () => {
    const { submitted, project } = sample(), historical = structuredClone(submitted.change.recipe!);
    historical.scene_intents[0].scene_id = id(901);
    historical.original_mix!.music_asset_id = id(902); historical.original_mix!.sfx_asset_id = id(903);
    const before = structuredClone(historical);
    assertRecipeHistoryPayload(null); assertRecipeHistoryPayload(historical);
    expect(historical).toEqual(before);
    expect(project.scenes.some(scene => scene.id === id(901))).toBe(false);
    expect(project.assets.some(asset => asset.id === id(902))).toBe(false);
  });
  it("rejects missing, malformed, unknown or out-of-contract history fields before comparing them", () => {
    const { submitted } = sample(), recipe = submitted.change.recipe!, mix = recipe.original_mix!;
    const invalid: unknown[] = [undefined, [], {}, { ...recipe, schema: "future-unreviewed-schema" },
      { ...recipe, unexpected: true }, { ...recipe, scene_intents: "wrong" },
      { ...recipe, scene_intents: [{ ...recipe.scene_intents[0], role: "invented" }] },
      { ...recipe, scene_intents: [recipe.scene_intents[0], recipe.scene_intents[0]] },
      { ...recipe, original_mix: { ...mix, unexpected: true } },
      { ...recipe, original_mix: { ...mix, sfx_gain_db: Infinity } },
      { ...recipe, original_mix: { ...mix, sfx_gain_db: 13 } },
      { ...recipe, original_mix: { ...mix, duck_attack_samples: 1.5 } },
      { ...recipe, original_mix: { ...mix, duck_release_samples: 48001 } },
      { ...recipe, original_mix: { ...mix, music_asset_id: id(902), sfx_asset_id: id(902) } }];
    for (const value of invalid) expect(() => assertRecipeHistoryPayload(value)).toThrow("saved recipe history");
  });
});
