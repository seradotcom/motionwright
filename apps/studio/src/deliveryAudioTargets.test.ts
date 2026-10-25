import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import {
  assertDeliveryAudioTargets, canonicalRecipeField, defaultOriginalMix, deliveryTargetMilli, emptyDeliveryRecipe,
  parseDeliveryAudioTarget, proposedDeliveryRecipe, recipeDraftScope, recipeDraftStorageKey, recipeFields, validateDeliveryRecipe,
} from "./deliveryRecipe";
import {
  acknowledgeRecipeOriginal, assertRecipeHistoryPayload, createRecipeAuthorState, decodeRecipeRecovery, editRecipeAuthorState,
  inspectRecipeOutcome, observeRecipeAuthorState, prepareRecipeSubmission, recipeFingerprint, recipeOutcomeMatches,
  recipePointerKey, readRecipeOwnRecovery, releaseRecipeOutcome, retainReviewedRecipeIntent, writeRecipeRecovery,
} from "./deliveryRecipeDraft";
import type { DeliveryAudioTargets, DeliveryRecipe, RecipeGroup } from "./deliveryRecipe";
import type { RecipeAuthorState, RecipeCustody, RecipeReadObservation } from "./deliveryRecipeDraft";
import type { Project, ProjectEvent } from "./types";

