import { describe, expect, it, vi } from "vitest";
import { fixtureProject } from "./fixture";
import { defaultOriginalMix, recipeDraftScope, recipeDraftStorageKey, recipeFields, savedDeliveryRecipe, RECIPE_DRAFT_STORAGE_PREFIX } from "./deliveryRecipe";
import {
  acknowledgeRecipeOriginal, createRecipeAuthorState, decodeRecipeRecovery, editRecipeAuthorState,
  inspectRecipeOutcome, observeRecipeAuthorState, prepareRecipeSubmission, readRecipeCopies,
  recipeFingerprint, recipeOutcomeMatches, recipePointerKey, recipeUnconfirmed, releaseRecipeOutcome,
  resolveRecipeAuthorField, retainRecipeFailure, retainReviewedRecipeIntent, writeRecipeRecovery,
} from "./deliveryRecipeDraft";
import type { RecipeAuthorState, RecipeCustody, RecipeOutcomeTransport } from "./deliveryRecipeDraft";
import type { DeliveryRecipe, RecipeGroup } from "./deliveryRecipe";
import type { Project, ProjectEvent } from "./types";

// Pure author-state and Storage fixtures, not service or native evidence.
const id = (n: number) => "abcdef00-0000-4000-8000-" + n.toString(16).padStart(12, "0");
class MemoryStorage implements Storage {
  readonly entries = new Map<string, string>();
  dropWrites = false; writes = 0; dropAt: number | null = null;
  get length() { return this.entries.size; }
  clear() { this.entries.clear(); }
  key(index: number) { return [...this.entries.keys()][index] ?? null; }
  getItem(key: string) { return this.entries.get(key) ?? null; }
  setItem(key: string, value: string) { this.writes++; if (!this.dropWrites && this.writes !== this.dropAt) this.entries.set(key, value); }
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
  return { project, profileId, sceneId, canonical, state, field, current, edit, submitted, receipt, custody };
}
function transport(project: Project, events: ProjectEvent[] = []): RecipeOutcomeTransport {
  return { readProject: vi.fn(async () => structuredClone(project)),
    readHistory: vi.fn(async (_project, after, limit) => structuredClone(events.filter(event => event.revision > after).slice(0, limit))) };
}
function event(project: Project, change: ProjectEvent["change"]): ProjectEvent {
  return { revision: project.revision, change, created_at: "2026-10-21T00:00:00.000Z" };
}
function stored(state: RecipeAuthorState, custody: RecipeCustody = { writerId: id(201), expectedRaw: null, expectedPointer: null }) {
  const local = new MemoryStorage(), session = new MemoryStorage();
  writeRecipeRecovery(local, session, state, custody);
  return { local, session, custody, key: recipeDraftStorageKey(state.input.scope, custody.writerId) };
}

