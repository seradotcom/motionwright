import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import { planContentDigest } from "./creativeProduction";
import type { Change, Project, ProjectEvent } from "./types";
import {
  acknowledgeProductionDraft, assertProductionOrigins, cleanProductionPlan, createProductionDraft, decodeProductionCopy, editProductionDraft,
  encodeProductionCopy, encodeProductionFile, inspectProductionOutcome, prepareProductionSubmission, productionConflict,
  productionCopyKey, productionOutcomeMatches, productionPointerKey, productionPreparationMatches, productionScope,
  productionUnconfirmed, readProductionCopies, readProductionRecoveryPointer, recoverProductionAfterReadFailure, reconcileProductionDraft, releaseProductionOutcome, resolveProductionDraft,
  retainProductionFailure, retainProductionOrigin, retainReviewedProductionIntent, writeProductionCopy, PRODUCTION_RECOVERY_PREFIX,
} from "./productionDesignDraft";
import type { ProductionCustody, ProductionDraft, ProductionInput, ProductionStorage, ProductionTarget } from "./productionDesignDraft";

const id = "00000000-0000-4000-8000-000000000001", writer = "00000000-0000-4000-8000-000000000002", fork = "00000000-0000-4000-8000-000000000003";
function source(): Project {
  const project = structuredClone(fixtureProject); project.locks = []; project.branch_workspaces = [];
  project.production_design = { plan: null, heroes: [], capsules: [] }; return project;
}
function target(project: Project, kind: "hero" | "plan"): ProductionTarget { return kind === "plan" ? { kind } : { kind, sceneId: project.scenes[0].id }; }
function edit(draft: ProductionDraft, text: string): ProductionDraft {
  const input = draft.input;
  return editProductionDraft(draft, input.kind === "hero" ? { ...input, config: { ...input.config, headline: text } }
    : { ...input, plan: { ...input.plan, concept: text } });
}
async function armed(kind: "hero" | "plan" = "plan") {
  const project = source(), draft = edit(createProductionDraft(project, target(project, kind)), "Author direction");
  return { project, source: draft, prepared: await prepareProductionSubmission(draft, project, id) };
}
class MemoryStorage implements ProductionStorage {
  values = new Map<string, string>();
  get length() { return this.values.size; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); }
  removeItem(key: string) { this.values.delete(key); }
}
const custody = (): ProductionCustody => ({ writerId: writer, expectedRaw: null, expectedPointer: null });
const journal = (project: Project, change: Change): ProjectEvent[] => [{ revision: project.revision, change, created_at: "2026-10-21T00:00:00Z" }];

