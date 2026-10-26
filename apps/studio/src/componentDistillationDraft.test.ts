import { describe, expect, it, vi } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import type { CanvasNode, Change, Project, ProjectEvent } from "./types";
import {
  acknowledgeDistillationDraft, createDistillationDraft, decodeDistillationCopy, editDistillationDraft,
  encodeDistillationCopy, encodeDistillationFile, inspectDistillationOutcome, observeDistillationRecoveryCopy, prepareDistillationSubmission,
  readDistillationCopies, reconcileDistillationDraft, releaseDistillationOutcome, resolveDistillationDraft,
  retainDistillationFailure, retainReviewedDistillationIntent, selectDistillationReview,
  distillationConflict, distillationCopyKey, distillationOutcomeMatches, distillationPointerKey, readDistillationRecovery,
  distillationScope, distillationUnconfirmed, writeDistillationCopy, DISTILLATION_RECOVERY_PREFIX,
} from "./componentDistillationDraft";
import type { DistillationAuthorRaw, DistillationCustody, DistillationDraft, DistillationRecoveryPointer, DistillationReviewRaw, DistillationStorage } from "./componentDistillationDraft";
const requestId = "00000000-0000-4000-8000-000000000081", writer = "00000000-0000-4000-8000-000000000082", fork = "00000000-0000-4000-8000-000000000083";
async function source(): Promise<Project> {
  let project = structuredClone(fixtureProject); project.locks = []; project.branch_workspaces = []; project.production_design = emptyProductionDesign();
  const original = project.scenes.flatMap(scene => scene.nodes).find(value => value.kind === "text");
  if (!original) throw new Error("Missing complete original Canvas node template.");
  for (const [index, scene] of project.scenes.filter(value => value.renderer === "motion-canvas").slice(0, 2).entries()) {
    if (!scene.nodes.some(value => value.kind === "text")) project = await applyChange(project, { type: "add_canvas_node", scene_id: scene.id, node: {
      ...structuredClone(original), id: "00000000-0000-4000-8000-000000000095", name: "Original second source heading", text: "A distinct authored source variation.",
      x: 220, parent_id: null, relations: [], property_locks: [], keyframes: [],
    } });
    const node: CanvasNode = { ...structuredClone(original), id: "00000000-0000-4000-8000-00000000009" + (index + 1),
      name: "Original source variation " + index, kind: "rectangle", text: null, x: 230 + index * 20, y: 460, width: 500, height: 180,
      parent_id: null, relations: [], property_locks: [], keyframes: [] };
    project = await applyChange(project, { type: "add_canvas_node", scene_id: scene.id, node });
  }
  return project;
}
function author(project: Project): DistillationDraft {
  const draft = createDistillationDraft(project, { kind: "author" }), scenes = project.scenes.filter(value => value.renderer === "motion-canvas");
  return editDistillationDraft(draft, { aId: scenes[0].id, bId: scenes[1].id, negativeId: project.scenes.find(value => value.renderer !== "motion-canvas")!.id,
    rolesCount: "2", label: "  Candidate A  ", author: "  Original author  ", rights: "  Original source objects authored for this controlled fixture.  ",
    requirements: "  Preserve source roles  \n\nKeep review independent\n" });
}
function edit(draft: DistillationDraft, value: string) {
  return editDistillationDraft(draft, draft.target.kind === "author" ? { ...(draft.raw as DistillationAuthorRaw), label: value }
    : { ...(draft.raw as DistillationReviewRaw), note: value });
}
async function armed(kind: "author" | "review" = "author") {
  let project = await source(), draft = author(project);
  if (kind === "review") {
    const proposed = prepareDistillationSubmission(draft, project, requestId); project = await applyChange(project, proposed.change);
    const id = project.production_design!.distillations![0].input.id;
    draft = editDistillationDraft(createDistillationDraft(project, { kind: "review", proposalId: id }),
      { reviewer: "  Independent reviewer  ", note: "  Source-compatible test proposal; no executable admission.  ", verdict: "compatible_proposal" });
  }
  return { project, source: draft, prepared: prepareDistillationSubmission(draft, project, requestId) };
}
class MemoryStorage implements DistillationStorage {
  values = new Map<string, string>(); get length() { return this.values.size; } key(index: number) { return [...this.values.keys()][index] ?? null; }
  getItem(key: string) { return this.values.get(key) ?? null; } setItem(key: string, value: string) { this.values.set(key, value); } removeItem(key: string) { this.values.delete(key); }
}
const custody = (): DistillationCustody => ({ writerId: writer, expectedRaw: null, expectedPointer: null });
const journal = (project: Project, change: Change): ProjectEvent[] => [{ revision: project.revision, change, created_at: "2026-10-21T20:10:00Z" }];

function recoverySource(draft: DistillationDraft) {
  const storage = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
  writeDistillationCopy(storage, pointers, draft, owner);
  return { storage, pointers, owner, key: distillationCopyKey(draft.scope, writer), pointer: distillationPointerKey(draft.scope) };
}