describe("recipe original-response authority", () => {
  for (const group of ["film", "mix"] as const) {
    it(`${group}: a newer matching Project observation never consumes awaiting input`, () => {
      const { submitted, receipt, field } = sample(group);
      const observed = observeRecipeAuthorState(submitted.state, receipt);
      expect(observed.pending).toEqual(submitted.state.pending);
      expect(observed.input.awaiting).toEqual(submitted.state.input.awaiting);
      expect(observed.input.edits[field]).toEqual(submitted.state.input.edits[field]);
      expect(recipeUnconfirmed(observed)).toBe(true);
      expect(() => prepareRecipeSubmission(observed, receipt, id(101))).toThrow("Read the saved recipe and history");
      expect(() => resolveRecipeAuthorField(observed, receipt, field, "saved")).toThrow("unconfirmed");
    });
    it(`${group}: only the matching original r+1 receipt consumes unchanged submitted fields`, () => {
      const { submitted, receipt } = sample(group);
      const wrongId = acknowledgeRecipeOriginal(submitted.state, id(999), receipt);
      expect(wrongId).toBe(submitted.state);
      const confirmed = acknowledgeRecipeOriginal(submitted.state, id(100), receipt);
      expect(confirmed.pending).toBeNull(); expect(confirmed.input.awaiting).toBeUndefined();
      expect(confirmed.input.edits).toEqual({}); expect(confirmed.input.revision).toBe(21);
    });
  }
  for (const field of ["id", "generation", "active_branch"] as const) {
    it(`an original reply with another ${field} cannot acknowledge this recipe`, () => {
      const { submitted, receipt } = sample(); receipt[field] += "-other";
      expect(() => acknowledgeRecipeOriginal(submitted.state, id(100), receipt)).toThrow("belongs to another");
      expect(submitted.state.pending?.outcome).toBe("UNKNOWN");
    });
  }
  for (const revision of [20, 22]) it(`an original reply at r${revision} cannot replace the exact next-revision requirement`, () => {
    const { submitted, receipt } = sample(); receipt.revision = revision;
    expect(() => acknowledgeRecipeOriginal(submitted.state, id(100), receipt)).toThrow("exact submitted recipe and next revision");
  });
  it("equal edited values with a different untouched recipe group cannot acknowledge the original request", () => {
    const { submitted, receipt, profileId } = sample();
    receipt.production_design!.delivery_recipes![profileId].scene_intents[0].role = "payoff";
    expect(() => acknowledgeRecipeOriginal(submitted.state, id(100), receipt)).toThrow("exact submitted recipe");
  });
  it("keeps later numeric raw even after the author returns to the submitted value", () => {
    const { project, state, field, current } = sample();
    const raw = "  -04.500e0  ";
    const first = prepareRecipeSubmission(editRecipeAuthorState(state, field, raw, current), project, id(100));
    let later = editRecipeAuthorState(first.state, field, "-6", current);
    later = editRecipeAuthorState(later, field, raw, current);
    const receipt = structuredClone(project); receipt.revision++;
    receipt.production_design!.delivery_recipes![project.deliverables[0].id] = first.change.recipe!;
    const confirmed = acknowledgeRecipeOriginal(later, id(100), receipt);
    expect(confirmed.pending).toBeNull(); expect(confirmed.input.edits[field]).toEqual({ base: "-4.5", value: raw, intent: true });
    expect(confirmed.fieldSequences[field]).toBeGreaterThan(first.state.pending!.sequences[field]);
  });
  it("keeps Old → Sent → Old as a new intent after original ACK and allows a separate explicit save", () => {
    const { edit, submitted, receipt, field } = sample("film");
    const later = edit("hook", submitted.state), confirmed = acknowledgeRecipeOriginal(later, id(100), receipt);
    expect(confirmed.input.edits[field]).toEqual({ base: "reveal", value: "hook", intent: true });
    const next = prepareRecipeSubmission(confirmed, receipt, id(101));
    expect(next.change.recipe!.scene_intents[0].role).toBe("hook");
    expect(next.state.pending?.revision).toBe(21);
  });
  it("rejects no-op, content-locked, removed-profile and future-base submissions without changing the input", () => {
    const { project, state, edit } = sample();
    expect(() => prepareRecipeSubmission(state, project, id(100))).toThrow("already match");
    const raw = edit("-6"), before = structuredClone(raw);
    const locked = structuredClone(project); locked.locks.push({ id: id(900), kind: "content", resource: "project:" + project.id, note: "unit lock" });
    expect(() => prepareRecipeSubmission(raw, locked, id(100))).toThrow("content lock");
    const missing = structuredClone(project); missing.deliverables = [];
    expect(() => prepareRecipeSubmission(raw, missing, id(100))).toThrow("unavailable profile");
    const old = { ...project, revision: 19 };
    expect(() => prepareRecipeSubmission(raw, old, id(100))).toThrow("behind");
    expect(raw).toEqual(before);
  });
});

