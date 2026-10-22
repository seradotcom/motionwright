import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import { planContentDigest } from "./creativeProduction";
import type { Change, Project, ProjectEvent } from "./types";
import {
  acknowledgeProductionDraft, cleanProductionPlan, createProductionDraft, decodeProductionCopy, editProductionDraft,
  encodeProductionCopy, encodeProductionFile, inspectProductionOutcome, prepareProductionSubmission, productionConflict,
  productionCopyKey, productionOutcomeMatches, productionPointerKey, productionPreparationMatches, productionScope,
  productionUnconfirmed, readProductionCopies, reconcileProductionDraft, releaseProductionOutcome, resolveProductionDraft,
  retainProductionFailure, retainReviewedProductionIntent, writeProductionCopy, PRODUCTION_RECOVERY_PREFIX,
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
