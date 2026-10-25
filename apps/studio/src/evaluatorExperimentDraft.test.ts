import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import { EVALUATOR_SCHEMA, type CreativeEvaluatorBundle, type EvaluatorChange } from "./creativeEvaluator";
import {
  acknowledgeExperimentSubmission, adoptExperimentFile, compareExperiment, editExperimentField, experimentComparisonCurrent,
  experimentCopies, experimentCopyKey, experimentPointerKey, experimentRaw, experimentReviewCurrent,
  exportExperimentDraft, forkExperimentWriter, guardExperimentSubmission, importExperimentDraft, inspectExperimentOutcome,
  openExperimentSession, originalExperimentRecoveryCopy, persistExperimentSession, prepareExperimentSubmission,
  retainExperimentFailure, retryExperimentCustody,
  type ExperimentFieldName,
} from "./evaluatorExperimentDraft";
import type { Project, ProjectEvent } from "./types";

const id = (value: number): string => `018f0000-0000-7000-8000-${String(value).padStart(12, "0")}`;
class Memory {
  data = new Map<string, string>(); onGet: ((key: string) => void) | null = null; silent = false;
  get length() { return this.data.size; }
  key(index: number) { return [...this.data.keys()][index] ?? null; }
  getItem(key: string) { this.onGet?.(key); return this.data.get(key) ?? null; }
  setItem(key: string, raw: string) { if (!this.silent) this.data.set(key, raw); }
}
function baseline(version = 1): CreativeEvaluatorBundle {
  return { schema: EVALUATOR_SCHEMA, id: id(801), version, label: "Original experimental baseline",
    purpose: "Observe original source without claiming creative approval", author_attestation: "I authored this original data-only recipe",
    playbook: ["Inspect the editable source objects", "Ask an independent human reviewer"], criteria: [
      { kind: "minimum_nodes", id: id(802), minimum: 2 },
      { kind: "human_question", id: id(803), question: "Does the scene communicate the point clearly?" },
    ] };
}
const candidate = () => ({ ...baseline(2), label: "Original candidate with changed editorial guidance" });
function setup() {
  const project = structuredClone(fixtureProject); project.schema_version = 2;
  project.locks = []; project.reviews = []; project.branch_workspaces = [];
  project.production_design = emptyProductionDesign(); project.production_design.evaluator_bundles = [baseline()];
  const storage = new Memory(), pointers = new Memory();
  const session = openExperimentSession(project, baseline(), project.scenes[0].id, storage, pointers);
  return { project, storage, pointers, session };
}
function input(value: ReturnType<typeof setup>, key: ExperimentFieldName, raw: string) {
  editExperimentField(value.session, value.project, baseline(), key, raw);
}
async function ready() {
  const value = setup(); input(value, "candidateText", JSON.stringify(candidate(), null, 2));
  input(value, "selectedScenes", JSON.stringify(value.project.scenes.slice(0, 2).map(scene => scene.id)));
  input(value, "reviewerReason", "I reviewed the original source differences and retained human abstention.");
  input(value, "ackRights", "true"); input(value, "ackLimit", "true");
  persistExperimentSession(value.session, value.storage, value.pointers);
  const comparison = await compareExperiment(value.session, value.project); return { ...value, comparison };
}
async function arm() {
  const value = await ready(), prepared = await prepareExperimentSubmission(value.session, value.project, value.comparison, null, id(850));
  forkExperimentWriter(value.session); value.session.draft = prepared.draft; value.session.epoch++;
  persistExperimentSession(value.session, value.storage, value.pointers);
  const armedKey = experimentCopyKey(value.session.draft.scope, value.session.custody.writer), armedRaw = value.storage.getItem(armedKey)!;
  guardExperimentSubmission(value.session, value.project, prepared.id, prepared.change, value.storage, value.pointers);
  forkExperimentWriter(value.session); persistExperimentSession(value.session, value.storage, value.pointers);
  return { ...value, prepared, change: prepared.change, armedKey, armedRaw };
}
function receipt(value: Awaited<ReturnType<typeof arm>>): Project {
  const result = structuredClone(value.project); result.revision++;
  if (value.change.type !== "register_creative_evaluator") throw new Error("Expected registration");
  result.production_design!.evaluator_bundles!.push(structuredClone(value.change.bundle)); return result;
}
function pointed(value: ReturnType<typeof setup>) {
  const key = experimentCopyKey(value.session.draft.scope, value.session.custody.writer);
  return { key, raw: value.storage.getItem(key)! };
}
const event = (project: Project, change: EvaluatorChange): ProjectEvent => ({ revision: project.revision,
  change: structuredClone(change), created_at: "2026-10-24T00:00:00Z" });