describe("Distillation first-read recovery preserves observed custody and later input", () => {
  it("keeps a throwing first pointer read unobserved and then restores the original ordinary draft", async () => {
    const project = await source(), retained = author(project), current = createDistillationDraft(project, retained.target);
    const { storage, pointers, key } = recoverySource(retained), raw = storage.getItem(key), get = pointers.getItem.bind(pointers);
    const observation: DistillationRecoveryPointer = { observed: false, value: null };
    pointers.getItem = () => { throw new Error("First pointer read unavailable"); };
    expect(() => readDistillationRecovery(storage, pointers, current, project, observation)).toThrow("unavailable");
    expect(observation).toEqual({ observed: false, value: null });
    pointers.getItem = get;
    expect(readDistillationRecovery(storage, pointers, current, project, observation)).toEqual(retained);
    expect(observation).toMatchObject({ observed: true, value: writer }); expect(storage.getItem(key)).toBe(raw);
    expect(current).toEqual(createDistillationDraft(project, retained.target));
  });
  it("a first confirmed null retains the complete current draft, including any pending comparison", async () => {
    const { project, prepared } = await armed(), current = edit(prepared.draft, "  Later current input  ");
    const storage = new MemoryStorage(), pointers = new MemoryStorage(), observation: DistillationRecoveryPointer = { observed: false, value: null };
    expect(readDistillationRecovery(storage, pointers, current, project, observation)).toBe(current);
    expect(observation).toEqual({ observed: true, value: null }); expect(storage.length).toBe(0); expect(pointers.length).toBe(0);
  });
  it("anchors a successfully observed writer before a copy read fails and rejects its later removal", async () => {
    const project = await source(), retained = author(project), current = createDistillationDraft(project, retained.target);
    const { storage, pointers, pointer, key } = recoverySource(retained), raw = storage.getItem(key), get = storage.getItem.bind(storage);
    const observation: DistillationRecoveryPointer = { observed: false, value: null };
    storage.getItem = () => { throw new Error("Copy read unavailable"); };
    expect(() => readDistillationRecovery(storage, pointers, current, project, observation)).toThrow("unavailable");
    expect(observation).toMatchObject({ observed: true, value: writer });
    storage.getItem = get; pointers.removeItem(pointer);
    expect(() => readDistillationRecovery(storage, pointers, current, project, observation)).toThrow("pointer changed");
    expect(observation.value).toBe(writer); expect(storage.getItem(key)).toBe(raw);
  });
  for (const [observed, actual] of [[writer, null], [writer, fork], [null, writer]] as const) {
    it(`rejects an observed pointer transition from ${observed ?? "null"} to ${actual ?? "null"}`, async () => {
      const project = await source(), current = author(project), { storage, pointers, pointer, key } = recoverySource(current), raw = storage.getItem(key);
      if (actual === null) pointers.removeItem(pointer); else pointers.setItem(pointer, actual);
      const observation: DistillationRecoveryPointer = { observed: true, value: observed };
      expect(() => readDistillationRecovery(storage, pointers, current, project, observation)).toThrow("pointer changed");
      expect(observation).toEqual({ observed: true, value: observed }); expect(storage.getItem(key)).toBe(raw); expect(pointers.getItem(pointer)).toBe(actual);
    });
  }
  it("rereads a newly observed null before allowing a fresh copy", async () => {
    const project = await source(), current = author(project), storage = new MemoryStorage(), pointers = new MemoryStorage();
    const observation: DistillationRecoveryPointer = { observed: false, value: null }; let reads = 0;
    pointers.getItem = () => ++reads === 1 ? null : writer;
    expect(() => readDistillationRecovery(storage, pointers, current, project, observation)).toThrow("pointer changed");
    expect(observation).toEqual({ observed: true, value: null }); expect(storage.length).toBe(0);
  });
  for (const kind of ["author", "review"] as const) {
    it(`${kind} UNKNOWN recovery keeps clear/ABA input and the complete original guard through fork and reload`, async () => {
      const { project, prepared } = await armed(kind), initial = createDistillationDraft(project, prepared.draft.target);
      const current = edit(edit(initial, "Later input typed while recovery was blocked"), "");
      expect(current.raw).toEqual(initial.raw); expect(current.intent).toBe(true); expect(current.sequence).toBeGreaterThan(initial.sequence);
      const { storage, pointers, key } = recoverySource(prepared.draft), original = storage.getItem(key);
      const observation: DistillationRecoveryPointer = { observed: false, value: null };
      const recovered = readDistillationRecovery(storage, pointers, current, project, observation);
      expect(recovered.raw).toEqual(current.raw); expect(recovered.pending).toEqual(prepared.draft.pending);
      expect(recovered.baseRevision).toBe(prepared.draft.baseRevision); expect(recovered.baseProposal).toEqual(prepared.draft.baseProposal);
      expect(recovered.sequence).toBeGreaterThan(Math.max(current.sequence, prepared.draft.sequence)); expect(recovered.intent).toBe(true);
      const next: DistillationCustody = { writerId: fork, expectedRaw: null, expectedPointer: observation.value };
      writeDistillationCopy(storage, pointers, recovered, next);
      expect(storage.getItem(key)).toBe(original); expect(pointers.getItem(distillationPointerKey(recovered.scope))).toBe(fork);
      const reloaded = decodeDistillationCopy(next.expectedRaw!, recovered.scope, fork).draft;
      expect(reloaded).toEqual(recovered); expect(() => prepareDistillationSubmission(reloaded, project, writer)).toThrow("uncertain");
    });
  }
  it("ordinary recovery cannot erase a current clear/ABA edit even when its raw equals the initial blank", async () => {
    const project = await source(), retained = author(project), initial = createDistillationDraft(project, retained.target), current = edit(edit(initial, "Later input"), "");
    const { storage, pointers, key } = recoverySource(retained), original = storage.getItem(key);
    expect(current.raw).toEqual(initial.raw);
    expect(readDistillationRecovery(storage, pointers, current, project, { observed: false, value: null })).toBe(current);
    expect(storage.getItem(key)).toBe(original);
  });
  it("does not replace a different current pending attempt with the recovered one", async () => {
    const { project, prepared } = await armed(), current = { ...prepared.draft, pending: { ...prepared.draft.pending!, id: fork } }, before = structuredClone(current);
    const { storage, pointers, key } = recoverySource(prepared.draft), original = storage.getItem(key);
    expect(() => readDistillationRecovery(storage, pointers, current, project, { observed: false, value: null })).toThrow("different Distillation attempt");
    expect(current).toEqual(before); expect(storage.getItem(key)).toBe(original);
  });
  for (const race of ["copy", "pointer"] as const) {
    it(`rejects changed ${race} bytes during adoption without replacing current input`, async () => {
      const project = await source(), retained = author(project), current = createDistillationDraft(project, retained.target), before = structuredClone(current);
      const { storage, pointers, key, pointer } = recoverySource(retained), original = storage.getItem(key);
      const get = storage.getItem.bind(storage); let armed = true;
      storage.getItem = name => {
        const value = get(name);
        if (armed && name === key) { armed = false; if (race === "copy") storage.values.set(key, "Changed source bytes"); else pointers.values.set(pointer, fork); }
        return value;
      };
      const observation: DistillationRecoveryPointer = { observed: false, value: null };
      expect(() => readDistillationRecovery(storage, pointers, current, project, observation)).toThrow("changed while recovery");
      expect(current).toEqual(before); expect(observation).toMatchObject({ observed: true, value: writer });
      expect(storage.getItem(key)).toBe(race === "copy" ? "Changed source bytes" : original); expect(pointers.getItem(pointer)).toBe(race === "pointer" ? fork : writer);
    });
  }
});

