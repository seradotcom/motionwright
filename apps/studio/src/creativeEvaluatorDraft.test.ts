import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import { EVALUATOR_SCHEMA, REPORT_SCHEMA, evaluatorDigest, observeCriterion } from "./creativeEvaluator";
import type { CreativeEvaluatorBundle, EvaluatorChange } from "./creativeEvaluator";
import { EVALUATOR_DEFAULTS, acknowledgeEvaluatorSubmission, adoptEvaluatorFile, decodeEvaluatorCopy, editEvaluatorField,
  evaluatorCopies, evaluatorCopyKey, evaluatorPointerKey, evaluatorRaw, evaluatorReviewCurrent, evaluatorScope,
  exportEvaluatorDraft, forkEvaluatorWriter, guardEvaluatorSubmission, importEvaluatorDraft, inspectEvaluatorOutcome,
  openEvaluatorSession, originalEvaluatorRecoveryCopy, persistEvaluatorSession, prepareEvaluatorSubmission,
  restoreEvaluatorCopy, retainEvaluatorFailure, retryEvaluatorCustody } from "./creativeEvaluatorDraft";
import type { EvaluatorFieldName, EvaluatorOperation } from "./creativeEvaluatorDraft";
import type { Project, ProjectEvent } from "./types";

const id = (n: number): string => `018f0000-0000-7000-8000-${String(n).padStart(12, "0")}`;
class Memory {
  data = new Map<string, string>(); onGet: ((key: string) => void) | null = null; silent = false;
  get length() { return this.data.size; }
  key(index: number) { return [...this.data.keys()][index] ?? null; }
  getItem(key: string) { this.onGet?.(key); return this.data.get(key) ?? null; }
  setItem(key: string, value: string) { if (!this.silent) this.data.set(key, value); }
}
function bundle(version = 1): CreativeEvaluatorBundle {
  return { schema: EVALUATOR_SCHEMA, id: id(400), version, label: "Original evaluator",
    purpose: "Observe editable source and retain human judgment", author_attestation: "I authored this data-only recipe",
    playbook: ["Inspect the original editable objects", "Request independent human review"], criteria: [
      { kind: "minimum_nodes", id: id(401), minimum: 2 },
      { kind: "text_contains", id: id(402), phrase: "Original claim" },
      { kind: "human_question", id: id(403), question: "Is the intended idea clear?" },
    ] };
}
function setup() {
  const project = structuredClone(fixtureProject); project.schema_version = 2;
  project.locks = []; project.reviews = []; project.branch_workspaces = [];
  project.production_design = emptyProductionDesign();
  project.scenes = project.scenes.slice(0, 1).map(scene => ({ ...scene, renderer: "motion-canvas" as const, status: "draft" as const, beats: [],
    nodes: [0, 1].map(index => ({ ...structuredClone(fixtureProject.scenes[0].nodes[0]), id: id(420 + index),
      kind: "text", text: index === 0 ? "Original claim" : "Source detail", coordinate_space: "project_pixels" as const,
      x: 260, y: 210 + index * 170, width: 550, height: 90 })) }));
  const storage = new Memory(), pointers = new Memory(), session = openEvaluatorSession(project, storage, pointers);
  return { project, storage, pointers, session };
}
function input(value: ReturnType<typeof setup>, key: EvaluatorFieldName, raw: string): void {
  editEvaluatorField(value.session, value.project, key, raw);
}
function arm(op: EvaluatorOperation = { kind: "import" }) {
  const value = setup(), { session, project, storage, pointers } = value;
  if (op.kind === "import") { input(value, "importText", JSON.stringify(bundle(), null, 2)); input(value, "confirmImport", "true"); }
  if (op.kind === "author") input(value, "author", "  I authored the original checklist  ");
  if (op.kind === "evaluate") {
    project.production_design!.evaluator_bundles = [bundle()];
    op = { kind: "evaluate", sceneId: project.scenes[0].id };
    input(value, "selected", bundle().id + ":1");
  }
  persistEvaluatorSession(session, storage, pointers);
  const prepared = prepareEvaluatorSubmission(session, project, op, null, id(450));
  forkEvaluatorWriter(session); session.draft = prepared.draft; session.epoch++;
  persistEvaluatorSession(session, storage, pointers);
  const armedKey = evaluatorCopyKey(session.draft.scope, session.custody.writer), armedRaw = storage.getItem(armedKey)!;
  guardEvaluatorSubmission(session, project, prepared.id, prepared.change, storage, pointers);
  forkEvaluatorWriter(session); persistEvaluatorSession(session, storage, pointers);
  return { ...value, prepared, change: prepared.change, armedKey, armedRaw };
}
function registerReceipt(value: ReturnType<typeof arm>): Project {
  const receipt = structuredClone(value.project); receipt.revision++;
  if (value.change.type !== "register_creative_evaluator") throw new Error("Test requires a register operation");
  receipt.production_design ??= emptyProductionDesign();
  receipt.production_design.evaluator_bundles = [...(receipt.production_design.evaluator_bundles ?? []), structuredClone(value.change.bundle)];
  return receipt;
}
async function evaluationReceipt(value: ReturnType<typeof arm>): Promise<Project> {
  const receipt = structuredClone(value.project); receipt.revision++;
  if (value.change.type !== "evaluate_creative_scene") throw new Error("Test requires an evaluate operation");
  const change = value.change, evaluator = value.project.production_design!.evaluator_bundles!.find(entry => entry.id === change.evaluator_id)!;
  const scene = value.project.scenes.find(scene => scene.id === change.scene_id)!;
  const observations = evaluator.criteria.map(criterion => observeCriterion(scene, criterion));
  const conclusion = observations.some(observation => observation.outcome === "fail") ? "fail"
    : observations.some(observation => observation.outcome === "not_assessed") ? "needs_human_review" : "pass_technical_only";
  receipt.production_design!.evaluation_reports = [...(receipt.production_design!.evaluation_reports ?? []), {
    schema: REPORT_SCHEMA, id: id(470), evaluator_id: evaluator.id, evaluator_version: evaluator.version,
    evaluator_sha256: await evaluatorDigest(evaluator), scene_id: scene.id, source_generation: value.project.generation,
    source_revision: value.project.revision, observations, conclusion, creative_approval: "not_assessed",
  }];
  return receipt;
}
const event = (project: Project, change: EvaluatorChange): ProjectEvent => ({ revision: project.revision,
  change: JSON.parse(JSON.stringify(change)), created_at: "2026-10-24T00:00:00Z" });