// Custody/receipt model tests. Existing E13 interpreter tests remain unchanged;
// none of these cases is a service, native renderer or human acceptance claim.
describe("Experiment raw input and explicit comparison", () => {
  it("retains invalid candidate JSON, rationale whitespace, scene choices and declarations through reopen", () => {
    const value = setup(); input(value, "candidateText", " {broken candidate\n "); input(value, "reviewerReason", "  retained\nreason  ");
    input(value, "selectedScenes", JSON.stringify([value.project.scenes[1].id])); input(value, "ackRights", "true");
    persistExperimentSession(value.session, value.storage, value.pointers);
    const next = openExperimentSession(value.project, baseline(), value.project.scenes[0].id, value.storage, value.pointers);
    expect(next.draft.fields).toEqual(value.session.draft.fields); expect(next.liveAttempt).toBeNull();
    expect(experimentRaw(next.draft, "candidateText")).toBe(" {broken candidate\n ");
  });
  it.each(["id", "generation", "active_branch"] as const)("isolates experimental input by project %s", key => {
    const value = setup(); input(value, "candidateText", "Only this owner"); persistExperimentSession(value.session, value.storage, value.pointers);
    const foreign = structuredClone(value.project); foreign[key] = id(899);
    expect(openExperimentSession(foreign, baseline(), foreign.scenes[0].id, value.storage, value.pointers).draft.fields).toEqual({});
    expect(() => editExperimentField(value.session, foreign, baseline(), "candidateText", "Other")).toThrow(/owner/);
  });
  it("isolates immutable baseline versions and preserves the earlier candidate when returning", () => {
    const value = setup(); input(value, "candidateText", "Source version one input"); persistExperimentSession(value.session, value.storage, value.pointers);
    const newer = structuredClone(value.project); newer.production_design!.evaluator_bundles!.push(baseline(2));
    const other = openExperimentSession(newer, baseline(2), newer.scenes[0].id, value.storage, value.pointers);
    expect(other.draft.scope).not.toBe(value.session.draft.scope); expect(other.draft.fields).toEqual({});
    expect(experimentRaw(openExperimentSession(value.project, baseline(), value.project.scenes[0].id, value.storage, value.pointers).draft, "candidateText"))
      .toBe("Source version one input");
  });
  it("compares through the existing data-only interpreter without changing Project or granting admission", async () => {
    const value = await ready(), original = structuredClone(value.project);
    expect(value.comparison.result.status).toBe("TECHNICAL_OBSERVATION_ONLY");
    expect(value.comparison.result.humanCreativeApproval).toBe("not_assessed");
    expect(value.comparison.result.executableCodeRun).toBe(false); expect(value.project).toEqual(original);
    expect(value.session.draft.pending).toBeNull(); expect(value.session.liveAttempt).toBeNull();
  });
  it("candidate changes back to the same bytes invalidate prior comparison while rationale edits preserve its technical meaning", async () => {
    const value = await ready(), old = experimentRaw(value.session.draft, "candidateText");
    input(value, "reviewerReason", "A later explicit reason for inspecting these same technical differences.");
    expect(experimentComparisonCurrent(value.session, value.project, value.comparison)).toBe(true);
    input(value, "candidateText", "Another candidate"); input(value, "candidateText", old);
    expect(experimentComparisonCurrent(value.session, value.project, value.comparison)).toBe(false);
    await expect(prepareExperimentSubmission(value.session, value.project, value.comparison)).rejects.toThrow(/Compare the exact/);
  });
  it("a later blank authored during async revalidation cancels dispatch and stays retained", async () => {
    const value = await ready(); const preparation = prepareExperimentSubmission(value.session, value.project, value.comparison);
    input(value, "reviewerReason", "");
    await expect(preparation).rejects.toThrow(/input changed during revalidation/);
    expect(experimentRaw(value.session.draft, "reviewerReason")).toBe(""); expect(value.session.draft.pending).toBeNull(); expect(value.session.liveAttempt).toBeNull();
  });
  it.each(["ackRights", "ackLimit", "reviewerReason"] as const)("cannot prepare without the current explicit %s", async key => {
    const value = await ready(); input(value, key, key === "reviewerReason" ? "too short" : "false");
    persistExperimentSession(value.session, value.storage, value.pointers);
    await expect(prepareExperimentSubmission(value.session, value.project, value.comparison)).rejects.toThrow(/Confirm source rights/);
    expect(value.session.draft.pending).toBeNull();
  });
  it("a forged comparison or changed scene observation is revalidated before any pending request exists", async () => {
    const value = await ready(), forged = structuredClone(value.comparison); forged.result.comparisons[0].candidatePass = 999;
    await expect(prepareExperimentSubmission(value.session, value.project, forged)).rejects.toThrow(/observations changed/);
    expect(value.session.draft.pending).toBeNull(); expect(value.session.liveAttempt).toBeNull();
  });
  it.each(["deliverables", "audio", "production_design"] as const)("same-stamp %s source changes invalidate comparison instead of changing authority silently", async key => {
    const value = await ready(), different = structuredClone(value.project);
    if (key === "deliverables") different.deliverables[0].width++;
    else if (key === "audio") different.audio.mix.voice_gain_db++;
    else different.production_design!.plan = { foreign: true } as never;
    expect(experimentComparisonCurrent(value.session, different, value.comparison)).toBe(false);
    await expect(prepareExperimentSubmission(value.session, different, value.comparison)).rejects.toThrow(/Compare the exact/);
  });
});

