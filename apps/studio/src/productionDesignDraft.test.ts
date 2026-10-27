import { describe, expect, it, vi } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import { planContentDigest } from "./creativeProduction";
import type { NativeCapsule } from "./creativeProduction";
import type { CanvasNode, Change, Project, ProjectEvent, SceneBoundary, SceneBoundarySource } from "./types";
import {
  acknowledgeProductionDraft, assertProductionOrigins, boundaryFilmOptionsFromRawInput, boundaryFromRawInput, cleanProductionPlan, createProductionDraft, decodeProductionCopy, editProductionDraft,
  encodeProductionCopy, encodeProductionFile, inspectProductionOutcome, prepareProductionSubmission, productionConflict,
  productionCopyKey, productionDraftValue, productionHasInput, productionHasWork, productionLabel, productionOutcomeMatches, productionPointerKey, productionPreparationMatches, productionScope, productionValue,
  productionUnconfirmed, readProductionCopies, readProductionRecoveryPointer, recoverProductionAfterReadFailure, reconcileProductionDraft, releaseProductionOutcome, resolveProductionDraft,
  retainProductionFailure, retainProductionOrigin, retainReviewedProductionIntent, writeProductionCopy, PRODUCTION_RECOVERY_PREFIX, MAX_PRODUCTION_COPY_BYTES, MAX_PRODUCTION_RECOVERY_BYTES,
} from "./productionDesignDraft";
import type { ProductionBoundaryInput, ProductionCustody, ProductionDraft, ProductionInput, ProductionStorage, ProductionTarget } from "./productionDesignDraft";

const id = "00000000-0000-4000-8000-000000000001", writer = "00000000-0000-4000-8000-000000000002", fork = "00000000-0000-4000-8000-000000000003";
type SourceInput = { kind: "source"; assetId: string; renderer: string; version: string };
// The proposed target is passed through the existing API so this test patch
// can compile against the old helper and fail on behavior before product edits.
const sourceTarget = (project: Project, sceneId: string | null = project.scenes[0].id): ProductionTarget =>
  ({ kind: "source", sceneId } as unknown as ProductionTarget);
const sourceInput = (project: Project): SourceInput => ({ kind: "source", assetId: project.assets[2].id,
  renderer: "  hyperframes  ", version: "  unverified  " });