function pointed(value: ReturnType<typeof setup>) {
  const { session, storage } = value, key = evaluatorCopyKey(session.draft.scope, session.custody.writer);
  return { key, raw: storage.getItem(key)! };
}

// These are custody/receipt model tests. They never imply native execution, human approval or service acceptance.
describe("Evaluator exact raw input and original callback authority", () => {
  it("retains invalid JSON, partial numerics, whitespace, blank author controls and explicit blank selection through reopen", () => {
    const value = setup();
    const cases: [EvaluatorFieldName, string][] = [["importText", " {not valid JSON\n  "], ["minimum", "1e"],
      ["safeMargin", ""], ["author", " \n "], ["steps", "one\n\n two  "], ["selected", ""]];
    for (const [key, raw] of cases) input(value, key, raw);
    persistEvaluatorSession(value.session, value.storage, value.pointers);
    const reopened = openEvaluatorSession(value.project, value.storage, value.pointers);
    for (const [key, raw] of cases) expect(evaluatorRaw(reopened.draft, key, "foreign fallback")).toBe(raw);
    expect(reopened.custody.writer).not.toBe(value.session.custody.writer);
    expect(reopened.liveAttempt).toBeNull();
  });
  it.each(["id", "generation", "active_branch"] as const)("isolates recovery ownership by %s", key => {
    const value = setup(); input(value, "importText", "Only this scope"); persistEvaluatorSession(value.session, value.storage, value.pointers);
    const foreign = structuredClone(value.project); foreign[key] = id(999);
    expect(openEvaluatorSession(foreign, value.storage, value.pointers).draft.fields).toEqual({});
    expect(() => editEvaluatorField(value.session, foreign, "label", "Foreign")).toThrow(/owner/);
  });
  it.each(["", " ", "1e", "NaN", "Infinity", "1.5"])("does not coerce incomplete/noninteger author input %j into a valid number", raw => {
    const value = setup(); input(value, "author", "I authored this recipe"); input(value, "safeMargin", raw);
    persistEvaluatorSession(value.session, value.storage, value.pointers);
    expect(() => prepareEvaluatorSubmission(value.session, value.project, { kind: "author" })).toThrow(/complete integer/);
    expect(evaluatorRaw(value.session.draft, "safeMargin")).toBe(raw); expect(value.session.draft.pending).toBeNull();
  });
  it("accepts complete zero margin without changing its raw spelling or the existing criterion grammar", () => {
    const value = setup(); input(value, "author", "I authored this recipe"); input(value, "safeMargin", " 0 ");
    persistEvaluatorSession(value.session, value.storage, value.pointers);
    const prepared = prepareEvaluatorSubmission(value.session, value.project, { kind: "author" });
    expect(prepared.change.type).toBe("register_creative_evaluator");
    if (prepared.change.type !== "register_creative_evaluator") throw new Error("Wrong command");
    expect(prepared.change.bundle.criteria[1]).toMatchObject({ kind: "project_pixel_safe_area", margin_permille: 0 });
    expect(prepared.draft.pending?.sentFields.safeMargin.raw).toBe(" 0 ");
  });
  it("invalid or executable imported JSON remains downloadable and cannot create pending intent", () => {
    for (const raw of [" {broken\n", JSON.stringify({ ...bundle(), execute_shell: "do not execute" })]) {
      const value = setup(); input(value, "importText", raw); input(value, "confirmImport", "true");
      persistEvaluatorSession(value.session, value.storage, value.pointers);
      expect(() => prepareEvaluatorSubmission(value.session, value.project, { kind: "import" })).toThrow();
      expect(importEvaluatorDraft(exportEvaluatorDraft(value.session.draft), value.session.draft.scope).fields.importText.raw).toBe(raw);
      expect(value.session.draft.pending).toBeNull();
    }
  });
  it("a confirmation is required and historical versions cannot be re-registered", () => {
    const value = setup(); input(value, "importText", JSON.stringify(bundle())); persistEvaluatorSession(value.session, value.storage, value.pointers);
    expect(() => prepareEvaluatorSubmission(value.session, value.project, { kind: "import" })).toThrow(/source rights/);
    input(value, "confirmImport", "true"); value.project.production_design!.evaluator_bundles = [bundle()];
    persistEvaluatorSession(value.session, value.storage, value.pointers);
    expect(() => prepareEvaluatorSubmission(value.session, value.project, { kind: "import" })).toThrow(/source history/);
    expect(value.project.production_design!.evaluator_bundles).toEqual([bundle()]);
  });
  it("generates the author identity once and retains its exact command across persistence/reopen without a replay permit", () => {
    const value = arm({ kind: "author" }), original = JSON.stringify(value.change);
    const reopened = openEvaluatorSession(value.project, value.storage, value.pointers);
    expect(reopened.draft.pending?.changeJson).toBe(original);
    expect(reopened.draft.pending?.sourceProjectJson).toBe(JSON.stringify(value.project));
    expect(reopened.liveAttempt).toBeNull();
    expect(() => guardEvaluatorSubmission(reopened, value.project, value.prepared.id, value.change, value.storage, value.pointers)).toThrow(/never replayed/);
    expect(() => guardEvaluatorSubmission(value.session, value.project, value.prepared.id, value.change, value.storage, value.pointers)).toThrow(/once/);
  });
  it("a void callback result, unrelated identity and mutated command cannot acknowledge registration", async () => {
    const value = arm(), receipt = registerReceipt(value), before = structuredClone(value.session.draft);
    await expect(acknowledgeEvaluatorSubmission(value.session, value.prepared.id, value.change, undefined as unknown as Project)).rejects.toThrow(/complete saved/);
    await expect(acknowledgeEvaluatorSubmission(value.session, id(999), value.change, receipt)).rejects.toThrow(/unrelated/);
    const foreign = structuredClone(value.change); if (foreign.type === "register_creative_evaluator") foreign.bundle.label = "Other";
    await expect(acknowledgeEvaluatorSubmission(value.session, value.prepared.id, foreign, receipt)).rejects.toThrow(/unrelated/);
    expect(value.session.draft).toEqual(before);
  });
  it.each(["id", "generation", "active_branch", "revision"] as const)("a register receipt with foreign/advanced %s keeps UNKNOWN and JSON", async key => {
    const value = arm(), receipt = registerReceipt(value), before = structuredClone(value.session.draft);
    if (key === "revision") receipt.revision++; else receipt[key] = id(999);
    await expect(acknowledgeEvaluatorSubmission(value.session, value.prepared.id, value.change, receipt)).rejects.toThrow(/authority/);
    expect(value.session.draft).toEqual(before); expect(evaluatorRaw(value.session.draft, "confirmImport")).toBe("true");
  });
  it("rejection or lost response preserves exact JSON/confirmation/UNKNOWN with no success-shaped local cleanup", () => {
    const value = arm(), before = structuredClone(value.session.draft.fields);
    value.session.draft = retainEvaluatorFailure(value.session.draft, value.prepared.id, "CAS or response unavailable");
    expect(value.session.draft.fields).toEqual(before); expect(value.session.draft.pending?.outcome).toBe("UNKNOWN");
    persistEvaluatorSession(value.session, value.storage, value.pointers);
    expect(openEvaluatorSession(value.project, value.storage, value.pointers).draft.pending?.diagnostic).toBe("CAS or response unavailable");
    expect(() => prepareEvaluatorSubmission(value.session, value.project, { kind: "import" })).toThrow(/UNKNOWN/);
  });
  it("confirmed import clears only its sent text/confirmation and selects the exact immutable bundle", async () => {
    const value = arm(), receipt = registerReceipt(value);
    await acknowledgeEvaluatorSubmission(value.session, value.prepared.id, value.change, receipt);
    expect(evaluatorRaw(value.session.draft, "importText")).toBe(""); expect(evaluatorRaw(value.session.draft, "confirmImport")).toBe("false");
    expect(evaluatorRaw(value.session.draft, "selected")).toBe(bundle().id + ":1"); expect(value.session.draft.pending).toBeNull();
    expect(value.storage.getItem(value.armedKey)).toBe(value.armedRaw);
  });
  it("later import text, checkbox changes and explicit selection blank survive original ACK by sequence even when returning to sent values", async () => {
    const value = arm(), receipt = registerReceipt(value), original = evaluatorRaw(value.session.draft, "importText");
    input(value, "importText", "Later draft"); input(value, "importText", original);
    input(value, "confirmImport", "false"); input(value, "confirmImport", "true"); input(value, "selected", "");
    await acknowledgeEvaluatorSubmission(value.session, value.prepared.id, value.change, receipt);
    expect(evaluatorRaw(value.session.draft, "importText")).toBe(original); expect(evaluatorRaw(value.session.draft, "confirmImport")).toBe("true");
    expect(evaluatorRaw(value.session.draft, "selected", bundle().id + ":1")).toBe(""); expect(value.session.draft.pending).toBeNull();
  });
  it("a later blank import and independent author editing survive the original ACK", async () => {
    const value = arm(), receipt = registerReceipt(value);
    input(value, "importText", ""); input(value, "purpose", " Later author purpose ");
    await acknowledgeEvaluatorSubmission(value.session, value.prepared.id, value.change, receipt);
    expect(value.session.draft.fields.importText.raw).toBe(""); expect(value.session.draft.fields.purpose.raw).toBe(" Later author purpose ");
  });
  it("author registration preserves its visible original raw controls after success", async () => {
    const value = arm({ kind: "author" }), receipt = registerReceipt(value), raw = value.session.draft.fields.author.raw;
    await acknowledgeEvaluatorSubmission(value.session, value.prepared.id, value.change, receipt);
    expect(value.session.draft.fields.author.raw).toBe(raw); expect(raw.startsWith("  ")).toBe(true);
    expect(value.session.draft.pending).toBeNull();
  });
  it("a recovered matching Project is not an original callback even when bundle/revision agree", async () => {
    const value = arm(), receipt = registerReceipt(value), reopened = openEvaluatorSession(receipt, value.storage, value.pointers);
    await expect(acknowledgeEvaluatorSubmission(reopened, value.prepared.id, value.change, receipt)).rejects.toThrow(/observed snapshot/);
    expect(reopened.draft.pending?.id).toBe(value.prepared.id);
  });
  it("registration refuses a different bundle or altered historic reports without consuming the author buffer", async () => {
    for (const mutate of [(project: Project) => { project.production_design!.evaluator_bundles![0].label = "Foreign wording"; },
      (project: Project) => { project.production_design!.evaluation_reports!.push({} as never); }]) {
      const value = arm(), receipt = registerReceipt(value); mutate(receipt);
      await expect(acknowledgeEvaluatorSubmission(value.session, value.prepared.id, value.change, receipt)).rejects.toThrow(/exact immutable/);
      expect(value.session.draft.pending?.id).toBe(value.prepared.id); expect(evaluatorRaw(value.session.draft, "importText")).not.toBe("");
    }
  });
  it("accepts the existing minimal registration fixture without fabricating optional production fields or branch metadata", async () => {
    const value = setup(); delete value.project.production_design; value.project.schema_version = 1;
    input(value, "importText", JSON.stringify(bundle())); input(value, "confirmImport", "true"); persistEvaluatorSession(value.session, value.storage, value.pointers);
    const prepared = prepareEvaluatorSubmission(value.session, value.project, { kind: "import" });
    forkEvaluatorWriter(value.session); value.session.draft = prepared.draft; persistEvaluatorSession(value.session, value.storage, value.pointers);
    guardEvaluatorSubmission(value.session, value.project, prepared.id, prepared.change, value.storage, value.pointers);
    const receipt = structuredClone(value.project); receipt.revision++;
    receipt.production_design = { plan: null, heroes: [], capsules: [], evaluator_bundles: [bundle()], evaluation_reports: [] } as unknown as NonNullable<Project["production_design"]>;
    await acknowledgeEvaluatorSubmission(value.session, prepared.id, prepared.change, receipt);
    expect(value.session.draft.pending).toBeNull(); expect(evaluatorRaw(value.session.draft, "selected")).toBe(bundle().id + ":1");
    expect(receipt.branches).toEqual(value.project.branches); expect(receipt.schema_version).toBe(1);
  });
});