describe("recipe UNKNOWN review never replays or acknowledges", () => {
  it("after fresh history review, explicit abandonment resolves one field at a time without a Project write", async () => {
    const { project, state, current, field } = sample("film"), second = project.scenes[1].id + ":role";
    let edited = editRecipeAuthorState(state, field, "reveal", current);
    edited = editRecipeAuthorState(edited, second, "comparison", current);
    const submitted = prepareRecipeSubmission(edited, project, id(100));
    const before = structuredClone(project);
    const review = await inspectRecipeOutcome(submitted.state, project, transport(project));
    const released = releaseRecipeOutcome(submitted.state, project, review);
    const one = resolveRecipeAuthorField(released, project, field, "saved");
    expect(one.input.edits[field]).toBeUndefined(); expect(one.input.edits[second].value).toBe("comparison");
    const all = resolveRecipeAuthorField(one, project, second, "saved");
    expect(all.input.edits).toEqual({}); expect(all.pending).toBeNull(); expect(project).toEqual(before);
  });
  it("reads the saved snapshot twice and contiguous matching history, retaining UNKNOWN until an explicit local fork", async () => {
    const { submitted, receipt, profileId } = sample();
    const saved = transport(receipt, [event(receipt, submitted.change)]);
    const review = await inspectRecipeOutcome(submitted.state, receipt, saved);
    expect(saved.readProject).toHaveBeenCalledTimes(2); expect(saved.readHistory).toHaveBeenCalledTimes(1);
    expect(review.matchingValueRevisions).toEqual([21]); expect(submitted.state.pending?.outcome).toBe("UNKNOWN");
    expect(recipeOutcomeMatches(submitted.state, receipt, review)).toBe(true);
    const released = releaseRecipeOutcome(submitted.state, receipt, review);
    expect(released.pending).toBeNull(); expect(released.input.edits.sfx_gain_db.value).toBe("-4.5");
    expect(released.input.edits.sfx_gain_db.base).toBe("-4.5");
    const separate = editRecipeAuthorState(released, "sfx_gain_db", "-11", recipeFields(savedDeliveryRecipe(receipt, profileId), "mix"));
    expect(prepareRecipeSubmission(separate, receipt, id(101)).change.recipe!.original_mix!.sfx_gain_db).toBe(-11);
    expect(receipt.production_design!.delivery_recipes![profileId]).toEqual(submitted.change.recipe);
  });
  it("permits current empty history as a comparison, with no automatic new request", async () => {
    const { project, submitted } = sample();
    const review = await inspectRecipeOutcome(submitted.state, project, transport(project));
    expect(review.events).toEqual([]); expect(review.matchingValueRevisions).toEqual([]);
    const released = releaseRecipeOutcome(submitted.state, project, review);
    expect(released.pending).toBeNull(); expect(released.input.edits.sfx_gain_db.value).toBe("-4.5");
  });
  it("rejects incomplete, noncontiguous, excessive and changing saved-history windows", async () => {
    const { project, submitted, receipt } = sample();
    await expect(inspectRecipeOutcome(submitted.state, receipt, transport(receipt))).rejects.toThrow("incomplete");
    await expect(inspectRecipeOutcome(submitted.state, { ...receipt, revision: 22 }, transport({ ...receipt, revision: 22 }, [event({ ...receipt, revision: 22 }, submitted.change)]))).rejects.toThrow("contiguous");
    const huge = { ...project, revision: project.revision + 2049 };
    await expect(inspectRecipeOutcome(submitted.state, huge, transport(huge))).rejects.toThrow("bounded review window");
    const changing = transport(project); let reads = 0;
    changing.readProject = async () => ({ ...project, revision: project.revision + reads++ });
    await expect(inspectRecipeOutcome(submitted.state, project, changing)).rejects.toThrow("changed while recipe history");
  });
  it("rejects another saved identity and an old review after the project or pending comparison changes", async () => {
    const { project, submitted } = sample();
    await expect(inspectRecipeOutcome(submitted.state, project, transport({ ...project, generation: "other" }))).rejects.toThrow("changed");
    const review = await inspectRecipeOutcome(submitted.state, project, transport(project));
    expect(recipeOutcomeMatches(submitted.state, { ...project, revision: 21 }, review)).toBe(false);
    const different = retainRecipeFailure(submitted.state, id(100), "later transport diagnostic");
    expect(recipeOutcomeMatches(different, project, review)).toBe(false);
    expect(() => releaseRecipeOutcome(different, project, review)).toThrow("Read the current saved recipe");
  });
  it("retains an old UNKNOWN record byte-exact before releasing a separate intent", async () => {
    const { project, submitted } = sample(), original = stored(submitted.state);
    const raw = original.local.getItem(original.key);
    const review = await inspectRecipeOutcome(submitted.state, project, transport(project));
    const next = releaseRecipeOutcome(submitted.state, project, review);
    const fork = retainReviewedRecipeIntent(original.local, original.session, submitted.state, next, original.custody, id(202));
    expect(original.local.getItem(original.key)).toBe(raw);
    expect(decodeRecipeRecovery(fork.expectedRaw!, next.input.scope, "mix", id(202)).pending).toBeNull();
    expect(original.session.getItem(recipePointerKey(next.input.scope))).toBe(id(202));
  });
  it("a silent failure on the released checkpoint leaves both original and pointer-target snapshots UNKNOWN", async () => {
    const { project, submitted } = sample(), original = stored(submitted.state);
    const raw = original.local.getItem(original.key), review = await inspectRecipeOutcome(submitted.state, project, transport(project));
    const next = releaseRecipeOutcome(submitted.state, project, review);
    original.local.dropAt = original.local.writes + 2;
    expect(() => retainReviewedRecipeIntent(original.local, original.session, submitted.state, next, original.custody, id(202))).toThrow("not confirmed");
    expect(original.local.getItem(original.key)).toBe(raw);
    const pointed = original.session.getItem(recipePointerKey(next.input.scope))!;
    const recovered = decodeRecipeRecovery(original.local.getItem(recipeDraftStorageKey(next.input.scope, pointed))!, next.input.scope, "mix", pointed);
    expect(recovered.pending?.outcome).toBe("UNKNOWN"); expect(submitted.state.pending?.outcome).toBe("UNKNOWN");
  });
});