describe("Experiment original registration receipt", () => {
  it("acknowledges only the exact appended immutable version and keeps all visible reviewed input", async () => {
    const value = await arm(), fields = structuredClone(value.session.draft.fields), result = receipt(value);
    await acknowledgeExperimentSubmission(value.session, value.prepared.id, value.change, result);
    expect(value.session.draft.pending).toBeNull(); expect(value.session.draft.fields).toEqual(fields);
    expect(result.production_design!.evaluator_bundles).toEqual([baseline(), candidate()]);
    expect(result.production_design!.evaluation_reports).toEqual(value.project.production_design!.evaluation_reports);
  });
  it("later blank candidate, reason, declarations and scene choices survive the old original ACK", async () => {
    const value = await arm(), result = receipt(value);
    input(value, "candidateText", "A later draft"); input(value, "candidateText", ""); input(value, "reviewerReason", "");
    input(value, "ackRights", "false"); input(value, "selectedScenes", JSON.stringify([value.project.scenes[1].id]));
    const later = structuredClone(value.session.draft.fields);
    const acknowledging = acknowledgeExperimentSubmission(value.session, value.prepared.id, value.change, result);
    input(value, "ackLimit", "false"); await acknowledging;
    expect(value.session.draft.fields.candidateText).toEqual(later.candidateText); expect(value.session.draft.fields.reviewerReason).toEqual(later.reviewerReason);
    expect(experimentRaw(value.session.draft, "ackLimit")).toBe("false"); expect(value.session.draft.pending).toBeNull();
    expect(value.storage.getItem(value.armedKey)).toBe(value.armedRaw);
  });
  it.each(["void", "revision_only", "foreign", "historic_report"])("rejects %s callback without erasing input or UNKNOWN", async kind => {
    const value = await arm(); let result = receipt(value);
    if (kind === "void") result = undefined as unknown as Project;
    if (kind === "revision_only") result = { ...structuredClone(value.project), revision: value.project.revision + 1 };
    if (kind === "foreign") result.generation = id(899);
    if (kind === "historic_report") result.production_design!.evaluation_reports!.push({ forged: true } as never);
    const original = structuredClone(value.session.draft);
    await expect(acknowledgeExperimentSubmission(value.session, value.prepared.id, value.change, result)).rejects.toThrow();
    expect(value.session.draft).toEqual(original);
  });
  it("a recovered matching Project cannot acknowledge or replay the original registration", async () => {
    const value = await arm(), result = receipt(value);
    const recovered = openExperimentSession(result, baseline(), result.scenes[0].id, value.storage, value.pointers);
    expect(recovered.liveAttempt).toBeNull();
    await expect(acknowledgeExperimentSubmission(recovered, value.prepared.id, value.change, result)).rejects.toThrow(/original experiment callback/);
    expect(() => guardExperimentSubmission(recovered, value.project, value.prepared.id, value.change, value.storage, value.pointers)).toThrow(/dispatch once/);
    expect(recovered.draft.pending?.id).toBe(value.prepared.id);
  });
  it("rejected or lost delivery retains candidate, rationale and its exact original command", async () => {
    const value = await arm(), fields = structuredClone(value.session.draft.fields);
    value.session.draft = retainExperimentFailure(value.session.draft, value.prepared.id, "Original delivery failed");
    persistExperimentSession(value.session, value.storage, value.pointers);
    const restored = openExperimentSession(value.project, baseline(), value.project.scenes[0].id, value.storage, value.pointers);
    expect(restored.draft.fields).toEqual(fields); expect(restored.draft.pending?.changeJson).toBe(JSON.stringify(value.change));
    expect(restored.draft.pending?.outcome).toBe("UNKNOWN"); expect(restored.liveAttempt).toBeNull();
  });
  it("failed ACK retention leaves the immutable original UNKNOWN and all later input available", async () => {
    const value = await arm(); input(value, "candidateText", "Later input after dispatch");
    await acknowledgeExperimentSubmission(value.session, value.prepared.id, value.change, receipt(value));
    forkExperimentWriter(value.session); value.storage.silent = true;
    expect(() => persistExperimentSession(value.session, value.storage, value.pointers)).toThrow(/write\/readback/);
    expect(value.storage.getItem(value.armedKey)).toBe(value.armedRaw);
    expect(experimentRaw(value.session.draft, "candidateText")).toBe("Later input after dispatch");
    expect(value.session.custody.attempt).not.toBeNull();
  });
});