describe("Evaluator exact source-bound technical report receipts", () => {
  it("accepts the expected report, preserves selection and never changes human abstention into approval", async () => {
    const value = arm({ kind: "evaluate", sceneId: id(1) }), receipt = await evaluationReceipt(value);
    await acknowledgeEvaluatorSubmission(value.session, value.prepared.id, value.change, receipt);
    expect(value.session.draft.pending).toBeNull(); expect(evaluatorRaw(value.session.draft, "selected")).toBe(bundle().id + ":1");
    expect(receipt.production_design!.evaluation_reports![0]).toMatchObject({ source_revision: value.project.revision,
      source_generation: value.project.generation, conclusion: "needs_human_review", creative_approval: "not_assessed" });
    expect(receipt.production_design!.evaluation_reports![0].observations.map(item => item.outcome)).toEqual(["pass", "pass", "not_assessed"]);
  });
  it.each(["scene", "generation", "revision", "digest", "criterion", "conclusion", "approval", "unknown_field"])("rejects report %s forgery and retains UNKNOWN", async field => {
    const value = arm({ kind: "evaluate", sceneId: id(1) }), receipt = await evaluationReceipt(value), report = receipt.production_design!.evaluation_reports![0];
    if (field === "scene") report.scene_id = id(999);
    if (field === "generation") report.source_generation = id(999);
    if (field === "revision") report.source_revision++;
    if (field === "digest") report.evaluator_sha256 = "f".repeat(64);
    if (field === "criterion") report.observations[2].outcome = "pass";
    if (field === "conclusion") report.conclusion = "pass_technical_only";
    if (field === "approval") report.creative_approval = "approved";
    if (field === "unknown_field") Object.assign(report, { claimed_native_acceptance: true });
    await expect(acknowledgeEvaluatorSubmission(value.session, value.prepared.id, value.change, receipt)).rejects.toThrow(/exact source-bound/);
    expect(value.session.draft.pending?.outcome).toBe("UNKNOWN");
  });
  it("edits made while digest validation awaits survive because ACK reads the live session at completion", async () => {
    const value = arm({ kind: "evaluate", sceneId: id(1) }), receipt = await evaluationReceipt(value);
    const acknowledging = acknowledgeEvaluatorSubmission(value.session, value.prepared.id, value.change, receipt);
    input(value, "selected", ""); input(value, "importText", " {later invalid JSON ");
    await acknowledging;
    expect(value.session.draft.fields.selected.raw).toBe(""); expect(value.session.draft.fields.importText.raw).toBe(" {later invalid JSON ");
  });
  it("a different pending operation installed during receipt validation cannot be consumed by the late response", async () => {
    const value = arm({ kind: "evaluate", sceneId: id(1) }), receipt = await evaluationReceipt(value);
    const acknowledging = acknowledgeEvaluatorSubmission(value.session, value.prepared.id, value.change, receipt);
    value.session.draft = { ...value.session.draft, pending: { ...value.session.draft.pending!, id: id(999) } };
    await expect(acknowledging).rejects.toThrow(/attempt changed/); expect(value.session.draft.pending?.id).toBe(id(999));
  });
  it.each(["deliverables", "audio", "production_design"] as const)("rejects unrelated %s changes in an otherwise matching receipt", async field => {
    const value = arm(), receipt = registerReceipt(value);
    if (field === "deliverables") receipt.deliverables[0].width += 2;
    else if (field === "audio") receipt.audio.mix.voice_gain_db += 1;
    else receipt.production_design!.plan = { invented: true } as never;
    await expect(acknowledgeEvaluatorSubmission(value.session, value.prepared.id, value.change, receipt)).rejects.toThrow(/unrelated/);
    expect(value.session.draft.pending?.id).toBe(value.prepared.id);
  });
});