// Pure authoring/custody tests. These values are not measured audio or native QC.
const id = (n: number) => "cdefab00-0000-4000-8000-" + n.toString(16).padStart(12, "0");
const defaults = (): DeliveryAudioTargets => ({ target_lufs: -23, target_true_peak_dbtp: -1 });
function sample(targets: DeliveryAudioTargets | undefined = defaults(), group: RecipeGroup = "targets") {
  const project = structuredClone(fixtureProject); project.revision = 20; project.locks = [];
  project.scenes = project.scenes.slice(0, 2).map((scene, index) => ({ ...scene, start: { num: String(index), den: "1" }, duration: { num: "1", den: "1" } }));
  project.deliverables[0].included_scene_ids = [];
  if (project.deliverables.length < 2) project.deliverables.push({ ...structuredClone(project.deliverables[0]), id: id(2), name: "Other delivery" });
  const profileId = project.deliverables[0].id;
  const recipe: DeliveryRecipe = { schema: "motionwright.delivery-recipe/1",
    scene_intents: [{ scene_id: project.scenes[0].id, role: "hook", archetype: "statement" }],
    original_mix: { ...defaultOriginalMix(), voice_envelope: { muted: false, fade_in_samples: 2400, fade_out_samples: 1200 },
      music_envelope: { muted: true, fade_in_samples: 0, fade_out_samples: 600 }, sfx_envelope: { muted: false, fade_in_samples: 120, fade_out_samples: 240 } },
    ...(targets ? { audio_targets: structuredClone(targets) } : {}) };
  project.production_design = { plan: null, heroes: [], capsules: [], delivery_recipes: { [profileId]: recipe, [project.deliverables[1].id]: emptyDeliveryRecipe() } };
  const state = createRecipeAuthorState(project, profileId, group), current = recipeFields(recipe, group);
  return { project, profileId, recipe, state, current };
}
function withRecipe(project: Project, profileId: string, recipe: DeliveryRecipe | null): Project {
  const saved = structuredClone(project); saved.revision++;
  if (recipe) saved.production_design!.delivery_recipes![profileId] = structuredClone(recipe);
  else delete saved.production_design!.delivery_recipes![profileId];
  return saved;
}
function submitted() {
  const value = sample(), edited = editRecipeAuthorState(value.state, "target_lufs", "-14", value.current);
  const prepared = prepareRecipeSubmission(edited, value.project, id(100));
  return { ...value, prepared, receipt: withRecipe(value.project, value.profileId, prepared.change.recipe) };
}
class Store implements Storage {
  values = new Map<string, string>(); drop = false; onGet: ((key: string) => void) | null = null;
  get length() { return this.values.size; } key(index: number) { return [...this.values.keys()][index] ?? null; }
  getItem(key: string) { this.onGet?.(key); return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { if (!this.drop) this.values.set(key, value); }
  removeItem(key: string) { this.values.delete(key); } clear() { this.values.clear(); }
}
function retain(state: RecipeAuthorState) {
  const local = new Store(), session = new Store(), custody: RecipeCustody = { writerId: id(201), expectedRaw: null, expectedPointer: null };
  writeRecipeRecovery(local, session, state, custody); return { local, session, custody };
}
const event = (revision: number, change: ProjectEvent["change"]): ProjectEvent => ({ revision, change, created_at: "2026-10-21T00:00:00Z" });

describe("target recovery after the first pointer read fails", () => {
  const unread = (): RecipeReadObservation => ({ observed: false, pointer: null, copy: null });
  it("does not treat a throwing first pointer read as null and later recovers the untouched ordinary raw", () => {
    const { project, state, current } = sample(), edited = editRecipeAuthorState(state, "target_lufs", "  1e  ", current);
    const { local, session, custody } = retain(edited), before = custody.expectedRaw, read = session.getItem.bind(session), observation = unread();
    session.getItem = () => { throw new Error("First target pointer read unavailable"); };
    expect(() => readRecipeOwnRecovery(local, session, state, project, observation)).toThrow("unavailable");
    expect(observation).toEqual(unread()); session.getItem = read;
    const recovered = readRecipeOwnRecovery(local, session, state, project, observation);
    expect(recovered).toEqual(edited); expect(observation.observed).toBe(true); expect(observation.pointer).toBe(custody.writerId);
    expect(local.getItem(recipeDraftStorageKey(state.input.scope, custody.writerId))).toBe(before); expect(state.input.edits).toEqual({});
  });
  it("a first confirmed null keeps the entire current state including any pending comparison", () => {
    const { project, prepared } = submitted(), local = new Store(), session = new Store(), observation = unread();
    expect(readRecipeOwnRecovery(local, session, prepared.state, project, observation)).toBe(prepared.state);
    expect(observation).toEqual({ observed: true, pointer: null, copy: null }); expect(local.length).toBe(0); expect(session.length).toBe(0);
  });
  for (const [before, after] of [[id(201), null], [id(201), id(202)], [null, id(201)]] as const) {
    it(`rejects the observed pointer transition ${before ?? "null"} to ${after ?? "null"}`, () => {
      const { project, state, current } = sample(), edited = editRecipeAuthorState(state, "target_lufs", "-14", current);
      const { local, session, custody } = retain(edited), pointer = recipePointerKey(state.input.scope), raw = custody.expectedRaw;
      if (after === null) session.removeItem(pointer); else session.setItem(pointer, after);
      const observation: RecipeReadObservation = { observed: true, pointer: before, copy: null };
      expect(() => readRecipeOwnRecovery(local, session, state, project, observation)).toThrow("pointer changed");
      expect(observation.pointer).toBe(before); expect(session.getItem(pointer)).toBe(after);
      expect(local.getItem(recipeDraftStorageKey(state.input.scope, custody.writerId))).toBe(raw);
    });
  }
  it("anchors the first writer before a failed copy read and cannot replace it with a later null", () => {
    const { project, state, current } = sample(), edited = editRecipeAuthorState(state, "target_lufs", "-14", current);
    const { local, session, custody } = retain(edited), key = recipeDraftStorageKey(state.input.scope, custody.writerId), get = local.getItem.bind(local), observation = unread();
    local.getItem = () => { throw new Error("Target copy unavailable"); };
    expect(() => readRecipeOwnRecovery(local, session, state, project, observation)).toThrow("unavailable");
    expect(observation).toEqual({ observed: true, pointer: custody.writerId, copy: { key, raw: undefined } });
    local.getItem = get; session.removeItem(recipePointerKey(state.input.scope));
    expect(() => readRecipeOwnRecovery(local, session, state, project, observation)).toThrow("pointer changed");
    expect(local.getItem(key)).toBe(custody.expectedRaw);
  });
  it("keeps clear/ABA author decisions and the complete original UNKNOWN through fork and reload", () => {
    const { prepared, receipt, profileId } = submitted(), { local, session, custody } = retain(prepared.state), sourceKey = recipeDraftStorageKey(prepared.state.input.scope, custody.writerId), original = custody.expectedRaw;
    const current = recipeFields(receipt.production_design!.delivery_recipes![profileId], "targets");
    let later = createRecipeAuthorState(receipt, profileId, "targets");
    later = editRecipeAuthorState(later, "target_lufs", "Later incomplete input", current, true);
    later = editRecipeAuthorState(later, "target_lufs", "", current, true);
    later = editRecipeAuthorState(later, "target_true_peak_dbtp", "-2", current, true);
    later = editRecipeAuthorState(later, "target_true_peak_dbtp", current.target_true_peak_dbtp, current, true);
    const observation = unread(), recovered = readRecipeOwnRecovery(local, session, later, receipt, observation);
    expect(recovered.pending).toEqual(prepared.state.pending); expect(recovered.input.awaiting).toEqual(prepared.state.input.awaiting);
    expect(recovered.input.edits.target_lufs.value).toBe(""); expect(recovered.input.edits.target_true_peak_dbtp.value).toBe(current.target_true_peak_dbtp);
    expect(recovered.fieldSequences.target_lufs).toBeGreaterThan(prepared.state.pending!.sequences.target_lufs);
    const fork: RecipeCustody = { writerId: id(202), expectedRaw: null, expectedPointer: observation.pointer };
    writeRecipeRecovery(local, session, recovered, fork);
    expect(local.getItem(sourceKey)).toBe(original);
    const reloaded = decodeRecipeRecovery(fork.expectedRaw!, recovered.input.scope, "targets", fork.writerId);
    expect(reloaded).toEqual(recovered); expect(() => prepareRecipeSubmission(reloaded, receipt, id(203))).toThrow("saved recipe and history");
  });
  it("retains an ordinary ABA raw decision while restoring untouched fields from the earlier copy", () => {
    const { project, state, current } = sample();
    let original = editRecipeAuthorState(state, "target_lufs", "-14", current); original = editRecipeAuthorState(original, "target_true_peak_dbtp", "  -2.125  ", current);
    const stored = retain(original); let later = editRecipeAuthorState(state, "target_lufs", "1e", current, true);
    later = editRecipeAuthorState(later, "target_lufs", current.target_lufs, current, true);
    const recovered = readRecipeOwnRecovery(stored.local, stored.session, later, project, unread());
    expect(recovered.input.edits.target_lufs).toEqual({ base: current.target_lufs, value: current.target_lufs, intent: true });
    expect(recovered.input.edits.target_true_peak_dbtp.value).toBe("  -2.125  ");
  });
  for (const race of ["copy", "pointer"] as const) {
    it(`rejects a changed ${race} during readback before adopting the recovered input`, () => {
      const { project, state, current } = sample(), edited = editRecipeAuthorState(state, "target_lufs", "-14", current), before = structuredClone(state);
      const { local, session, custody } = retain(edited), key = recipeDraftStorageKey(state.input.scope, custody.writerId), pointer = recipePointerKey(state.input.scope), observation = unread(); let reads = 0;
      if (race === "copy") local.onGet = name => { if (name === key && ++reads === 2) local.values.set(key, "Changed source bytes"); };
      else session.onGet = name => { if (name === pointer && ++reads === 2) session.values.set(pointer, id(202)); };
      expect(() => readRecipeOwnRecovery(local, session, state, project, observation)).toThrow("changed during Retry");
      expect(state).toEqual(before); expect(observation.pointer).toBe(custody.writerId);
      expect(local.values.get(key)).toBe(race === "copy" ? "Changed source bytes" : custody.expectedRaw);
      expect(session.values.get(pointer)).toBe(race === "pointer" ? id(202) : custody.writerId);
    });
  }
  it("rereads a newly observed null and rejects a writer appearing before adoption", () => {
    const { project, state } = sample(), local = new Store(), session = new Store(), observation = unread(), pointer = recipePointerKey(state.input.scope); let reads = 0;
    session.onGet = key => { if (key === pointer && ++reads === 2) session.values.set(key, id(201)); };
    expect(() => readRecipeOwnRecovery(local, session, state, project, observation)).toThrow("changed during Retry");
    expect(observation).toEqual({ observed: true, pointer: null, copy: null }); expect(local.length).toBe(0);
  });
  it("preserves the legacy global writer's empty-scope behavior without discarding current raw", () => {
    const { project, state, current } = sample(), edited = editRecipeAuthorState(state, "target_lufs", "  1e  ", current), local = new Store(), session = new Store(), observation = unread();
    session.setItem("motionwright.delivery-recipe-draft/1:writer", id(201));
    expect(readRecipeOwnRecovery(local, session, edited, project, observation, true)).toBe(edited);
    expect(observation).toEqual({ observed: true, pointer: null, copy: null }); expect(local.length).toBe(0);
  });
  it("recovers a real legacy-writer UNKNOWN copy instead of treating the scoped null as no earlier attempt", () => {
    const { project, state, prepared } = submitted(), { local, session, custody } = retain(prepared.state), original = custody.expectedRaw, observation = unread();
    session.removeItem(recipePointerKey(state.input.scope)); session.setItem("motionwright.delivery-recipe-draft/1:writer", custody.writerId);
    const recovered = readRecipeOwnRecovery(local, session, state, project, observation, true);
    expect(recovered.pending).toEqual(prepared.state.pending); expect(recovered.input.awaiting).toEqual(prepared.state.input.awaiting);
    expect(observation.pointer).toBeNull(); expect(local.getItem(recipeDraftStorageKey(state.input.scope, custody.writerId))).toBe(original);
  });
  it("cannot merge a different current pending attempt into the recovered UNKNOWN", () => {
    const { project, prepared } = submitted(), { local, session, custody } = retain(prepared.state);
    const current = { ...prepared.state, pending: { ...prepared.state.pending!, id: id(299) } }, before = structuredClone(current);
    expect(() => readRecipeOwnRecovery(local, session, current, project, unread())).toThrow("separate uncertain recipe attempt");
    expect(current).toEqual(before); expect(local.getItem(recipeDraftStorageKey(prepared.state.input.scope, custody.writerId))).toBe(custody.expectedRaw);
  });
});

describe("closed nullable delivery audio-target wire", () => {
  for (const targets of [defaults(), { target_lufs: -23, target_true_peak_dbtp: null }, { target_lufs: null, target_true_peak_dbtp: -1 }])
    it("accepts the exact two-key target shape " + JSON.stringify(targets), () => {
      const { project, profileId, recipe } = sample(targets); validateDeliveryRecipe(project, profileId, recipe); assertRecipeHistoryPayload(recipe);
      assertDeliveryAudioTargets(targets); expect(Object.keys(targets).sort()).toEqual(["target_lufs", "target_true_peak_dbtp"]);
    });
  for (const targets of [null, undefined, {}, [], { target_lufs: -23 }, { target_true_peak_dbtp: -1 }, { target_lufs: null, target_true_peak_dbtp: null },
    { ...defaults(), approved: true }, { target_lufs: "-23", target_true_peak_dbtp: -1 }, { target_lufs: false, target_true_peak_dbtp: -1 }])
    it("rejects malformed present target container " + String(JSON.stringify(targets)), () => {
      const { project, profileId } = sample(), recipe = { ...emptyDeliveryRecipe(), audio_targets: targets };
      expect(() => validateDeliveryRecipe(project, profileId, recipe)).toThrow(); expect(() => assertRecipeHistoryPayload(recipe)).toThrow(/audio target/i);
    });
  it("absence is the only empty-target wire and remains compatible with a legacy recipe", () => {
    const { project, profileId } = sample(); const recipe = emptyDeliveryRecipe(); validateDeliveryRecipe(project, profileId, recipe); assertRecipeHistoryPayload(recipe);
    expect(recipeFields(recipe, "targets")).toEqual({ target_lufs: "", target_true_peak_dbtp: "" }); expect(Object.hasOwn(recipe, "audio_targets")).toBe(false);
  });
  for (const value of [NaN, Infinity, -Infinity, -23.0001, 0.0001, 2147483.648, -2147483.649])
    it("rejects non-exact or out-of-i32 target " + String(value), () => {
      expect(() => deliveryTargetMilli(value)).toThrow(/exact multiples/);
      expect(() => assertDeliveryAudioTargets({ target_lufs: value, target_true_peak_dbtp: null })).toThrow();
    });
  for (const value of [-2147483.648, 2147483.647, -23.001, -0.001, 0, 0.001, -1])
    it("retains the exact public milli-unit value " + value, () => {
      const milli = deliveryTargetMilli(value); expect(Number.isSafeInteger(milli)).toBe(true); expect(milli / 1000).toBe(value);
      assertDeliveryAudioTargets({ target_lufs: value, target_true_peak_dbtp: null });
    });
});

describe("exact raw target authoring and whole-recipe preservation", () => {
  for (const [raw, value] of [["  -023.0010  ", -23.001], ["-23001e-3", -23.001], [".001", 0.001], ["-2147483.648", -2147483.648], ["2147483.647", 2147483.647], ["-0e99999999999999999999", 0]] as const)
    it("parses exact target raw without replacing its buffer " + raw, () => {
      const { project, state, current } = sample(), edited = editRecipeAuthorState(state, "target_lufs", raw, current);
      expect(parseDeliveryAudioTarget(raw)).toBe(value); expect(canonicalRecipeField("targets", "target_lufs", raw)).toBe(String(value));
      const prepared = prepareRecipeSubmission(edited, project, id(100)); expect(prepared.change.recipe!.audio_targets!.target_lufs).toBe(value);
      expect(prepared.state.input.edits.target_lufs.value).toBe(raw);
    });
  for (const raw of ["1e", "0x10", "NaN", "Infinity", "1_000", "-23.0001", "-23.00000000000000000001", "0.0000000000000000001", "1e99999999999999999999", "1e-99999999999999999999", "2147483.648", "-2147483.649"])
    it("keeps invalid or finer raw but refuses dispatch " + raw, () => {
      const { project, state, current } = sample(), edited = editRecipeAuthorState(state, "target_lufs", raw, current), before = structuredClone(edited);
      const stored = retain(edited), decoded = decodeRecipeRecovery(stored.custody.expectedRaw!, state.input.scope, "targets", stored.custody.writerId);
      expect(decoded.input.edits.target_lufs.value).toBe(raw); expect(() => prepareRecipeSubmission(edited, project, id(100))).toThrow();
      expect(edited).toEqual(before); expect(project.revision).toBe(20);
    });
  it("edits targets without changing Film, global mix or any saved bus envelope", () => {
    const { project, recipe, state, current } = sample(), before = structuredClone(project), prepared = prepareRecipeSubmission(editRecipeAuthorState(state, "target_lufs", "-14", current), project, id(100));
    expect(prepared.change.recipe!.scene_intents).toEqual(recipe.scene_intents); expect(prepared.change.recipe!.original_mix).toEqual(recipe.original_mix);
    expect(prepared.change.recipe!.audio_targets).toEqual({ target_lufs: -14, target_true_peak_dbtp: -1 }); expect(project).toEqual(before);
  });
  for (const group of ["film", "mix"] as const) it(group + " edit preserves complete saved targets and envelopes", () => {
    const { project, recipe, state, current } = sample(defaults(), group);
    const field = group === "film" ? project.scenes[0].id + ":role" : "sfx_gain_db", raw = group === "film" ? "payoff" : "-11";
    const prepared = prepareRecipeSubmission(editRecipeAuthorState(state, field, raw, current), project, id(100));
    expect(prepared.change.recipe!.audio_targets).toEqual(recipe.audio_targets);
    for (const bus of ["voice_envelope", "music_envelope", "sfx_envelope"] as const) expect(prepared.change.recipe!.original_mix![bus]).toEqual(recipe.original_mix![bus]);
    expect(project.audio.mix).toEqual(fixtureProject.audio.mix);
  });
  it("saves a targets-only recipe as a non-null full recipe", () => {
    const { project, profileId } = sample(); project.production_design!.delivery_recipes![profileId] = emptyDeliveryRecipe();
    const state = createRecipeAuthorState(project, profileId, "targets"), current = recipeFields(null, "targets");
    const prepared = prepareRecipeSubmission(editRecipeAuthorState(state, "target_lufs", "-23", current), project, id(100));
    expect(prepared.change.recipe).toEqual({ ...emptyDeliveryRecipe(), audio_targets: { target_lufs: -23, target_true_peak_dbtp: null } });
    expect(recipeFingerprint(prepared.change.recipe)).not.toBe("null");
  });
  it("clears one optional target with explicit null and both by absence, preserving other recipe groups", () => {
    const { project, recipe, state, current } = sample(); let edited = editRecipeAuthorState(state, "target_lufs", " ", current);
    expect(prepareRecipeSubmission(edited, project, id(100)).change.recipe!.audio_targets).toEqual({ target_lufs: null, target_true_peak_dbtp: -1 });
    edited = editRecipeAuthorState(edited, "target_true_peak_dbtp", "", current); const proposed = proposedDeliveryRecipe(recipe, edited.input);
    expect(Object.hasOwn(proposed, "audio_targets")).toBe(false); expect(proposed.scene_intents).toEqual(recipe.scene_intents); expect(proposed.original_mix).toEqual(recipe.original_mix);
    expect(prepareRecipeSubmission(edited, project, id(100)).change.recipe).toEqual(proposed);
  });
  it("clearing the last values in a targets-only recipe sends the canonical null whole-recipe change", () => {
    const { project, profileId } = sample(); const recipe = { ...emptyDeliveryRecipe(), audio_targets: defaults() }; project.production_design!.delivery_recipes![profileId] = recipe;
    const current = recipeFields(recipe, "targets"); let state = createRecipeAuthorState(project, profileId, "targets");
    state = editRecipeAuthorState(state, "target_lufs", "", current); state = editRecipeAuthorState(state, "target_true_peak_dbtp", "", current);
    expect(prepareRecipeSubmission(state, project, id(100)).change.recipe).toBeNull();
  });
  it("does not consume unsent target intent merely because another saved revision has the same spelling", () => {
    const { project, profileId, recipe, state, current } = sample(); const edited = editRecipeAuthorState(state, "target_lufs", "-14", current);
    const observed = observeRecipeAuthorState(edited, withRecipe(project, profileId, { ...recipe, audio_targets: { target_lufs: -14, target_true_peak_dbtp: -1 } }));
    expect(observed.input.edits.target_lufs.value).toBe("-14"); expect(observed.input.edits.target_lufs.intent).toBe(true);
  });
});

describe("target original-response, recovery and history authority", () => {
  it("includes both targets and all other recipe groups in the original ACK fingerprint", () => {
    const { prepared, receipt, profileId } = submitted();
    for (const mutate of [(recipe: DeliveryRecipe) => { recipe.audio_targets!.target_true_peak_dbtp = -2; },
      (recipe: DeliveryRecipe) => { delete recipe.audio_targets; }, (recipe: DeliveryRecipe) => { recipe.scene_intents[0].role = "payoff"; },
      (recipe: DeliveryRecipe) => { recipe.original_mix!.music_envelope!.muted = false; }]) {
      const wrong = structuredClone(receipt); mutate(wrong.production_design!.delivery_recipes![profileId]);
      expect(() => acknowledgeRecipeOriginal(prepared.state, id(100), wrong)).toThrow(/exact submitted recipe/);
    }
    const reordered = structuredClone(receipt); reordered.production_design!.delivery_recipes![profileId].audio_targets = { target_true_peak_dbtp: -1, target_lufs: -14 };
    expect(acknowledgeRecipeOriginal(prepared.state, id(100), reordered).pending).toBeNull();
  });
  it("a peer's equal r+1 stays UNKNOWN and later B to A to B remains authored after the real original ACK", () => {
    const { prepared, receipt, current } = submitted(), observed = observeRecipeAuthorState(prepared.state, receipt);
    expect(observed.pending).toEqual(prepared.state.pending); expect(observed.input.awaiting).toEqual(prepared.state.input.awaiting);
    let later = editRecipeAuthorState(prepared.state, "target_lufs", "-23", current); later = editRecipeAuthorState(later, "target_lufs", "  -014.000  ", current);
    later = editRecipeAuthorState(later, "target_true_peak_dbtp", "1e", current);
    const ack = acknowledgeRecipeOriginal(later, id(100), receipt);
    expect(ack.pending).toBeNull(); expect(ack.input.edits.target_lufs.value).toBe("  -014.000  "); expect(ack.input.edits.target_true_peak_dbtp.value).toBe("1e");
    expect(ack.fieldSequences.target_lufs > prepared.state.pending!.sequences.target_lufs).toBe(true);
  });
  for (const identity of ["id", "generation", "active_branch"] as const) it("separates target input from another " + identity, () => {
    const { prepared, receipt } = submitted(), foreign = structuredClone(receipt); foreign[identity] = id(999);
    expect(() => acknowledgeRecipeOriginal(prepared.state, id(100), foreign)).toThrow(/another/);
  });
  it("scopes target copies separately from Film, mix and the second profile", () => {
    const { project, profileId, prepared } = submitted(), stored = retain(prepared.state);
    for (const [profile, group] of [[profileId, "film"], [profileId, "mix"], [project.deliverables[1].id, "targets"]] as const) {
      const scope = recipeDraftScope(project, profile, group); expect(scope).not.toBe(prepared.state.input.scope);
      expect(() => decodeRecipeRecovery(stored.custody.expectedRaw!, scope, group, stored.custody.writerId)).toThrow();
    }
  });
  it("data quota/silent writes and pointer loss cannot confirm a target reservation", () => {
    const { prepared } = submitted();
    for (const mode of ["data", "pointer"] as const) {
      const local = new Store(), session = new Store(), custody = { writerId: id(201), expectedRaw: null, expectedPointer: null };
      if (mode === "data") local.drop = true; else session.drop = true;
      expect(() => writeRecipeRecovery(local, session, prepared.state, custody)).toThrow(/not confirmed/);
      expect(prepared.state.pending?.outcome).toBe("UNKNOWN");
    }
  });
  it("full target payloads in contiguous saved history inform review without acknowledging or replaying the request", async () => {
    const { prepared, receipt, project } = submitted(), events = [event(receipt.revision, prepared.change)]; let reads = 0;
    const review = await inspectRecipeOutcome(prepared.state, receipt, { readProject: async () => { reads++; return structuredClone(receipt); }, readHistory: async () => structuredClone(events) });
    expect(reads).toBe(2); expect(review.matchingValueRevisions).toEqual([receipt.revision]); expect(prepared.state.pending?.outcome).toBe("UNKNOWN");
    const next = releaseRecipeOutcome(prepared.state, receipt, review), original = retain(prepared.state), key = recipeDraftStorageKey(prepared.state.input.scope, original.custody.writerId), raw = original.local.getItem(key);
    const fork = retainReviewedRecipeIntent(original.local, original.session, prepared.state, next, original.custody, id(202));
    expect(original.local.getItem(key)).toBe(raw); expect(original.session.getItem(recipePointerKey(next.input.scope))).toBe(fork.writerId);
    expect(next.input.edits.target_lufs.value).toBe("-14"); expect(next.pending).toBeNull(); expect(project.revision).toBe(20);
  });
  it("unknown or malformed target history cannot release a retained attempt", async () => {
    const { prepared, receipt } = submitted(); const bad = structuredClone(prepared.change);
    (bad.recipe as unknown as { audio_targets: unknown }).audio_targets = { target_lufs: -14, target_true_peak_dbtp: null, approved: true };
    await expect(inspectRecipeOutcome(prepared.state, receipt, { readProject: async () => receipt, readHistory: async () => [event(receipt.revision, bad)] })).rejects.toThrow(/delivery audio targets/i);
    expect(prepared.state.pending?.outcome).toBe("UNKNOWN");
  });
  it("changing targets in the terminal read invalidates the complete recipe history review", async () => {
    const { prepared, receipt, profileId } = submitted(); let reads = 0;
    await expect(inspectRecipeOutcome(prepared.state, receipt, { readProject: async () => { const project = structuredClone(receipt); if (++reads > 1) project.production_design!.delivery_recipes![profileId].audio_targets!.target_lufs = -15; return project; },
      readHistory: async () => [event(receipt.revision, prepared.change)] })).rejects.toThrow(/changed while/);
    const review = await inspectRecipeOutcome(prepared.state, receipt, { readProject: async () => receipt, readHistory: async () => [event(receipt.revision, prepared.change)] });
    expect(recipeOutcomeMatches(prepared.state, { ...receipt, generation: id(999) }, review)).toBe(false);
  });
});