describe("Production author input and original response authority", () => {
  it("scopes component and plan input by project, generation, branch and component scene", () => {
    const project = source(), hero = target(project, "hero"), plan = target(project, "plan");
    expect(productionScope(project, hero)).not.toBe(productionScope(project, plan));
    for (const field of ["id", "generation", "active_branch"] as const)
      expect(productionScope({ ...project, [field]: project[field] + "-other" }, hero)).not.toBe(productionScope(project, hero));
    expect(productionScope(project, { kind: "hero", sceneId: project.scenes[1].id })).not.toBe(productionScope(project, hero));
  });
  it("retains exact plan text, empty lines, reviewer, source references and clock without cleaning author input", () => {
    const project = source(), draft = createProductionDraft(project, { kind: "plan" }), input = draft.input as Extract<ProductionInput, { kind: "plan" }>;
    const changed = editProductionDraft(draft, { ...input, reviewer: "  Reviewer  ", note: "  reason\n ", plan: { ...input.plan,
      concept: "  concept\n ", reference_constraints: ["  source one  ", "", " source two "],
      shots: input.plan.shots.map((shot, index) => index ? shot : { ...shot, asset_ids: [project.assets[0].id], claim_ids: ["author-claim"] }),
      clock: { kind: "music", asset_id: project.assets[0].id, beats: [{ num: "1", den: "3" }] } } });
    const copy = encodeProductionCopy({ schema: "motionwright.production-recovery/1", scope: changed.scope, writerId: writer, updatedAt: 1, draft: changed });
    expect(decodeProductionCopy(copy, changed.scope, writer).draft.input).toEqual(changed.input);
    expect(changed.input).not.toEqual(input); expect(draft.input).toEqual(input);
  });
  for (const kind of ["hero", "plan"] as const) {
    it(`an observed identical ${kind} value at r+1 never consumes its pending request`, async () => {
      const { project, prepared } = await armed(kind), observed = await applyChange(project, prepared.change);
      const reconciled = reconcileProductionDraft(prepared.draft, observed);
      expect(reconciled).toBe(prepared.draft); expect(productionUnconfirmed(reconciled)).toBe(true);
      expect(productionConflict(reconciled, observed)).toBe(true);
    });
    it(`the original ${kind} reply preserves later raw input including a return to the old value`, async () => {
      const { project, prepared } = await armed(kind), original = createProductionDraft(project, target(project, kind)).input;
      const later = editProductionDraft(prepared.draft, original), receipt = await applyChange(project, prepared.change);
      const acknowledged = acknowledgeProductionDraft(later, id, receipt);
      expect(acknowledged.input).toEqual(original); expect(acknowledged.pending).toBeNull();
      expect(acknowledged.intent).toBe(true); expect(acknowledged.baseRevision).toBe(receipt.revision);
      expect(productionConflict(acknowledged, receipt)).toBe(false);
      const next = await prepareProductionSubmission(acknowledged, receipt, fork);
      expect(next.draft.pending?.sequence).toBe(later.sequence);
    });
  }
  for (const field of ["id", "generation", "active_branch", "revision", "value"] as const) {
    it(`rejects an original response with the wrong ${field} without consuming raw input`, async () => {
      const { project, prepared } = await armed(), receipt = await applyChange(project, prepared.change);
      if (field === "revision") receipt.revision++;
      else if (field === "value") receipt.production_design!.plan!.concept = "Different value";
      else receipt[field] += "-other";
      expect(() => acknowledgeProductionDraft(prepared.draft, id, receipt)).toThrow();
      expect(prepared.draft.pending?.id).toBe(id);
    });
  }
  it("treats an unrelated observed revision as a reviewable conflict rather than silently rebasing authored intent", () => {
    const project = source(), draft = edit(createProductionDraft(project, { kind: "plan" }), "Held raw input"), updated = { ...project, revision: project.revision + 1 };
    expect(reconcileProductionDraft(draft, updated)).toBe(draft); expect(productionConflict(draft, updated)).toBe(true);
    const kept = resolveProductionDraft(draft, updated, "draft"); expect(kept.input).toEqual(draft.input); expect(kept.baseRevision).toBe(updated.revision);
  });
  it("invalidates a prepared digest when input, owner, revision, pending attempt, or source target changes", async () => {
    const { project, source: draft } = await armed();
    expect(productionPreparationMatches(draft, project, draft, project)).toBe(true);
    expect(productionPreparationMatches(draft, project, edit(draft, "Later concept"), project)).toBe(false);
    for (const field of ["id", "generation", "active_branch"] as const)
      expect(productionPreparationMatches(draft, project, draft, { ...project, [field]: project[field] + "-other" })).toBe(false);
    expect(productionPreparationMatches(draft, project, draft, { ...project, revision: project.revision + 1 })).toBe(false);
    const prepared = await prepareProductionSubmission(draft, project, id);
    expect(productionPreparationMatches(draft, project, prepared.draft, project)).toBe(false);
  });
  it("cleans only a plan submission and binds approval to that exact content while retaining author bytes", async () => {
    const project = source(), draft = createProductionDraft(project, { kind: "plan" }), input = draft.input as Extract<ProductionInput, { kind: "plan" }>;
    const authored = editProductionDraft(draft, { ...input, reviewer: "Reviewer", note: "Human decision",
      plan: { ...input.plan, reference_constraints: ["  first  ", "", " second "] } });
    const prepared = await prepareProductionSubmission(authored, project, id, "approve");
    expect(prepared.change.type).toBe("set_production_plan");
    if (prepared.change.type !== "set_production_plan" || !prepared.change.plan) throw new Error("Missing approved plan.");
    expect(prepared.change.plan.reference_constraints).toEqual(["first", "second"]);
    expect(prepared.change.plan.approval?.content_sha256).toBe(await planContentDigest(prepared.change.plan));
    expect(cleanProductionPlan(prepared.change.plan).approval).toBeNull();
    expect(prepared.draft.input).toEqual(authored.input);
    const acknowledged = acknowledgeProductionDraft(prepared.draft, id, await applyChange(project, prepared.change));
    expect(acknowledged.input).toEqual(authored.input); expect(acknowledged.intent).toBe(false);
  });
  it("keeps editable scene objects on detach and rejects a receipt that changes them", async () => {
    const { project, prepared } = await armed("hero"), saved = await applyChange(project, prepared.change);
    const draft = createProductionDraft(saved, target(saved, "hero")), detach = await prepareProductionSubmission(draft, saved, fork, "detach");
    const receipt = await applyChange(saved, detach.change), changed = structuredClone(receipt);
    changed.scenes[0].nodes.pop();
    expect(() => acknowledgeProductionDraft(detach.draft, fork, changed)).toThrow("preserve");
    const acknowledged = acknowledgeProductionDraft(detach.draft, fork, receipt);
    expect(acknowledged.input).toEqual(draft.input); expect(acknowledged.pending).toBeNull(); expect(acknowledged.intent).toBe(true);
    expect(receipt.scenes[0].nodes).toEqual(saved.scenes[0].nodes);
  });
  it("will not dispatch plan or component submissions through content locks", async () => {
    const { project, source: draft } = await armed();
    project.locks.push({ id: writer, resource: "project:" + project.id, kind: "content", note: "Human lock" });
    await expect(prepareProductionSubmission(draft, project, id)).rejects.toThrow("locked");
  });
});