describe("Distillation first observed bytes remain the retry source", () => {
  for (const kind of ["author", "review"] as const) {
    it(`${kind} pins UNKNOWN bytes seen during inventory after a failed read and refuses same-writer substitution`, async () => {
      const { project, prepared } = await armed(kind), current = edit(createDistillationDraft(project, prepared.draft.target), "Later input");
      const { storage, pointers, key } = recoverySource(prepared.draft), original = storage.getItem(key)!;
      const observation: DistillationRecoveryPointer = { observed: false, value: null }, get = storage.getItem.bind(storage);
      storage.getItem = () => { throw new Error("Transient initial copy read failure"); };
      expect(() => readDistillationRecovery(storage, pointers, current, project, observation)).toThrow("Transient");
      expect(observation.firstRead).toBeUndefined();
      storage.getItem = get;
      observeDistillationRecoveryCopy(storage, current.scope, observation);
      expect(observation.firstRead).toEqual({ key, writerId: writer, raw: original });
      const replacement = JSON.parse(original); replacement.draft.pending = null;
      storage.setItem(key, JSON.stringify(replacement));
      expect(decodeDistillationCopy(storage.getItem(key)!, current.scope, writer).draft.pending).toBeNull();
      expect(() => readDistillationRecovery(storage, pointers, current, project, observation)).toThrow("original Distillation recovery bytes changed");
      expect(observation.firstRead!.raw).toBe(original); expect(current.pending).toBeNull();
      storage.setItem(key, original);
      const recovered = readDistillationRecovery(storage, pointers, current, project, observation);
      expect(recovered.raw).toEqual(current.raw); expect(recovered.pending).toEqual(prepared.draft.pending);
      expect(recovered.sequence).toBeGreaterThan(current.sequence);
    });
  }
  it("pins corrupt observed bytes before decoding and never treats a replacement as their repair", async () => {
    const project = await source(), current = createDistillationDraft(project, { kind: "author" });
    const { storage, pointers, key } = recoverySource(author(project)), healthy = storage.getItem(key)!;
    const observation: DistillationRecoveryPointer = { observed: false, value: null };
    storage.setItem(key, "{incomplete original");
    expect(() => readDistillationRecovery(storage, pointers, current, project, observation)).toThrow();
    expect(observation.firstRead).toEqual({ key, writerId: writer, raw: "{incomplete original" });
    storage.setItem(key, healthy);
    expect(() => readDistillationRecovery(storage, pointers, current, project, observation)).toThrow("original Distillation recovery bytes changed");
    expect(observation.firstRead!.raw).toBe("{incomplete original");
  });
  it("pins a missing copy before validation and rejects a later same-writer insertion", async () => {
    const project = await source(), current = createDistillationDraft(project, { kind: "author" });
    const { storage, pointers, key } = recoverySource(author(project)), healthy = storage.getItem(key)!;
    const observation: DistillationRecoveryPointer = { observed: false, value: null };
    storage.removeItem(key);
    expect(() => readDistillationRecovery(storage, pointers, current, project, observation)).toThrow("still missing");
    expect(observation.firstRead).toEqual({ key, writerId: writer, raw: null });
    storage.setItem(key, healthy);
    expect(() => readDistillationRecovery(storage, pointers, current, project, observation)).toThrow("original Distillation recovery bytes changed");
    expect(observation.firstRead!.raw).toBeNull();
  });
});