describe("Evaluator recovery custody preserves the first observation", () => {
  it("a silent data write blocks prepare until exact readback recovery succeeds", () => {
    const value = setup(); input(value, "importText", JSON.stringify(bundle())); input(value, "confirmImport", "true"); value.storage.silent = true;
    expect(() => persistEvaluatorSession(value.session, value.storage, value.pointers)).toThrow(/write\/readback/);
    expect(() => prepareEvaluatorSubmission(value.session, value.project, { kind: "import" })).toThrow(/Confirm/);
    value.storage.silent = false; retryEvaluatorCustody(value.session, value.storage, value.pointers);
    expect(value.session.custody.attempt).toBeNull(); expect(value.session.custody.expectedRaw).not.toBeNull();
  });
  it("a silent pointer failure leaves its attempted exact copy and blocks new intent", () => {
    const value = setup(); input(value, "importText", "Retained raw"); value.pointers.silent = true;
    expect(() => persistEvaluatorSession(value.session, value.storage, value.pointers)).toThrow(/pointer/);
    expect(value.storage.length).toBe(1); expect(value.session.custody.attempt).not.toBeNull();
    expect(() => prepareEvaluatorSubmission(value.session, value.project, { kind: "author" })).toThrow(/Confirm/);
  });
  it("corruption during final pointer confirmation is not a successful recoverable write", () => {
    const value = setup(); input(value, "label", "Keep"); const key = evaluatorCopyKey(value.session.draft.scope, value.session.custody.writer);
    let armedPointer = false; const original = value.pointers.setItem.bind(value.pointers);
    value.pointers.setItem = (name, raw) => { original(name, raw); armedPointer = true; };
    value.pointers.onGet = () => { if (armedPointer) value.storage.data.set(key, "corrupt after pointer"); };
    expect(() => persistEvaluatorSession(value.session, value.storage, value.pointers)).toThrow(/final pointer/);
    expect(value.session.custody.expectedRaw).toBeNull();
  });
  it("Retry cannot adopt another writer's pointer while inspecting an incomplete own write", () => {
    const value = setup(); input(value, "label", "Keep"); value.pointers.silent = true;
    expect(() => persistEvaluatorSession(value.session, value.storage, value.pointers)).toThrow(); value.pointers.silent = false;
    value.pointers.setItem(evaluatorPointerKey(value.session.draft.scope), id(999));
    expect(() => retryEvaluatorCustody(value.session, value.storage, value.pointers)).toThrow(/exact bytes\/pointer/);
  });
  it("a cloned tab cannot overwrite an original UNKNOWN copy and has no dispatch/ACK permit", () => {
    const value = arm(), { key, raw } = pointed(value), clonePointers = new Memory(); clonePointers.data = new Map(value.pointers.data);
    const clone = openEvaluatorSession(value.project, value.storage, clonePointers);
    editEvaluatorField(clone, value.project, "importText", " {clone draft"); persistEvaluatorSession(clone, value.storage, clonePointers);
    expect(value.storage.getItem(key)).toBe(raw); expect(value.storage.getItem(value.armedKey)).toBe(value.armedRaw);
    expect(clone.liveAttempt).toBeNull(); expect(clone.draft.pending?.outcome).toBe("UNKNOWN");
  });
  it("failed first pointer read recovers original UNKNOWN and keeps a later blank, including a return to default", () => {
    const value = arm(), { key, raw } = pointed(value);
    value.pointers.onGet = () => { throw new Error("Pointer unreadable"); };
    const reopened = openEvaluatorSession(value.project, value.storage, value.pointers); value.pointers.onGet = null;
    editEvaluatorField(reopened, value.project, "importText", "Later"); editEvaluatorField(reopened, value.project, "importText", "");
    editEvaluatorField(reopened, value.project, "label", "Later label"); editEvaluatorField(reopened, value.project, "label", EVALUATOR_DEFAULTS.label);
    expect(() => persistEvaluatorSession(reopened, value.storage, value.pointers)).toThrow(/failed source/);
    retryEvaluatorCustody(reopened, value.storage, value.pointers);
    expect(reopened.draft.pending?.id).toBe(value.prepared.id); expect(reopened.draft.fields.importText.raw).toBe("");
    expect(reopened.draft.fields.label.raw).toBe(EVALUATOR_DEFAULTS.label); expect(value.storage.getItem(key)).toBe(raw);
    expect(reopened.liveAttempt).toBeNull(); expect(() => prepareEvaluatorSubmission(reopened, value.project, { kind: "import" })).toThrow(/UNKNOWN/);
  });
  it("failed first copy read recovers the original bytes without overwriting later independent input", () => {
    const value = arm(), { key, raw } = pointed(value);
    value.storage.onGet = name => { if (name === key) throw new Error("Temporary source read failure"); };
    const reopened = openEvaluatorSession(value.project, value.storage, value.pointers); value.storage.onGet = null;
    editEvaluatorField(reopened, value.project, "steps", "Later independent steps\n\n");
    retryEvaluatorCustody(reopened, value.storage, value.pointers);
    expect(reopened.draft.pending?.id).toBe(value.prepared.id); expect(reopened.draft.fields.steps.raw).toBe("Later independent steps\n\n");
    expect(value.storage.getItem(key)).toBe(raw);
  });
  it.each(["{broken original bytes", "null", "{}"])("observed corrupt bytes %j cannot be replaced by a clean same-writer copy through Retry", corrupt => {
    const value = arm(), { key } = pointed(value); value.storage.data.set(key, corrupt);
    const reopened = openEvaluatorSession(value.project, value.storage, value.pointers);
    editEvaluatorField(reopened, value.project, "importText", "Keep later author input"); const before = structuredClone(reopened.draft);
    const replacement = JSON.stringify({ schema: "motionwright.evaluator-copy/1", writer: value.session.custody.writer, draft: setup().session.draft });
    value.storage.data.set(key, replacement);
    expect(() => retryEvaluatorCustody(reopened, value.storage, value.pointers)).toThrow(/original.*bytes changed/);
    expect(reopened.draft).toEqual(before); expect(reopened.failedSource?.raw).toBe(corrupt);
    expect(originalEvaluatorRecoveryCopy(reopened.draft.scope, reopened.failedSource)?.raw).toBe(corrupt);
    expect(() => prepareEvaluatorSubmission(reopened, value.project, { kind: "import" })).toThrow(/Confirm/);
  });
  it("successfully decoded source is still pinned until the first new writer is confirmed", () => {
    const value = arm(), { key } = pointed(value), reopened = openEvaluatorSession(value.project, value.storage, value.pointers);
    value.storage.data.set(key, JSON.stringify({ schema: "motionwright.evaluator-copy/1", writer: value.session.custody.writer, draft: setup().session.draft }));
    editEvaluatorField(reopened, value.project, "label", "Later"); const count = value.storage.length;
    expect(() => persistEvaluatorSession(reopened, value.storage, value.pointers)).toThrow(/original.*bytes changed/);
    expect(value.storage.length).toBe(count); expect(reopened.draft.pending?.id).toBe(value.prepared.id);
  });
  it("an original read during a partial listing remains pinned after a later index failure", () => {
    const value = arm(), { key, raw } = pointed(value);
    value.storage.onGet = name => { if (name === key) throw new Error("First source read failed"); };
    const reopened = openEvaluatorSession(value.project, value.storage, value.pointers); value.storage.onGet = null;
    value.storage.key = index => { if (index === 0) return key; throw new Error("Later index failed"); };
    expect(() => evaluatorCopies(value.storage, reopened.draft.scope, reopened.failedSource)).toThrow(/Later index/);
    expect(reopened.failedSource?.raw).toBe(raw);
    value.storage.data.set(key, "replacement");
    expect(() => retryEvaluatorCustody(reopened, value.storage, value.pointers)).toThrow(/original.*bytes changed/);
  });
  it("pins an observed null before continue during partial listing and never adopts a later same-writer replacement", () => {
    const value = arm(), { key } = pointed(value);
    value.storage.onGet = name => { if (name === key) throw new Error("First source read failed"); };
    const reopened = openEvaluatorSession(value.project, value.storage, value.pointers); value.storage.onGet = null;
    editEvaluatorField(reopened, value.project, "importText", "Later input"); const before = structuredClone(reopened.draft);
    value.storage.data.delete(key); value.storage.data.set("unrelated-list-entry", "keep");
    value.storage.key = index => { if (index === 0) return key; throw new Error("Later index failed"); };
    expect(() => evaluatorCopies(value.storage, reopened.draft.scope, reopened.failedSource)).toThrow(/Later index/);
    expect(reopened.failedSource?.rawObserved).toBe(true); expect(reopened.failedSource?.raw).toBeNull();
    const replacement = JSON.stringify({ schema: "motionwright.evaluator-copy/1", writer: value.session.custody.writer, draft: setup().session.draft });
    value.storage.data.set(key, replacement);
    expect(() => retryEvaluatorCustody(reopened, value.storage, value.pointers)).toThrow(/original.*bytes changed/);
    expect(reopened.draft).toEqual(before); expect(reopened.failedSource?.raw).toBeNull(); expect(value.storage.getItem(key)).toBe(replacement);
  });
  it("an observed missing pointed copy cannot become a fresh saveable session even when the key later appears", () => {
    const value = setup(), scope = evaluatorScope(value.project); value.pointers.setItem(evaluatorPointerKey(scope), id(999));
    const reopened = openEvaluatorSession(value.project, value.storage, value.pointers);
    expect(() => retryEvaluatorCustody(reopened, value.storage, value.pointers)).toThrow(/still missing/);
    value.storage.setItem(evaluatorCopyKey(scope, id(999)), JSON.stringify({ schema: "motionwright.evaluator-copy/1", writer: id(999), draft: value.session.draft }));
    expect(() => retryEvaluatorCustody(reopened, value.storage, value.pointers)).toThrow(/original.*bytes changed/);
  });
  it("changed bytes during recovery confirmation cannot mutate the later author buffer first", () => {
    const value = arm(), { key } = pointed(value);
    value.storage.onGet = name => { if (name === key) throw new Error("Initial read failed"); };
    const reopened = openEvaluatorSession(value.project, value.storage, value.pointers); value.storage.onGet = null;
    editEvaluatorField(reopened, value.project, "purpose", "Later purpose"); const before = structuredClone(reopened.draft); let reads = 0;
    value.storage.onGet = name => { if (name === key && ++reads === 2) value.storage.data.set(key, "changed during confirmation"); };
    expect(() => retryEvaluatorCustody(reopened, value.storage, value.pointers)).toThrow(/changed/); expect(reopened.draft).toEqual(before);
  });
  it("interrupted first publication preserves UNKNOWN plus later blank input through exact Retry", () => {
    const value = arm(), { key, raw } = pointed(value);
    value.storage.onGet = name => { if (name === key) throw new Error("Initial source failed"); };
    const reopened = openEvaluatorSession(value.project, value.storage, value.pointers); value.storage.onGet = null;
    editEvaluatorField(reopened, value.project, "importText", "Later"); value.pointers.silent = true;
    expect(() => retryEvaluatorCustody(reopened, value.storage, value.pointers)).toThrow(/pointer/);
    editEvaluatorField(reopened, value.project, "importText", ""); value.pointers.silent = false;
    retryEvaluatorCustody(reopened, value.storage, value.pointers);
    expect(reopened.draft.pending?.id).toBe(value.prepared.id); expect(reopened.draft.fields.importText.raw).toBe("");
    expect(value.storage.getItem(key)).toBe(raw); expect(openEvaluatorSession(value.project, value.storage, value.pointers).draft.pending?.outcome).toBe("UNKNOWN");
  });
  it("NOT_SENT is retained live but decoded conservatively as UNKNOWN without live dispatch authority", () => {
    const value = arm(); value.session.draft = retainEvaluatorFailure(value.session.draft, value.prepared.id, "Before callback", true);
    persistEvaluatorSession(value.session, value.storage, value.pointers);
    expect(value.session.draft.pending?.outcome).toBe("NOT_SENT");
    expect(decodeEvaluatorCopy(value.session.custody.expectedRaw!, value.session.draft.scope, value.session.custody.writer).draft.pending?.outcome).toBe("UNKNOWN");
  });
  it("failed ACK retention leaves an armed UNKNOWN recoverable at the previous pointer", async () => {
    const value = arm(), { key, raw } = pointed(value), receipt = registerReceipt(value);
    await acknowledgeEvaluatorSubmission(value.session, value.prepared.id, value.change, receipt); forkEvaluatorWriter(value.session);
    value.pointers.silent = true; expect(() => persistEvaluatorSession(value.session, value.storage, value.pointers)).toThrow(/pointer/);
    expect(value.storage.getItem(key)).toBe(raw); expect(value.storage.getItem(value.armedKey)).toBe(value.armedRaw);
    expect(openEvaluatorSession(receipt, value.storage, value.pointers).draft.pending?.id).toBe(value.prepared.id);
  });
  it("author exports preserve exact raw values and imported UNKNOWN cannot replace an existing UNKNOWN", () => {
    const value = arm(), file = exportEvaluatorDraft(value.session.draft), decoded = importEvaluatorDraft(file, value.session.draft.scope);
    expect(decoded.fields.importText.raw).toBe(evaluatorRaw(value.session.draft, "importText")); expect(decoded.pending?.outcome).toBe("UNKNOWN");
    expect(() => adoptEvaluatorFile(value.session, decoded, value.storage, value.pointers)).toThrow(/UNKNOWN/);
    expect(() => restoreEvaluatorCopy(value.session, pointed(value).raw, value.session.custody.writer, value.storage, value.pointers)).toThrow(/UNKNOWN/);
    expect(() => importEvaluatorDraft(file, JSON.stringify([id(999), value.project.generation, value.project.active_branch]))).toThrow(/foreign/);
  });
  it("a clean checkpoint is not misrepresented as a historical author draft", () => {
    const value = setup(); persistEvaluatorSession(value.session, value.storage, value.pointers);
    expect(() => restoreEvaluatorCopy(value.session, value.session.custody.expectedRaw!, value.session.custody.writer, value.storage, value.pointers)).toThrow(/clean checkpoint/);
  });
  it("failed retention of an explicitly imported UNKNOWN never rolls back to a saveable clean buffer", () => {
    const unknown = arm(), value = setup(), imported = importEvaluatorDraft(exportEvaluatorDraft(unknown.session.draft), value.session.draft.scope);
    persistEvaluatorSession(value.session, value.storage, value.pointers);
    const originalSet = value.pointers.setItem.bind(value.pointers); let writes = 0;
    value.pointers.setItem = (key, raw) => { if (++writes < 2) originalSet(key, raw); };
    expect(() => adoptEvaluatorFile(value.session, imported, value.storage, value.pointers)).toThrow(/pointer/);
    expect(value.session.draft.pending?.id).toBe(unknown.prepared.id); value.pointers.setItem = originalSet;
    retryEvaluatorCustody(value.session, value.storage, value.pointers);
    expect(value.session.draft.pending?.outcome).toBe("UNKNOWN");
    expect(() => prepareEvaluatorSubmission(value.session, value.project, { kind: "import" })).toThrow(/UNKNOWN/);
  });
});