describe("Production durable custody", () => {
  it("recovers NOT_SENT conservatively as UNKNOWN and exports complete raw input without write authority", async () => {
    const { prepared } = await armed(), draft = retainProductionFailure(prepared.draft, id, "data unconfirmed", true);
    const raw = encodeProductionCopy({ schema: "motionwright.production-recovery/1", scope: draft.scope, writerId: writer, updatedAt: 1, draft });
    expect(decodeProductionCopy(raw, draft.scope, writer).draft.pending?.outcome).toBe("UNKNOWN");
    const file = JSON.parse(encodeProductionFile(draft));
    expect(file.draft.input).toEqual(draft.input); expect(file.draft.pending.outcome).toBe("UNKNOWN");
    expect(Object.keys(file)).toEqual(["schema", "exportedAt", "draft"]); expect(raw).not.toContain("effect_grant");
  });
  it("rejects foreign scope, writer and authority fields without altering any stored bytes", async () => {
    const { prepared } = await armed(), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
    writeProductionCopy(store, pointers, prepared.draft, owner); const raw = owner.expectedRaw!;
    expect(() => decodeProductionCopy(raw, "other scope", writer)).toThrow(); expect(() => decodeProductionCopy(raw, prepared.draft.scope, fork)).toThrow();
    const foreign = JSON.parse(raw); foreign.draft.effect_grant = "not authority";
    expect(() => decodeProductionCopy(JSON.stringify(foreign), prepared.draft.scope, writer)).toThrow();
    expect(store.getItem(productionCopyKey(prepared.draft.scope, writer))).toBe(raw);
  });
  for (const fault of ["data", "pointer"] as const) {
    it(`requires confirmed ${fault} storage before treating a Production backup as durable`, async () => {
      const { prepared } = await armed(), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
      const affected = fault === "data" ? store : pointers, set = affected.setItem.bind(affected); affected.setItem = () => {};
      expect(() => writeProductionCopy(store, pointers, prepared.draft, owner)).toThrow("confirmed");
      affected.setItem = set; writeProductionCopy(store, pointers, prepared.draft, owner);
      expect(pointers.getItem(productionPointerKey(prepared.draft.scope))).toBe(writer);
      expect(store.getItem(productionCopyKey(prepared.draft.scope, writer))).toBe(owner.expectedRaw);
    });
  }
  for (const race of ["data", "pointer"] as const) {
    it(`rechecks ${race} custody after budget enumeration before replacing any author bytes`, async () => {
      const { prepared } = await armed(), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
      writeProductionCopy(store, pointers, prepared.draft, owner);
      const key = productionCopyKey(prepared.draft.scope, writer), pointer = productionPointerKey(prepared.draft.scope);
      const original = store.key.bind(store); let raceArmed = true;
      store.key = index => { if (raceArmed) { raceArmed = false; if (race === "data") store.values.set(key, "other author's bytes"); else pointers.values.set(pointer, fork); } return original(index); };
      expect(() => writeProductionCopy(store, pointers, edit(prepared.draft, "Later input"), owner)).toThrow("changed");
      if (race === "data") expect(store.getItem(key)).toBe("other author's bytes"); else expect(pointers.getItem(pointer)).toBe(fork);
    });
  }
  it("rereads the data after pointer publication and never adopts the interleaved replacement", async () => {
    const { prepared } = await armed(), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
    const set = pointers.setItem.bind(pointers), key = productionCopyKey(prepared.draft.scope, writer);
    pointers.setItem = (name, value) => { set(name, value); store.values.set(key, "interleaved replacement"); };
    expect(() => writeProductionCopy(store, pointers, prepared.draft, owner)).toThrow("pointer was published");
    expect(owner.expectedRaw).not.toBe("interleaved replacement"); expect(store.getItem(key)).toBe("interleaved replacement");
    pointers.setItem = set; expect(() => writeProductionCopy(store, pointers, prepared.draft, owner)).toThrow("changed");
  });
  it("retains corrupt copies and refuses to mutate a clean checkpoint", () => {
    const project = source(), draft = createProductionDraft(project, { kind: "plan" }), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
    writeProductionCopy(store, pointers, draft, owner); const old = owner.expectedRaw;
    expect(() => writeProductionCopy(store, pointers, edit(draft, "Later input"), owner)).toThrow("immutable");
    const badKey = productionCopyKey(draft.scope, fork); store.setItem(badKey, "{corrupt");
    const entries = readProductionCopies(store, draft.scope).entries;
    expect(entries.find(entry => entry.key === badKey)?.value).toBeNull(); expect(store.getItem(badKey)).toBe("{corrupt");
    expect(store.getItem(productionCopyKey(draft.scope, writer))).toBe(old);
  });
  it("refuses a full recovery budget without deleting older copies or the live author's input", async () => {
    const { prepared } = await armed(), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
    for (let index = 0; index < 64; index++) store.setItem(PRODUCTION_RECOVERY_PREFIX + "older-" + index, "retained bytes");
    const before = [...store.values]; expect(() => writeProductionCopy(store, pointers, prepared.draft, owner)).toThrow("full");
    expect([...store.values]).toEqual(before); expect(prepared.draft.pending?.id).toBe(id); expect(pointers.length).toBe(0);
  });
});