describe("Distillation raw author/reviewer scope and original response authority", () => {
  it("scopes author and each immutable proposal by project, generation and branch", async () => {
    const project = await source(), authorScope = distillationScope(project, { kind: "author" });
    for (const identity of ["id", "generation", "active_branch"] as const) expect(distillationScope({ ...project, [identity]: project[identity] + "-other" }, { kind: "author" })).not.toBe(authorScope);
    const a = distillationScope(project, { kind: "review", proposalId: writer }), b = distillationScope(project, { kind: "review", proposalId: fork });
    expect(a).not.toBe(b); expect(a).not.toBe(authorScope); expect(a).not.toBe(distillationScope(project, { kind: "review", proposalId: null }));
  });
  it("retains exact author spelling while the existing domain prepares normalized immutable evidence", async () => {
    const project = await source(), draft = author(project), before = structuredClone(project), prepared = prepareDistillationSubmission(draft, project, requestId);
    expect(project).toEqual(before); expect(prepared.draft.raw).toEqual(draft.raw);
    if (prepared.change.type !== "propose_component_distillation") throw new Error("Wrong operation");
    expect(prepared.change.input.label).toBe("Candidate A"); expect(prepared.change.input.requirements).toEqual(["Preserve source roles", "Keep review independent"]);
    expect(prepared.draft.pending!.expected.samples.map(sample => sample.nodes)).toEqual(prepared.change.input.positives.map(selector => selector.node_ids.map(id => project.scenes.find(scene => scene.id === selector.scene_id)!.nodes.find(node => node.id === id))));
  });
  it("rejects incomplete or out-of-range role counts without coercing or changing raw input", async () => {
    const project = await source(), draft = author(project);
    for (const rolesCount of ["", "1e", "2.5", "0x2", "1", "99"]) {
      const current = editDistillationDraft(draft, { ...(draft.raw as DistillationAuthorRaw), rolesCount });
      expect(() => prepareDistillationSubmission(current, project, requestId)).toThrow(); expect((current.raw as DistillationAuthorRaw).rolesCount).toBe(rolesCount);
    }
  });
  it("uses the actual source validator to reject cloned positive samples and false counterevidence", async () => {
    const project = await source(), draft = author(project), copied = structuredClone(project), a = copied.scenes[0], b = copied.scenes[1];
    b.nodes = a.nodes.map((node, index) => ({ ...structuredClone(node), id: b.nodes[index].id }));
    expect(() => prepareDistillationSubmission(draft, copied, requestId)).toThrow("variation");
    const invalid = editDistillationDraft(draft, { ...(draft.raw as DistillationAuthorRaw), negativeId: a.id });
    expect(() => prepareDistillationSubmission(invalid, project, requestId)).toThrow();
  });
  it("remembers review selection without marking or replacing author raw input", async () => {
    const project = await source(), draft = createDistillationDraft(project, { kind: "author" }), selected = selectDistillationReview(draft, writer);
    expect(selected.selection).toBe(writer); expect(selected.raw).toEqual(draft.raw); expect(selected.sequence).toBe(draft.sequence); expect(selected.intent).toBe(false);
  });
  for (const kind of ["author", "review"] as const) {
    it(`${kind} matching observed values never consume the original UNKNOWN attempt`, async () => {
      const { project, prepared } = await armed(kind), observed = await applyChange(project, prepared.change);
      expect(reconcileDistillationDraft(prepared.draft, observed)).toBe(prepared.draft); expect(distillationUnconfirmed(prepared.draft)).toBe(true);
      expect(distillationConflict(prepared.draft, observed)).toBe(true);
    });
    it(`${kind} original ACK preserves later raw input and leaves a separate editable intent`, async () => {
      const { project, prepared } = await armed(kind), later = edit(prepared.draft, "  Later raw input\n "), receipt = await applyChange(project, prepared.change);
      const acknowledged = acknowledgeDistillationDraft(later, requestId, receipt);
      expect(acknowledged.raw).toEqual(later.raw); expect(acknowledged.intent).toBe(true); expect(acknowledged.pending).toBeNull(); expect(acknowledged.baseRevision).toBe(receipt.revision);
      expect(receipt.scenes).toEqual(project.scenes);
    });
    it(`${kind} clean original ACK keeps raw spelling and cannot be implicitly submitted again`, async () => {
      const { project, prepared } = await armed(kind), receipt = await applyChange(project, prepared.change), acknowledged = acknowledgeDistillationDraft(prepared.draft, requestId, receipt);
      expect(acknowledged.raw).toEqual(prepared.draft.raw); expect(acknowledged.intent).toBe(false); expect(acknowledged.pending).toBeNull();
      expect(() => prepareDistillationSubmission(acknowledged, receipt, fork)).toThrow();
      expect(reconcileDistillationDraft(acknowledged, { ...receipt, revision: receipt.revision + 1 }).raw).toEqual(acknowledged.raw);
    });
    it(`${kind} roundtrip recovery and explicit file carry exact raw input without grants`, async () => {
      const { prepared } = await armed(kind), raw = encodeDistillationCopy({ schema: "motionwright.component-distillation-recovery/1", scope: prepared.draft.scope, writerId: writer, updatedAt: 1, draft: prepared.draft });
      expect(decodeDistillationCopy(raw, prepared.draft.scope, writer).draft).toEqual(prepared.draft);
      const file = JSON.parse(encodeDistillationFile(prepared.draft)); expect(file.draft.raw).toEqual(prepared.draft.raw);
      expect(Object.keys(file)).toEqual(["schema", "exportedAt", "draft"]); expect(file).not.toHaveProperty("effect_grant"); expect(file.draft).not.toHaveProperty("effect_grant");
    });
  }
  for (const field of ["id", "generation", "active_branch", "revision", "value", "samples"] as const) {
    it(`rejects an original proposal receipt with wrong ${field} and leaves UNKNOWN intact`, async () => {
      const { project, prepared } = await armed(), receipt = await applyChange(project, prepared.change);
      if (field === "revision") receipt.revision++; else if (field === "value") receipt.production_design!.distillations![0].input.label = "Another candidate";
      else if (field === "samples") receipt.production_design!.distillations![0].samples[0].nodes[0].x += 1; else receipt[field] += "-other";
      expect(() => acknowledgeDistillationDraft(prepared.draft, requestId, receipt)).toThrow(); expect(prepared.draft.pending?.id).toBe(requestId);
    });
  }
  it("a review receipt must preserve exact original samples, source input and submitted independent review", async () => {
    const { project, prepared } = await armed("review"), receipt = await applyChange(project, prepared.change);
    for (const field of ["sample", "input", "review"] as const) {
      const broken = structuredClone(receipt), candidate = broken.production_design!.distillations![0];
      if (field === "sample") candidate.samples[0].nodes[0].text = "Changed source"; else if (field === "input") candidate.input.requirements = ["Changed requirement"]; else candidate.review!.rationale = "Other reviewer text";
      expect(() => acknowledgeDistillationDraft(prepared.draft, requestId, broken)).toThrow("exact");
    }
    expect(receipt.production_design!.distillations![0].samples).toEqual(project.production_design!.distillations![0].samples);
  });
  it("review ACK cannot mutate the independent draft for another proposal", async () => {
    const { project, prepared } = await armed("review"), other = editDistillationDraft(createDistillationDraft(project, { kind: "review", proposalId: fork }),
      { reviewer: "Reviewer B", note: "  B rationale remains local  ", verdict: "rejected" }), original = structuredClone(other);
    acknowledgeDistillationDraft(prepared.draft, requestId, await applyChange(project, prepared.change)); expect(other).toEqual(original);
  });
  it("frozen review cannot be rewritten even when later local rationale remains editable", async () => {
    const { project, prepared } = await armed("review"), receipt = await applyChange(project, prepared.change), acknowledged = acknowledgeDistillationDraft(prepared.draft, requestId, receipt), later = edit(acknowledged, "Later retained reconsideration");
    expect(() => prepareDistillationSubmission(later, receipt, fork)).toThrow("immutable"); expect((later.raw as DistillationReviewRaw).note).toBe("Later retained reconsideration");
  });
  it("review of imported immutable source can use the current generation without rewriting frozen generation", async () => {
    const { project, source: draft } = await armed("review"), imported = { ...structuredClone(project), generation: project.generation + "-new" };
    const fresh = editDistillationDraft(createDistillationDraft(imported, draft.target), draft.raw), prepared = prepareDistillationSubmission(fresh, imported, requestId);
    expect(prepared.draft.pending!.expected.input.source_generation).toBe(project.production_design!.distillations![0].input.source_generation);
    expect(prepared.change.type).toBe("review_component_distillation");
  });
  it("external revisions require explicit base selection and cannot silently replace current raw input", async () => {
    const { project, source: draft } = await armed(), newer = { ...project, revision: project.revision + 1 };
    expect(reconcileDistillationDraft(draft, newer)).toBe(draft); expect(distillationConflict(draft, newer)).toBe(true);
    const chosen = resolveDistillationDraft(draft, newer, "draft"); expect(chosen.raw).toEqual(draft.raw); expect(chosen.baseRevision).toBe(newer.revision);
  });
});