describe("recipe recovery custody and bounds", () => {
  it("reads back both author data and the tab pointer before confirming a checkpoint", () => {
    const { edit, custody } = sample(), state = edit("1e"), local = new MemoryStorage(), session = new MemoryStorage();
    local.dropWrites = true;
    expect(() => writeRecipeRecovery(local, session, state, custody)).toThrow("write was not confirmed");
    expect(session.length).toBe(0);
    local.dropWrites = false; session.dropWrites = true;
    expect(() => writeRecipeRecovery(local, session, state, custody)).toThrow("pointer was not confirmed");
    expect(local.getItem(recipeDraftStorageKey(state.input.scope, custody.writerId))).toBe(custody.expectedRaw);
    session.dropWrites = false; writeRecipeRecovery(local, session, state, custody);
    expect(session.getItem(recipePointerKey(state.input.scope))).toBe(custody.writerId);
  });
  it("a changed own raw or pointer blocks replacement and preserves foreign bytes", () => {
    const { edit } = sample(), state = edit("-4.5"), value = stored(state);
    value.local.setItem(value.key, "foreign bytes");
    expect(() => writeRecipeRecovery(value.local, value.session, state, value.custody)).toThrow("another window");
    expect(value.local.getItem(value.key)).toBe("foreign bytes");
    value.local.setItem(value.key, value.custody.expectedRaw!);
    value.session.setItem(recipePointerKey(state.input.scope), id(999));
    expect(() => writeRecipeRecovery(value.local, value.session, state, value.custody)).toThrow("Another writer");
    expect(value.session.getItem(recipePointerKey(state.input.scope))).toBe(id(999));
  });
  it("independent writers preserve cloned-tab source bytes and a clean checkpoint cannot be overwritten", () => {
    const { state, edit } = sample(), source = stored(state), original = source.local.getItem(source.key);
    expect(() => writeRecipeRecovery(source.local, source.session, edit("-4.5"), source.custody)).toThrow("clean recipe checkpoint is immutable");
    const ownTab = new MemoryStorage(); ownTab.setItem(recipePointerKey(state.input.scope), source.custody.writerId);
    const fork = { writerId: id(202), expectedRaw: null, expectedPointer: source.custody.writerId };
    writeRecipeRecovery(source.local, ownTab, edit("1e"), fork);
    expect(source.local.getItem(source.key)).toBe(original);
    expect(source.session.getItem(recipePointerKey(state.input.scope))).toBe(source.custody.writerId);
    expect(ownTab.getItem(recipePointerKey(state.input.scope))).toBe(id(202));
  });
  it("NOT_SENT is only live knowledge; a recovered copy is UNKNOWN and cannot be resubmitted", () => {
    const { project, submitted } = sample();
    const notSent = retainRecipeFailure(submitted.state, id(100), "pointer readback failed before dispatch", true);
    expect(notSent.pending?.outcome).toBe("NOT_SENT");
    expect(prepareRecipeSubmission(notSent, project, id(101)).state.pending?.id).toBe(id(101));
    const retained = stored(notSent), recovered = decodeRecipeRecovery(retained.custody.expectedRaw!, notSent.input.scope, "mix", retained.custody.writerId);
    expect(recovered.pending?.outcome).toBe("UNKNOWN");
    expect(() => prepareRecipeSubmission(recovered, project, id(101))).toThrow("Read the saved recipe and history");
  });
  it("legacy pending copies migrate as UNKNOWN with no forged original full-recipe receipt", () => {
    const { submitted, receipt } = sample();
    const recovered = decodeRecipeRecovery(JSON.stringify(submitted.state.input), submitted.state.input.scope, "mix", id(201));
    expect(recovered.pending?.outcome).toBe("UNKNOWN"); expect(recovered.pending?.fingerprint).toBeNull();
    expect(() => acknowledgeRecipeOriginal(recovered, id(201), receipt)).toThrow("exact submitted recipe");
    expect(observeRecipeAuthorState(recovered, receipt).pending).toEqual(recovered.pending);
  });
  it("strict decoding rejects corrupt, foreign, authority-bearing and out-of-range snapshots without writes", () => {
    const { submitted } = sample(), retained = stored(submitted.state), raw = retained.custody.expectedRaw!;
    const scope = submitted.state.input.scope;
    expect(() => decodeRecipeRecovery("{", scope, "mix", id(201))).toThrow();
    expect(() => decodeRecipeRecovery(raw, scope, "mix", id(202))).toThrow("another writer");
    expect(() => decodeRecipeRecovery(raw, recipeDraftScope({ ...sample().project, generation: "other" }, sample().profileId, "mix"), "mix", id(201))).toThrow();
    const extra = JSON.parse(raw); extra.state.effect_grant = "never authority";
    expect(() => decodeRecipeRecovery(JSON.stringify(extra), scope, "mix", id(201))).toThrow("author-input shape");
    const sequence = JSON.parse(raw); sequence.state.fieldSequences.sfx_gain_db = 999;
    expect(() => decodeRecipeRecovery(JSON.stringify(sequence), scope, "mix", id(201))).toThrow("retained range");
    expect(() => decodeRecipeRecovery(" ".repeat(256 * 1024 + 1), scope, "mix", id(201))).toThrow("256 KiB");
    expect(retained.local.getItem(retained.key)).toBe(raw);
  });
  it("a full copy budget preserves every prior byte and rejects a new checkpoint", () => {
    const { edit, custody } = sample(), state = edit("-4.5"), local = new MemoryStorage(), session = new MemoryStorage();
    for (let index = 0; index < 64; index++) local.setItem(RECIPE_DRAFT_STORAGE_PREFIX + "retained-" + index, "author-" + index);
    const before = [...local.entries];
    expect(() => writeRecipeRecovery(local, session, state, custody)).toThrow("64-copy / 2 MiB budget");
    expect([...local.entries]).toEqual(before); expect(session.length).toBe(0);
  });
  it("lists corrupt copies for explicit decisions and bounds the list without removing originals", () => {
    const { state } = sample(), local = new MemoryStorage();
    for (let index = 0; index < 65; index++) local.setItem(recipeDraftStorageKey(state.input.scope, id(1000 + index)), "corrupt-" + index);
    const before = [...local.entries], listed = readRecipeCopies(local, state.input.scope, "mix");
    expect(listed.copies).toHaveLength(64); expect(listed.limited).toBe(true);
    expect(listed.copies.every(copy => !copy.readable && copy.state === null)).toBe(true);
    expect([...local.entries]).toEqual(before);
  });
  it("fingerprints compare complete recipe meaning without imposing scene array ordering", () => {
    const { canonical, sceneId } = sample();
    const two = structuredClone(canonical); two.scene_intents.push({ scene_id: id(802), role: "payoff", archetype: "endcard" });
    const reversed = structuredClone(two); reversed.scene_intents.reverse();
    expect(recipeFingerprint(two)).toBe(recipeFingerprint(reversed));
    reversed.scene_intents.find(intent => intent.scene_id === sceneId)!.role = "problem";
    expect(recipeFingerprint(two)).not.toBe(recipeFingerprint(reversed));
  });
});