describe("Experiment exact recovery custody", () => {
  it.each(["pointer", "copy"])("failed first %s read recovers original UNKNOWN and preserves a later blank rationale", async failed => {
    const value = await arm(), original = pointed(value), pointer = experimentPointerKey(value.session.draft.scope);
    const store = failed === "pointer" ? value.pointers : value.storage; const key = failed === "pointer" ? pointer : original.key;
    let first = true; store.onGet = name => { if (name === key && first) { first = false; throw new Error("Controlled first read failure"); } };
    const opened = openExperimentSession(value.project, baseline(), value.project.scenes[0].id, value.storage, value.pointers);
    expect(opened.failedSource).not.toBeNull(); editExperimentField(opened, value.project, baseline(), "reviewerReason", "");
    retryExperimentCustody(opened, value.storage, value.pointers);
    expect(opened.draft.pending).toEqual(value.session.draft.pending); expect(experimentRaw(opened.draft, "reviewerReason")).toBe("");
    expect(value.storage.getItem(original.key)).toBe(original.raw); expect(opened.liveAttempt).toBeNull();
  });
  it.each(["{original corrupt", "null", "{}"]) ("observed corrupt %j bytes cannot be replaced by a valid same-writer copy", async corrupt => {
    const value = await arm(), original = pointed(value); value.storage.data.set(original.key, corrupt);
    const opened = openExperimentSession(value.project, baseline(), value.project.scenes[0].id, value.storage, value.pointers);
    editExperimentField(opened, value.project, baseline(), "candidateText", "Keep later raw input");
    const replacement = JSON.parse(original.raw); replacement.draft.pending = null;
    value.storage.data.set(original.key, JSON.stringify(replacement));
    expect(() => retryExperimentCustody(opened, value.storage, value.pointers)).toThrow(/original experiment recovery bytes changed/);
    expect(experimentRaw(opened.draft, "candidateText")).toBe("Keep later raw input");
    expect(originalExperimentRecoveryCopy(opened.draft.scope, opened.failedSource)?.raw).toBe(corrupt);
  });
  it("anchors an observed null during listing after the first read throws, before a same-writer replacement appears", async () => {
    const value = await arm(), original = pointed(value); let first = true;
    value.storage.onGet = key => { if (key === original.key && first) { first = false; throw new Error("Initial source unavailable"); } };
    const opened = openExperimentSession(value.project, baseline(), value.project.scenes[0].id, value.storage, value.pointers);
    value.storage.data.delete(original.key);
    const other = id(898), prefix = experimentCopyKey(opened.draft.scope, other);
    value.storage.key = index => index === 0 ? original.key : prefix;
    value.storage.data.set(prefix, "{other unreadable bytes");
    experimentCopies(value.storage, opened.draft.scope, opened.failedSource);
    expect(opened.failedSource?.rawObserved).toBe(true); expect(opened.failedSource?.raw).toBeNull();
    value.storage.data.set(original.key, original.raw);
    expect(() => retryExperimentCustody(opened, value.storage, value.pointers)).toThrow(/original experiment recovery bytes changed/);
  });
  it.each(["copy", "pointer"])("silent %s write failure blocks prepare until exact readback succeeds", async lost => {
    const value = await ready(); if (lost === "copy") value.storage.silent = true; else value.pointers.silent = true;
    input(value, "reviewerReason", "A later review declaration needs an exact recoverable copy.");
    if (lost === "pointer") forkExperimentWriter(value.session);
    expect(() => persistExperimentSession(value.session, value.storage, value.pointers)).toThrow(/readback/);
    await expect(prepareExperimentSubmission(value.session, value.project, value.comparison)).rejects.toThrow(/Confirm/);
    value.storage.silent = false; value.pointers.silent = false;
    retryExperimentCustody(value.session, value.storage, value.pointers); expect(value.session.custody.attempt).toBeNull();
    expect(experimentRaw(value.session.draft, "reviewerReason")).toBe("A later review declaration needs an exact recoverable copy.");
  });
  it("duplicate tabs fork writers without replacing an armed original or gaining dispatch authority", async () => {
    const value = await arm(), original = pointed(value);
    const peerPointers = new Memory(); peerPointers.data = new Map(value.pointers.data);
    const peer = openExperimentSession(value.project, baseline(), value.project.scenes[0].id, value.storage, peerPointers);
    editExperimentField(peer, value.project, baseline(), "reviewerReason", "Separate peer review input");
    persistExperimentSession(peer, value.storage, peerPointers);
    expect(peer.custody.writer).not.toBe(value.session.custody.writer); expect(peer.liveAttempt).toBeNull();
    expect(value.storage.getItem(original.key)).toBe(original.raw); expect(value.storage.getItem(value.armedKey)).toBe(value.armedRaw);
  });
  it("an exported UNKNOWN has no dispatch permission and cannot replace another pending registration", async () => {
    const value = await arm(), imported = importExperimentDraft(exportExperimentDraft(value.session.draft), value.session.draft.scope);
    expect(imported.pending).toEqual(value.session.draft.pending);
    expect(() => adoptExperimentFile(value.session, imported, value.storage, value.pointers)).toThrow(/UNKNOWN/);
    const next = setup(); adoptExperimentFile(next.session, imported, next.storage, next.pointers);
    expect(next.session.liveAttempt).toBeNull(); expect(next.session.draft.pending?.outcome).toBe("UNKNOWN");
  });
});