describe("Production UNKNOWN review without inferred acknowledgement or replay", () => {
  it("reads two current snapshots and complete history; matching values remain UNKNOWN until explicit separate intent", async () => {
    const { project, prepared } = await armed(), saved = await applyChange(project, prepared.change), events = journal(saved, prepared.change);
    let reads = 0;
    const review = await inspectProductionOutcome(prepared.draft, saved, { readProject: async () => { reads++; return saved; }, readHistory: async () => events });
    expect(reads).toBe(2); expect(review.matchingValueRevisions).toEqual([saved.revision]); expect(productionUnconfirmed(prepared.draft)).toBe(true);
    expect(productionOutcomeMatches(prepared.draft, saved, review)).toBe(true);
    const next = releaseProductionOutcome(prepared.draft, saved, review);
    expect(next.pending).toBeNull(); expect(next.input).toEqual(prepared.draft.input); expect(next.intent).toBe(true);
    expect(productionOutcomeMatches(prepared.draft, { ...saved, revision: saved.revision + 1 }, review)).toBe(false);
  });
  it("rejects truncated history and a project that changes during inspection", async () => {
    const { project, prepared } = await armed(), saved = await applyChange(project, prepared.change);
    await expect(inspectProductionOutcome(prepared.draft, saved, { readProject: async () => saved, readHistory: async () => [] })).rejects.toThrow("incomplete");
    let reads = 0;
    await expect(inspectProductionOutcome(prepared.draft, saved, { readProject: async () => ++reads === 1 ? saved : { ...saved, revision: saved.revision + 1 },
      readHistory: async () => journal(saved, prepared.change) })).rejects.toThrow("changed");
  });
  it("retains the old UNKNOWN through a verified two-phase new writer before releasing a separate intent", async () => {
    const { project, prepared } = await armed(), saved = await applyChange(project, prepared.change);
    const review = await inspectProductionOutcome(prepared.draft, saved, { readProject: async () => saved, readHistory: async () => journal(saved, prepared.change) });
    const next = releaseProductionOutcome(prepared.draft, saved, review), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
    writeProductionCopy(store, pointers, prepared.draft, owner); const old = owner.expectedRaw;
    const result = retainReviewedProductionIntent(store, pointers, prepared.draft, next, owner, fork);
    expect(store.getItem(productionCopyKey(prepared.draft.scope, writer))).toBe(old);
    expect(decodeProductionCopy(result.expectedRaw!, next.scope, fork).draft.pending).toBeNull();
    expect(pointers.getItem(productionPointerKey(next.scope))).toBe(fork);
  });
  it("a failed second fork phase leaves a recoverable UNKNOWN and the earlier author bytes intact", async () => {
    const { project, prepared } = await armed(), saved = await applyChange(project, prepared.change);
    const review = await inspectProductionOutcome(prepared.draft, saved, { readProject: async () => saved, readHistory: async () => journal(saved, prepared.change) });
    const next = releaseProductionOutcome(prepared.draft, saved, review), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
    writeProductionCopy(store, pointers, prepared.draft, owner); const old = owner.expectedRaw, set = store.setItem.bind(store);
    store.setItem = (key, raw) => { if (key.endsWith(fork) && JSON.parse(raw).draft.pending === null) throw new Error("Simulated quota"); set(key, raw); };
    expect(() => retainReviewedProductionIntent(store, pointers, prepared.draft, next, owner, fork)).toThrow("quota");
    expect(store.getItem(productionCopyKey(prepared.draft.scope, writer))).toBe(old);
    expect(decodeProductionCopy(store.getItem(productionCopyKey(next.scope, fork))!, next.scope, fork).draft.pending?.outcome).toBe("UNKNOWN");
  });
});