describe("Distillation verified durable custody and current unknown-outcome review", () => {
  for (const kind of ["author", "review"] as const) {
    for (const outcome of ["UNKNOWN", "NOT_SENT"] as const) {
      it(`${kind} ${outcome} confirmation preserves the exact same draft bytes when the clock advances`, async () => {
        const { prepared } = await armed(kind), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
        const draft = outcome === "NOT_SENT" ? retainDistillationFailure(prepared.draft, requestId, "Local dispatch was not confirmed", true) : prepared.draft;
        const clock = vi.spyOn(Date, "now").mockReturnValue(1000);
        try {
          writeDistillationCopy(store, pointers, draft, owner);
          const key = distillationCopyKey(draft.scope, writer), pointer = distillationPointerKey(draft.scope), raw = store.getItem(key)!;
          const before = structuredClone(owner), writes = vi.spyOn(store, "setItem");
          expect(JSON.parse(raw).updatedAt).toBe(1000); expect(JSON.parse(raw).draft.pending.outcome).toBe(outcome);
          clock.mockReturnValue(2000);
          writeDistillationCopy(store, pointers, structuredClone(draft), owner);
          expect(store.getItem(key)).toBe(raw); expect(owner).toEqual(before); expect(writes).not.toHaveBeenCalled();
          expect(pointers.getItem(pointer)).toBe(writer);
        } finally { clock.mockRestore(); }
      });
    }
  }
  it("same-draft confirmation still rejects a missing publication and repairs only a successful pointer retry", async () => {
    const { prepared } = await armed(), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
    const pointer = distillationPointerKey(prepared.draft.scope), key = distillationCopyKey(prepared.draft.scope, writer);
    const publish = pointers.setItem.bind(pointers), clock = vi.spyOn(Date, "now").mockReturnValue(1000);
    try {
      pointers.setItem = () => {};
      expect(() => writeDistillationCopy(store, pointers, prepared.draft, owner)).toThrow("pointer was not confirmed");
      const raw = store.getItem(key)!, writes = vi.spyOn(store, "setItem");
      expect(owner.expectedRaw).toBe(raw); expect(owner.expectedPointer).toBeNull(); expect(pointers.getItem(pointer)).toBeNull();
      clock.mockReturnValue(2000);
      expect(() => writeDistillationCopy(store, pointers, structuredClone(prepared.draft), owner)).toThrow("pointer was not confirmed");
      expect(store.getItem(key)).toBe(raw); expect(owner.expectedRaw).toBe(raw); expect(owner.expectedPointer).toBeNull();
      expect(writes).not.toHaveBeenCalled(); expect(pointers.getItem(pointer)).toBeNull();
      pointers.setItem = publish; clock.mockReturnValue(3000);
      writeDistillationCopy(store, pointers, structuredClone(prepared.draft), owner);
      expect(store.getItem(key)).toBe(raw); expect(writes).not.toHaveBeenCalled();
      expect(owner.expectedRaw).toBe(raw); expect(owner.expectedPointer).toBe(writer); expect(pointers.getItem(pointer)).toBe(writer);
    } finally { clock.mockRestore(); }
  });
  for (const replacement of [null, fork]) {
    it(`same-draft confirmation rejects a previously confirmed pointer replaced by ${replacement ?? "missing"}`, async () => {
      const { prepared } = await armed(), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
      const clock = vi.spyOn(Date, "now").mockReturnValue(1000);
      try {
        writeDistillationCopy(store, pointers, prepared.draft, owner);
        const key = distillationCopyKey(prepared.draft.scope, writer), pointer = distillationPointerKey(prepared.draft.scope);
        const raw = store.getItem(key), before = structuredClone(owner), writes = vi.spyOn(store, "setItem");
        if (replacement === null) pointers.removeItem(pointer); else pointers.setItem(pointer, replacement);
        clock.mockReturnValue(2000);
        expect(() => writeDistillationCopy(store, pointers, structuredClone(prepared.draft), owner)).toThrow("writer pointer");
        expect(store.getItem(key)).toBe(raw); expect(owner).toEqual(before); expect(writes).not.toHaveBeenCalled();
        expect(pointers.getItem(pointer)).toBe(replacement);
      } finally { clock.mockRestore(); }
    });
  }


  for (const fault of ["data", "pointer"] as const) {
    it(`requires ${fault} readback before an armed recovery copy can permit dispatch`, async () => {
      const { prepared } = await armed(), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody(), affected = fault === "data" ? store : pointers;
      const original = affected.setItem.bind(affected); affected.setItem = () => {};
      expect(() => writeDistillationCopy(store, pointers, prepared.draft, owner)).toThrow("confirmed");
      affected.setItem = original; writeDistillationCopy(store, pointers, prepared.draft, owner); expect(pointers.getItem(distillationPointerKey(prepared.draft.scope))).toBe(writer);
    });
  }
  for (const race of ["data", "pointer"] as const) {
    it(`rechecks ${race} custody after enumeration and preserves the changed record`, async () => {
      const { prepared } = await armed(), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody(); writeDistillationCopy(store, pointers, prepared.draft, owner);
      const key = distillationCopyKey(prepared.draft.scope, writer), pointer = distillationPointerKey(prepared.draft.scope), original = store.key.bind(store); let raceArmed = true;
      store.key = index => { if (raceArmed) { raceArmed = false; if (race === "data") store.values.set(key, "Other author's bytes"); else pointers.values.set(pointer, fork); } return original(index); };
      expect(() => writeDistillationCopy(store, pointers, edit(prepared.draft, "Later raw"), owner)).toThrow("changed");
      if (race === "data") expect(store.getItem(key)).toBe("Other author's bytes"); else expect(pointers.getItem(pointer)).toBe(fork);
    });
  }
  it("rereads data after publishing the pointer and rejects interleaved replacement", async () => {
    const { prepared } = await armed(), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody(), key = distillationCopyKey(prepared.draft.scope, writer), set = pointers.setItem.bind(pointers);
    pointers.setItem = (name, value) => { set(name, value); store.values.set(key, "Foreign replacement"); };
    expect(() => writeDistillationCopy(store, pointers, prepared.draft, owner)).toThrow("pointer was published"); expect(store.getItem(key)).toBe("Foreign replacement");
  });
  it("preserves corrupt bytes and refuses foreign copies or clean checkpoint overwrite", async () => {
    const project = await source(), draft = createDistillationDraft(project, { kind: "author" }), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
    writeDistillationCopy(store, pointers, draft, owner); expect(() => writeDistillationCopy(store, pointers, edit(draft, "New candidate"), owner)).toThrow("immutable");
    const key = distillationCopyKey(draft.scope, fork); store.setItem(key, "{corrupt"); expect(readDistillationCopies(store, draft.scope).entries.find(entry => entry.key === key)?.value).toBeNull();
    expect(store.getItem(key)).toBe("{corrupt"); expect(() => decodeDistillationCopy(owner.expectedRaw!, draft.scope + "-other", writer)).toThrow();
  });
  it("full recovery budget blocks saving without deleting earlier source/reviewer bytes", async () => {
    const { prepared } = await armed(), store = new MemoryStorage(), pointers = new MemoryStorage();
    for (let index = 0; index < 64; index++) store.setItem(DISTILLATION_RECOVERY_PREFIX + "older-" + index, "Earlier author bytes");
    expect(() => writeDistillationCopy(store, pointers, prepared.draft, custody())).toThrow("full"); expect(store.length).toBe(64); expect(pointers.length).toBe(0);
  });
  it("NOT_SENT recovery remains conservative UNKNOWN and rejects extra authority fields", async () => {
    const { prepared } = await armed(), draft = retainDistillationFailure(prepared.draft, requestId, "Local publication not confirmed", true);
    const copy = { schema: "motionwright.component-distillation-recovery/1" as const, scope: draft.scope, writerId: writer, updatedAt: 1, draft };
    expect(decodeDistillationCopy(encodeDistillationCopy(copy), draft.scope, writer).draft.pending?.outcome).toBe("UNKNOWN");
    expect(() => decodeDistillationCopy(JSON.stringify({ ...copy, effect_grant: "forged" }), draft.scope, writer)).toThrow();
    expect(() => decodeDistillationCopy(JSON.stringify({ ...copy, draft: { ...draft, raw: { ...draft.raw, acknowledged: true } } }), draft.scope, writer)).toThrow();
    expect(() => decodeDistillationCopy(JSON.stringify({ ...copy, draft: { ...draft, pending: { ...draft.pending, effect_grant: "forged" } } }), draft.scope, writer)).toThrow();
  });
  for (const kind of ["author", "review"] as const) {
    it(`${kind} same-revision inspection cannot release an original attempt that may still commit`, async () => {
      const { project, prepared } = await armed(kind);
      const later = edit(prepared.draft, "  Later input while the original result is unknown  ");
      const { storage, pointers, owner, key, pointer } = recoverySource(later);
      const original = storage.getItem(key), pending = structuredClone(later.pending);
      let reads = 0;
      const review = await inspectDistillationOutcome(later, project, {
        readProject: async () => { reads++; return structuredClone(project); },
        readHistory: async () => [],
      });
      expect(reads).toBe(2);
      expect(review.project.revision).toBe(pending!.revision);
      expect(review.events).toEqual([]);
      expect(distillationOutcomeMatches(later, project, review)).toBe(true);
      expect(() => releaseDistillationOutcome(later, project, review)).toThrow("could still commit");
      expect(() => prepareDistillationSubmission(later, project, fork)).toThrow("uncertain attempt");
      expect(later.pending).toEqual(pending);
      expect(storage.getItem(key)).toBe(original);
      expect(pointers.getItem(pointer)).toBe(writer);
      expect(owner.expectedRaw).toBe(original);
      const reopened = decodeDistillationCopy(original!, later.scope, writer).draft;
      expect(reopened.raw).toEqual(later.raw);
      expect(reopened.pending).toEqual(pending);
      expect(distillationUnconfirmed(reopened)).toBe(true);
    });
    it(`${kind} an advanced inspected revision permits separate intent without acknowledging or deleting the original`, async () => {
      const { project, prepared } = await armed(kind);
      const later = edit(prepared.draft, "  Later input retained after a peer revision  ");
      const { storage, pointers, owner, key } = recoverySource(later), original = storage.getItem(key);
      const peer: Change = { type: "rename_project", title: "Independent peer change closes the original base" };
      const saved = await applyChange(project, peer);
      const review = await inspectDistillationOutcome(later, saved, {
        readProject: async () => structuredClone(saved), readHistory: async () => journal(saved, peer),
      });
      expect(saved.revision).toBe(project.revision + 1);
      expect(review.matchingValueRevisions).toEqual([]);
      const next = releaseDistillationOutcome(later, saved, review);
      expect(next.baseRevision).toBe(saved.revision);
      expect(next.raw).toEqual(later.raw);
      expect(next.pending).toBeNull();
      expect(next.intent).toBe(true);
      expect(later.pending).toEqual(prepared.draft.pending);
      const newer = retainReviewedDistillationIntent(storage, pointers, later, next, owner, fork);
      expect(storage.getItem(key)).toBe(original);
      const retained = decodeDistillationCopy(original!, later.scope, writer).draft;
      expect(retained.pending).toEqual(prepared.draft.pending);
      expect(retained.raw).toEqual(later.raw);
      const reopened = decodeDistillationCopy(newer.expectedRaw!, next.scope, fork).draft;
      expect(reopened.pending).toBeNull();
      expect(reopened.raw).toEqual(later.raw);
      expect(pointers.getItem(distillationPointerKey(next.scope))).toBe(fork);
    });
  }
  it("requires fresh read-history-read while matching history remains comparison only", async () => {
    const { project, prepared } = await armed(), saved = await applyChange(project, prepared.change); let reads = 0;
    const review = await inspectDistillationOutcome(prepared.draft, saved, { readProject: async () => { reads++; return saved; }, readHistory: async () => journal(saved, prepared.change) });
    expect(reads).toBe(2); expect(review.matchingValueRevisions).toEqual([saved.revision]); expect(distillationUnconfirmed(prepared.draft)).toBe(true);
    expect(distillationOutcomeMatches(prepared.draft, saved, review)).toBe(true); const next = releaseDistillationOutcome(prepared.draft, saved, review);
    expect(next.raw).toEqual(prepared.draft.raw); expect(next.pending).toBeNull(); expect(next.intent).toBe(true);
  });
  it("rejects incomplete history, another proposal scope and changes during inspection", async () => {
    const { project, prepared } = await armed(), saved = await applyChange(project, prepared.change);
    await expect(inspectDistillationOutcome(prepared.draft, saved, { readProject: async () => saved, readHistory: async () => [] })).rejects.toThrow("incomplete");
    let reads = 0; await expect(inspectDistillationOutcome(prepared.draft, saved, { readProject: async () => ++reads === 1 ? saved : { ...saved, revision: saved.revision + 1 }, readHistory: async () => journal(saved, prepared.change) })).rejects.toThrow("changed");
    const review = await inspectDistillationOutcome(prepared.draft, saved, { readProject: async () => saved, readHistory: async () => journal(saved, prepared.change) });
    expect(distillationOutcomeMatches({ ...prepared.draft, scope: distillationScope(saved, { kind: "review", proposalId: writer }) }, saved, review)).toBe(false);
  });
  it("separate reviewed intent verifies both copies while retaining the earlier exact UNKNOWN", async () => {
    const { project, prepared } = await armed(), saved = await applyChange(project, prepared.change), store = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
    writeDistillationCopy(store, pointers, prepared.draft, owner); const original = owner.expectedRaw;
    const review = await inspectDistillationOutcome(prepared.draft, saved, { readProject: async () => saved, readHistory: async () => journal(saved, prepared.change) }), next = releaseDistillationOutcome(prepared.draft, saved, review);
    const newer = retainReviewedDistillationIntent(store, pointers, prepared.draft, next, owner, fork);
    expect(store.getItem(distillationCopyKey(prepared.draft.scope, writer))).toBe(original); expect(decodeDistillationCopy(newer.expectedRaw!, next.scope, fork).draft.pending).toBeNull();
    expect(pointers.getItem(distillationPointerKey(next.scope))).toBe(fork);
  });
});