describe("Experiment saved-outcome observation is not ACK or replay", () => {
  it("two complete matching reads and contiguous history authorize review without consuming UNKNOWN", async () => {
    const value = await arm(), result = receipt(value); let reads = 0;
    const review = await inspectExperimentOutcome(value.session, result, {
      readProject: async () => { reads++; return structuredClone(result); }, readHistory: async () => [event(result, value.change)],
    });
    expect(reads).toBe(2); expect(review.matchingRevisions).toEqual([result.revision]);
    expect(experimentReviewCurrent(value.session, result, review)).toBe(true); expect(value.session.draft.pending?.id).toBe(value.prepared.id);
    expect(() => guardExperimentSubmission(value.session, value.project, value.prepared.id, value.change, value.storage, value.pointers)).toThrow(/dispatch once/);
  });
  it.each(["deliverables", "audio", "production_design"] as const)("same-stamp %s differences fail first read, final read and review reuse", async field => {
    const value = await arm(), result = receipt(value), changed = structuredClone(result);
    if (field === "deliverables") changed.deliverables[0].width++;
    else if (field === "audio") changed.audio.mix.voice_gain_db++;
    else delete changed.production_design;
    await expect(inspectExperimentOutcome(value.session, result, { readProject: async () => changed, readHistory: async () => [] })).rejects.toThrow(/Reload the current saved/);
    let reads = 0;
    await expect(inspectExperimentOutcome(value.session, result, { readProject: async () => ++reads === 1 ? result : changed,
      readHistory: async () => [event(result, value.change)] })).rejects.toThrow(/saved project changed/);
    const review = await inspectExperimentOutcome(value.session, result, { readProject: async () => result, readHistory: async () => [event(result, value.change)] });
    expect(experimentReviewCurrent(value.session, changed, review)).toBe(false); expect(value.session.draft.pending?.outcome).toBe("UNKNOWN");
  });
  it("a saved-history gap or a later edit during review cannot unlock another registration", async () => {
    const value = await arm(), result = receipt(value);
    await expect(inspectExperimentOutcome(value.session, result, { readProject: async () => result, readHistory: async () => [] })).rejects.toThrow(/incomplete/);
    await expect(inspectExperimentOutcome(value.session, result, { readProject: async () => result, readHistory: async () => {
      input(value, "reviewerReason", "Later reason while the history was being read"); return [event(result, value.change)];
    } })).rejects.toThrow(/buffer or saved project changed/);
    expect(experimentRaw(value.session.draft, "reviewerReason")).toBe("Later reason while the history was being read");
  });
  it("an explicitly reviewed separate attempt preserves the original UNKNOWN and never reuses its request identity", async () => {
    const value = await arm();
    value.session.draft = retainExperimentFailure(value.session.draft, value.prepared.id, "Original response rejected or lost");
    persistExperimentSession(value.session, value.storage, value.pointers); const old = pointed(value);
    const review = await inspectExperimentOutcome(value.session, value.project, { readProject: async () => value.project, readHistory: async () => [] });
    const next = await prepareExperimentSubmission(value.session, value.project, value.comparison, review, id(851));
    value.session.draft = next.draft;
    expect(() => persistExperimentSession(value.session, value.storage, value.pointers)).toThrow(/Fork the experiment writer/);
    expect(value.storage.getItem(old.key)).toBe(old.raw);
    forkExperimentWriter(value.session); persistExperimentSession(value.session, value.storage, value.pointers);
    guardExperimentSubmission(value.session, value.project, next.id, next.change, value.storage, value.pointers);
    expect(next.id).not.toBe(value.prepared.id); expect(value.storage.getItem(old.key)).toBe(old.raw);
    expect(() => guardExperimentSubmission(value.session, value.project, next.id, next.change, value.storage, value.pointers)).toThrow(/dispatch once/);
  });
});