describe("Evaluator UNKNOWN review requires the complete stable saved Project and journal", () => {
  it("two identical full reads plus contiguous history permit review but neither acknowledge nor dispatch", async () => {
    const value = arm(), receipt = registerReceipt(value), before = structuredClone(value.session.draft); let reads = 0;
    const reordered = Object.fromEntries(Object.entries(receipt).reverse()) as unknown as Project;
    const review = await inspectEvaluatorOutcome(value.session, receipt, { readProject: async () => { reads++; return structuredClone(reordered); },
      readHistory: async () => [event(receipt, value.change)] });
    expect(reads).toBe(2); expect(review.matchingRevisions).toEqual([receipt.revision]); expect(evaluatorReviewCurrent(value.session, receipt, review)).toBe(true);
    expect(value.session.draft).toEqual(before); expect(value.session.draft.pending?.outcome).toBe("UNKNOWN");
    expect(() => guardEvaluatorSubmission(value.session, receipt, value.prepared.id, value.change, value.storage, value.pointers)).toThrow(/never replayed/);
  });
  it.each(["deliverables", "audio", "production_design"] as const)("refuses different %s with the same stamp at initial read, final read and review reuse", async field => {
    const value = arm(), receipt = registerReceipt(value), different = structuredClone(receipt), before = structuredClone(value.session.draft);
    if (field === "deliverables") different.deliverables[0].width += 2;
    else if (field === "audio") different.audio.mix.voice_gain_db += 1;
    else delete different.production_design;
    expect([different.id, different.generation, different.active_branch, different.revision])
      .toEqual([receipt.id, receipt.generation, receipt.active_branch, receipt.revision]);
    let historyReads = 0;
    await expect(inspectEvaluatorOutcome(value.session, receipt, { readProject: async () => structuredClone(different),
      readHistory: async () => { historyReads++; return [event(receipt, value.change)]; } })).rejects.toThrow(/Reload the current saved project/);
    expect(historyReads).toBe(0); let reads = 0;
    await expect(inspectEvaluatorOutcome(value.session, receipt, { readProject: async () => structuredClone(++reads === 1 ? receipt : different),
      readHistory: async () => { historyReads++; return [event(receipt, value.change)]; } })).rejects.toThrow(/saved project changed during outcome review/);
    expect(reads).toBe(2); expect(historyReads).toBe(1);
    const review = await inspectEvaluatorOutcome(value.session, receipt, { readProject: async () => receipt, readHistory: async () => [event(receipt, value.change)] });
    expect(evaluatorReviewCurrent(value.session, receipt, review)).toBe(true); expect(evaluatorReviewCurrent(value.session, different, review)).toBe(false);
    expect(() => prepareEvaluatorSubmission(value.session, different, { kind: "author" }, review)).toThrow(/UNKNOWN is retained/);
    expect(value.session.draft).toEqual(before);
  });
  it("rejects history gaps and a late field edit while reading the journal", async () => {
    const value = arm(), receipt = registerReceipt(value);
    await expect(inspectEvaluatorOutcome(value.session, receipt, { readProject: async () => receipt, readHistory: async () => [] })).rejects.toThrow(/incomplete/);
    await expect(inspectEvaluatorOutcome(value.session, receipt, { readProject: async () => receipt,
      readHistory: async () => [{ ...event(receipt, value.change), revision: receipt.revision + 1 }] })).rejects.toThrow(/contiguous/);
    await expect(inspectEvaluatorOutcome(value.session, receipt, { readProject: async () => receipt,
      readHistory: async () => { editEvaluatorField(value.session, receipt, "importText", "Later"); return [event(receipt, value.change)]; } })).rejects.toThrow(/changed during/);
    expect(value.session.draft.pending?.id).toBe(value.prepared.id); expect(evaluatorRaw(value.session.draft, "importText")).toBe("Later");
  });
  it("an explicitly reviewed new operation forks and preserves the original UNKNOWN instead of replaying it", async () => {
    const value = arm(), receipt = registerReceipt(value);
    editEvaluatorField(value.session, receipt, "author", "I authored a separate original recipe"); persistEvaluatorSession(value.session, value.storage, value.pointers);
    const original = pointed(value);
    const review = await inspectEvaluatorOutcome(value.session, receipt, { readProject: async () => receipt, readHistory: async () => [event(receipt, value.change)] });
    const next = prepareEvaluatorSubmission(value.session, receipt, { kind: "author" }, review, id(999));
    expect(value.session.draft.pending?.id).toBe(value.prepared.id); expect(next.id).not.toBe(value.prepared.id);
    forkEvaluatorWriter(value.session); value.session.draft = next.draft; value.session.epoch++;
    persistEvaluatorSession(value.session, value.storage, value.pointers);
    guardEvaluatorSubmission(value.session, receipt, next.id, next.change, value.storage, value.pointers);
    expect(value.storage.getItem(original.key)).toBe(original.raw); expect(value.storage.getItem(value.armedKey)).toBe(value.armedRaw);
    expect(decodeEvaluatorCopy(original.raw, value.session.draft.scope, original.key.slice(original.key.lastIndexOf(":") + 1)).draft.pending?.id).toBe(value.prepared.id);
  });
  it("omitting the fork for a reviewed new attempt fails before overwriting the original UNKNOWN bytes", async () => {
    const value = arm(); input(value, "author", "I authored a separately chosen recipe");
    persistEvaluatorSession(value.session, value.storage, value.pointers); const original = pointed(value);
    const review = await inspectEvaluatorOutcome(value.session, value.project, { readProject: async () => value.project, readHistory: async () => [] });
    const next = prepareEvaluatorSubmission(value.session, value.project, { kind: "author" }, review, id(999));
    value.session.draft = next.draft;
    expect(() => persistEvaluatorSession(value.session, value.storage, value.pointers)).toThrow(/Fork the evaluator writer/);
    expect(value.storage.getItem(original.key)).toBe(original.raw); expect(value.storage.getItem(value.armedKey)).toBe(value.armedRaw);
    expect(value.session.custody.attempt).toBeNull();
  });
  it("same-stamp source changes before dispatch block the callback while preserving prepared intent", () => {
    const value = setup(); input(value, "importText", JSON.stringify(bundle())); input(value, "confirmImport", "true");
    persistEvaluatorSession(value.session, value.storage, value.pointers); const prepared = prepareEvaluatorSubmission(value.session, value.project, { kind: "import" });
    forkEvaluatorWriter(value.session); value.session.draft = prepared.draft; persistEvaluatorSession(value.session, value.storage, value.pointers);
    const changed = structuredClone(value.project); changed.audio.mix.voice_gain_db += 1;
    expect(() => guardEvaluatorSubmission(value.session, changed, prepared.id, prepared.change, value.storage, value.pointers)).toThrow(/complete evaluator Project changed/);
    expect(value.session.liveAttempt?.dispatched).toBe(false); expect(value.session.draft.pending?.id).toBe(prepared.id);
  });
});