describe("Production recovery after an initially unreadable pointer or copy", () => {
  it("does not treat a failed first pointer read as an observed empty writer", () => {
    const draft = createProductionDraft(source(), { kind: "plan" }), pointers = new MemoryStorage();
    pointers.setItem(productionPointerKey(draft.scope), writer);
    const get = pointers.getItem.bind(pointers), prior = { observed: false, expectedPointer: null };
    let fail = true;
    pointers.getItem = key => { if (fail) { fail = false; throw new Error("Transient first read"); } return get(key); };
    expect(() => readProductionRecoveryPointer(pointers, draft.scope, prior)).toThrow("Transient first read");
    expect(prior).toEqual({ observed: false, expectedPointer: null });
    expect(readProductionRecoveryPointer(pointers, draft.scope, prior)).toEqual({ observed: true, expectedPointer: writer });
  });
  it("confirms an initially unknown empty pointer and then rejects a writer appearing after that observation", () => {
    const draft = createProductionDraft(source(), { kind: "plan" }), pointers = new MemoryStorage();
    const observation = readProductionRecoveryPointer(pointers, draft.scope, { observed: false, expectedPointer: null });
    expect(observation).toEqual({ observed: true, expectedPointer: null });
    expect(readProductionRecoveryPointer(pointers, draft.scope, observation)).toEqual(observation);
    pointers.setItem(productionPointerKey(draft.scope), writer);
    expect(() => readProductionRecoveryPointer(pointers, draft.scope, observation)).toThrow("pointer changed");
  });
  it("rejects replacement or removal of a previously observed writer after a copy read failed", () => {
    const draft = createProductionDraft(source(), { kind: "plan" }), pointers = new MemoryStorage(), prior = { observed: true, expectedPointer: writer };
    for (const replacement of [fork, null]) {
      if (replacement === null) pointers.removeItem(productionPointerKey(draft.scope)); else pointers.setItem(productionPointerKey(draft.scope), replacement);
      expect(() => readProductionRecoveryPointer(pointers, draft.scope, prior)).toThrow("pointer changed");
      expect(pointers.getItem(productionPointerKey(draft.scope))).toBe(replacement);
    }
  });
  for (const kind of ["hero", "plan"] as const) {
    it(`restores intact ordinary ${kind} input without manufacturing a new edit after a failed copy read`, () => {
      const project = source(), original = edit(createProductionDraft(project, target(project, kind)), "  Earlier author input\n  ");
      const raw = encodeProductionCopy({ schema: "motionwright.production-recovery/1", scope: original.scope, writerId: writer, updatedAt: 1, draft: original });
      const recovered = decodeProductionCopy(raw, original.scope, writer).draft, placeholder = createProductionDraft(project, target(project, kind));
      const next = recoverProductionAfterReadFailure(recovered, placeholder, false, project);
      expect(next).toEqual(recovered); expect(next.input).toEqual(original.input); expect(next.sequence).toBe(original.sequence);
      expect(next.pending).toBeNull(); expect(placeholder.sequence).toBe(0); expect(placeholder.intent).toBe(false);
    });
    for (const gesture of ["clear", "ABA"] as const) {
      it(`retains an explicit ${gesture} ${kind} edit made while recovery was blocked together with the original UNKNOWN`, async () => {
        const { project, prepared } = await armed(kind), initial = createProductionDraft(project, target(project, kind));
        const interim = edit(initial, "Temporary input while the original is unreadable");
        const current = gesture === "clear" ? edit(interim, "") : editProductionDraft(interim, initial.input);
        const before = structuredClone(prepared.draft);
        const next = recoverProductionAfterReadFailure(prepared.draft, current, true, project);
        expect(next.input).toEqual(current.input); expect(next.intent).toBe(true);
        expect(next.sequence).toBe(Math.max(current.sequence, prepared.draft.sequence) + 1);
        expect(next.sequence).toBeGreaterThan(next.pending!.sequence); expect(next.pending).toEqual(prepared.draft.pending);
        expect(prepared.draft).toEqual(before); expect(productionUnconfirmed(next)).toBe(true);
      });
    }
  }
  it("recovers untouched pending input rather than the placeholder and treats an old NOT_SENT label as UNKNOWN", async () => {
    const { project, prepared } = await armed(), prior = retainProductionFailure(prepared.draft, id, "Original caller did not dispatch", true);
    const next = recoverProductionAfterReadFailure(prior, createProductionDraft(project, { kind: "plan" }), false, project);
    expect(next.input).toEqual(prior.input); expect(next.sequence).toBe(prior.sequence); expect(next.intent).toBe(prior.intent);
    expect(next.pending).toEqual({ ...prior.pending!, outcome: "UNKNOWN" }); expect(prior.pending?.outcome).toBe("NOT_SENT");
  });
  it("rejects recovery into another target or over a different pending attempt", async () => {
    const { project, prepared } = await armed();
    expect(() => recoverProductionAfterReadFailure(prepared.draft, createProductionDraft(project, target(project, "hero")), false, project)).toThrow("another editor target");
    const other = { ...prepared.draft, pending: { ...prepared.draft.pending!, id: fork } };
    expect(() => recoverProductionAfterReadFailure(prepared.draft, other, true, project)).toThrow("attempts differ");
    expect(other.pending.id).toBe(fork); expect(prepared.draft.pending?.id).toBe(id);
  });
  it("refuses sequence overflow while retaining both the earlier and later input", () => {
    const project = source(), original = edit(createProductionDraft(project, { kind: "plan" }), "Earlier input");
    const current = { ...edit(original, "Later input"), sequence: Number.MAX_SAFE_INTEGER };
    expect(() => recoverProductionAfterReadFailure(original, current, true, project)).toThrow("cannot advance safely");
    expect(() => editProductionDraft(current, original.input)).toThrow("cannot advance safely");
    expect(current.sequence).toBe(Number.MAX_SAFE_INTEGER); expect(current.input).not.toEqual(original.input);
  });
  it("forks recovered UNKNOWN input while retaining exact original bytes through reload without nesting each reload again", async () => {
    const { project, prepared } = await armed(), store = new MemoryStorage(), pointers = new MemoryStorage(), originalOwner = custody();
    writeProductionCopy(store, pointers, prepared.draft, originalOwner);
    const key = productionCopyKey(prepared.draft.scope, writer), raw = originalOwner.expectedRaw!;
    const recovered = retainProductionOrigin(decodeProductionCopy(raw, prepared.draft.scope, writer).draft, { key, raw });
    const current = edit(createProductionDraft(project, { kind: "plan" }), "Later local input");
    const next = recoverProductionAfterReadFailure(recovered, current, true, project), owner: ProductionCustody = { writerId: fork, expectedRaw: null, expectedPointer: writer };
    writeProductionCopy(store, pointers, next, owner); expect(store.getItem(key)).toBe(raw);
    const reloaded = decodeProductionCopy(owner.expectedRaw!, next.scope, fork).draft;
    const retained = retainProductionOrigin(reloaded, { key: productionCopyKey(next.scope, fork), raw: owner.expectedRaw! });
    expect(retained.origins).toEqual([{ key, raw }]); expect(retained.input).toEqual(current.input);
    expect(retained.pending).toEqual(prepared.draft.pending); expect(pointers.getItem(productionPointerKey(next.scope))).toBe(fork);
  });
  it("blocks another copy write if a retained original changed while keeping a complete export available", async () => {
    const { prepared } = await armed(), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
    writeProductionCopy(store, pointers, prepared.draft, owner);
    const key = productionCopyKey(prepared.draft.scope, writer), raw = owner.expectedRaw!;
    const recovered = retainProductionOrigin(decodeProductionCopy(raw, prepared.draft.scope, writer).draft, { key, raw });
    const nextOwner: ProductionCustody = { writerId: fork, expectedRaw: null, expectedPointer: writer };
    store.setItem(key, "An external replacement must not be overwritten");
    expect(() => assertProductionOrigins(store, recovered)).toThrow("original unconfirmed");
    expect(() => writeProductionCopy(store, pointers, recovered, nextOwner)).toThrow("original unconfirmed");
    expect(store.getItem(key)).toBe("An external replacement must not be overwritten"); expect(nextOwner.expectedRaw).toBeNull();
    const exported = JSON.parse(encodeProductionFile(recovered));
    expect(exported.draft.origins).toEqual([{ key, raw }]); expect(exported.draft.pending.id).toBe(id); expect(exported.draft.input).toEqual(recovered.input);
  });
  it("rechecks a retained original after the new writer pointer is published", async () => {
    const { prepared } = await armed(), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
    writeProductionCopy(store, pointers, prepared.draft, owner);
    const key = productionCopyKey(prepared.draft.scope, writer), raw = owner.expectedRaw!;
    const recovered = retainProductionOrigin(decodeProductionCopy(raw, prepared.draft.scope, writer).draft, { key, raw });
    const set = pointers.setItem.bind(pointers);
    pointers.setItem = (name, value) => { set(name, value); if (value === fork) store.setItem(key, "Changed during publication"); };
    const nextOwner: ProductionCustody = { writerId: fork, expectedRaw: null, expectedPointer: writer };
    expect(() => writeProductionCopy(store, pointers, recovered, nextOwner)).toThrow("original unconfirmed");
    const retained = decodeProductionCopy(store.getItem(productionCopyKey(recovered.scope, fork))!, recovered.scope, fork).draft;
    expect(retained.pending?.outcome).toBe("UNKNOWN"); expect(retained.input).toEqual(recovered.input); expect(retained.origins).toEqual([{ key, raw }]);
  });
  it("reads older v1 copies without origins and preserves malformed new-origin copies as downloadable raw bytes", async () => {
    const { prepared } = await armed(), stored = JSON.parse(encodeProductionCopy({ schema: "motionwright.production-recovery/1", scope: prepared.draft.scope,
      writerId: writer, updatedAt: 1, draft: prepared.draft }));
    delete stored.draft.origins;
    const original = JSON.stringify(stored), decoded = decodeProductionCopy(original, prepared.draft.scope, writer);
    expect(decoded.draft.origins).toEqual([]); expect(decoded.draft.input).toEqual(prepared.draft.input); expect(decoded.draft.pending?.id).toBe(id);
    const malformed = JSON.stringify({ ...stored, writerId: fork, draft: { ...stored.draft, origins: null } }), store = new MemoryStorage();
    const key = productionCopyKey(prepared.draft.scope, fork); store.setItem(key, malformed);
    expect(() => decodeProductionCopy(malformed, prepared.draft.scope, fork)).toThrow("origins");
    expect(readProductionCopies(store, prepared.draft.scope).entries).toEqual([{ key, writerId: fork, raw: malformed, value: null }]);
    expect(store.getItem(key)).toBe(malformed); expect(JSON.parse(encodeProductionFile(decoded.draft)).draft.pending.id).toBe(id);
  });
});