function source(): Project {
  const project = structuredClone(fixtureProject); project.locks = []; project.branch_workspaces = [];
  project.production_design = { plan: null, heroes: [], capsules: [] }; return project;
}
function target(project: Project, kind: "hero" | "plan"): ProductionTarget { return kind === "plan" ? { kind } : { kind, sceneId: project.scenes[0].id }; }
function edit(draft: ProductionDraft, text: string): ProductionDraft {
  const input = draft.input as ProductionInput | SourceInput;
  return editProductionDraft(draft, input.kind === "hero" ? { ...input, config: { ...input.config, headline: text } }
    : input.kind === "plan" ? { ...input, plan: { ...input.plan, concept: text } }
      : { ...input, renderer: text } as unknown as ProductionInput);
}
async function armed(kind: "hero" | "plan" = "plan") {
  const project = source(), draft = edit(createProductionDraft(project, target(project, kind)), "Author direction");
  return { project, source: draft, prepared: await prepareProductionSubmission(draft, project, id) };
}
function authoredSource(project = source(), input = sourceInput(project), sceneId: string | null = project.scenes[0].id) {
  return editProductionDraft(createProductionDraft(project, sourceTarget(project, sceneId)), input as unknown as ProductionInput);
}
async function armedSource() {
  const project = source(), draft = authoredSource(project);
  return { project, source: draft, prepared: await prepareProductionSubmission(draft, project, id) };
}
function sourceCapsule(change: Change): NativeCapsule {
  if (change.type !== "upsert_native_capsule") throw new Error("Expected the existing source attachment Change.");
  return change.capsule;
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

describe("legacy source attachment uses durable Production intent without native admission", () => {
  it("starts an explicitly scoped creation with no selected asset or existing capsule base", () => {
    const project = source(), selected = sourceTarget(project), draft = createProductionDraft(project, selected);
    expect(draft.input).toEqual({ kind: "source", assetId: "", renderer: "hyperframes", version: "unverified" });
    expect(draft.base).toBeNull(); expect(productionValue(project, selected)).toBeNull(); expect(productionHasWork(draft)).toBe(false);
    expect(productionLabel(selected)).toBe("Source attachment");
    expect(draft.scope).toBe(JSON.stringify([project.id, project.generation, project.active_branch, "source", project.scenes[0].id]));
    expect(productionScope(project, sourceTarget(project, project.scenes[1].id))).not.toBe(draft.scope);
    expect(productionScope(project, sourceTarget(project, null))).not.toBe(draft.scope);
    for (const field of ["id", "generation", "active_branch"] as const)
      expect(productionScope({ ...project, [field]: fork }, selected)).not.toBe(draft.scope);
  });
  it("prepares the exact eight-field opaque capsule using the pending UUID and raw renderer/version", async () => {
    const project = source(), draft = authoredSource(project), before = structuredClone(project), input = sourceInput(project);
    const random = vi.spyOn(crypto, "randomUUID").mockImplementation(() => { throw new Error("No second attachment identity is allowed during preparation"); });
    try {
      const prepared = await prepareProductionSubmission(draft, project, id), repeated = await prepareProductionSubmission(draft, project, id);
      const capsule = sourceCapsule(prepared.change);
      expect(capsule).toEqual({ id, scene_id: project.scenes[0].id, source_asset_id: input.assetId,
        source_sha256: project.assets[2].content_sha256, label: project.assets[2].name,
        fidelity: { renderer: input.renderer, renderer_version: input.version, visual: "unavailable", temporal: "unavailable",
          structural: "native", editable: "unavailable", losses: ["Source preserved as an opaque attachment. Parameter editing and renderer execution are not admitted."], evidence_sha256: null },
        editable_parameters: [], native_editor_hint: "Open with the original application's trusted environment" });
      expect(Object.keys(capsule).sort()).toEqual(["id", "scene_id", "source_asset_id", "source_sha256", "label", "fidelity", "editable_parameters", "native_editor_hint"].sort());
      expect(prepared.change).toEqual(repeated.change); expect(prepared.draft.pending?.id).toBe(capsule.id);
      expect(prepared.draft.pending?.value).toEqual(capsule); expect(prepared.draft.pending?.input).toEqual(input);
      expect(prepared.draft.base).toBeNull(); expect(prepared.draft.input).toEqual(input); expect(project).toEqual(before);
    } finally { random.mockRestore(); }
  });
  it.each(["missing", "ambiguous", "none"] as const)("a %s scene prevents preparation and retains the raw source input", async mode => {
    const project = source(), draft = authoredSource(project, sourceInput(project), mode === "none" ? null : project.scenes[0].id);
    if (mode === "missing") project.scenes.shift();
    if (mode === "ambiguous") project.scenes.push(structuredClone(project.scenes[0]));
    const raw = structuredClone(draft.input), before = structuredClone(project);
    await expect(prepareProductionSubmission(draft, project, id)).rejects.toThrow(/scene/i);
    expect(draft.input).toEqual(raw); expect(draft.pending).toBeNull(); expect(project).toEqual(before);
  });
  it.each(["empty", "missing", "ambiguous", "no digest", "malformed digest", "uppercase digest"] as const)("a source asset with %s cannot become an attachment", async mode => {
    const project = source(), input = sourceInput(project);
    if (mode === "empty") input.assetId = "";
    if (mode === "missing") input.assetId = fork;
    if (mode === "ambiguous") project.assets.push(structuredClone(project.assets[2]));
    if (mode === "no digest") project.assets[2].content_sha256 = null;
    if (mode === "malformed digest") project.assets[2].content_sha256 = "not a digest";
    if (mode === "uppercase digest") project.assets[2].content_sha256 = "AB".repeat(32);
    const draft = authoredSource(project, input), before = structuredClone(project);
    await expect(prepareProductionSubmission(draft, project, id)).rejects.toThrow(/asset/i);
    expect(draft.input).toEqual(input); expect(project).toEqual(before);
  });
  it.each([
    ["renderer", ""], ["renderer", "  \t"], ["renderer", "🙂".repeat(33)], ["renderer", "value\u0000"],
    ["version", ""], ["version", "  "], ["version", "🙂".repeat(33)], ["version", "value\u0000"],
  ] as const)("invalid %s metadata stays raw and fails before a Change can be sent", async (field, value) => {
    const project = source(), input = { ...sourceInput(project), [field]: value }, draft = authoredSource(project, input);
    await expect(prepareProductionSubmission(draft, project, id)).rejects.toThrow(/renderer/i);
    const raw = encodeProductionCopy({ schema: "motionwright.production-recovery/1", scope: draft.scope, writerId: writer, updatedAt: 1, draft });
    expect(decodeProductionCopy(raw, draft.scope, writer).draft.input).toEqual(input); expect(draft.pending).toBeNull();
  });
  it.each(["project", "scene"] as const)("a %s content lock prevents source preparation without replacing input", async scope => {
    const project = source(), draft = authoredSource(project);
    project.locks.push({ id: writer, resource: scope + ":" + (scope === "project" ? project.id : project.scenes[0].id), kind: "content", note: "Keep authored source" });
    const before = structuredClone(project);
    await expect(prepareProductionSubmission(draft, project, id)).rejects.toThrow(/locked/i);
    expect(project).toEqual(before); expect(draft.pending).toBeNull();
  });
  it.each(["approve", "detach"] as const)("source creation cannot perform %s", async operation => {
    const project = source(), draft = authoredSource(project);
    await expect(prepareProductionSubmission(draft, project, id, operation)).rejects.toThrow(/attachment|source/i);
    expect(draft.pending).toBeNull();
  });
  it("refuses an existing capsule UUID and the existing capsule budget without updating any capsule", async () => {
    const { project, source: draft, prepared } = await armedSource(), capsule = sourceCapsule(prepared.change);
    project.production_design!.capsules = [structuredClone(capsule)];
    const collision = structuredClone(project);
    await expect(prepareProductionSubmission(draft, project, id)).rejects.toThrow(/identity|already|exists/i);
    expect(project).toEqual(collision);
    project.production_design!.capsules = Array.from({ length: 256 }, (_, index) => ({ ...structuredClone(capsule),
      id: "30000000-0000-4000-8000-" + String(index).padStart(12, "0") }));
    const full = structuredClone(project);
    await expect(prepareProductionSubmission(draft, project, fork)).rejects.toThrow(/budget/i);
    expect(project).toEqual(full);
  });
  it("a matching original source ACK makes the form clean; another attachment requires an explicit new edit", async () => {
    const { project, prepared } = await armedSource(), saved = await applyChange(project, prepared.change);
    const next = acknowledgeProductionDraft(prepared.draft, id, saved);
    expect(next.pending).toBeNull(); expect(next.base).toBeNull(); expect(next.input).toEqual(prepared.draft.input);
    expect(productionHasInput(next)).toBe(false); expect(productionHasWork(next)).toBe(false);
    expect(saved.assets).toEqual(project.assets); expect(saved.scenes).toEqual(project.scenes);
    await expect(prepareProductionSubmission(next, saved, fork)).rejects.toThrow(/explicit/i);
    const authored = editProductionDraft(next, next.input), separate = await prepareProductionSubmission(authored, saved, fork);
    expect(sourceCapsule(separate.change).id).toBe(fork); expect(sourceCapsule(separate.change).id).not.toBe(id);
    const twice = await applyChange(saved, separate.change);
    expect(twice.production_design!.capsules.map(value => value.id)).toEqual([id, fork]);
    expect(twice.production_design!.capsules.find(value => value.id === id)).toEqual(sourceCapsule(prepared.change));
    expect(twice.assets).toEqual(project.assets); expect(twice.scenes).toEqual(project.scenes);
  });
  it.each(["asset clear", "renderer", "version", "ABA"] as const)("a later %s survives the original attachment ACK", async mode => {
    const { project, prepared } = await armedSource(), original = prepared.draft.input as unknown as SourceInput;
    const input = mode === "asset clear" ? { ...original, assetId: "" }
      : mode === "version" ? { ...original, version: "  later version  " } : { ...original, renderer: "  Later renderer  " };
    let later = editProductionDraft(prepared.draft, input as unknown as ProductionInput);
    if (mode === "ABA") later = editProductionDraft(later, original as unknown as ProductionInput);
    const next = acknowledgeProductionDraft(later, id, await applyChange(project, prepared.change));
    expect(next.input).toEqual(later.input); expect(next.intent).toBe(true); expect(next.pending).toBeNull(); expect(next.base).toBeNull();
  });
  it.each(["id", "generation", "active_branch", "revision", "capsule id", "capsule value", "missing", "duplicate"] as const)("a wrong %s source ACK cannot clear the original intent", async field => {
    const { project, prepared } = await armedSource(), receipt = await applyChange(project, prepared.change);
    const capsules = receipt.production_design!.capsules;
    if (field === "id" || field === "generation" || field === "active_branch") receipt[field] = fork;
    if (field === "revision") receipt.revision++;
    if (field === "capsule id") capsules[0].id = fork;
    if (field === "capsule value") capsules[0].fidelity.renderer += " changed";
    if (field === "missing") capsules.length = 0;
    if (field === "duplicate") capsules.push(structuredClone(capsules[0]));
    const before = structuredClone(prepared.draft);
    expect(() => acknowledgeProductionDraft(prepared.draft, id, receipt)).toThrow();
    expect(prepared.draft).toEqual(before); expect(productionUnconfirmed(prepared.draft)).toBe(true);
  });
  it.each(["asset digest", "asset name", "asset removal", "scene", "locks", "capsules"] as const)("same-stamp %s changes invalidate source preparation despite a null base", async field => {
    const { project, source: draft, prepared } = await armedSource(), changed = structuredClone(project);
    if (field === "asset digest") changed.assets[2].content_sha256 = "ef".repeat(32);
    if (field === "asset name") changed.assets[2].name = "A different source label";
    if (field === "asset removal") changed.assets.pop();
    if (field === "scene") changed.scenes[0].objective = "Another scene context";
    if (field === "locks") changed.locks.push({ id: fork, resource: "scene:" + changed.scenes[0].id, kind: "content", note: "Later lock" });
    if (field === "capsules") changed.production_design!.capsules.push({ ...sourceCapsule(prepared.change), id: fork });
    expect(changed.revision).toBe(project.revision);
    expect(productionPreparationMatches(draft, project, draft, project)).toBe(true);
    expect(productionPreparationMatches(draft, project, draft, changed)).toBe(false);
  });
  it("same-stamp source changes invalidate the two outcome reads and a previously accepted review", async () => {
    const { project, prepared } = await armedSource(), saved = await applyChange(project, prepared.change);
    const changed = structuredClone(saved); changed.assets[2].name = "Changed under the same stamp";
    let reads = 0;
    await expect(inspectProductionOutcome(prepared.draft, saved, { readProject: async () => ++reads === 1 ? saved : changed,
      readHistory: async () => journal(saved, prepared.change) })).rejects.toThrow(/changed/i);
    const review = await inspectProductionOutcome(prepared.draft, saved, { readProject: async () => saved, readHistory: async () => journal(saved, prepared.change) });
    expect(productionOutcomeMatches(prepared.draft, changed, review)).toBe(false);
    expect(reconcileProductionDraft(prepared.draft, saved)).toBe(prepared.draft);
    expect(acknowledgeProductionDraft(prepared.draft, fork, saved)).toBe(prepared.draft);
  });
  it("an equivalent peer capsule with another ID is not a matching original attachment", async () => {
    const { project, prepared } = await armedSource(), peerChange: Change = { type: "upsert_native_capsule", capsule: { ...sourceCapsule(prepared.change), id: fork } };
    const peer = await applyChange(project, peerChange);
    const review = await inspectProductionOutcome(prepared.draft, peer, { readProject: async () => peer, readHistory: async () => journal(peer, peerChange) });
    expect(review.matchingValueRevisions).toEqual([]); expect(productionUnconfirmed(prepared.draft)).toBe(true);
    expect(() => acknowledgeProductionDraft(prepared.draft, id, peer)).toThrow(/exact|confirm/i);
  });
  it("Continue after an observed source result preserves the original intent and creates no capsule until a separate Attach", async () => {
    const { project, prepared } = await armedSource(), saved = await applyChange(project, prepared.change);
    const review = await inspectProductionOutcome(prepared.draft, saved, { readProject: async () => saved,
      readHistory: async () => journal(saved, prepared.change) });
    const store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
    writeProductionCopy(store, pointers, prepared.draft, owner);
    const original = owner.expectedRaw!, key = productionCopyKey(prepared.draft.scope, writer), before = structuredClone(saved);
    const random = vi.spyOn(crypto, "randomUUID").mockImplementation(() => { throw new Error("Continue cannot generate an attachment identity"); });
    try {
      const next = releaseProductionOutcome(prepared.draft, saved, review);
      retainReviewedProductionIntent(store, pointers, prepared.draft, next, owner, fork);
      expect(next.pending).toBeNull(); expect(next.intent).toBe(true); expect(next.input).toEqual(prepared.draft.input);
      expect(saved).toEqual(before); expect(saved.production_design!.capsules).toEqual([sourceCapsule(prepared.change)]);
      expect(store.getItem(key)).toBe(original); expect(decodeProductionCopy(original, next.scope, writer).draft.pending).toEqual(prepared.draft.pending);
      const separate = await prepareProductionSubmission(next, saved, fork);
      expect(sourceCapsule(separate.change).id).toBe(fork); expect(sourceCapsule(separate.change).id).not.toBe(id);
      expect(separate.draft.pending?.input).toEqual(prepared.draft.input); expect(saved).toEqual(before);
      expect(store.getItem(key)).toBe(original); expect(prepared.draft.pending?.id).toBe(id);
      expect(random).not.toHaveBeenCalled();
    } finally { random.mockRestore(); }
  });
  it.each(["native authority", "fidelity", "editable", "disclosure", "identity", "scene", "asset", "renderer", "base"] as const)("rejects a source copy with changed %s without expanding the eight-field contract", async field => {
    const { prepared } = await armedSource();
    const raw = encodeProductionCopy({ schema: "motionwright.production-recovery/1", scope: prepared.draft.scope, writerId: writer, updatedAt: 1, draft: prepared.draft });
    const value = JSON.parse(raw), pending = value.draft.pending, capsule = pending.value;
    if (field === "native authority") capsule.property_fidelity = [];
    if (field === "fidelity") capsule.fidelity.visual = "native";
    if (field === "editable") capsule.editable_parameters = ["arbitrary execution parameter"];
    if (field === "disclosure") capsule.fidelity.losses = [];
    if (field === "identity") capsule.id = fork;
    if (field === "scene") capsule.scene_id = fork;
    if (field === "asset") capsule.source_asset_id = fork;
    if (field === "renderer") capsule.fidelity.renderer += " new authority";
    if (field === "base") value.draft.base = structuredClone(capsule);
    expect(() => decodeProductionCopy(JSON.stringify(value), prepared.draft.scope, writer)).toThrow();
    expect(decodeProductionCopy(raw, prepared.draft.scope, writer).draft.pending?.value).toEqual(sourceCapsule(prepared.change));
  });
});

describe("Production UNKNOWN base authority across component, plan and source", () => {
  for (const kind of ["hero", "plan", "source"] as const) {
    const preparedCase = () => kind === "source" ? armedSource() : armed(kind);
    it(`${kind} keeps UNKNOWN on the same saved revision with an empty journal`, async () => {
      const { project, prepared } = await preparedCase();
      const review = await inspectProductionOutcome(prepared.draft, project, { readProject: async () => project, readHistory: async () => [] });
      expect(review.events).toEqual([]); expect(productionOutcomeMatches(prepared.draft, project, review)).toBe(true);
      const before = structuredClone(prepared.draft);
      expect(() => releaseProductionOutcome(prepared.draft, project, review)).toThrow(/could still commit/i);
      await expect(prepareProductionSubmission(prepared.draft, project, fork)).rejects.toThrow(/unconfirmed/i);
      expect(prepared.draft).toEqual(before);
    });
    it(`${kind} permits the live NOT_SENT caller to continue explicitly on the same base while recovery remains UNKNOWN`, async () => {
      const { project, prepared } = await preparedCase(), known = retainProductionFailure(prepared.draft, id, "Local backup failed before dispatch", true);
      const live = resolveProductionDraft(known, project, "draft"), next = await prepareProductionSubmission(live, project, fork);
      expect(next.draft.pending?.id).toBe(fork); expect(next.draft.pending?.revision).toBe(project.revision);
      const raw = encodeProductionCopy({ schema: "motionwright.production-recovery/1", scope: known.scope, writerId: writer, updatedAt: 1, draft: known });
      const recovered = decodeProductionCopy(raw, known.scope, writer).draft;
      expect(recovered.pending?.outcome).toBe("UNKNOWN"); expect(JSON.parse(raw).draft.pending.outcome).toBe("NOT_SENT");
      const review = await inspectProductionOutcome(recovered, project, { readProject: async () => project, readHistory: async () => [] });
      expect(() => releaseProductionOutcome(recovered, project, review)).toThrow(/could still commit/i);
      expect(() => resolveProductionDraft(recovered, project, "draft")).toThrow(/unconfirmed/i);
    });
    it(`${kind} retains later input and exact original bytes after an independent peer advances the base`, async () => {
      const { project, prepared } = await preparedCase(), later = edit(prepared.draft, "  Separate later intent  ");
      const change: Change = { type: "rename_project", title: "Independent peer closes the Production base" }, peer = await applyChange(project, change);
      const review = await inspectProductionOutcome(later, peer, { readProject: async () => peer, readHistory: async () => journal(peer, change) });
      expect(review.matchingValueRevisions).toEqual([]);
      const store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody(), clock = vi.spyOn(Date, "now").mockReturnValue(1000);
      try {
        writeProductionCopy(store, pointers, later, owner); const original = owner.expectedRaw!, key = productionCopyKey(later.scope, writer);
        clock.mockReturnValue(2000); writeProductionCopy(store, pointers, later, owner);
        expect(owner.expectedRaw).toBe(original);
        const next = releaseProductionOutcome(later, peer, review), successor = retainReviewedProductionIntent(store, pointers, later, next, owner, fork);
        expect(next.pending).toBeNull(); expect(next.input).toEqual(later.input); expect(next.intent).toBe(true);
        expect(next.baseRevision).toBe(peer.revision); expect(store.getItem(key)).toBe(original);
        expect(decodeProductionCopy(successor.expectedRaw!, next.scope, fork).draft.input).toEqual(later.input);
        expect(decodeProductionCopy(original, later.scope, writer).draft.pending).toEqual(prepared.draft.pending);
      } finally { clock.mockRestore(); }
    });
  }
});

describe("Production confirmation preserves actual raw bytes and full custody checks", () => {
  it("retains whitespace, timestamp and absent legacy origins when confirming unchanged decoded input", async () => {
    const { prepared } = await armed(), value = { schema: "motionwright.production-recovery/1", scope: prepared.draft.scope, writerId: writer, updatedAt: 1, draft: structuredClone(prepared.draft) };
    delete value.draft.origins;
    const raw = " \n" + JSON.stringify(value, null, 2) + "\n ", draft = decodeProductionCopy(raw, value.scope, writer).draft;
    const store = new MemoryStorage(), pointers = new MemoryStorage(), key = productionCopyKey(draft.scope, writer), pointer = productionPointerKey(draft.scope);
    store.setItem(key, raw); pointers.setItem(pointer, writer);
    const owner: ProductionCustody = { writerId: writer, expectedRaw: raw, expectedPointer: writer };
    const clock = vi.spyOn(Date, "now").mockReturnValue(2000), writes = vi.spyOn(store, "setItem");
    try {
      writeProductionCopy(store, pointers, draft, owner);
      expect(writes).not.toHaveBeenCalled(); expect(owner.expectedRaw).toBe(raw); expect(store.getItem(key)).toBe(raw);
      expect(pointers.getItem(pointer)).toBe(writer); expect(Object.hasOwn(JSON.parse(raw).draft, "origins")).toBe(false);
    } finally { writes.mockRestore(); clock.mockRestore(); }
  });
  it("unchanged live NOT_SENT bytes stay exact while a recovered UNKNOWN is published into a separate writer", async () => {
    const { prepared } = await armedSource(), known = retainProductionFailure(prepared.draft, id, "No dispatch occurred in this live caller", true);
    const store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody(), clock = vi.spyOn(Date, "now").mockReturnValue(1000);
    try {
      writeProductionCopy(store, pointers, known, owner); const raw = owner.expectedRaw!, key = productionCopyKey(known.scope, writer);
      clock.mockReturnValue(2000); writeProductionCopy(store, pointers, known, owner);
      expect(owner.expectedRaw).toBe(raw); expect(JSON.parse(raw).draft.pending.outcome).toBe("NOT_SENT");
      const recovered = decodeProductionCopy(raw, known.scope, writer).draft, other: ProductionCustody = { writerId: fork, expectedRaw: null, expectedPointer: writer };
      writeProductionCopy(store, pointers, recovered, other);
      expect(store.getItem(key)).toBe(raw); expect(JSON.parse(other.expectedRaw!).draft.pending.outcome).toBe("UNKNOWN");
    } finally { clock.mockRestore(); }
  });
  it("counts the exact retained legacy bytes before confirming the global recovery quota", async () => {
    const { prepared } = await armed(), draft = prepared.draft, key = productionCopyKey(draft.scope, writer), pointer = productionPointerKey(draft.scope);
    const value = { schema: "motionwright.production-recovery/1", scope: draft.scope, writerId: writer, updatedAt: 2000, draft };
    const compact = JSON.stringify(value), raw = JSON.stringify({ ...value, updatedAt: 1 }, null, 2) + " ".repeat(1024);
    const size = (text: string) => new TextEncoder().encode(text).length, store = new MemoryStorage(), pointers = new MemoryStorage();
    store.setItem(key, raw); pointers.setItem(pointer, writer);
    let remaining = MAX_PRODUCTION_RECOVERY_BYTES - size(key) - size(compact), index = 0;
    while (remaining > 0) {
      const filler = PRODUCTION_RECOVERY_PREFIX + "retained-budget-" + index++, amount = Math.min(MAX_PRODUCTION_COPY_BYTES, remaining - size(filler));
      expect(amount).toBeGreaterThan(0); store.setItem(filler, "x".repeat(amount)); remaining -= size(filler) + amount;
    }
    expect(size(raw)).toBeGreaterThan(size(compact));
    const before = [...store.values], owner: ProductionCustody = { writerId: writer, expectedRaw: raw, expectedPointer: writer };
    const clock = vi.spyOn(Date, "now").mockReturnValue(2000);
    try {
      expect(() => writeProductionCopy(store, pointers, draft, owner)).toThrow(/full/i);
      expect([...store.values]).toEqual(before); expect(owner.expectedRaw).toBe(raw); expect(pointers.getItem(pointer)).toBe(writer);
    } finally { clock.mockRestore(); }
  });
  it("does not overwrite a peer pointer installed during data publication", async () => {
    const { prepared } = await armed(), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
    writeProductionCopy(store, pointers, prepared.draft, owner);
    const pointer = productionPointerKey(prepared.draft.scope), set = store.setItem.bind(store);
    store.setItem = (key, raw) => { set(key, raw); pointers.setItem(pointer, fork); };
    expect(() => writeProductionCopy(store, pointers, edit(prepared.draft, "Later local input"), owner)).toThrow(/pointer/i);
    expect(pointers.getItem(pointer)).toBe(fork);
  });
  it("an unchanged-byte confirmation still rejects a removed pointer", async () => {
    const { prepared } = await armed(), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
    writeProductionCopy(store, pointers, prepared.draft, owner); const raw = owner.expectedRaw!;
    pointers.removeItem(productionPointerKey(prepared.draft.scope));
    expect(() => writeProductionCopy(store, pointers, prepared.draft, owner)).toThrow(/pointer/i);
    expect(store.getItem(productionCopyKey(prepared.draft.scope, writer))).toBe(raw);
  });
});

describe("Scene boundary authoring through existing Production custody", () => {
  const boundaryId = "00000000-0000-4000-8000-000000000010";
  const predecessorId = "00000000-0000-4000-8000-000000000011";
  const endpoint = { nodes: [], camera: { center_x: 0, center_y: 0, zoom: 1, rotation_deg: 0, safe_margin: 0.05 }, assets: [] };
  const boundaryTarget = (project: Project, selected: string | null = null): ProductionTarget =>
    ({ kind: "boundary", sceneId: project.scenes[0].id, boundaryId: selected });
  const inputOf = (draft: ProductionDraft) => draft.input as ProductionBoundaryInput;
  const editBoundary = (draft: ProductionDraft, patch: Partial<ProductionBoundaryInput>) => editProductionDraft(draft, { ...inputOf(draft), ...patch });
  function context(project: Project): SceneBoundarySource {
    // Unit comparison fixture only: the integration lane obtains this object from StudioService.
    return { project_id: project.id, generation: project.generation, branch_id: project.active_branch, authored_revision: project.revision,
      deliverable_profile_id: project.deliverables[0].id, dependencies_sha256: "a".repeat(64),
      film_options: { frame_rate: { num: 24, den: 1 }, font_family: "Inter", mono_font_family: "JetBrains Mono", role: "mechanism", archetype: "diagram_build" } };
  }
  function bindFixture(draft: ProductionDraft, boundSource: SceneBoundarySource): ProductionDraft {
    return editBoundary(draft, { boundSource, profileId: boundSource.deliverable_profile_id,
      frameRateNum: String(boundSource.film_options.frame_rate.num), frameRateDen: String(boundSource.film_options.frame_rate.den),
      fontFamily: boundSource.film_options.font_family, monoFontFamily: boundSource.film_options.mono_font_family,
      role: boundSource.film_options.role, archetype: boundSource.film_options.archetype });
  }
  function receiptFor(project: Project, change: Change): Project {
    // A unit oracle for the draft coordinator, not an emulated service or native result.
    const receipt = structuredClone(project), current = receipt.production_design!.scene_boundaries ?? [];
    if (change.type === "upsert_scene_boundary") receipt.production_design!.scene_boundaries = [
      ...current.filter(boundary => boundary.id !== change.boundary.id), structuredClone(change.boundary)];
    else if (change.type === "remove_scene_boundary") receipt.production_design!.scene_boundaries = current.filter(boundary => boundary.id !== change.boundary_id);
    else throw new Error("Expected a scene boundary Change.");
    receipt.revision++; return receipt;
  }
  async function savedBoundary(bound = false) {
    const project = source(); let draft = createProductionDraft(project, boundaryTarget(project));
    draft = bound ? bindFixture(draft, context(project)) : editBoundary(draft, { entryJson: JSON.stringify(endpoint) });
    const prepared = await prepareProductionSubmission(draft, project, id);
    const receipt = receiptFor(project, prepared.change);
    return { project, draft, prepared, receipt, acknowledged: acknowledgeProductionDraft(prepared.draft, id, receipt) };
  }
  const copy = (draft: ProductionDraft) => encodeProductionCopy({ schema: "motionwright.production-recovery/1", scope: draft.scope, writerId: writer, updatedAt: 1, draft });

  it("keeps legacy five-element scopes unchanged and scopes each boundary by a sixth explicit selector", () => {
    const project = source();
    for (const selected of [target(project, "hero"), target(project, "plan"), sourceTarget(project)]) {
      expect(JSON.parse(productionScope(project, selected))).toEqual([project.id, project.generation, project.active_branch, selected.kind,
        selected.kind === "plan" ? null : selected.sceneId]);
    }
    const fresh = productionScope(project, boundaryTarget(project));
    expect(JSON.parse(fresh)).toEqual([project.id, project.generation, project.active_branch, "boundary", project.scenes[0].id, null]);
    expect(productionScope(project, boundaryTarget(project, boundaryId))).not.toBe(fresh);
    for (const field of ["id", "generation", "active_branch"] as const)
      expect(productionScope({ ...project, [field]: project[field] + "-other" }, boundaryTarget(project))).not.toBe(fresh);
    expect(productionScope(project, { kind: "boundary", sceneId: project.scenes[1].id, boundaryId: null })).not.toBe(fresh);
    expect(readProductionRecoveryPointer(new MemoryStorage(), fresh, { observed: false, expectedPointer: null }).observed).toBe(true);
    expect(() => readProductionRecoveryPointer(new MemoryStorage(), JSON.stringify([...JSON.parse(fresh), "extra"]), { observed: false, expectedPointer: null })).toThrow();
  });
  it("creates one declaration UUID and retains it through read, reset, preparation and ACK without changing scope", async () => {
    const project = source(), initial = createProductionDraft(project, boundaryTarget(project)), stable = inputOf(initial).boundaryId;
    expect(stable).toMatch(/^[0-9a-f-]{36}$/i);
    const observed = reconcileProductionDraft(initial, { ...project, revision: project.revision + 1 });
    expect(inputOf(observed).boundaryId).toBe(stable);
    expect(inputOf(resolveProductionDraft(initial, project, "saved")).boundaryId).toBe(stable);
    const draft = editBoundary(initial, { entryJson: JSON.stringify(endpoint) });
    const first = await prepareProductionSubmission(draft, project, id), second = await prepareProductionSubmission(draft, project, fork);
    expect(inputOf(first.draft).boundaryId).toBe(stable); expect(first.change).toEqual(second.change);
    const receipt = receiptFor(project, first.change), acknowledged = acknowledgeProductionDraft(first.draft, id, receipt);
    expect(acknowledged.scope).toBe(initial.scope); expect(acknowledged.target).toEqual(initial.target);
    expect(productionDraftValue(acknowledged, receipt)).toEqual(first.draft.pending?.value);
    expect(productionConflict(acknowledged, receipt)).toBe(false);
    expect(inputOf(reconcileProductionDraft(acknowledged, { ...receipt, revision: receipt.revision + 1 })).boundaryId).toBe(stable);
    expect(() => editBoundary(draft, { boundaryId })).toThrow(/separate explicit/i);
  });
  it("recovers every incomplete numeric, option and JSON string exactly without inventing a source", async () => {
    const project = source(), draft = editBoundary(createProductionDraft(project, boundaryTarget(project)), {
      profileId: " unselected ", frameRateNum: "1e", frameRateDen: "-", fontFamily: "  ", monoFontFamily: " mono\n",
      role: "not chosen", archetype: "", predecessorBoundaryId: "partial-id", appearancesJson: " [ { ", entryJson: "{\n", exitJson: " null " });
    const recovered = decodeProductionCopy(copy(draft), draft.scope, writer).draft;
    expect(recovered.input).toEqual(draft.input); expect(JSON.parse(encodeProductionFile(draft)).draft.input).toEqual(draft.input);
    expect(inputOf(recovered).boundSource).toBeNull(); expect(inputOf(recovered).boundPredecessor).toBeNull();
    await expect(prepareProductionSubmission(recovered, project, id)).rejects.toThrow();
    expect(recovered.input).toEqual(draft.input);
    expect(() => boundaryFilmOptionsFromRawInput(inputOf(draft))).toThrow(/complete positive/i);
  });
  it("retains unsourced authoring fields after a persisted incomplete draft and subsequent peer revision", async () => {
    const project = source(), draft = editBoundary(createProductionDraft(project, boundaryTarget(project)), {
      profileId: "author has not selected a profile", frameRateNum: "1e", fontFamily: "  desired family  ", role: "pending" });
    const prepared = await prepareProductionSubmission(draft, project, id);
    expect(prepared.change.type).toBe("upsert_scene_boundary");
    if (prepared.change.type !== "upsert_scene_boundary") throw new Error("Missing boundary submission.");
    expect(prepared.change.boundary.definition).toEqual({ kind: "draft_incomplete", source: null, entry: null, exit: null });
    const receipt = receiptFor(project, prepared.change), acknowledged = acknowledgeProductionDraft(prepared.draft, id, receipt);
    expect(acknowledged.input).toEqual(draft.input); expect(acknowledged.intent).toBe(true);
    const peer = { ...receipt, revision: receipt.revision + 1 };
    expect(reconcileProductionDraft(acknowledged, peer)).toBe(acknowledged);
    expect(inputOf(resolveProductionDraft(acknowledged, peer, "draft")).frameRateNum).toBe("1e");
  });
  it("requires complete explicit endpoints and a bound source rather than promoting a draft to readiness", async () => {
    const project = source(), draft = createProductionDraft(project, boundaryTarget(project));
    const explicit = editBoundary(draft, { definitionKind: "explicit", entryJson: JSON.stringify(endpoint), exitJson: JSON.stringify(endpoint) });
    await expect(prepareProductionSubmission(explicit, project, id)).rejects.toThrow();
    const bound = bindFixture(explicit, context(project)), prepared = await prepareProductionSubmission(bound, project, id);
    expect(prepared.change.type).toBe("upsert_scene_boundary");
    expect(copy(prepared.draft)).not.toContain("effect_grant");
    expect(() => boundaryFromRawInput({ ...inputOf(bound), entryJson: '{"nodes":[],"assets":[]}' }, project.scenes[0].id)).toThrow(/camera/i);
  });
  it("keeps an unchanged stale or foreign source during edits but rejects a changed source without a current explicit bind", async () => {
    const { receipt, acknowledged } = await savedBoundary(true), previous = productionDraftValue(acknowledged, receipt) as SceneBoundary;
    const stale = structuredClone(receipt); stale.production_design!.scene_boundaries![0].definition.source!.generation = writer;
    const draft = createProductionDraft(stale, boundaryTarget(stale, previous.id));
    const edited = editBoundary(draft, { entryJson: JSON.stringify(endpoint) });
    const prepared = await prepareProductionSubmission(edited, stale, fork);
    expect((prepared.draft.pending!.value as SceneBoundary).definition.source).toEqual(stale.production_design!.scene_boundaries![0].definition.source);
    const changed = editBoundary(edited, { boundSource: { ...inputOf(edited).boundSource!, dependencies_sha256: "b".repeat(64) } });
    await expect(prepareProductionSubmission(changed, stale, fork)).rejects.toThrow(/pre-change/i);
    expect(inputOf(changed).boundSource?.generation).toBe(writer);
  });
  for (const field of ["profileId", "frameRateNum", "frameRateDen", "fontFamily", "monoFontFamily", "role", "archetype"] as const) {
    it(`preserves changed ${field} and the earlier source while requiring an explicit rebind`, async () => {
      const project = source(), bound = bindFixture(createProductionDraft(project, boundaryTarget(project)), context(project));
      const changed = editBoundary(bound, { [field]: field === "frameRateNum" ? "30" : field === "frameRateDen" ? "2" : "changed author input" });
      await expect(prepareProductionSubmission(changed, project, id)).rejects.toThrow();
      expect(inputOf(changed).boundSource).toEqual(inputOf(bound).boundSource);
      expect(decodeProductionCopy(copy(changed), changed.scope, writer).draft.input).toEqual(changed.input);
    });
  }
  it("preserves a partial predecessor declaration without substituting a guessed scene or exit digest", async () => {
    const project = source(), previous: SceneBoundary = { version: 1, id: boundaryId, scene_id: project.scenes[0].id,
      input: { kind: "predecessor", scene_id: project.scenes[1].id, boundary_id: null, exit_sha256: "c".repeat(64), appearances: [] },
      definition: { kind: "draft_incomplete", source: null, entry: null, exit: null } };
    project.production_design!.scene_boundaries = [previous];
    const draft = editBoundary(createProductionDraft(project, boundaryTarget(project, boundaryId)), { entryJson: JSON.stringify(endpoint) });
    expect(inputOf(draft).inputKind).toBe("predecessor"); expect(inputOf(draft).boundPredecessor).toBeNull();
    const prepared = await prepareProductionSubmission(draft, project, id);
    expect((prepared.draft.pending!.value as SceneBoundary).input).toEqual(previous.input);
    expect(decodeProductionCopy(copy(prepared.draft), prepared.draft.scope, writer).draft.pending?.value).toEqual(prepared.draft.pending?.value);
    const changed = boundaryFromRawInput({ ...inputOf(draft), predecessorBoundaryId: predecessorId }, project.scenes[0].id, previous);
    expect(changed.input).toEqual({ kind: "predecessor", scene_id: null, boundary_id: predecessorId, exit_sha256: null, appearances: [] });
  });
  it("does not reuse bound predecessor context after the author selects another declaration", async () => {
    const project = source(), draft = editBoundary(createProductionDraft(project, boundaryTarget(project)), {
      inputKind: "predecessor", predecessorBoundaryId: predecessorId,
      boundPredecessor: { scene_id: project.scenes[1].id, boundary_id: boundaryId, exit_sha256: "d".repeat(64) } });
    await expect(prepareProductionSubmission(draft, project, id)).rejects.toThrow(/predecessor/i);
    expect(decodeProductionCopy(copy(draft), draft.scope, writer).draft.input).toEqual(draft.input);
  });
  it("requires full Project equality through asynchronous preparation, including unselected scenes and assets", () => {
    const project = source(), draft = createProductionDraft(project, boundaryTarget(project));
    expect(productionPreparationMatches(draft, project, draft, structuredClone(project))).toBe(true);
    for (const change of [(p: Project) => { p.brief.objective += " peer"; }, (p: Project) => { p.scenes[1].name += " peer"; },
      (p: Project) => { p.assets[0].content_sha256 = "f".repeat(64); }]) {
      const changed = structuredClone(project); change(changed);
      expect(productionPreparationMatches(draft, project, draft, changed)).toBe(false);
    }
  });
  it("keeps later invalid input and another writer's raw bytes when the original reply arrives", async () => {
    const project = source(), bound = bindFixture(createProductionDraft(project, boundaryTarget(project)), context(project));
    const prepared = await prepareProductionSubmission(bound, project, id), later = editBoundary(prepared.draft, { frameRateNum: "1e", entryJson: "{ later" });
    const store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
    writeProductionCopy(store, pointers, prepared.draft, owner); const original = owner.expectedRaw!;
    const acknowledged = acknowledgeProductionDraft(later, id, receiptFor(project, prepared.change));
    expect(acknowledged.input).toEqual(later.input); expect(acknowledged.pending).toBeNull(); expect(acknowledged.intent).toBe(true);
    writeProductionCopy(store, pointers, acknowledged, { writerId: fork, expectedRaw: null, expectedPointer: writer });
    expect(store.getItem(productionCopyKey(prepared.draft.scope, writer))).toBe(original);
    expect(decodeProductionCopy(original, prepared.draft.scope, writer).draft.pending?.outcome).toBe("UNKNOWN");
  });
  it("retains UNKNOWN after observing matching history and rejects a same-revision dependency change during review", async () => {
    const { project, prepared, receipt } = await savedBoundary();
    const review = await inspectProductionOutcome(prepared.draft, receipt, { readProject: async () => receipt,
      readHistory: async () => journal(receipt, prepared.change) });
    expect(review.matchingValueRevisions).toEqual([receipt.revision]); expect(productionUnconfirmed(prepared.draft)).toBe(true);
    expect(reconcileProductionDraft(prepared.draft, receipt)).toBe(prepared.draft);
    await expect(prepareProductionSubmission(prepared.draft, project, fork)).rejects.toThrow(/unconfirmed/i);
    let reads = 0;
    const changed = structuredClone(receipt); changed.brief.objective += " peer";
    await expect(inspectProductionOutcome(prepared.draft, receipt, { readProject: async () => ++reads === 1 ? receipt : changed,
      readHistory: async () => journal(receipt, prepared.change) })).rejects.toThrow(/changed during/i);
  });
  it("removes only the selected declaration, preserves editable nodes and durably blocks accidental identity reuse", async () => {
    const { receipt, acknowledged } = await savedBoundary(true), existingId = inputOf(acknowledged).boundaryId;
    const draft = editBoundary(acknowledged, { entryJson: "{ unfinished local edit" });
    const prepared = await prepareProductionSubmission(draft, receipt, fork, "remove");
    expect(prepared.change).toEqual({ type: "remove_scene_boundary", boundary_id: existingId });
    const recoveredPending = decodeProductionCopy(copy(prepared.draft), prepared.draft.scope, writer).draft;
    expect(recoveredPending.pending?.operation).toBe("remove"); expect(recoveredPending.pending?.value).toBeNull();
    expect(recoveredPending.pending?.nodesBefore).toEqual(receipt.scenes[0].nodes);
    const later = editBoundary(prepared.draft, { exitJson: "{ later input while removal is pending" });
    const removed = receiptFor(receipt, prepared.change), wrong = structuredClone(removed);
    wrong.scenes[0].nodes.push({ id: writer } as CanvasNode);
    expect(() => acknowledgeProductionDraft(prepared.draft, fork, wrong)).toThrow(/preserve/i);
    const done = acknowledgeProductionDraft(later, fork, removed), recovered = decodeProductionCopy(copy(done), done.scope, writer).draft;
    expect(done.scope).toBe(draft.scope); expect(done.input).toEqual(later.input); expect(done.removedBoundaryId).toBe(existingId);
    await expect(prepareProductionSubmission(recovered, removed, id)).rejects.toThrow(/removed/i);
    expect(inputOf(createProductionDraft(removed, boundaryTarget(removed))).boundaryId).not.toBe(existingId);
    const review = await inspectProductionOutcome(prepared.draft, removed, { readProject: async () => removed, readHistory: async () => journal(removed, prepared.change) });
    expect(releaseProductionOutcome(prepared.draft, removed, review).removedBoundaryId).toBe(existingId);
  });
  it("rejects forged pending values and authority fields while leaving the complete original copy intact", async () => {
    const { prepared } = await savedBoundary(), raw = copy(prepared.draft), corrupted = JSON.parse(raw);
    corrupted.draft.pending.value.scene_id = fork;
    expect(() => decodeProductionCopy(JSON.stringify(corrupted), prepared.draft.scope, writer)).toThrow(/snapshot/i);
    const project = source(), bound = bindFixture(createProductionDraft(project, boundaryTarget(project)), context(project)), authority = JSON.parse(copy(bound));
    authority.draft.input.boundSource.effect_grant = "untrusted";
    expect(() => decodeProductionCopy(JSON.stringify(authority), bound.scope, writer)).toThrow();
    expect(decodeProductionCopy(raw, prepared.draft.scope, writer).draft.pending?.value).toEqual(prepared.draft.pending?.value);
  });
  it("rejects oversized recovery without truncating endpoint input and refuses boundary operations on legacy targets", async () => {
    const project = source(), draft = editBoundary(createProductionDraft(project, boundaryTarget(project)), { entryJson: "x".repeat(MAX_PRODUCTION_COPY_BYTES + 1) });
    expect(() => copy(draft)).toThrow(/1 MiB/i); expect(inputOf(draft).entryJson.length).toBe(MAX_PRODUCTION_COPY_BYTES + 1);
    const plan = createProductionDraft(project, { kind: "plan" });
    await expect(prepareProductionSubmission(plan, project, id, "remove")).rejects.toThrow(/Only a scene boundary/i);
    const boundary = createProductionDraft(project, boundaryTarget(project));
    for (const operation of ["approve", "detach"] as const)
      await expect(prepareProductionSubmission(boundary, project, id, operation)).rejects.toThrow(/only an explicit save or removal/i);
  });
});
